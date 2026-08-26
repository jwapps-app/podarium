"""Download worker.

Files land at ``{DOWNLOAD_DIR}/{feed_id}/{episode_id}.{ext}`` -- IDs in the path, never
titles. That avoids filesystem-unsafe characters and means a publisher editing an episode
title does not orphan a file we already have.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.clients.http import build_client
from podarium.config import get_settings
from podarium.db import get_sessionmaker
from podarium.metrics import download_total, downloaded_bytes_total
from podarium.models import DownloadJob, Episode, JobState
from podarium.services import get_app_settings

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 60


def _extension_for(episode: Episode) -> str:
    """Prefer the declared MIME type; fall back to the URL suffix; then .mp3."""
    if episode.enclosure_type:
        guessed = mimetypes.guess_extension(episode.enclosure_type.split(";")[0].strip())
        if guessed:
            return ".mp3" if guessed == ".mpga" else guessed
    if episode.enclosure_url:
        suffix = Path(urlparse(episode.enclosure_url).path).suffix
        if 1 < len(suffix) <= 5:
            return suffix
    return ".mp3"


def target_path(episode: Episode) -> Path:
    root = get_settings().download_dir
    return root / str(episode.feed_id) / f"{episode.id}{_extension_for(episode)}"


async def requeue_stuck_jobs() -> int:
    """Anything left ``running`` by a crash or restart is picked up again (spec 4)."""
    async with get_sessionmaker()() as session:
        result = await session.execute(
            update(DownloadJob)
            .where(DownloadJob.state == JobState.running)
            .values(state=JobState.queued, next_attempt_at=datetime.now(UTC))
        )
        await session.commit()
        return result.rowcount or 0


async def _claim_job(session: AsyncSession) -> DownloadJob | None:
    """Claim one job. SKIP LOCKED lets the workers share the table without coordination."""
    job = (
        await session.execute(
            select(DownloadJob)
            .where(DownloadJob.state == JobState.queued)
            .where(DownloadJob.next_attempt_at <= datetime.now(UTC))
            .order_by(DownloadJob.next_attempt_at, DownloadJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    job.state = JobState.running
    await session.commit()
    return job


async def _fail_job(session: AsyncSession, job: DownloadJob, message: str) -> None:
    job.attempts += 1
    job.last_error = message[:1000]
    if job.attempts >= MAX_ATTEMPTS:
        job.state = JobState.failed
        download_total.labels(result="failed").inc()
        log.warning("download job %s failed permanently: %s", job.id, message)
    else:
        job.state = JobState.queued
        job.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=BASE_BACKOFF_SECONDS * (2 ** (job.attempts - 1))
        )
    await session.commit()


async def run_job(session: AsyncSession, job: DownloadJob, *, user_agent: str) -> None:
    episode = await session.get(Episode, job.episode_id)
    if episode is None:
        job.state = JobState.failed
        job.last_error = "episode no longer exists"
        await session.commit()
        download_total.labels(result="failed").inc()
        return

    # Idempotent: a job for an episode already on disk completes immediately (spec 4).
    if episode.local_path is not None and Path(episode.local_path).exists():
        job.state = JobState.done
        await session.commit()
        download_total.labels(result="skipped").inc()
        return

    if not episode.enclosure_url:
        job.state = JobState.failed
        job.last_error = "episode has no enclosure URL"
        await session.commit()
        download_total.labels(result="failed").inc()
        return

    path = target_path(episode)
    partial = path.with_suffix(path.suffix + ".part")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        declared_length: int | None = None
        async with build_client(user_agent) as client:
            async with client.stream("GET", episode.enclosure_url) as response:
                response.raise_for_status()
                raw_length = response.headers.get("content-length")
                if raw_length and raw_length.isdigit():
                    declared_length = int(raw_length)
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                        handle.write(chunk)
                        written += len(chunk)

        if written == 0:
            raise ValueError("empty response body")

        # Content-Length is the transfer's own contract, so a short read here really is a
        # truncated download and must retry.
        if declared_length is not None and written != declared_length:
            raise ValueError(
                f"truncated download: got {written} bytes, Content-Length was {declared_length}"
            )

        # The feed's declared enclosure length is recorded but never used to reject a
        # download. It is wrong in both directions in practice: dynamic ad insertion makes
        # the real file larger on every request, and stale or copy-pasted <enclosure length>
        # values can overstate a short episode by an order of magnitude. Content-Length
        # above is the only size claim the transfer itself stands behind.
        if episode.enclosure_bytes and written != episode.enclosure_bytes:
            log.debug(
                "episode %s is %s bytes, feed declared %s",
                episode.id,
                written,
                episode.enclosure_bytes,
            )

        # Atomic rename, so a partially written file is never visible at the real path.
        partial.replace(path)

        episode.local_path = str(path)
        episode.local_bytes = written
        episode.downloaded_at = datetime.now(UTC)
        episode.purged_at = None
        job.state = JobState.done
        job.last_error = None
        await session.commit()

        downloaded_bytes_total.inc(written)
        download_total.labels(result="done").inc()
        log.info("downloaded episode %s (%s bytes)", episode.id, written)
    except Exception as exc:  # noqa: BLE001 - every failure retries with backoff
        partial.unlink(missing_ok=True)
        await _fail_job(session, job, f"{type(exc).__name__}: {exc}")


async def _worker(stop: asyncio.Event, poll_seconds: float) -> None:
    sessionmaker = get_sessionmaker()
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                app_settings = await get_app_settings(session)
                job = await _claim_job(session)
                if job is not None:
                    await run_job(session, job, user_agent=app_settings.user_agent)
                    continue
        except Exception:  # noqa: BLE001 - a bad job must not kill the worker
            log.exception("download worker iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


async def download_workers(stop: asyncio.Event, poll_seconds: float = 5.0) -> None:
    concurrency = max(1, get_settings().download_concurrency)
    await requeue_stuck_jobs()
    async with asyncio.TaskGroup() as group:
        for _ in range(concurrency):
            group.create_task(_worker(stop, poll_seconds))
