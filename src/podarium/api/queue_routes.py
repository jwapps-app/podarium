from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import Episode, EpisodeState, JobSource, QueueItem, User
from podarium.schemas import QueueAddRequest, QueueItemOut, QueueOrderRequest, episode_out
from podarium.services import compact_queue_positions, enqueue_download, without_large_text

router = APIRouter(prefix="/api/queue", tags=["queue"])


async def _load_queue(session: AsyncSession, user_id: int) -> list[QueueItemOut]:
    state = aliased(EpisodeState)
    rows = (
        await session.execute(
            select(QueueItem, Episode, state)
            .options(*without_large_text())
            .join(Episode, Episode.id == QueueItem.episode_id)
            .outerjoin(state, (state.episode_id == Episode.id) & (state.user_id == user_id))
            .where(QueueItem.user_id == user_id)
            .order_by(QueueItem.position, QueueItem.id)
        )
    ).all()
    return [
        QueueItemOut(
            episode_id=item.episode_id,
            position=item.position,
            added_at=item.added_at,
            episode=episode_out(episode, episode_state),
        )
        for item, episode, episode_state in rows
    ]


@router.get("", response_model=list[QueueItemOut])
async def get_queue(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[QueueItemOut]:
    return await _load_queue(session, user.id)


@router.post("", response_model=list[QueueItemOut], status_code=status.HTTP_201_CREATED)
async def add_to_queue(
    body: QueueAddRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[QueueItemOut]:
    """Queueing an episode is also what triggers its download (spec 2)."""
    episode = await session.get(Episode, body.episode_id)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode not found")

    existing = (
        await session.execute(
            select(QueueItem)
            .where(QueueItem.user_id == user.id)
            .where(QueueItem.episode_id == body.episode_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        tail = (
            await session.execute(
                select(func.coalesce(func.max(QueueItem.position), -1)).where(
                    QueueItem.user_id == user.id
                )
            )
        ).scalar_one()
        position = body.position if body.position is not None else tail + 1
        if body.position is not None:
            # Insert: push everything at or after the target down one slot.
            for item in (
                await session.execute(
                    select(QueueItem)
                    .where(QueueItem.user_id == user.id)
                    .where(QueueItem.position >= position)
                )
            ).scalars():
                item.position += 1
        session.add(QueueItem(user_id=user.id, episode_id=episode.id, position=position))
        await session.flush()
        await compact_queue_positions(session, user.id)

    await enqueue_download(session, episode, JobSource.queue)
    await session.commit()
    return await _load_queue(session, user.id)


@router.put("/order", response_model=list[QueueItemOut])
async def reorder_queue(
    body: QueueOrderRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[QueueItemOut]:
    items = {
        item.episode_id: item
        for item in (
            await session.execute(select(QueueItem).where(QueueItem.user_id == user.id))
        ).scalars()
    }
    unknown = [eid for eid in body.episode_ids if eid not in items]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Not in queue: {unknown}"
        )

    for index, episode_id in enumerate(body.episode_ids):
        items[episode_id].position = index
    # Anything the client left out keeps its relative order, appended after the reordered
    # block, so a partial list cannot silently drop items from the queue.
    remaining = [item for eid, item in items.items() if eid not in set(body.episode_ids)]
    for offset, item in enumerate(sorted(remaining, key=lambda i: i.position)):
        item.position = len(body.episode_ids) + offset

    await session.commit()
    return await _load_queue(session, user.id)


@router.delete("/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_queue(
    episode_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    item = (
        await session.execute(
            select(QueueItem)
            .where(QueueItem.user_id == user.id)
            .where(QueueItem.episode_id == episode_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode is not in the queue")
    await session.delete(item)
    await session.flush()
    await compact_queue_positions(session, user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
