"""RSS fetching and parsing.

Once a feed is subscribed, the RSS document is the source of truth -- Podcast Index is
discovery only (spec 7).
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

from podarium.clients.http import build_client

log = logging.getLogger("podarium")


@dataclass(slots=True)
class ParsedEpisode:
    guid: str
    title: str | None = None
    description_html: str | None = None
    image_url: str | None = None
    episode_number: int | None = None
    season: int | None = None
    explicit: bool = False
    published_at: datetime | None = None
    duration_seconds: int | None = None
    enclosure_url: str | None = None
    enclosure_type: str | None = None
    enclosure_bytes: int | None = None
    chapters_url: str | None = None
    transcript_url: str | None = None
    transcript_type: str | None = None


@dataclass(slots=True)
class ParsedFeed:
    title: str | None = None
    author: str | None = None
    description: str | None = None
    link: str | None = None
    language: str | None = None
    image_url: str | None = None
    explicit: bool = False
    episodes: list[ParsedEpisode] = field(default_factory=list)


@dataclass(slots=True)
class FetchResult:
    status_code: int
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    parsed: ParsedFeed | None = None
    final_url: str | None = None


_DURATION_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")


def _to_datetime(struct_time) -> datetime | None:
    if not struct_time:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(struct_time), tz=UTC)
    except (ValueError, OverflowError, TypeError):
        return None


def parse_duration(raw: str | None) -> int | None:
    """iTunes durations arrive as seconds, MM:SS, or HH:MM:SS depending on the publisher."""
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.isdigit():
        return int(raw)
    m = _DURATION_RE.match(raw)
    if not m:
        return None
    hours, minutes, seconds = m.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def _int_or_none(raw) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _short(raw, limit: int) -> str | None:
    """A publisher string cut to fit its column.

    These land in VARCHAR columns, and a value past the width fails the whole refresh at
    commit -- every hour, with backoff, for as long as the feed carries it. Nothing here
    is worth that: a language tag or a MIME type past the limit is garbage either way.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:limit] if text else None


# Column widths from the model, so a change there is caught here.
LANGUAGE_MAX = 32
MIME_MAX = 128


def _is_explicit(raw) -> bool:
    return str(raw).strip().lower() in {"yes", "true", "explicit"}


def _entry_guid(entry) -> str | None:
    """Pick the most stable identifier the entry offers.

    Order matters: a publisher that rewrites titles and re-stamps pubDate usually keeps
    <guid> intact, and when it does not, the enclosure URL is the next most stable thing.
    Falling through to the title would make every title edit look like a new episode.
    """
    for key in ("id", "guid"):
        value = entry.get(key)
        if value:
            return str(value).strip()
    for enclosure in entry.get("enclosures") or []:
        if enclosure.get("href"):
            return str(enclosure["href"]).strip()
    link = entry.get("link")
    return str(link).strip() if link else None


def _pick_enclosure(entry) -> dict | None:
    enclosures = entry.get("enclosures") or []
    for enclosure in enclosures:
        if str(enclosure.get("type", "")).startswith("audio/"):
            return enclosure
    return enclosures[0] if enclosures else None


def _entry_image(entry) -> str | None:
    image = entry.get("image")
    if isinstance(image, dict) and image.get("href"):
        return image["href"]
    itunes_image = entry.get("itunes_image")
    if isinstance(itunes_image, dict) and itunes_image.get("href"):
        return itunes_image["href"]
    return None


def _chapters_url(entry: object) -> str | None:
    """The ``podcast:chapters`` link, when the publisher supplies a JSON one.

    feedparser has no mapping for the podcast namespace, so the element arrives as a
    generic key with its attributes intact. Only the JSON type is taken: the spec allows
    others in principle, and a URL we cannot parse is worse than none because it would
    look like chapters exist right up until it is opened.
    """
    raw = entry.get("podcast_chapters") if hasattr(entry, "get") else None
    if isinstance(raw, dict):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = [item for item in raw if isinstance(item, dict)]
    else:
        return None

    for item in candidates:
        url = item.get("url") or item.get("href")
        mime = (item.get("type") or "").lower()
        if url and ("json" in mime or not mime):
            return url
    return None


# Transcript formats, best first. Plain text and SRT/VTT are what a search index wants;
# HTML would need stripping and JSON needs a schema nobody agrees on.
_TRANSCRIPT_PREFERENCE = ("text/plain", "application/srt", "text/srt", "text/vtt", "application/x-subrip")


def _transcript_link(entry: object) -> tuple[str | None, str | None]:
    """The best ``podcast:transcript`` a feed offers, as (url, type).

    A show commonly publishes the same transcript several ways. The preference order picks
    the one that turns into searchable text with the least mangling; anything unrecognised
    is taken only if nothing better is there, since a wrong guess costs one fetch and is
    discarded at parse time.
    """
    raw = entry.get("podcast_transcript") if hasattr(entry, "get") else None
    if isinstance(raw, dict):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = [item for item in raw if isinstance(item, dict)]
    else:
        return None, None

    def rank(item: dict) -> int:
        mime = (item.get("type") or "").lower()
        for index, known in enumerate(_TRANSCRIPT_PREFERENCE):
            if known in mime:
                return index
        return len(_TRANSCRIPT_PREFERENCE)

    usable = [item for item in candidates if item.get("url") or item.get("href")]
    if not usable:
        return None, None

    best = min(usable, key=rank)
    return best.get("url") or best.get("href"), (best.get("type") or None)


def parse_feed_bytes(raw: bytes) -> ParsedFeed:
    document = feedparser.parse(raw)
    channel = document.feed

    image_url = None
    if isinstance(channel.get("image"), dict):
        image_url = channel["image"].get("href")
    if not image_url and isinstance(channel.get("itunes_image"), dict):
        image_url = channel["itunes_image"].get("href")

    parsed = ParsedFeed(
        title=channel.get("title"),
        author=channel.get("author") or channel.get("itunes_author") or channel.get("publisher"),
        description=channel.get("subtitle") or channel.get("description"),
        link=channel.get("link"),
        language=_short(channel.get("language"), LANGUAGE_MAX),
        image_url=image_url,
        explicit=_is_explicit(channel.get("itunes_explicit")),
    )

    for entry in document.entries:
        guid = _entry_guid(entry)
        if not guid:
            continue
        enclosure = _pick_enclosure(entry) or {}
        content = entry.get("content")
        description = None
        if content and isinstance(content, list) and content[0].get("value"):
            description = content[0]["value"]
        else:
            description = entry.get("summary") or entry.get("description")

        transcript = _transcript_link(entry)

        parsed.episodes.append(
            ParsedEpisode(
                guid=guid,
                title=entry.get("title"),
                description_html=description,
                image_url=_entry_image(entry),
                episode_number=_int_or_none(entry.get("itunes_episode")),
                season=_int_or_none(entry.get("itunes_season")),
                explicit=_is_explicit(entry.get("itunes_explicit")),
                published_at=_to_datetime(entry.get("published_parsed") or entry.get("updated_parsed")),
                duration_seconds=parse_duration(entry.get("itunes_duration")),
                enclosure_url=enclosure.get("href"),
                enclosure_type=_short(enclosure.get("type"), MIME_MAX),
                enclosure_bytes=_int_or_none(enclosure.get("length")),
                chapters_url=_chapters_url(entry),
                transcript_url=transcript[0],
                transcript_type=_short(transcript[1], MIME_MAX),
            )
        )
    return parsed


async def resolve_feed_url(url: str, *, user_agent: str) -> str | None:
    """Follow redirects to find where a feed URL actually lands, without downloading it.

    The response is streamed and closed immediately: resolving a 3,000-episode feed should
    cost one round trip, not several megabytes.
    """
    try:
        async with build_client(user_agent) as client:
            async with client.stream("GET", url) as response:
                return str(response.url)
    except httpx.HTTPError:
        # Unresolvable is not fatal; the caller falls back to the URL it was given.
        return None


# The most feed XML one fetch may hold in memory. Publisher-controlled input.
MAX_FEED_BYTES = 30 * 1024 * 1024

# Failures that mean "the answer arrived broken", as distinct from "the host is unwell".
#
# A truncated gzip body surfaces as DecodingError -- zlib gets a stream that starts clean
# and ends early ("invalid distance code"). Megaphone does this to the Rogan feed
# occasionally: 20 consecutive fetches succeeded while reproducing it, so it is a CDN
# hiccup on a 5 MB document, not a property of the feed.
#
# Timeouts and connection errors are deliberately not in here. Those say the host is slow
# or down, and the right answer is the exponential backoff that already exists -- retrying
# immediately would just double the time a sick host costs every refresh pass.
TRUNCATION_ERRORS = (httpx.DecodingError, httpx.RemoteProtocolError, httpx.ReadError)

# One retry, after long enough for a blip to pass and not so long that a refresh stalls.
RETRY_PAUSE_SECONDS = 1.0


async def fetch_feed(
    feed_url: str,
    *,
    user_agent: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    """Conditional GET. A 304 costs one round trip and no parsing."""
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    # Retried once on a broken body, because the alternative is a wait measured in hours
    # for a fault measured in milliseconds. The request is a GET, so repeating it is free
    # of consequences.
    #
    # The retry is deliberately not identical. A gzip stream that fails to decompress can
    # mean a corrupted *compressed object cached at the CDN edge*, and asking the same way
    # a second later reaches the same cache and fails the same way -- which is what a retry
    # that changes nothing buys you. So a decoding failure specifically is retried asking
    # for the feed uncompressed: different representation, different cache entry, and no
    # decompressor in the path at all. It costs bandwidth (5 MB rather than 700 KB for the
    # feed this was written for) on an attempt that only happens after a failure.
    last_error: Exception | None = None

    for attempt in range(2):
        attempt_headers = dict(headers)
        if attempt == 1 and isinstance(last_error, httpx.DecodingError):
            attempt_headers["Accept-Encoding"] = "identity"

        try:
            return await _fetch_once(
                feed_url,
                headers=attempt_headers,
                user_agent=user_agent,
                etag=etag,
                last_modified=last_modified,
            )
        except TRUNCATION_ERRORS as exc:
            last_error = exc
            if attempt == 1:
                raise
            log.info(
                "refetching %s%s after a broken response: %s",
                feed_url,
                " uncompressed" if isinstance(exc, httpx.DecodingError) else "",
                exc,
            )
            await asyncio.sleep(RETRY_PAUSE_SECONDS)

    raise AssertionError("unreachable")  # pragma: no cover


async def _fetch_once(
    feed_url: str,
    *,
    headers: dict[str, str],
    user_agent: str,
    etag: str | None,
    last_modified: str | None,
) -> FetchResult:
    """One attempt. The stored validators are passed through because a 304 reports them
    back unchanged -- the server sends no body and no headers to re-derive them from."""
    async with build_client(user_agent) as client:
        async with client.stream("GET", feed_url, headers=headers) as response:
            if response.status_code != httpx.codes.NOT_MODIFIED:
                response.raise_for_status()
                # Read with a ceiling rather than trusting Content-Length: the body is
                # publisher-controlled, and an endless stream would otherwise be held in
                # memory until the process died. The largest real feeds -- full back
                # catalogues on Megaphone or Libsyn -- run a few megabytes.
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > MAX_FEED_BYTES:
                        raise ValueError(
                            f"feed exceeds {MAX_FEED_BYTES // (1024 * 1024)} MB; refusing to parse it"
                        )

                # On a compressed response a short read fails in the decompressor, loudly.
                # On an uncompressed one nothing checks, and a truncated document parses
                # into a feed that is merely missing its oldest episodes -- which would be
                # accepted in silence. Content-Length describes the encoded body, so this
                # is only meaningful when there is no encoding applied.
                if not response.headers.get("content-encoding"):
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and len(body) != int(declared):
                        raise httpx.RemoteProtocolError(
                            f"truncated feed: got {len(body)} bytes, Content-Length was {declared}",
                            request=response.request,
                        )

    if response.status_code == httpx.codes.NOT_MODIFIED:
        # final_url is reported here too. An unchanged feed still tells us where it serves
        # from, and a feed that rarely changes would otherwise never record it at all.
        return FetchResult(
            status_code=304,
            not_modified=True,
            etag=etag,
            last_modified=last_modified,
            final_url=str(response.url),
        )

    # Parsed off the event loop. feedparser is pure Python and takes over a second on a
    # full back catalogue, and for that second nothing else would be served -- including
    # the byte-range requests of whoever is listening right now.
    parsed = await asyncio.to_thread(parse_feed_bytes, bytes(body))

    return FetchResult(
        status_code=response.status_code,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        parsed=parsed,
        final_url=str(response.url),
    )
