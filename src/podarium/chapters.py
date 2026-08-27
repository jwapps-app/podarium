"""podcast:chapters, fetched by the server.

The chapters file lives on the publisher's host like the audio and the artwork do, so it
gets the same treatment: the server fetches it, caches the result, and serves it from
/api/episodes/{id}/chapters. Handing a client the publisher's URL would leak exactly the
request the whole design exists to prevent -- and it would leak it on every episode you
opened, which is worse than the artwork case.

Only what the player needs is kept. A chapters file may carry per-chapter images, links
and location data; none of it is rendered, and the image URLs in particular would be
publisher URLs smuggled back into a client response through a field nobody was checking.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.clients.http import build_client
from podarium.models import Episode

log = logging.getLogger("podarium")

# Same reasoning as the artwork cache: a chapters URL that 404s is usually permanent, and
# refetching it on every open would hammer a publisher for a file that is not coming back.
RETRY_FAILED_AFTER = timedelta(hours=6)

# Chapters files are small. A publisher serving something enormous under this name is
# either broken or hostile, and either way it is not going in the database.
MAX_BYTES = 1_000_000


@dataclass(slots=True)
class Chapter:
    start_seconds: float
    title: str | None


def parse_chapters(raw: str) -> list[Chapter]:
    """Read the podcast namespace chapters format, keeping only what is rendered.

    Anything unparseable yields no chapters rather than an error: a malformed file is the
    publisher's problem, and it should degrade to "this episode has no chapters" rather
    than breaking the episode.
    """
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(document, dict):
        return []

    chapters: list[Chapter] = []
    for item in document.get("chapters") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("startTime")
        if not isinstance(start, (int, float)) or isinstance(start, bool):
            continue
        # toc:false marks a chapter the publisher does not want listed.
        if item.get("toc") is False:
            continue
        title = item.get("title")
        chapters.append(
            Chapter(
                start_seconds=float(start),
                title=title if isinstance(title, str) and title.strip() else None,
            )
        )

    chapters.sort(key=lambda chapter: chapter.start_seconds)
    return chapters


async def ensure_chapters(
    session: AsyncSession, episode: Episode, *, user_agent: str
) -> list[Chapter]:
    """Return the episode's chapters, fetching and caching them on first use.

    Lazy rather than fetched during refresh, for the same reason episode artwork is: a
    feed's worth of chapter files is a lot of outbound requests for something almost none
    of which will ever be opened.
    """
    if not episode.chapters_url:
        return []

    if episode.chapters_json is not None:
        return parse_chapters(episode.chapters_json)

    last_try = episode.chapters_fetched_at
    if last_try is not None:
        if last_try.tzinfo is None:
            last_try = last_try.replace(tzinfo=UTC)
        if datetime.now(UTC) - last_try < RETRY_FAILED_AFTER:
            return []

    # Stamped before the request, not after, so a fetch that fails still records an attempt
    # and the backoff above has something to measure from.
    episode.chapters_fetched_at = datetime.now(UTC)

    try:
        async with build_client(user_agent) as client:
            response = await client.get(episode.chapters_url)
            response.raise_for_status()
            raw = response.text[:MAX_BYTES]
    except (httpx.HTTPError, UnicodeDecodeError) as exc:
        log.info("chapters fetch failed for %s: %s", episode.chapters_url, exc)
        await session.commit()
        return []

    chapters = parse_chapters(raw)
    if not chapters:
        # Cache nothing rather than an empty list: a file that parsed to nothing is more
        # likely a publisher mistake being fixed than a settled answer.
        log.info("chapters at %s parsed to nothing", episode.chapters_url)
        await session.commit()
        return []

    episode.chapters_json = raw
    await session.commit()
    return chapters
