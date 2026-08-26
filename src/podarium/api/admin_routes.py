from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from podarium.db import get_session
from podarium.metrics import (
    download_dir_bytes,
    download_queue_depth,
    feeds_with_errors,
)
from podarium.models import DownloadJob, Episode, Feed, JobState

router = APIRouter(tags=["admin"])


@router.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> Response:
    try:
        await session.execute(select(1))
    except SQLAlchemyError as exc:
        return Response(
            content=f'{{"status":"error","database":"{type(exc).__name__}"}}',
            media_type="application/json",
            status_code=503,
        )
    return Response(content='{"status":"ok"}', media_type="application/json")


@router.get("/metrics")
async def metrics(session: AsyncSession = Depends(get_session)) -> Response:
    """Gauges are sampled at scrape time; counters are incremented by the jobs themselves."""
    depth = (
        await session.execute(
            select(func.count())
            .select_from(DownloadJob)
            .where(DownloadJob.state.in_([JobState.queued, JobState.running]))
        )
    ).scalar_one()
    download_queue_depth.set(int(depth))

    on_disk = (
        await session.execute(
            select(func.coalesce(func.sum(Episode.local_bytes), 0)).where(
                Episode.local_path.is_not(None)
            )
        )
    ).scalar_one()
    download_dir_bytes.set(int(on_disk or 0))

    errored = (
        await session.execute(
            select(func.count()).select_from(Feed).where(Feed.fetch_error_count > 0)
        )
    ).scalar_one()
    feeds_with_errors.set(int(errored))

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
