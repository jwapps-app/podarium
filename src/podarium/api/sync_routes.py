"""Delta sync.

One call returns everything that changed since a timestamp, with a server-supplied ``now``
to use as the next cursor. Nothing consumed this in phase 1, but it ships early so the
iOS client inherits an endpoint that has been exercised rather than invented for it.

Paging is the subtle part. A first sync against a library with a few large back catalogues
runs to thousands of episodes, and a client that took a truncated page and then advanced
``since`` to ``now`` would never see the remainder -- they would not be missing, they would
be permanently invisible. So a truncated response carries ``next_cursor``, and ``since``
must not advance until the pages run out.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from podarium.api.queue_routes import _load_queue
from podarium.auth import current_user
from podarium.cursor import InvalidCursor, decode_cursor, encode_cursor
from podarium.db import get_session
from podarium.models import DeletedFeed, Episode, EpisodeState, Feed, FeedState, User
from podarium.schemas import SyncOut, episode_out, feed_out
from podarium.services import feed_counts, get_app_settings

router = APIRouter(prefix="/api/sync", tags=["sync"])

DEFAULT_PAGE_SIZE = 1000
MAX_PAGE_SIZE = 2000


@router.get("", response_model=SyncOut)
async def sync(
    since: datetime | None = Query(default=None),
    cursor: str | None = Query(
        default=None,
        description="From a previous response's next_cursor. Keep `since` unchanged while paging.",
    ),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> SyncOut:
    """Everything changed since ``since``, plus the cursor to use next time.

    ``now`` is server-supplied so a client with a skewed clock cannot open a gap and miss a
    window of changes. Adopt it as the next ``since`` only once ``next_cursor`` is null.
    """
    # Read the clock from the database, not this process.
    #
    # Every updated_at in the delta is stamped by Postgres, so a cursor taken from the
    # application's clock is comparing against a different clock. Even small drift between
    # the API container and the database breaks the contract in one of two ways: run behind
    # and changes fall into a gap the client never revisits, run ahead and every sync
    # re-sends the whole library. Both are invisible until they are not.
    now = (await session.execute(select(func.now()))).scalar_one()
    state = aliased(EpisodeState)

    # When a record last changed, from the client's point of view. An episode is in the
    # delta if its own row changed *or* the user's state for it did -- marking something
    # played has to reach the other client -- so ordering and paging both key off whichever
    # is later. Using episode.updated_at alone would sort a state-only change by a stale
    # timestamp and let paging skip straight past it.
    changed_at = func.greatest(
        Episode.updated_at, func.coalesce(state.updated_at, Episode.updated_at)
    )

    episode_statement = (
        select(Episode, state)
        .outerjoin(state, (state.episode_id == Episode.id) & (state.user_id == user.id))
        .order_by(changed_at, Episode.id)
        .limit(limit + 1)
    )

    if since is not None:
        episode_statement = episode_statement.where(changed_at > since)

    if cursor:
        try:
            cursor_stamp, cursor_id = decode_cursor(cursor)
        except InvalidCursor as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid cursor") from exc
        episode_statement = episode_statement.where(
            (changed_at > cursor_stamp)
            | ((changed_at == cursor_stamp) & (Episode.id > cursor_id))
        )

    rows = (await session.execute(episode_statement)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last_episode, last_state = rows[-1]
        marker = last_episode.updated_at
        if last_state is not None and last_state.updated_at > marker:
            marker = last_state.updated_at
        next_cursor = encode_cursor(marker, last_episode.id)

    episodes = [episode_out(episode, episode_state) for episode, episode_state in rows]

    # Feeds, the queue and deletions are one person's, so they are small enough to send
    # whole -- but only on the first page. Repeating them on every page of a large backfill
    # would be pure overhead, and a client that restarts a paging run gets them again anyway.
    if cursor:
        return SyncOut(
            now=now, feeds=[], episodes=episodes, queue=[], deleted_feed_ids=[],
            next_cursor=next_cursor,
        )

    feed_state = aliased(FeedState)

    # A feed belongs in the delta when its own row changed *or* when this user's state for
    # it did. Marking a show seen writes only to feed_state, so keying off Feed.updated_at
    # alone would leave a client's new-episode badge permanently stale.
    feed_changed_at = func.greatest(
        Feed.updated_at, func.coalesce(feed_state.updated_at, Feed.updated_at)
    )

    feed_statement = (
        select(Feed)
        .outerjoin(
            feed_state, (feed_state.feed_id == Feed.id) & (feed_state.user_id == user.id)
        )
        .order_by(Feed.id)
    )
    if since is not None:
        feed_statement = feed_statement.where(feed_changed_at > since)

    changed_feeds = list((await session.execute(feed_statement)).scalars())

    # The same counts /api/feeds reports. Without them a synced client holds feeds with no
    # episode totals and no badge, and has to re-fetch every one of them to fill the gaps.
    app_settings = await get_app_settings(session)
    counts = await feed_counts(session, user.id, [feed.id for feed in changed_feeds])

    deleted_statement = select(DeletedFeed.feed_id)
    if since is not None:
        deleted_statement = deleted_statement.where(DeletedFeed.deleted_at > since)

    return SyncOut(
        now=now,
        feeds=[
            feed_out(
                feed,
                episode_count=counts.get(feed.id, (0, 0, 0))[0],
                unplayed_count=counts.get(feed.id, (0, 0, 0))[1],
                new_episode_count=counts.get(feed.id, (0, 0, 0))[2],
                global_auto_download_count=app_settings.global_auto_download_count,
            )
            for feed in changed_feeds
        ],
        episodes=episodes,
        queue=await _load_queue(session, user.id),
        deleted_feed_ids=list((await session.execute(deleted_statement)).scalars()),
        next_cursor=next_cursor,
    )
