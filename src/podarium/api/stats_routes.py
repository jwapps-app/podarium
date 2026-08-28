"""Listening statistics, derived rather than tracked.

Everything here is computed from state this server already keeps -- what is played, where
you are in what you have not finished. Nothing new is recorded, and no event log is kept:
a self-hosted server has no reason to accumulate a behavioural record, and the interesting
numbers do not need one.

That does make these estimates, and the docstrings say where. An episode marked played is
counted as heard in full, which is true of a finished episode and generous about one you
marked played to clear it.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.api.episode_routes import IN_PROGRESS_MIN_ELAPSED, IN_PROGRESS_MIN_REMAINING
from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import Bookmark, Episode, EpisodeState, Feed, User
from podarium.services import get_app_settings

router = APIRouter(prefix="/api/stats", tags=["stats"])


class ShowStats(BaseModel):
    feed_id: int
    title: str | None
    episodes_played: int
    seconds_played: int


class StatsOut(BaseModel):
    episodes_played: int
    seconds_played: int
    # What playing above 1x has saved, at the rate that currently applies to each show.
    seconds_saved_by_speed: int
    # Dead air removed by processing, where a processed copy exists.
    episodes_processed: int
    in_progress: int
    bookmarks: int
    shows: list[ShowStats]


@router.get("", response_model=StatsOut)
async def stats(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> StatsOut:
    app_settings = await get_app_settings(session)

    # Played episodes count their whole duration; unfinished ones count how far in you are.
    played_seconds = func.sum(
        case(
            (EpisodeState.played.is_(True), func.coalesce(Episode.duration_seconds, 0)),
            else_=func.coalesce(EpisodeState.position_seconds, 0),
        )
    )
    played_count = func.count().filter(EpisodeState.played.is_(True))

    rows = (
        await session.execute(
            select(
                Feed.id,
                Feed.title,
                Feed.playback_rate,
                played_count,
                func.coalesce(played_seconds, 0),
            )
            .select_from(EpisodeState)
            .join(Episode, Episode.id == EpisodeState.episode_id)
            .join(Feed, Feed.id == Episode.feed_id)
            .where(EpisodeState.user_id == user.id)
            .group_by(Feed.id, Feed.title, Feed.playback_rate)
            .order_by(func.coalesce(played_seconds, 0).desc())
        )
    ).all()

    shows: list[ShowStats] = []
    total_seconds = 0
    total_played = 0
    saved = 0.0

    for feed_id, title, feed_rate, count, seconds in rows:
        seconds = int(seconds or 0)
        shows.append(
            ShowStats(
                feed_id=feed_id, title=title, episodes_played=int(count), seconds_played=seconds
            )
        )
        total_seconds += seconds
        total_played += int(count)

        # At 1.5x, an hour of audio takes forty minutes: the saving is the difference. Uses
        # the rate that applies now, since no per-episode rate is recorded -- so changing a
        # show's speed re-scores its history.
        rate = feed_rate if feed_rate is not None else app_settings.default_playback_rate
        if rate and rate > 0:
            saved += seconds - (seconds / rate)

    processed = (
        await session.execute(
            select(func.count()).select_from(Episode).where(Episode.processed_path.is_not(None))
        )
    ).scalar_one()

    # The same definition the inbox's "In progress" filter uses, thresholds and all.
    # Counting it differently here would put two numbers for one idea in front of the same
    # person, which is how a statistic stops being believed.
    in_progress = (
        await session.execute(
            select(func.count())
            .select_from(EpisodeState)
            .join(Episode, Episode.id == EpisodeState.episode_id)
            .where(EpisodeState.user_id == user.id)
            .where(EpisodeState.played.is_(False))
            .where(EpisodeState.position_seconds > IN_PROGRESS_MIN_ELAPSED)
            .where(
                (Episode.duration_seconds.is_(None))
                | (
                    Episode.duration_seconds - EpisodeState.position_seconds
                    > IN_PROGRESS_MIN_REMAINING
                )
            )
        )
    ).scalar_one()

    bookmarks = (
        await session.execute(
            select(func.count()).select_from(Bookmark).where(Bookmark.user_id == user.id)
        )
    ).scalar_one()

    return StatsOut(
        episodes_played=total_played,
        seconds_played=total_seconds,
        seconds_saved_by_speed=int(saved),
        episodes_processed=int(processed),
        in_progress=int(in_progress),
        bookmarks=int(bookmarks),
        shows=shows,
    )
