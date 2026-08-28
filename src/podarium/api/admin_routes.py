import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from podarium.config import get_settings
from podarium.db import get_session
from podarium.metrics import (
    download_dir_bytes,
    processed_bytes_gauge,
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
async def metrics(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    """Gauges are sampled at scrape time; counters are incremented by the jobs themselves.

    Open by default, because a Prometheus on the same LAN is the expected consumer. Once
    METRICS_TOKEN is set the scrape must carry it as a bearer token -- through a public
    hostname, an open /metrics hands queue depths and byte counts to anyone who asks.
    """
    expected = get_settings().metrics_token
    if expected:
        header = request.headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="metrics token required")
    depth = (
        await session.execute(
            select(func.count())
            .select_from(DownloadJob)
            .where(DownloadJob.state.in_([JobState.queued, JobState.running]))
        )
    ).scalar_one()
    download_queue_depth.set(int(depth))

    # Both copies: trimming keeps the original beside the processed file, so summing only
    # the original reports about 60% of the real figure -- and a dashboard fed a wrong
    # number is worse than one fed none.
    on_disk, processed = (
        await session.execute(
            select(
                func.coalesce(func.sum(Episode.local_bytes), 0)
                + func.coalesce(func.sum(Episode.processed_bytes), 0),
                func.coalesce(func.sum(Episode.processed_bytes), 0),
            ).where(Episode.local_path.is_not(None))
        )
    ).one()
    download_dir_bytes.set(int(on_disk or 0))
    processed_bytes_gauge.set(int(processed or 0))

    errored = (
        await session.execute(
            select(func.count()).select_from(Feed).where(Feed.fetch_error_count > 0)
        )
    ).scalar_one()
    feeds_with_errors.set(int(errored))

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
