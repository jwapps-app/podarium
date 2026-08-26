from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from podarium.auth import current_user
from podarium.cursor import InvalidCursor, decode_cursor, encode_cursor
from podarium.db import get_session
from podarium.jobs.retention import purge_episode
from podarium.models import Episode, EpisodeState, Feed, JobSource, User
from podarium.schemas import EpisodeListOut, EpisodeOut, EpisodeStateUpdate, episode_out
from podarium.services import enqueue_download

router = APIRouter(prefix="/api/episodes", tags=["episodes"])


# How far behind a write may be before it is treated as overtaken rather than concurrent.
#
# Devices do not agree on the time to the second, and a phone running a little slow would
# otherwise have perfectly ordinary writes declined the moment anything else touched the
# same episode. The case worth catching is an offline flush that is hours old, so the
# tolerance can be generous: inside a minute, arrival order decides, which is what happened
# before this existed and is fine for two devices one person is holding.
CONCURRENT_WRITE_TOLERANCE = timedelta(seconds=60)


def _is_stale(changed_at: datetime | None, stored_at: datetime | None) -> bool:
    """Whether a write describes a moment that has already been overtaken.

    A write with no timestamp is taken as happening now, which keeps existing clients
    working unchanged. A timestamp in the future is clamped to now rather than trusted:
    a device with a fast clock would otherwise win every conflict it ever had.
    """
    if changed_at is None or stored_at is None:
        return False

    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=UTC)
    if stored_at.tzinfo is None:
        stored_at = stored_at.replace(tzinfo=UTC)

    changed_at = min(changed_at, datetime.now(UTC))
    return changed_at < stored_at - CONCURRENT_WRITE_TOLERANCE


async def _get_episode_or_404(session: AsyncSession, episode_id: int) -> Episode:
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode not found")
    return episode


async def _get_or_create_state(
    session: AsyncSession, user_id: int, episode_id: int
) -> tuple[EpisodeState, bool]:
    """Return the state row and whether it already existed.

    The caller needs to know: a row that did not exist cannot be stale, so a first write
    always applies whatever timestamp it carries.
    """
    state = await session.get(EpisodeState, {"user_id": user_id, "episode_id": episode_id})
    if state is not None:
        return state, True
    state = EpisodeState(user_id=user_id, episode_id=episode_id)
    session.add(state)
    await session.flush()
    return state, False


@router.get("", response_model=EpisodeListOut)
async def list_episodes(
    feed_id: int | None = Query(default=None),
    unplayed: bool | None = Query(default=None),
    downloaded: bool | None = Query(default=None),
    starred: bool | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EpisodeListOut:
    """Newest first by ``first_seen_at``.

    ``since`` and the sort both key off ``first_seen_at``, not ``published_at``: a
    publisher re-stamping pubDate across its back catalogue must not shuffle the inbox or
    make an archive look new.
    """
    state = aliased(EpisodeState)

    # Chronological by publication, but never later than when this server first saw the
    # episode.
    #
    # Sorting on published_at alone would read naturally right up until a publisher migrates
    # hosts and re-stamps pubDate across its whole archive -- the case first_seen_at exists
    # for -- at which point hundreds of old episodes leap to the top of the inbox. Sorting on
    # first_seen_at alone avoids that but clumps by subscription: a show added today lands
    # its entire back catalogue above episodes from shows added last week, whatever their
    # real dates.
    #
    # Taking the earlier of the two gives the natural order in the normal case, because
    # publication precedes discovery, while capping a re-stamped episode at the moment we
    # first saw it so it cannot climb. "Is this new?" is unaffected -- that still compares
    # first_seen_at against the feed's last_seen_at.
    sort_key = func.least(
        func.coalesce(Episode.published_at, Episode.first_seen_at), Episode.first_seen_at
    ).label("sort_key")

    statement = (
        select(Episode, state, sort_key)
        .join(Feed, Feed.id == Episode.feed_id)
        .outerjoin(state, (state.episode_id == Episode.id) & (state.user_id == user.id))
        .order_by(sort_key.desc(), Episode.id.desc())
        .limit(limit + 1)
    )

    if feed_id is not None:
        statement = statement.where(Episode.feed_id == feed_id)

    # Unsubscribed shows drop out of the general browse, but not out of anything you asked
    # for specifically. A show requested by id, or an episode you starred, is an explicit
    # choice and is returned whatever the subscription state -- otherwise the library's
    # Unsubscribed section would lead to an empty page, and unsubscribing would silently
    # empty your starred list.
    if feed_id is None and starred is not True:
        statement = statement.where(Feed.active.is_(True))
    if unplayed is True:
        statement = statement.where((state.played.is_(None)) | (state.played.is_(False)))
    elif unplayed is False:
        statement = statement.where(state.played.is_(True))
    if downloaded is True:
        statement = statement.where(Episode.local_path.is_not(None))
    elif downloaded is False:
        statement = statement.where(Episode.local_path.is_(None))
    if starred is True:
        statement = statement.where(state.starred.is_(True))
    if since is not None:
        statement = statement.where(Episode.first_seen_at > since)
    if cursor:
        try:
            cursor_stamp, cursor_id = decode_cursor(cursor)
        except InvalidCursor as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid cursor") from exc
        # Keyed to the same expression the ordering uses, or paging would skip and repeat.
        statement = statement.where(
            (sort_key < cursor_stamp)
            | ((sort_key == cursor_stamp) & (Episode.id < cursor_id))
        )

    rows = (await session.execute(statement)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [episode_out(episode, episode_state) for episode, episode_state, _ in rows]
    next_cursor = encode_cursor(rows[-1][2], rows[-1][0].id) if has_more and rows else None
    return EpisodeListOut(items=items, next_cursor=next_cursor)


@router.get("/{episode_id}", response_model=EpisodeOut)
async def get_episode(
    episode_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EpisodeOut:
    episode = await _get_episode_or_404(session, episode_id)
    state = await session.get(EpisodeState, {"user_id": user.id, "episode_id": episode_id})
    return episode_out(episode, state)


@router.post("/{episode_id}/download", response_model=EpisodeOut, status_code=status.HTTP_202_ACCEPTED)
async def request_download(
    episode_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EpisodeOut:
    episode = await _get_episode_or_404(session, episode_id)
    if not episode.enclosure_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Episode has no audio enclosure")
    await enqueue_download(session, episode, JobSource.manual)
    await session.commit()
    state = await session.get(EpisodeState, {"user_id": user.id, "episode_id": episode_id})
    return episode_out(episode, state)


@router.delete("/{episode_id}/download", status_code=status.HTTP_204_NO_CONTENT)
async def delete_download(
    episode_id: int,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Purge the file, keep the row -- the same contract retention uses."""
    episode = await _get_episode_or_404(session, episode_id)
    await purge_episode(session, episode, reason="manual")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{episode_id}/state", response_model=EpisodeOut)
async def update_state(
    episode_id: int,
    body: EpisodeStateUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EpisodeOut:
    episode = await _get_episode_or_404(session, episode_id)
    state, existed = await _get_or_create_state(session, user.id, episode_id)

    if existed and _is_stale(body.changed_at, state.updated_at):
        # Older than what is already stored, so it describes a moment that has since been
        # overtaken. Returning the current state rather than an error lets the client
        # correct its own copy from the reply instead of handling a failure path.
        return episode_out(episode, state)

    if body.played is not None:
        # completed_at is what after_played retention measures from, so it is stamped on
        # the transition into played and cleared on the way back out.
        if body.played and not state.played:
            state.completed_at = datetime.now(UTC)
        elif not body.played:
            state.completed_at = None
        state.played = body.played
    if body.position_seconds is not None:
        state.position_seconds = body.position_seconds
    if body.starred is not None:
        state.starred = body.starred

    await session.commit()
    await session.refresh(state)
    return episode_out(episode, state)
