"""Small shared helpers used by both the API layer and the background jobs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.models import (
    AppSettings,
    EpisodeState,
    DownloadJob,
    Episode,
    Feed,
    FeedState,
    JobSource,
    JobState,
    RetentionMode,
    User,
)


async def feed_counts(
    session: AsyncSession, user_id: int, feed_ids: list[int]
) -> dict[int, tuple[int, int, int]]:
    """Per-feed (total, unplayed, new) counts.

    "New" means first seen since the show was last looked at, and not already played.
    Falling back to the feed's created_at keeps a subscription made before feed_state
    existed from reporting its whole back catalogue as new.
    """
    if not feed_ids:
        return {}

    seen_at = func.coalesce(FeedState.last_seen_at, Feed.created_at)

    rows = (
        await session.execute(
            select(
                Episode.feed_id,
                func.count(Episode.id),
                func.count(Episode.id).filter(
                    func.coalesce(EpisodeState.played, False).is_(False)
                ),
                func.count(Episode.id).filter(
                    (Episode.first_seen_at > seen_at)
                    & func.coalesce(EpisodeState.played, False).is_(False)
                ),
            )
            .select_from(Episode)
            .join(Feed, Feed.id == Episode.feed_id)
            .outerjoin(
                EpisodeState,
                (EpisodeState.episode_id == Episode.id) & (EpisodeState.user_id == user_id),
            )
            .outerjoin(
                FeedState,
                (FeedState.feed_id == Episode.feed_id) & (FeedState.user_id == user_id),
            )
            .where(Episode.feed_id.in_(feed_ids))
            .group_by(Episode.feed_id)
        )
    ).all()
    return {feed_id: (total, unplayed, new) for feed_id, total, unplayed, new in rows}


async def unseen_episode_count(session: AsyncSession, user_id: int) -> int:
    """How many episodes have arrived since the inbox was last looked at.

    The number on the home-screen icon. Same rule as the per-show "new" count in
    ``feed_counts`` -- first seen since a marker, and not already played -- against the
    inbox's own marker rather than each show's, so glancing at the inbox does not clear the
    new marker on shows that were never opened.

    Falls back to the account's creation date, which is the right answer for an account
    that has never opened the inbox and, for one made before this column existed, is what
    the migration stamps past.
    """
    seen_at = func.coalesce(User.inbox_seen_at, User.created_at)

    return (
        await session.execute(
            select(func.count(Episode.id))
            .select_from(Episode)
            .join(Feed, Feed.id == Episode.feed_id)
            .join(User, User.id == user_id)
            .outerjoin(
                EpisodeState,
                (EpisodeState.episode_id == Episode.id) & (EpisodeState.user_id == user_id),
            )
            .where(Feed.active.is_(True))
            .where(Episode.first_seen_at > seen_at)
            .where(func.coalesce(EpisodeState.played, False).is_(False))
        )
    ).scalar_one()


async def mark_inbox_seen(session: AsyncSession, user_id: int) -> None:
    """Clear the badge. Called when the inbox is actually on screen."""
    user = await session.get(User, user_id)
    if user is not None:
        user.inbox_seen_at = datetime.now(UTC)


async def mark_feed_seen(session: AsyncSession, user_id: int, feed_id: int) -> FeedState:
    """Reset a show's new-episode count. Called when its page is opened."""
    state = await session.get(FeedState, {"user_id": user_id, "feed_id": feed_id})
    now = datetime.now(UTC)
    if state is None:
        state = FeedState(user_id=user_id, feed_id=feed_id, last_seen_at=now)
        session.add(state)
    else:
        state.last_seen_at = now
    return state


async def mark_all_feeds_seen(session: AsyncSession, user_id: int) -> int:
    """Clear every active show's new-episode count. Called when the inbox is opened.

    Active only, matching the nav badge, which sums active feeds. A soft-unsubscribed show
    is not something the inbox is offering you.

    One upsert rather than a get-or-create per feed: this runs on opening the inbox, and
    a round trip per subscription is the shape of query that stays invisible at four shows
    and shows up as inbox lag at forty.
    """
    now = datetime.now(UTC)
    feed_ids = (
        await session.execute(select(Feed.id).where(Feed.active.is_(True)))
    ).scalars().all()
    if not feed_ids:
        return 0

    statement = pg_insert(FeedState).values(
        [{"user_id": user_id, "feed_id": feed_id, "last_seen_at": now} for feed_id in feed_ids]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[FeedState.user_id, FeedState.feed_id],
            set_={"last_seen_at": statement.excluded.last_seen_at},
        )
    )
    return len(feed_ids)


async def get_app_settings(session: AsyncSession) -> AppSettings:
    """Fetch (creating on first call) the singleton settings row."""
    settings_row = await session.get(AppSettings, 1)
    if settings_row is None:
        settings_row = AppSettings(id=1)
        session.add(settings_row)
        await session.commit()
        await session.refresh(settings_row)
    return settings_row


def effective_auto_download_count(feed: Feed, app_settings: AppSettings) -> int:
    """A NULL on the feed inherits the global; a value on the feed overrides it."""
    if feed.auto_download_count is not None:
        return feed.auto_download_count
    return app_settings.global_auto_download_count


def effective_retention(feed: Feed, app_settings: AppSettings) -> tuple[RetentionMode, int]:
    """A NULL on the feed inherits the global; a value on the feed overrides it."""
    mode = feed.retention_mode if feed.retention_mode is not None else app_settings.global_retention_mode
    days = feed.retention_days if feed.retention_days is not None else app_settings.global_retention_days
    return mode, days


async def enqueue_download(
    session: AsyncSession, episode: Episode, source: JobSource
) -> DownloadJob | None:
    """Queue an episode for download, idempotently.

    Returns None when there is nothing to do: the file is already on disk, or a job for
    this episode is already waiting or running. Callers can fire this freely.
    """
    if episode.local_path is not None:
        return None
    if not episode.enclosure_url:
        return None

    existing = (
        await session.execute(
            select(DownloadJob)
            .where(DownloadJob.episode_id == episode.id)
            .where(DownloadJob.state.in_([JobState.queued, JobState.running]))
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    job = DownloadJob(
        episode_id=episode.id,
        source=source,
        state=JobState.queued,
        next_attempt_at=datetime.now(UTC),
    )
    session.add(job)
    return job
