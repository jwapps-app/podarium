"""Server-side artwork cache.

Clients are handed ``/api/images/...`` and never a publisher CDN URL, so image loads in
the web UI and the iOS app do not leak a device IP to a publisher either.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
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

    async with lock:
        entry = (
            await session.execute(select(ArtworkCache).where(ArtworkCache.url_hash == digest))
        ).scalar_one_or_none()

        if entry is not None and entry.local_path and Path(entry.local_path).exists():
            return entry

        if entry is None:
            entry = ArtworkCache(url_hash=digest, source_url=source_url)
            session.add(entry)

        try:
            async with build_client(user_agent) as client:
                response = await client.get(source_url)
                response.raise_for_status()
                payload = response.content
                if len(payload) > MAX_ARTWORK_BYTES:
                    raise ValueError(f"artwork too large: {len(payload)} bytes")
                content_type = response.headers.get("content-type")

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
            log.info("artwork fetch failed for %s: %s", source_url, entry.fetch_error)

        await session.commit()
        return entry


async def ensure_feed_artwork(session: AsyncSession, feed: Feed, *, user_agent: str) -> ArtworkCache | None:
    if not feed.image_url:
        return None
    return await ensure_artwork(session, feed.image_url, user_agent=user_agent)


async def ensure_episode_artwork(session: AsyncSession, episode: Episode, *, user_agent: str) -> ArtworkCache | None:
    if not episode.image_url:
        return None
    return await ensure_artwork(session, episode.image_url, user_agent=user_agent)
