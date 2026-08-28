"""Podcast Index client (spec 7). Discovery only -- never a metadata source after subscribe.

Apple is deliberately not in this path: no iTunes Search API, no Apple artwork, no Apple
lookup for a moved feed. ``podcast_index_id`` is stored on subscribe purely so a feed URL
that permanently moves can be re-resolved later.
"""

from __future__ import annotations

import hashlib
import time

import httpx
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


# Podcast Index issues 40-character secrets. The exact length is theirs to change, so this
# is only used to notice a value that is implausibly short -- not to validate one.
TYPICAL_SECRET_LENGTH = 40


def describe_credential_problems(key: str | None, secret: str | None) -> list[str]:
    """Look for the ways these arrive damaged, before anything is sent.

    Every one of these surfaces otherwise as a 401 from Podcast Index, three layers from
    the cause and indistinguishable from simply having the wrong credentials.
    """
    problems: list[str] = []
    if not key and not secret:
        return problems

    if bool(key) != bool(secret):
        missing = "PODCASTINDEX_SECRET" if key else "PODCASTINDEX_KEY"
        problems.append(f"{missing} is empty while the other is set; both are required.")

    for name, value in (("PODCASTINDEX_KEY", key), ("PODCASTINDEX_SECRET", secret)):
        if not value:
            continue

        # A whole "NAME=value" line pasted into a value field.
        if value.startswith(f"{name}="):
            problems.append(f"{name} still has \"{name}=\" on the front of its value.")

        # Compose reads $ inside a value as a variable reference, so a secret containing one
        # is silently truncated at deploy time. Doubling it to $$ escapes it -- but if the
        # doubling survives into the container, it was escaped one time too many.
        if "$$" in value:
            problems.append(
                f"{name} contains a literal '$$', which usually means an escape was not "
                "collapsed. It should hold single dollar signs by the time it reaches here."
            )

        if value != value.strip():
            problems.append(f"{name} has leading or trailing whitespace.")

        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            problems.append(f"{name} looks like it still has its surrounding quotes.")

    if secret and len(secret) < TYPICAL_SECRET_LENGTH // 2:
        problems.append(
            f"PODCASTINDEX_SECRET is only {len(secret)} character"
            f"{'' if len(secret) == 1 else 's'}, well short of the usual "
            f"{TYPICAL_SECRET_LENGTH}. A secret containing '$' is truncated by Compose "
            "interpolation unless it is written as '$$'."
        )

    return problems


async def verify_credentials(user_agent: str) -> str:
    """One cheap live request, so a rejection is reported here rather than on first search.

    Catches what no local check can: wrong values, revoked credentials, and a host clock
    far enough out that the signed timestamp is refused.
    """
    try:
        await _get("/search/byterm", {"q": "podarium", "max": 1}, user_agent)
        return "accepted"
    except PodcastIndexUnavailable:
        return "not configured"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return (
                "rejected (401). The credentials are wrong, truncated, or this host's clock "
                "is too far off -- the signature includes a timestamp."
            )
        return f"rejected ({exc.response.status_code})"
    except httpx.HTTPError as exc:
        return f"unreachable ({type(exc).__name__})"


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


async def trending(
    *, user_agent: str, limit: int = 30, category: str | None = None
) -> list[PodcastIndexFeed]:
    """What is being talked about right now, optionally within one category.

    Discovery without a search term. Searching only finds what you can already name, which
    is the wrong tool for "show me something I do not know about".
    """
    params: dict = {"max": limit}
    if category:
        params["cat"] = category
    payload = await _get("/podcasts/trending", params, user_agent)
    results = [_feed_from_payload(item) for item in payload.get("feeds") or []]
    return [r for r in results if r]


async def categories(*, user_agent: str) -> list[str]:
    """Every category Podcast Index knows, for browsing rather than guessing at names."""
    payload = await _get("/categories/list", {}, user_agent)
    names = [
        item.get("name")
        for item in payload.get("feeds") or []
        if isinstance(item, dict) and item.get("name")
    ]
    return sorted(set(names))
