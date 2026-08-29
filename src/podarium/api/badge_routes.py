"""The number on the home-screen icon.

Kept as its own pair of endpoints because the badge is read and cleared far more often
than anything else in the app -- on every launch, and every time the app comes back to the
foreground -- and it should cost one small query, not a page of episodes.

The count itself is a server concern rather than something the client accumulates. A
client that counts push messages drifts the moment one is missed, dropped by the push
service, or delivered to a device that is switched off, and it has no way to recover the
true figure. Asking the server means the badge is right again the next time the app is
opened, whatever happened in between.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import User
from podarium.services import mark_inbox_seen, unseen_episode_count

router = APIRouter(prefix="/api/badge", tags=["badge"])


class BadgeOut(BaseModel):
    count: int


@router.get("", response_model=BadgeOut)
async def badge(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> BadgeOut:
    """Episodes that have arrived since the inbox was last looked at."""
    return BadgeOut(count=await unseen_episode_count(session, user.id))


@router.post("/seen", response_model=BadgeOut)
async def seen(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> BadgeOut:
    """Mark the inbox looked at, which is what clears the badge.

    Returns the new count rather than nothing, so the caller sets the badge from what the
    server says instead of assuming zero -- an episode can arrive between the two.
    """
    await mark_inbox_seen(session, user.id)
    await session.commit()
    return BadgeOut(count=await unseen_episode_count(session, user.id))
