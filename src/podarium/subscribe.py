"""Subscribing to a feed. Shared by POST /api/feeds and OPML import."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.jobs.refresh import refresh_feed
from podarium.models import Feed

log = logging.getLogger(__name__)


async def find_feed_by_url(session: AsyncSession, feed_url: str) -> Feed | None:
    return (
        await session.execute(select(Feed).where(Feed.feed_url == feed_url))
    ).scalar_one_or_none()


async def subscribe_feed(
    session: AsyncSession,
    feed_url: str,
    *,
    user_agent: str,
    podcast_index_id: int | None = None,
    refresh: bool = True,
) -> tuple[Feed, bool]:
    """Return (feed, created). Subscribing twice is a no-op, not an error.

    An immediate refresh is what populates the title and episode list, so the caller gets
    back a usable feed rather than a bare URL waiting on the next scheduled pass.
    """
    existing = await find_feed_by_url(session, feed_url)
    if existing is not None:
        changed = False
        if podcast_index_id and existing.podcast_index_id != podcast_index_id:
            existing.podcast_index_id = podcast_index_id
            changed = True
        if not existing.active:
            # Re-subscribing a soft-unsubscribed feed keeps every episode row and its
            # played state; the feed simply becomes visible again.
            existing.active = True
            changed = True
        if changed:
            await session.commit()
        return existing, False

    feed = Feed(feed_url=feed_url, podcast_index_id=podcast_index_id)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    if refresh:
        await refresh_feed(session, feed, user_agent=user_agent)
        await session.refresh(feed)

    return feed, True
