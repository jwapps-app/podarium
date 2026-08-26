"""What is actually on disk.

The download-directory ceiling is impossible to set sensibly without this: the honest
question is not "how many gigabytes feel safe" but "how much am I using, and how much of it
could retention reclaim if it needed to".

That second number matters more than it looks. Starred and queued episodes are exempt from
retention *and* from the ceiling, by design -- they are the one thing here that grows
without bound. A library that is 90% starred has a ceiling that cannot do anything.

Protection is read from the retention sweep's own definition rather than restated here, and
it is deliberately not scoped to the requesting user: the disk is shared and the ceiling is
global, so anything the sweep will refuse to delete counts against everyone's headroom. A
per-user number would report reclaimable space that retention would then decline to reclaim.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.db import get_session
from podarium.jobs.retention import protected_episode_ids
from podarium.models import Episode, Feed, User
from podarium.services import get_app_settings

router = APIRouter(prefix="/api/storage", tags=["storage"])


class FeedUsage(BaseModel):
    feed_id: int
    title: str | None
    bytes: int
    episodes: int


class StorageOut(BaseModel):
    total_bytes: int
    episodes: int

    # Starred or queued: exempt from retention and from the ceiling.
    protected_bytes: int
    protected_episodes: int

    # What retention could take back if it had to.
    reclaimable_bytes: int

    ceiling_bytes: int | None
    feeds: list[FeedUsage]


@router.get("", response_model=StorageOut)
async def storage(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> StorageOut:
    app_settings = await get_app_settings(session)

    protected = await protected_episode_ids(session)

    rows = (
        await session.execute(
            select(Episode.id, Episode.feed_id, Episode.local_bytes, Feed.title)
            .join(Feed, Feed.id == Episode.feed_id)
            .where(Episode.local_path.is_not(None))
        )
    ).all()

    per_feed: dict[int, FeedUsage] = {}
    total = protected_bytes = protected_count = 0

    for episode_id, feed_id, local_bytes, title in rows:
        size = local_bytes or 0
        total += size
        if episode_id in protected:
            protected_bytes += size
            protected_count += 1
        usage = per_feed.setdefault(
            feed_id, FeedUsage(feed_id=feed_id, title=title, bytes=0, episodes=0)
        )
        usage.bytes += size
        usage.episodes += 1

    return StorageOut(
        total_bytes=total,
        episodes=len(rows),
        protected_bytes=protected_bytes,
        protected_episodes=protected_count,
        reclaimable_bytes=total - protected_bytes,
        ceiling_bytes=app_settings.download_dir_max_bytes,
        feeds=sorted(per_feed.values(), key=lambda f: f.bytes, reverse=True),
    )
