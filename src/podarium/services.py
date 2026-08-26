"""Small shared helpers used by both the API layer and the background jobs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.models import AppSettings, DownloadJob, Episode, Feed, JobSource, JobState, RetentionMode


async def get_app_settings(session: AsyncSession) -> AppSettings:
    """Fetch (creating on first call) the singleton settings row."""
    settings_row = await session.get(AppSettings, 1)
    if settings_row is None:
        settings_row = AppSettings(id=1)
        session.add(settings_row)
        await session.commit()
        await session.refresh(settings_row)
    return settings_row


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
