"""FastAPI application.

Background jobs run as asyncio tasks in the app lifespan rather than under a separate
scheduler: the per-feed jitter, per-feed backoff, and startup requeue of stuck download
jobs are all custom logic either way, so a scheduler dependency would add a moving part
without removing any.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from podarium import __version__
from podarium.api import (
    admin_routes,
    auth_routes,
    episode_routes,
    feed_routes,
    media_routes,
    opml_routes,
    queue_routes,
    search_routes,
    settings_routes,
    sync_routes,
)
from podarium.auth import bootstrap_user
from podarium.config import get_settings
from podarium.db import get_sessionmaker
from podarium.jobs.downloader import download_workers
from podarium.jobs.refresh import refresh_loop
from podarium.jobs.retention import retention_loop
from podarium.services import get_app_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("podarium")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.artwork_dir.mkdir(parents=True, exist_ok=True)

    async with get_sessionmaker()() as session:
        app_settings = await get_app_settings(session)
        if app_settings.user_agent == "Podarium/0.1.0" and __version__ != "0.1.0":
            app_settings.user_agent = f"Podarium/{__version__}"
            await session.commit()
        await bootstrap_user(session, settings)

    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    if settings.run_background_jobs:
        tasks = [
            asyncio.create_task(refresh_loop(stop), name="refresh"),
            asyncio.create_task(download_workers(stop), name="downloads"),
            asyncio.create_task(
                retention_loop(stop, settings.retention_sweep_minutes * 60), name="retention"
            ),
        ]
        log.info("background jobs started")

    try:
        yield
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown is best effort
                pass


app = FastAPI(title="Podarium", version=__version__, lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Uniform error envelope: {"error": {"code", "message"}} (spec 6)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)}},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": str(exc.errors())}},
    )


for module in (
    admin_routes,
    auth_routes,
    search_routes,
    feed_routes,
    episode_routes,
    queue_routes,
    media_routes,
    sync_routes,
    settings_routes,
    opml_routes,
):
    app.include_router(module.router)


# Phase 2 mounts the built web UI here. Absent in phase 1, which is API-only.
_web_root = get_settings().download_dir.parent / "web"
if _web_root.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_root), html=True), name="web")
