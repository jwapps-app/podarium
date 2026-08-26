from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import User
from podarium.schemas import SettingsOut, SettingsUpdate
from podarium.services import get_app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _out(row) -> SettingsOut:
    return SettingsOut(
        global_retention_mode=row.global_retention_mode,
        global_retention_days=row.global_retention_days,
        download_dir_max_bytes=row.download_dir_max_bytes,
        refresh_interval_minutes=row.refresh_interval_minutes,
        user_agent=row.user_agent,
    )


@router.get("", response_model=SettingsOut)
async def read_settings(
    _: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    return _out(await get_app_settings(session))


@router.put("", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdate,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> SettingsOut:
    row = await get_app_settings(session)

    if body.global_retention_mode is not None:
        row.global_retention_mode = body.global_retention_mode
    if body.global_retention_days is not None:
        row.global_retention_days = body.global_retention_days
    if body.refresh_interval_minutes is not None:
        row.refresh_interval_minutes = body.refresh_interval_minutes
    if body.user_agent:
        row.user_agent = body.user_agent

    # As with per-feed retention, NULL here is meaningful: it means "no ceiling".
    if body.clear_download_dir_max_bytes:
        row.download_dir_max_bytes = None
    elif body.download_dir_max_bytes is not None:
        row.download_dir_max_bytes = body.download_dir_max_bytes

    await session.commit()
    await session.refresh(row)
    return _out(row)
