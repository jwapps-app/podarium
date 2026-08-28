"""Audio streaming and artwork.

Both endpoints exist so that a client never has to touch a publisher host. Full
``Range``/``206``/``Accept-Ranges`` support is not optional here: AVPlayer on iOS seeks by
issuing byte-range requests, and a server that answers them with 200 breaks scrubbing.
"""

from __future__ import annotations

import re
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.clients.http import build_client
from podarium.db import get_session
from podarium.jobs.artwork import artwork_by_hash, ensure_episode_artwork, ensure_feed_artwork
from podarium.models import ArtworkCache, Episode, Feed, User
from podarium.services import get_app_settings
from podarium.streaming import (
    VERSION_PARAM,
    copy_for_token,
    preferred_copy,
)

router = APIRouter(prefix="/api", tags=["media"])

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK_SIZE = 256 * 1024


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return an inclusive (start, end) for a single-range request, or None for the whole file.

    Raises 416 for a syntactically valid range that falls outside the file, which is what
    a player needs in order to correct itself rather than hang.
    """
    if not header:
        return None
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None

    raw_start, raw_end = match.groups()
    if not raw_start and not raw_end:
        return None

    if not raw_start:
        # "bytes=-500" means the final 500 bytes.
        length = int(raw_end)
        if length <= 0:
            raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, detail="Invalid range")
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1

    if start >= size or start > end:
        # Deliberately not a 416.
        #
        # 416 is the correct answer and it is the wrong thing to send. A client asking for
        # a byte past the end is a client working from a length that no longer holds --
        # the file behind this episode was replaced by its trimmed copy. Observed on iOS:
        # a 416 is not read as "re-read the length", it is retried immediately, sixty-three
        # bytes further on, over and over, while playback sits still.
        #
        # RFC 9110 lets a server ignore a Range it does not wish to honour. Ignoring it
        # hands back the whole current file, which is a length the client can believe, and
        # it recovers on its own.
        return None
    return start, min(end, size - 1)


def _iter_file(path: Path, start: int, end: int):
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _validators(path: Path) -> tuple[str, str, float, int]:
    """An entity tag and a last-modified date for a file on disk.

    Derived from size and mtime rather than a hash of the contents: these files run to
    hundreds of megabytes, and the question being answered is only "is this the same file
    I was reading a moment ago", which size and mtime settle.
    """
    stat = path.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    return etag, formatdate(stat.st_mtime, usegmt=True), stat.st_mtime, stat.st_size


def _range_still_applies(header: str | None, etag: str, mtime: float) -> bool:
    """Whether an If-Range condition still holds, per RFC 9110.

    This is the whole point of the header, and this server needs it more than most: the
    file behind an episode is replaced when trimming finishes, and the replacement is a
    third shorter with an entirely different byte-to-time mapping. A player that started
    on the original and asks for a later range holds offsets that mean nothing in the new
    file -- so it lands past the end, decides the media is over, and stops mid-episode.

    Answering such a request with 200 and the whole new file is what the header is for:
    the player throws away what it had and resynchronises.
    """
    if not header:
        return True
    candidate = header.strip()
    if candidate.startswith(("W/", '"')):
        # Weak tags never validate a range; only a strong, exact match does.
        return candidate == etag
    try:
        return int(parsedate_to_datetime(candidate).timestamp()) == int(mtime)
    except (TypeError, ValueError):
        return False


def _serve_local(path: Path, request: Request, media_type: str) -> Response:
    etag, last_modified, mtime, size = _validators(path)
    # Validators on every response, including the 206s. Without them a client has no way
    # to notice the bytes it is reading now came from a different file than the bytes it
    # read a minute ago, and no way to ask us to check.
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
    }

    byte_range = (
        parse_range(request.headers.get("range"), size)
        if _range_still_applies(request.headers.get("if-range"), etag, mtime)
        else None
    )

    if byte_range is None:
        return FileResponse(
            path,
            media_type=media_type,
            headers={**headers, "Content-Length": str(size)},
        )

    start, end = byte_range
    return StreamingResponse(
        _iter_file(path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )


async def _proxy_origin(episode: Episode, request: Request, user_agent: str) -> Response:
    """Stream an undownloaded episode through this server.

    The client still never contacts the publisher. Nothing is written to disk here on
    purpose: audio lands on disk when an episode is queued or explicitly requested, and
    silently caching every scrub would undo that.
    """
    headers = {}
    if range_header := request.headers.get("range"):
        headers["Range"] = range_header

    client = build_client(user_agent)
    try:
        upstream = await client.send(
            client.build_request("GET", episode.enclosure_url, headers=headers), stream=True
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Upstream fetch failed: {exc}") from exc

    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Upstream returned {upstream.status_code}")

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=CHUNK_SIZE):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    # Keys are normalised to lower case before setdefault: a plain dict would otherwise
    # treat "accept-ranges" and "Accept-Ranges" as two headers and emit both.
    passthrough = {"content-length", "content-range", "accept-ranges", "content-type"}
    response_headers = {
        k.lower(): v for k, v in upstream.headers.items() if k.lower() in passthrough
    }
    response_headers.setdefault("accept-ranges", "bytes")

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type", "audio/mpeg"),
    )


@router.head("/stream/{episode_id}", include_in_schema=False)
@router.get("/stream/{episode_id}")
async def stream_episode(
    episode_id: int,
    request: Request,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode not found")

    # The URL names which copy it wants, so that the bytes behind a given URL never
    # change: see podarium.streaming. An unrecognised or absent token takes whichever
    # copy is preferred now.
    chosen = copy_for_token(episode, request.query_params.get(VERSION_PARAM))
    if chosen is None:
        preferred = preferred_copy(episode)
        chosen = (preferred[0], preferred[1]) if preferred else None
    if chosen is not None:
        return _serve_local(chosen[0], request, chosen[1])

    if not episode.enclosure_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode has no audio")

    app_settings = await get_app_settings(session)
    return await _proxy_origin(episode, request, app_settings.user_agent)


def _artwork_response(
    entry: ArtworkCache | None, request: Request, *, immutable: bool = False
) -> Response:
    """Shared cache handling for every artwork route.

    Two policies, chosen by how the image is addressed. /api/images/feed/2 is addressed by
    *object*: the mapping can change when a publisher swaps cover art, so it carries an
    ETag keyed to the source URL's hash and revalidates hourly -- new art means a new hash
    means a refetch, at worst an hour late. /api/images/cache/{hash} is addressed by
    *content*: a given hash serves one image forever, so it is marked immutable and the
    browser never asks again. Artwork dominates this app's request count -- a library page
    is one JSON fetch and a tile's worth of images -- so the difference is most of the
    requests a phone makes.

    "private" because these endpoints are behind auth and must not sit in a shared proxy.
    """
    if entry is None or not entry.local_path or not Path(entry.local_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No artwork available")

    etag = f'"{entry.url_hash}"'
    policy = (
        "private, max-age=31536000, immutable"
        if immutable
        else "private, max-age=3600, must-revalidate"
    )
    cache_headers = {"ETag": etag, "Cache-Control": policy}

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

    return FileResponse(
        entry.local_path,
        media_type=entry.content_type or "image/jpeg",
        headers=cache_headers,
    )


@router.get("/images/cache/{url_hash}")
async def get_cached_image(
    url_hash: str,
    request: Request,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Artwork for a show that is not subscribed -- search results, mainly.

    Search would otherwise be the one place the browser still talks to publisher CDNs, and
    it is the worst place for it: you scroll past dozens of shows you never subscribe to,
    handing your IP to each of their hosts. The hash is minted by the server when it
    returns the results, so this cannot be pointed at an arbitrary address.
    """
    if len(url_hash) != 64 or not all(c in "0123456789abcdef" for c in url_hash):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown image")

    app_settings = await get_app_settings(session)
    entry = await artwork_by_hash(session, url_hash, user_agent=app_settings.user_agent)
    return _artwork_response(entry, request, immutable=True)


@router.get("/images/{kind}/{object_id}")
async def get_image(
    kind: str,
    object_id: int,
    request: Request,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if kind not in {"feed", "episode"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown image kind")

    app_settings = await get_app_settings(session)

    if kind == "feed":
        feed = await session.get(Feed, object_id)
        if feed is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Feed not found")
        entry = await ensure_feed_artwork(session, feed, user_agent=app_settings.user_agent)
    else:
        episode = await session.get(Episode, object_id)
        if episode is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode not found")
        entry = await ensure_episode_artwork(session, episode, user_agent=app_settings.user_agent)
        if entry is None or not entry.local_path:
            # Episodes usually inherit the show's artwork rather than carrying their own.
            feed = await session.get(Feed, episode.feed_id)
            if feed is not None:
                entry = await ensure_feed_artwork(session, feed, user_agent=app_settings.user_agent)

    return _artwork_response(entry, request)
