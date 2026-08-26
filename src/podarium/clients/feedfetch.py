"""RSS fetching and parsing.

Once a feed is subscribed, the RSS document is the source of truth -- Podcast Index is
discovery only (spec 7).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

from podarium.clients.http import build_client


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
        language=channel.get("language"),
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
                enclosure_type=enclosure.get("type"),
                enclosure_bytes=_int_or_none(enclosure.get("length")),
            )
        )
    return parsed


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

    async with build_client(user_agent) as client:
        response = await client.get(feed_url, headers=headers)

    if response.status_code == httpx.codes.NOT_MODIFIED:
        return FetchResult(status_code=304, not_modified=True, etag=etag, last_modified=last_modified)

    response.raise_for_status()
    return FetchResult(
        status_code=response.status_code,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        parsed=parse_feed_bytes(response.content),
        final_url=str(response.url),
    )
