"""OPML import and export -- the migration path off PinePods (spec 8)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import Feed, User
from podarium.schemas import OpmlImportResult
from podarium.services import get_app_settings
from podarium.subscribe import subscribe_feed

router = APIRouter(prefix="/api/opml", tags=["opml"])

MAX_OPML_BYTES = 5 * 1024 * 1024


@router.get("/export")
async def export_opml(
    _: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Response:
    feeds = (
        await session.execute(select(Feed).where(Feed.active.is_(True)).order_by(Feed.id))
    ).scalars().all()

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        "    <title>Podarium subscriptions</title>",
        f"    <dateCreated>{datetime.now(UTC).isoformat()}</dateCreated>",
        "  </head>",
        "  <body>",
    ]
    for feed in feeds:
        title = escape(feed.title or feed.feed_url, {'"': "&quot;"})
        url = escape(feed.feed_url, {'"': "&quot;"})
        lines.append(f'    <outline type="rss" text="{title}" title="{title}" xmlUrl="{url}" />')
    lines += ["  </body>", "</opml>"]

    return Response(
        "\n".join(lines),
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="podarium.opml"'},
    )


def _extract_feed_urls(raw: bytes) -> list[str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Invalid OPML: {exc}") from exc

    urls: list[str] = []
    seen: set[str] = set()
    for outline in root.iter("outline"):
        url = outline.get("xmlUrl") or outline.get("xmlurl")
        if url:
            url = url.strip()
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


@router.post("/import", response_model=OpmlImportResult)
async def import_opml(
    request: Request,
    file: UploadFile | None = File(default=None),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> OpmlImportResult:
    """Accepts either a multipart upload or a raw OPML body."""
    raw = await file.read() if file is not None else await request.body()
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Empty OPML body")
    if len(raw) > MAX_OPML_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="OPML file too large")

    app_settings = await get_app_settings(session)
    urls = _extract_feed_urls(raw)

    imported = skipped = failed = 0
    errors: list[str] = []

    for url in urls:
        try:
            # refresh=False keeps a large import fast; the scheduled pass fills in the
            # metadata and episodes shortly after.
            _, created = await subscribe_feed(
                session, url, user_agent=app_settings.user_agent, refresh=False
            )
            if created:
                imported += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001 - one bad feed must not abort the import
            await session.rollback()
            failed += 1
            if len(errors) < 20:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")

    return OpmlImportResult(imported=imported, skipped=skipped, failed=failed, errors=errors)
