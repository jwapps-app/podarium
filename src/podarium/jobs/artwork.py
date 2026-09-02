"""Server-side artwork cache.

Clients are handed ``/api/images/...`` and never a publisher CDN URL, so image loads in
the web UI and the iOS app do not leak a device IP to a publisher either.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.clients.http import build_client
from podarium.config import get_settings
from podarium.models import ArtworkCache, Episode, Feed

log = logging.getLogger(__name__)

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}

MAX_ARTWORK_BYTES = 12 * 1024 * 1024

# What the first bytes of each format look like. The publisher's Content-Type header is a
# claim; this is the file. Publishers serve JPEGs as text/plain and octet-stream often
# enough that the header alone would refuse real artwork, and a header of image/svg+xml on
# a file that is not an SVG is the one that matters: what is served back is served from
# this origin, and SVG can carry script.
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def detect_image_type(head: bytes) -> str | None:
    """The image format the bytes actually are, from their signature."""
    for magic, media_type in _MAGIC:
        if head.startswith(magic):
            return media_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


def canonical_image_type(declared: str | None) -> str | None:
    """A declared type, if it is one this server serves; None for anything else."""
    base = (declared or "").split(";")[0].strip().lower()
    if base == "image/jpg":
        base = "image/jpeg"
    return base if base in _EXTENSIONS else None


def image_type_for(head: bytes, declared: str | None) -> str | None:
    """Signature first, header second. None means this is not an image we will store."""
    return detect_image_type(head) or canonical_image_type(declared)


def served_image_type(entry: ArtworkCache) -> str:
    """The Content-Type to put on a cached image.

    The stored type is trusted only if it is one of ours. Rows written before types were
    checked may hold whatever a publisher sent; for those the file is sniffed, and a file
    that is not recognisably an image goes out as octet-stream, which nothing renders.
    """
    known = canonical_image_type(entry.content_type)
    if known:
        return known
    try:
        with open(entry.local_path, "rb") as handle:  # type: ignore[arg-type]
            sniffed = detect_image_type(handle.read(16))
    except OSError:
        sniffed = None
    return sniffed or "application/octet-stream"


# How long to leave a failed image alone before trying it again.
#
# Some feeds carry artwork URLs that simply do not work -- one here points at a bare
# directory and answers 403 every time. Without a pause, every library page load fires
# another request at a host already refusing us, which is wasted traffic at best and a way
# to get an address blocked at worst. Publishers do fix these, so it retries, just not
# dozens of times an hour.
RETRY_FAILED_AFTER = timedelta(hours=6)

# One in-flight fetch per URL hash. Two episodes sharing a feed image on a cold cache
# would otherwise both download it.
_locks: dict[str, asyncio.Lock] = {}


def url_hash(source_url: str) -> str:
    """Key the cache by URL, not by title -- a publisher retitling a show must not churn it."""
    return hashlib.sha256(source_url.encode()).hexdigest()


def _target_path(digest: str, content_type: str | None) -> Path:
    extension = _EXTENSIONS.get((content_type or "").split(";")[0].strip().lower(), ".img")
    root = get_settings().artwork_dir
    return root / digest[:2] / f"{digest}{extension}"


async def ensure_artwork(session: AsyncSession, source_url: str, *, user_agent: str) -> ArtworkCache | None:
    """Fetch and cache an image once. Safe to call repeatedly."""
    digest = url_hash(source_url)
    lock = _locks.setdefault(digest, asyncio.Lock())

    try:
        async with lock:
            return await _ensure_artwork_locked(session, source_url, digest, user_agent=user_agent)
    finally:
        # Dropped once nobody is waiting on it, or the table grows by one entry per image
        # ever requested for the life of the process.
        if not lock.locked() and _locks.get(digest) is lock:
            del _locks[digest]


async def _ensure_artwork_locked(
    session: AsyncSession, source_url: str, digest: str, *, user_agent: str
) -> ArtworkCache | None:
    entry = (
        await session.execute(select(ArtworkCache).where(ArtworkCache.url_hash == digest))
    ).scalar_one_or_none()

    if entry is not None and entry.local_path and Path(entry.local_path).exists():
        return entry

    if entry is not None and entry.fetch_error and entry.fetched_at is not None:
        last_try = entry.fetched_at
        if last_try.tzinfo is None:
            last_try = last_try.replace(tzinfo=UTC)
        if datetime.now(UTC) - last_try < RETRY_FAILED_AFTER:
            return entry

    if entry is None:
        entry = ArtworkCache(url_hash=digest, source_url=source_url)
        session.add(entry)

    try:
        async with build_client(user_agent) as client:
            # Streamed with the ceiling enforced as bytes arrive. Checking len() after
            # a full read enforced the limit on storage but not on memory -- the whole
            # oversized body was already held before the check ran.
            async with client.stream("GET", source_url) as response:
                response.raise_for_status()
                buffer = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    buffer.extend(chunk)
                    if len(buffer) > MAX_ARTWORK_BYTES:
                        raise ValueError(f"artwork too large: over {MAX_ARTWORK_BYTES} bytes")
                payload = bytes(buffer)
                declared = response.headers.get("content-type")

        content_type = image_type_for(payload[:16], declared)
        if content_type is None:
            raise ValueError(f"not an image (Content-Type {declared or 'absent'})")

        path = _target_path(digest, content_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(payload)
        temporary.replace(path)

        entry.local_path = str(path)
        entry.content_type = content_type
        entry.fetched_at = datetime.now(UTC)
        entry.fetch_error = None
    except Exception as exc:  # noqa: BLE001 - a missing image must not fail a refresh
        entry.fetch_error = f"{type(exc).__name__}: {exc}"[:500]
        # Stamped on failure too, so this records the last *attempt* rather than the
        # last success -- which is what the backoff above needs to read.
        entry.fetched_at = datetime.now(UTC)
        log.info("artwork fetch failed for %s: %s", source_url, entry.fetch_error)

    await session.commit()
    return entry


async def register_artwork(session: AsyncSession, source_url: str) -> str:
    """Record an image URL without fetching it, and return its hash.

    Used for search results, where the show is not subscribed and the image may never be
    looked at. Minting the hash server-side is what keeps /api/images/cache from being an
    open proxy: a client can only ask for a URL this server already chose to record, so
    there is no attacker-controlled address to redirect it at.
    """
    digest = url_hash(source_url)
    existing = (
        await session.execute(select(ArtworkCache).where(ArtworkCache.url_hash == digest))
    ).scalar_one_or_none()
    if existing is None:
        session.add(ArtworkCache(url_hash=digest, source_url=source_url))
        await session.commit()
    return digest


async def artwork_by_hash(
    session: AsyncSession, digest: str, *, user_agent: str
) -> ArtworkCache | None:
    """Serve a previously registered image, fetching it on first request."""
    entry = (
        await session.execute(select(ArtworkCache).where(ArtworkCache.url_hash == digest))
    ).scalar_one_or_none()
    if entry is None:
        return None
    if entry.local_path and Path(entry.local_path).exists():
        return entry
    return await ensure_artwork(session, entry.source_url, user_agent=user_agent)


async def ensure_feed_artwork(session: AsyncSession, feed: Feed, *, user_agent: str) -> ArtworkCache | None:
    if not feed.image_url:
        return None
    return await ensure_artwork(session, feed.image_url, user_agent=user_agent)


async def ensure_episode_artwork(session: AsyncSession, episode: Episode, *, user_agent: str) -> ArtworkCache | None:
    if not episode.image_url:
        return None
    return await ensure_artwork(session, episode.image_url, user_agent=user_agent)
