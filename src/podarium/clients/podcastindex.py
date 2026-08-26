"""Podcast Index client (spec 7). Discovery only -- never a metadata source after subscribe.

Apple is deliberately not in this path: no iTunes Search API, no Apple artwork, no Apple
lookup for a moved feed. ``podcast_index_id`` is stored on subscribe purely so a feed URL
that permanently moves can be re-resolved later.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from podarium.clients.http import build_client
from podarium.config import get_settings

BASE_URL = "https://api.podcastindex.org/api/1.0"


class PodcastIndexUnavailable(RuntimeError):
    """Raised when PODCASTINDEX_KEY/SECRET are absent. Surfaces as a 503, not a crash."""


@dataclass(slots=True)
class PodcastIndexFeed:
    podcast_index_id: int | None
    feed_url: str
    title: str | None = None
    author: str | None = None
    description: str | None = None
    image_url: str | None = None
    episode_count: int | None = None


def build_auth_headers(key: str, secret: str, user_agent: str, unix_seconds: int | None = None) -> dict[str, str]:
    """Authorization is sha1(key + secret + unix_seconds), per the Podcast Index docs."""
    stamp = str(unix_seconds if unix_seconds is not None else int(time.time()))
    digest = hashlib.sha1(f"{key}{secret}{stamp}".encode()).hexdigest()
    return {
        "X-Auth-Key": key,
        "X-Auth-Date": stamp,
        "Authorization": digest,
        "User-Agent": user_agent,
    }


def _feed_from_payload(payload: dict) -> PodcastIndexFeed | None:
    url = payload.get("url") or payload.get("originalUrl")
    if not url:
        return None
    return PodcastIndexFeed(
        podcast_index_id=payload.get("id"),
        feed_url=url,
        title=payload.get("title"),
        author=payload.get("author") or payload.get("ownerName"),
        description=payload.get("description"),
        image_url=payload.get("artwork") or payload.get("image"),
        episode_count=payload.get("episodeCount"),
    )


async def _get(path: str, params: dict, user_agent: str) -> dict:
    settings = get_settings()
    if not settings.podcastindex_configured:
        raise PodcastIndexUnavailable(
            "Podcast Index credentials are not configured. "
            "Set PODCASTINDEX_KEY and PODCASTINDEX_SECRET."
        )
    headers = build_auth_headers(settings.podcastindex_key, settings.podcastindex_secret, user_agent)
    async with build_client(user_agent) as client:
        response = await client.get(f"{BASE_URL}{path}", params=params, headers=headers)
    response.raise_for_status()
    return response.json()


async def search_by_term(term: str, *, user_agent: str, limit: int = 40) -> list[PodcastIndexFeed]:
    payload = await _get("/search/byterm", {"q": term, "max": limit}, user_agent)
    results = [_feed_from_payload(item) for item in payload.get("feeds") or []]
    return [r for r in results if r]


async def podcast_by_feed_url(feed_url: str, *, user_agent: str) -> PodcastIndexFeed | None:
    payload = await _get("/podcasts/byfeedurl", {"url": feed_url}, user_agent)
    feed = payload.get("feed")
    if isinstance(feed, list):
        feed = feed[0] if feed else None
    return _feed_from_payload(feed) if feed else None


async def podcast_by_feed_id(podcast_index_id: int, *, user_agent: str) -> PodcastIndexFeed | None:
    payload = await _get("/podcasts/byfeedid", {"id": podcast_index_id}, user_agent)
    feed = payload.get("feed")
    if isinstance(feed, list):
        feed = feed[0] if feed else None
    return _feed_from_payload(feed) if feed else None
