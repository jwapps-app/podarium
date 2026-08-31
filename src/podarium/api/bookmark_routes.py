"""Timestamps inside an episode worth coming back to.

Starring is about a whole episode. This is about the bit at 41:20 -- which is the thing a
podcast leaves you with that is hardest to recover: you remember a sentence and have no way
back to it short of scrubbing through three hours.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import Bookmark, Episode, User
from podarium.schemas import BookmarkOut

router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])


class BookmarkCreate(BaseModel):
    episode_id: int
    position_seconds: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=2000)


class BookmarkUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def _out(bookmark: Bookmark, episode: Episode | None = None) -> BookmarkOut:
    return BookmarkOut(
        id=bookmark.id,
        episode_id=bookmark.episode_id,
        position_seconds=bookmark.position_seconds,
        note=bookmark.note,
        created_at=bookmark.created_at,
        episode_title=episode.title if episode else None,
        feed_id=episode.feed_id if episode else None,
    )


async def load_bookmarks(session: AsyncSession, user_id: int) -> list[BookmarkOut]:
    """Every bookmark this person has, newest first. Shared with /api/sync."""
    rows = (
        await session.execute(
            select(Bookmark, Episode)
            .join(Episode, Episode.id == Bookmark.episode_id)
            .where(Bookmark.user_id == user_id)
            .order_by(Bookmark.created_at.desc())
        )
    ).all()
    return [_out(bookmark, episode) for bookmark, episode in rows]


@router.get("", response_model=list[BookmarkOut])
async def list_bookmarks(
    episode_id: int | None = Query(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BookmarkOut]:
    """Every bookmark, or one episode's.

    Ordered by position within an episode, since that is the order you would replay them
    in; newest first across the library, since that is the order you would look for them.
    """
    statement = (
        select(Bookmark, Episode)
        .join(Episode, Episode.id == Bookmark.episode_id)
        .where(Bookmark.user_id == user.id)
    )
    if episode_id is not None:
        statement = statement.where(Bookmark.episode_id == episode_id).order_by(
            Bookmark.position_seconds
        )
    else:
        statement = statement.order_by(Bookmark.created_at.desc())

    return [_out(bookmark, episode) for bookmark, episode in (await session.execute(statement)).all()]


@router.post("", response_model=BookmarkOut, status_code=status.HTTP_201_CREATED)
async def create_bookmark(
    body: BookmarkCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> BookmarkOut:
    episode = await session.get(Episode, body.episode_id)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Episode not found")

    bookmark = Bookmark(
        user_id=user.id,
        episode_id=body.episode_id,
        position_seconds=body.position_seconds,
        note=body.note,
    )
    session.add(bookmark)
    await session.commit()
    await session.refresh(bookmark)
    return _out(bookmark, episode)


@router.patch("/{bookmark_id}", response_model=BookmarkOut)
async def update_bookmark(
    bookmark_id: int,
    body: BookmarkUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> BookmarkOut:
    """Only the note is editable. The timestamp is what the bookmark *is*; changing it
    would be making a different bookmark, which is what creating one is for."""
    bookmark = await session.get(Bookmark, bookmark_id)
    if bookmark is None or bookmark.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bookmark not found")

    bookmark.note = body.note
    bookmark.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(bookmark)
    return _out(bookmark, await session.get(Episode, bookmark.episode_id))


@router.delete("/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bookmark(
    bookmark_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await session.execute(
        delete(Bookmark).where(Bookmark.id == bookmark_id).where(Bookmark.user_id == user.id)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
