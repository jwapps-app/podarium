"""Delta sync.

Nothing consumes this in phase 1, but it ships in phase 1 anyway: it is the single call
that makes the iOS client cheap, and building it alongside the endpoints it mirrors keeps
it honest.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import Episode, EpisodeState, Feed, User
from podarium.schemas import SyncOut, episode_out, feed_out
from podarium.api.queue_routes import _load_queue

router = APIRouter(prefix="/api/sync", tags=["sync"])

MAX_EPISODES_PER_SYNC = 2000


@router.get("", response_model=SyncOut)
async def sync(
    since: datetime | None = Query(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> SyncOut:
    """Everything changed since ``since``, plus the cursor to use next time.

    ``now`` is server-supplied so a client with a skewed clock cannot open a gap and miss
    a window of changes.
    """
    now = datetime.now(UTC)

    feed_statement = select(Feed).order_by(Feed.id)
    if since is not None:
        feed_statement = feed_statement.where(Feed.updated_at > since)
    feeds = (await session.execute(feed_statement)).scalars().all()

    state = aliased(EpisodeState)
    episode_statement = (
        select(Episode, state)
        .outerjoin(state, (state.episode_id == Episode.id) & (state.user_id == user.id))
        .order_by(Episode.updated_at, Episode.id)
        .limit(MAX_EPISODES_PER_SYNC)
    )
    if since is not None:
        # An episode is in the delta if the episode row changed *or* the user's state for
        # it changed -- marking something played must reach the other client.
        episode_statement = episode_statement.where(
            (Episode.updated_at > since) | (state.updated_at > since)
        )
    episode_rows = (await session.execute(episode_statement)).all()

    return SyncOut(
        now=now,
        feeds=[feed_out(feed) for feed in feeds],
        episodes=[episode_out(episode, episode_state) for episode, episode_state in episode_rows],
        queue=await _load_queue(session, user.id),
    )
