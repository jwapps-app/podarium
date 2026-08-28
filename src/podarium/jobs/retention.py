"""Retention sweeper.

The whole design rests on one rule: **purging removes the file, never the row.**

Deleting the episode row would delete its GUID, and the next refresh would see the GUID as
unseen, insert it as new, mark it unplayed, and -- on an auto-download feed -- fetch the
audio again. The purged episode would reappear forever. So a purge nulls ``local_path``,
stamps ``purged_at``, and unlinks the file. Played state survives untouched.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.db import get_sessionmaker
from podarium.jobs.audio import drop_processed
from podarium.metrics import download_dir_bytes, purged_total
from podarium.models import Episode, EpisodeState, Feed, QueueItem, RetentionMode
from podarium.services import effective_retention, get_app_settings

log = logging.getLogger(__name__)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def purge_episode(session: AsyncSession, episode: Episode, *, reason: str = "policy") -> bool:
    """Unlink the file and null the pointer. The row, its GUID, and its state remain."""
    if episode.local_path is None:
        return False

    try:
        Path(episode.local_path).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("could not unlink %s: %s", episode.local_path, exc)
        return False

    # The processed copy goes with it. Leaving it behind would mean retention reporting a
    # reclaim while half the bytes stayed on disk, and the storage panel adding up wrong.
    drop_processed(episode)

    episode.local_path = None
    episode.local_bytes = None
    episode.purged_at = datetime.now(UTC)
    purged_total.labels(reason=reason).inc()
    return True


async def protected_episode_ids(session: AsyncSession) -> set[int]:
    """Episodes no policy may purge, whatever the dates say.

    Two kinds, and both are an explicit instruction rather than an inference. Anything in
    the queue, because you have lined it up to listen to. And anything starred: a star that
    let the audio be deleted underneath it would be a bookmark pretending to be a promise.

    A large enough starred collection can therefore hold the directory above its ceiling.
    That is the correct outcome -- the ceiling is a policy for episodes nobody asked to
    keep, and it should not quietly delete the ones somebody did.
    """
    queued = set((await session.execute(select(QueueItem.episode_id))).scalars())
    starred = set(
        (
            await session.execute(
                select(EpisodeState.episode_id).where(EpisodeState.starred.is_(True))
            )
        ).scalars()
    )
    return queued | starred


def _is_expired(
    episode: Episode, state: EpisodeState | None, mode: RetentionMode, days: int, now: datetime
) -> bool:
    if mode is RetentionMode.never:
        return False

    cutoff = now - timedelta(days=days)

    if mode is RetentionMode.after_played:
        if state is None or not state.played:
            return False
        # completed_at can be missing on a state written before playback finished cleanly;
        # fall back to downloaded_at so such an episode is not retained forever.
        marker = _aware(state.completed_at) or _aware(episode.downloaded_at)
        return marker is not None and marker < cutoff

    if mode is RetentionMode.after_download:
        downloaded = _aware(episode.downloaded_at)
        return downloaded is not None and downloaded < cutoff

    return False


async def sweep(session: AsyncSession) -> int:
    """One retention pass. Returns the number of files purged."""
    app_settings = await get_app_settings(session)
    now = datetime.now(UTC)
    protected = await protected_episode_ids(session)

    rows = (
        await session.execute(
            select(Episode, EpisodeState, Feed)
            .join(Feed, Feed.id == Episode.feed_id)
            .outerjoin(EpisodeState, EpisodeState.episode_id == Episode.id)
            .where(Episode.local_path.is_not(None))
        )
    ).all()

    purged = 0
    survivors: list[tuple[Episode, EpisodeState | None]] = []

    for episode, state, feed in rows:
        if episode.id in protected:
            survivors.append((episode, state))
            continue
        mode, days = effective_retention(feed, app_settings)
        if _is_expired(episode, state, mode, days, now):
            if await purge_episode(session, episode, reason="policy"):
                purged += 1
        else:
            survivors.append((episode, state))

    # Global disk ceiling. Played episodes go first, then the oldest downloads, until the
    # directory is back under the limit. Queued and starred episodes are skipped here too:
    # being over the ceiling does not override an explicit "keep this".
    ceiling = app_settings.download_dir_max_bytes
    if ceiling:
        total = sum(episode.local_bytes or 0 for episode, _ in survivors)
        if total > ceiling:
            ordered = sorted(
                survivors,
                key=lambda pair: (
                    not (pair[1] is not None and pair[1].played),
                    _aware(pair[0].downloaded_at) or datetime.min.replace(tzinfo=UTC),
                ),
            )
            for episode, _ in ordered:
                if total <= ceiling:
                    break
                if episode.id in protected:
                    continue
                size = episode.local_bytes or 0
                if await purge_episode(session, episode, reason="ceiling"):
                    total -= size
                    purged += 1

    await session.commit()

    on_disk = (
        await session.execute(
            select(func.coalesce(func.sum(Episode.local_bytes), 0)).where(
                Episode.local_path.is_not(None)
            )
        )
    ).scalar_one()
    download_dir_bytes.set(int(on_disk or 0))

    if purged:
        log.info("retention purged %s files", purged)
    return purged


async def retention_loop(stop: asyncio.Event, interval_seconds: int = 3600) -> None:
    while not stop.is_set():
        try:
            async with get_sessionmaker()() as session:
                await sweep(session)
        except Exception:  # noqa: BLE001 - a bad sweep must not kill the loop
            log.exception("retention sweep failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
