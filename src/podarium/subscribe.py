"""Subscribing to a feed. Shared by POST /api/feeds and OPML import."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.clients.feedfetch import resolve_feed_url
from podarium.jobs.refresh import refresh_feed
from podarium.models import Feed
from podarium.urls import normalize_feed_url

log = logging.getLogger(__name__)


async def find_feed_by_url(session: AsyncSession, feed_url: str) -> Feed | None:
    return (
        await session.execute(select(Feed).where(Feed.feed_url == feed_url))
    ).scalar_one_or_none()


async def find_existing_feed(
    session: AsyncSession,
    *,
    feed_url: str | None = None,
    podcast_index_id: int | None = None,
) -> Feed | None:
    """Find an already-subscribed feed by any identity we have for it.

    Exact URL, then normalised URL, then Podcast Index id. The last of those is what the
    spec means by storing ``podcast_index_id`` even though the RSS feed is the source of
    truth: it identifies the show when the URL has moved or when Podcast Index and the
    user simply know it by different addresses.

    A subscription list is one person's, so scanning it in Python is cheaper than the
    schema change a normalised-URL index would need.
    """
    if feed_url:
        exact = await find_feed_by_url(session, feed_url)
        if exact is not None:
            return exact

    if not feed_url and podcast_index_id is None:
        return None

    target = normalize_feed_url(feed_url) if feed_url else None
    for feed in (await session.execute(select(Feed))).scalars():
        if target and target in _identities(feed):
            return feed
        if (
            podcast_index_id is not None
            and feed.podcast_index_id is not None
            and feed.podcast_index_id == podcast_index_id
        ):
            return feed

    return None


def _identities(feed: Feed) -> set[str]:
    """Every normalised URL that means "this feed"."""
    identities = {normalize_feed_url(feed.feed_url)}
    if feed.resolved_url:
        identities.add(normalize_feed_url(feed.resolved_url))
    return identities


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
    existing = await find_existing_feed(
        session, feed_url=feed_url, podcast_index_id=podcast_index_id
    )

    if existing is None:
        # Nothing matched the address we were given. Follow it to where it actually lands
        # before creating anything: a show reachable at both its host's URL and the
        # publisher's own would otherwise be subscribed twice, and two feed rows means two
        # copies of every episode -- the (feed_id, guid) constraint cannot see across them.
        resolved = await resolve_feed_url(feed_url, user_agent=user_agent)
        if resolved and normalize_feed_url(resolved) != normalize_feed_url(feed_url):
            existing = await find_existing_feed(session, feed_url=resolved)

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
