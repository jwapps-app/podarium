from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from podarium.auth import current_user
from podarium.cursor import InvalidCursor, decode_cursor, encode_cursor
from podarium.db import get_session
from podarium.jobs.retention import purge_episode
from podarium.models import Episode, EpisodeState, JobSource, User
from podarium.schemas import EpisodeListOut, EpisodeOut, EpisodeStateUpdate, episode_out
from podarium.services import enqueue_download

router = APIRouter(prefix="/api/episodes", tags=["episodes"])


async def _get_episode_or_404(session: AsyncSession, episode_id: int) -> Episode:
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode not found")
    return episode


async def _get_or_create_state(session: AsyncSession, user_id: int, episode_id: int) -> EpisodeState:
    state = await session.get(EpisodeState, {"user_id": user_id, "episode_id": episode_id})
    if state is None:
        state = EpisodeState(user_id=user_id, episode_id=episode_id)
        session.add(state)
        await session.flush()
    return state


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
    statement = (
        select(Episode, state)
        .outerjoin(state, (state.episode_id == Episode.id) & (state.user_id == user.id))
        .order_by(Episode.first_seen_at.desc(), Episode.id.desc())
        .limit(limit + 1)
    )

    if feed_id is not None:
        statement = statement.where(Episode.feed_id == feed_id)
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
        statement = statement.where(
            (Episode.first_seen_at < cursor_stamp)
            | ((Episode.first_seen_at == cursor_stamp) & (Episode.id < cursor_id))
        )

    rows = (await session.execute(statement)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [episode_out(episode, episode_state) for episode, episode_state in rows]
    next_cursor = encode_cursor(rows[-1][0].first_seen_at, rows[-1][0].id) if has_more and rows else None
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
    state = await _get_or_create_state(session, user.id, episode_id)

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
