"""FastAPI application.

Background jobs run as asyncio tasks in the app lifespan rather than under a separate
scheduler: the per-feed jitter, per-feed backoff, and startup requeue of stuck download
jobs are all custom logic either way, so a scheduler dependency would add a moving part
without removing any.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
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
    push_routes,
    storage_routes,
    sync_routes,
)
from podarium.auth import bootstrap_user
from podarium.clients.podcastindex import (
    describe_credential_problems,
    verify_credentials,
)
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

    # Credentials are checked at startup, not on first search. A bad one otherwise waits
    # until someone types a query and then reports a 401 from Podcast Index -- three layers
    # from the cause, and identical whether the value is wrong, truncated, or the clock is
    # off. The shape checks run inline; the live one does not hold up the boot.
    for problem in describe_credential_problems(
        settings.podcastindex_key, settings.podcastindex_secret
    ):
        log.warning("Podcast Index credentials: %s", problem)

    async def _check_credentials() -> None:
        async with get_sessionmaker()() as session:
            app_settings = await get_app_settings(session)
        result = await verify_credentials(app_settings.user_agent)
        if result == "accepted":
            log.info("Podcast Index credentials accepted; search is available")
        elif result == "not configured":
            log.info(
                "Podcast Index credentials not set; search returns 503 and the UI says so. "
                "Everything else is unaffected."
            )
        else:
            log.warning("Podcast Index credentials %s", result)

    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    if settings.run_background_jobs:
        tasks = [
            asyncio.create_task(_check_credentials(), name="credential-check"),
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
    storage_routes,
    push_routes,
    opml_routes,
):
    app.include_router(module.router)


# Registered explicitly rather than relying on the host's mime map. macOS knows this
# extension; the slim Debian image the container is built from may not, and a manifest
# served as octet-stream is ignored, which would quietly cost the home-screen install.
mimetypes.add_type("application/manifest+json", ".webmanifest")


# The built web UI, when one is present.
#
# Order matters: this block runs after every API router is registered, so /api, /healthz
# and /metrics already own their paths and the catch-all below cannot shadow them.
_web_root = get_settings().web_dir

if _web_root.is_dir() and (_web_root / "index.html").is_file():
    _index = _web_root / "index.html"

    if (_web_root / "assets").is_dir():
        # Hashed filenames, so these can be cached hard.
        app.mount("/assets", StaticFiles(directory=str(_web_root / "assets")), name="assets")

    # HEAD as well as GET. FastAPI, unlike a plain Starlette route, does not add HEAD
    # alongside GET, and a static asset answering a HEAD probe with 405 is simply wrong --
    # link checkers and some proxies ask that way.
    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_spa(path: str) -> Response:
        """History fallback for the single-page app.

        A deep link like /feeds/3 is a client-side route with no file behind it, so
        anything that is not a real file returns index.html and lets the router take over.
        An unknown /api path must still 404 as JSON rather than being handed HTML, which
        would turn a typo into a confusing parse error in the client.
        """
        if path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

        candidate = (_web_root / path).resolve()
        if path and _web_root.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)

        return FileResponse(_index, headers={"Cache-Control": "no-cache"})

    log.info("serving web UI from %s", _web_root)
else:
    log.info("no web UI at %s; running API-only", _web_root)
