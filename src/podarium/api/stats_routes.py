"""Listening statistics.

Time listened is measured, not inferred. The obvious implementation -- count the duration
of everything marked played -- describes tidying rather than listening: marking played is
how an inbox gets cleared, and it means "I am not going to listen to this", which is close
to the opposite of having listened. A library cleared of things you skipped would report
hundreds of hours nobody heard.

So the player counts audio as it actually plays and the total is accumulated per episode.
Seeking does not count; skipping to the end does not count; marking played does not count.

Two consequences worth stating plainly. There is still no event log -- one integer per
episode, not a behavioural record. And the counter starts from the update that introduced
it: listening before that was never measured and cannot be recovered, so the figures begin
at zero rather than being back-filled from a number known to be wrong.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.api.episode_routes import IN_PROGRESS_MIN_ELAPSED, IN_PROGRESS_MIN_REMAINING
from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import Bookmark, Episode, EpisodeState, Feed, User
from podarium.services import get_app_settings

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Below this, an episode was sampled rather than listened to -- pressing play to hear what
# a show sounds like should not count as an episode listened.
MIN_LISTENED_SECONDS = 60


class ShowStats(BaseModel):
    feed_id: int
    title: str | None
    # Marked played, whether or not it was heard -- clearing the inbox counts here.
    episodes_marked_played: int
    # Episodes with real listening time against them.
    episodes_listened: int
    seconds_listened: int


class StatsOut(BaseModel):
    # Kept separate on purpose: one is a count of a flag, the other of listening.
    episodes_marked_played: int
    episodes_listened: int
    seconds_listened: int
    # What playing above 1x has saved, at the rate that currently applies to each show.
    seconds_saved_by_speed: int
    # What trimming saved, counted only against episodes actually listened to.
    seconds_saved_by_trimming: int
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

    listened_seconds = func.sum(func.coalesce(EpisodeState.listened_seconds, 0))
    marked_count = func.count().filter(EpisodeState.played.is_(True))
    # "Listened to" needs a floor, or pressing play by accident counts as an episode.
    listened_count = func.count().filter(
        EpisodeState.listened_seconds > MIN_LISTENED_SECONDS
    )

    rows = (
        await session.execute(
            select(
                Feed.id,
                Feed.title,
                Feed.playback_rate,
                marked_count,
                listened_count,
                func.coalesce(listened_seconds, 0),
            )
            .select_from(EpisodeState)
            .join(Episode, Episode.id == EpisodeState.episode_id)
            .join(Feed, Feed.id == Episode.feed_id)
            .where(EpisodeState.user_id == user.id)
            .group_by(Feed.id, Feed.title, Feed.playback_rate)
            .order_by(func.coalesce(listened_seconds, 0).desc())
        )
    ).all()

    shows: list[ShowStats] = []
    total_seconds = 0
    total_marked = 0
    total_listened = 0
    saved = 0.0

    for feed_id, title, feed_rate, marked, listened, seconds in rows:
        seconds = int(seconds or 0)
        shows.append(
            ShowStats(
                feed_id=feed_id,
                title=title,
                episodes_marked_played=int(marked),
                episodes_listened=int(listened),
                seconds_listened=seconds,
            )
        )
        total_seconds += seconds
        total_marked += int(marked)
        total_listened += int(listened)

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

    # Trimming's saving, attributed to listening rather than to files.
    #
    # Silence removed from an episode sitting on disk has saved nobody anything. What was
    # saved is the silence you would have sat through in the part you actually heard -- so
    # each episode contributes in proportion to how much of it was played. Listening is
    # measured against the trimmed file, which is what gets served, so the ratio between the
    # two durations converts it back into time that would have been spent.
    trimmed_rows = (
        await session.execute(
            select(
                EpisodeState.listened_seconds,
                Episode.source_duration_seconds,
                Episode.processed_duration_seconds,
            )
            .join(Episode, Episode.id == EpisodeState.episode_id)
            .where(EpisodeState.user_id == user.id)
            .where(EpisodeState.listened_seconds > 0)
            .where(Episode.processed_duration_seconds.is_not(None))
            .where(Episode.source_duration_seconds.is_not(None))
        )
    ).all()

    saved_by_trimming = 0.0
    for listened, source_duration, processed_duration in trimmed_rows:
        if not processed_duration or processed_duration <= 0:
            continue
        removed = float(source_duration) - float(processed_duration)
        if removed <= 0:
            continue
        # Never credit more than the episode contained, however much was replayed.
        share = min(float(listened) / float(processed_duration), 1.0)
        saved_by_trimming += removed * share

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
        episodes_marked_played=total_marked,
        episodes_listened=total_listened,
        seconds_listened=total_seconds,
        seconds_saved_by_speed=int(saved),
        seconds_saved_by_trimming=int(saved_by_trimming),
        episodes_processed=int(processed),
        in_progress=int(in_progress),
        bookmarks=int(bookmarks),
        shows=shows,
    )
