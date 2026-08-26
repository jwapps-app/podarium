"""Feed refresh.

Refresh is idempotent: running it twice over the same document changes nothing. That
property depends on two rules that are easy to break by accident, so they are enforced in
one place, ``_apply_parsed_episode``:

1. ``first_seen_at`` is written when the row is created and never again.
2. Played state is never touched here at all.

The failure mode both rules prevent is real. A publisher migrating hosts (Anchor to
Megaphone, as PBD Podcast did) re-stamps ``pubDate`` on its whole back catalogue. If
"new" keyed off ``published_at``, the entire archive would resurface as unplayed and, on
a feed with ``auto_download_count``, immediately re-download.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.clients.feedfetch import ParsedEpisode, fetch_feed
from podarium.db import get_sessionmaker
from podarium.jobs.artwork import ensure_feed_artwork
from podarium.metrics import episodes_discovered_total, feed_refresh_total
from podarium.models import Episode, Feed, JobSource
from podarium.services import enqueue_download, get_app_settings

log = logging.getLogger(__name__)

MAX_BACKOFF_DOUBLINGS = 6


class RefreshOutcome:
    __slots__ = ("new_episodes", "updated_episodes", "not_modified", "error")

    def __init__(self) -> None:
        self.new_episodes = 0
        self.updated_episodes = 0
        self.not_modified = False
        self.error: str | None = None


def _apply_parsed_episode(episode: Episode, parsed: ParsedEpisode) -> bool:
    """Copy display fields onto an existing row. Returns True if anything changed.

    Deliberately absent: first_seen_at, local_path, downloaded_at, purged_at, and every
    column of episode_state. A refresh describes what the publisher says about an episode,
    not what this server has done with it.
    """
    changed = False
    for attr, value in (
        ("title", parsed.title),
        ("description_html", parsed.description_html),
        ("image_url", parsed.image_url),
        ("episode_number", parsed.episode_number),
        ("season", parsed.season),
        ("explicit", parsed.explicit),
        ("published_at", parsed.published_at),
        ("duration_seconds", parsed.duration_seconds),
        ("enclosure_url", parsed.enclosure_url),
        ("enclosure_type", parsed.enclosure_type),
        ("enclosure_bytes", parsed.enclosure_bytes),
    ):
        if value is None:
            # A field missing from this render of the feed is not an instruction to erase
            # what we already know.
            continue
        if getattr(episode, attr) != value:
            setattr(episode, attr, value)
            changed = True
    return changed


async def _auto_enqueue(session: AsyncSession, feed: Feed) -> None:
    """Pre-download the N newest episodes for a feed that opted in.

    ``purged_at IS NULL`` matters: without it, retention deleting a recent episode would
    be immediately undone by the next refresh re-enqueueing it.
    """
    if feed.auto_download_count <= 0:
        return
    candidates = (
        await session.execute(
            select(Episode)
            .where(Episode.feed_id == feed.id)
            .where(Episode.local_path.is_(None))
            .where(Episode.purged_at.is_(None))
            .where(Episode.enclosure_url.is_not(None))
            .order_by(Episode.published_at.desc().nullslast(), Episode.id.desc())
            .limit(feed.auto_download_count)
        )
    ).scalars().all()
    for episode in candidates:
        await enqueue_download(session, episode, JobSource.auto)


async def refresh_feed(session: AsyncSession, feed: Feed, *, user_agent: str) -> RefreshOutcome:
    outcome = RefreshOutcome()
    now = datetime.now(UTC)

    try:
        result = await fetch_feed(
            feed.feed_url,
            user_agent=user_agent,
            etag=feed.etag,
            last_modified=feed.last_modified,
        )
    except Exception as exc:  # noqa: BLE001 - any transport or parse failure backs the feed off
        feed.fetch_error = f"{type(exc).__name__}: {exc}"[:1000]
        feed.fetch_error_count += 1
        feed.last_fetched_at = now
        outcome.error = feed.fetch_error
        await session.commit()
        feed_refresh_total.labels(result="error").inc()
        log.warning("feed %s refresh failed: %s", feed.id, outcome.error)
        return outcome

    feed.last_fetched_at = now
    feed.fetch_error = None
    feed.fetch_error_count = 0

    if result.not_modified:
        outcome.not_modified = True
        # Auto-download still runs on an unchanged feed. The trigger for pre-downloading is
        # the setting, not new content: a feed the user just opted in to would otherwise
        # wait for its next episode before anything landed on disk.
        await _auto_enqueue(session, feed)
        await session.commit()
        feed_refresh_total.labels(result="not_modified").inc()
        return outcome

    if result.etag:
        feed.etag = result.etag
    if result.last_modified:
        feed.last_modified = result.last_modified

    parsed = result.parsed
    if parsed is not None:
        for attr, value in (
            ("title", parsed.title),
            ("author", parsed.author),
            ("description", parsed.description),
            ("link", parsed.link),
            ("language", parsed.language),
            ("image_url", parsed.image_url),
            ("explicit", parsed.explicit),
        ):
            if value is not None:
                setattr(feed, attr, value)

        existing = {
            episode.guid: episode
            for episode in (
                await session.execute(select(Episode).where(Episode.feed_id == feed.id))
            ).scalars()
        }

        # A single feed document can carry the same GUID twice. Keep the first render and
        # ignore the rest, so one refresh cannot insert a duplicate and break the upsert.
        seen_this_pass: set[str] = set()

        for parsed_episode in parsed.episodes:
            if parsed_episode.guid in seen_this_pass:
                continue
            seen_this_pass.add(parsed_episode.guid)

            episode = existing.get(parsed_episode.guid)
            if episode is None:
                episode = Episode(feed_id=feed.id, guid=parsed_episode.guid, first_seen_at=now)
                _apply_parsed_episode(episode, parsed_episode)
                session.add(episode)
                existing[parsed_episode.guid] = episode
                outcome.new_episodes += 1
            elif _apply_parsed_episode(episode, parsed_episode):
                outcome.updated_episodes += 1

        await session.flush()
        await _auto_enqueue(session, feed)

    await session.commit()

    if outcome.new_episodes:
        episodes_discovered_total.inc(outcome.new_episodes)
    feed_refresh_total.labels(result="success").inc()

    if feed.image_url:
        await ensure_feed_artwork(session, feed, user_agent=user_agent)

    return outcome


def _feed_jitter_seconds(feed_id: int, interval_seconds: int) -> int:
    """Deterministic per-feed offset so all feeds do not fire on the same tick."""
    return (feed_id * 2654435761) % max(interval_seconds, 1)


def _is_due(feed: Feed, interval_seconds: int, now: datetime) -> bool:
    if feed.last_fetched_at is None:
        return True
    # last_fetched_at records the last *attempt*; a failing feed backs off exponentially
    # rather than hammering a host that is down.
    backoff = 2 ** min(feed.fetch_error_count, MAX_BACKOFF_DOUBLINGS)
    due_after = interval_seconds * backoff + _feed_jitter_seconds(feed.id, interval_seconds)
    last = feed.last_fetched_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now >= last + timedelta(seconds=due_after)


async def refresh_due_feeds() -> int:
    """One pass over every active feed that is due. Returns how many were refreshed."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        app_settings = await get_app_settings(session)
        interval_seconds = app_settings.refresh_interval_minutes * 60
        user_agent = app_settings.user_agent
        feeds = (
            await session.execute(select(Feed).where(Feed.active.is_(True)))
        ).scalars().all()
        now = datetime.now(UTC)
        due = [feed for feed in feeds if _is_due(feed, interval_seconds, now)]

    refreshed = 0
    for feed in due:
        async with sessionmaker() as session:
            fresh = await session.get(Feed, feed.id)
            if fresh is None:
                continue
            await refresh_feed(session, fresh, user_agent=user_agent)
            refreshed += 1
    return refreshed


async def refresh_loop(stop: asyncio.Event, tick_seconds: int = 60) -> None:
    while not stop.is_set():
        try:
            await refresh_due_feeds()
        except Exception:  # noqa: BLE001 - a bad pass must not kill the loop
            log.exception("refresh pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
        except TimeoutError:
            pass
