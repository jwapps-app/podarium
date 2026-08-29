from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.clients import podcastindex
from podarium.clients.podcastindex import PodcastIndexUnavailable
from podarium.db import get_session
from podarium.jobs.refresh import apply_auto_download_window, refresh_feed
from podarium.jobs.retention import purge_episode
from podarium.models import DeletedFeed, Episode, EpisodeState, Feed, FeedState, User
from podarium.schemas import FeedCreateRequest, FeedOut, FeedUpdateRequest, feed_out
from podarium.services import (
    drop_from_queue,
    feed_counts,
    get_app_settings,
    mark_all_feeds_seen,
    mark_feed_seen,
)
from podarium.subscribe import subscribe_feed

router = APIRouter(prefix="/api/feeds", tags=["feeds"])




async def _feed_out(session: AsyncSession, feed: Feed, user_id: int) -> FeedOut:
    app_settings = await get_app_settings(session)
    total, unplayed, new = (await feed_counts(session, user_id, [feed.id])).get(feed.id, (0, 0, 0))
    return feed_out(
        feed,
        episode_count=total,
        unplayed_count=unplayed,
        new_episode_count=new,
        global_auto_download_count=app_settings.global_auto_download_count,
        global_playback_rate=app_settings.default_playback_rate,
        global_trim_silence=app_settings.global_trim_silence,
        global_normalize_audio=app_settings.global_normalize_audio,
        global_skip_sponsor_chapters=app_settings.global_skip_sponsor_chapters,
    )


@router.get("", response_model=list[FeedOut])
async def list_feeds(
    active: bool | None = Query(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[FeedOut]:
    statement = select(Feed).order_by(func.lower(func.coalesce(Feed.title, Feed.feed_url)))
    if active is not None:
        statement = statement.where(Feed.active.is_(active))
    feeds = (await session.execute(statement)).scalars().all()
    counts = await feed_counts(session, user.id, [f.id for f in feeds])
    app_settings = await get_app_settings(session)
    return [
        feed_out(
            f,
            episode_count=counts.get(f.id, (0, 0, 0))[0],
            unplayed_count=counts.get(f.id, (0, 0, 0))[1],
            new_episode_count=counts.get(f.id, (0, 0, 0))[2],
            global_auto_download_count=app_settings.global_auto_download_count,
            global_playback_rate=app_settings.default_playback_rate,
        global_trim_silence=app_settings.global_trim_silence,
        global_normalize_audio=app_settings.global_normalize_audio,
        global_skip_sponsor_chapters=app_settings.global_skip_sponsor_chapters,
        )
        for f in feeds
    ]


@router.post("", response_model=FeedOut, status_code=status.HTTP_201_CREATED)
async def create_feed(
    body: FeedCreateRequest,
    response: Response,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> FeedOut:
    app_settings = await get_app_settings(session)

    feed_url = body.feed_url
    podcast_index_id = body.podcast_index_id

    if not feed_url and podcast_index_id:
        try:
            found = await podcastindex.podcast_by_feed_id(
                podcast_index_id, user_agent=app_settings.user_agent
            )
        except PodcastIndexUnavailable as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Feed not found in Podcast Index")
        feed_url = found.feed_url

    if not feed_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide either feed_url or podcast_index_id",
        )

    feed, created = await subscribe_feed(
        session, feed_url, user_agent=app_settings.user_agent, podcast_index_id=podcast_index_id
    )
    if created:
        # The back catalogue arrives with the subscription; none of it is new to you.
        await mark_feed_seen(session, user.id, feed.id)
        await session.commit()
    else:
        response.status_code = status.HTTP_200_OK
    return await _feed_out(session, feed, user.id)


async def _get_feed_or_404(session: AsyncSession, feed_id: int) -> Feed:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Feed not found")
    return feed


@router.get("/{feed_id}", response_model=FeedOut)
async def get_feed(
    feed_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> FeedOut:
    feed = await _get_feed_or_404(session, feed_id)
    return await _feed_out(session, feed, user.id)


@router.patch("/{feed_id}", response_model=FeedOut)
async def update_feed(
    feed_id: int,
    body: FeedUpdateRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> FeedOut:
    feed = await _get_feed_or_404(session, feed_id)

    # NULL means "inherit the global" here too, so clearing needs its own flag.
    if body.clear_auto_download_count:
        feed.auto_download_count = None
    elif body.auto_download_count is not None:
        feed.auto_download_count = body.auto_download_count

    if body.active is not None:
        feed.active = body.active

    if body.notify is not None:
        feed.notify = body.notify

    if body.intro_skip_seconds is not None:
        feed.intro_skip_seconds = body.intro_skip_seconds
    if body.outro_skip_seconds is not None:
        feed.outro_skip_seconds = body.outro_skip_seconds

    # The same inherit-or-override dance the settings above use.
    for clear, value, attribute in (
        (body.clear_trim_silence, body.trim_silence, "trim_silence"),
        (body.clear_normalize_audio, body.normalize_audio, "normalize_audio"),
        (body.clear_skip_sponsor_chapters, body.skip_sponsor_chapters, "skip_sponsor_chapters"),
    ):
        if clear:
            setattr(feed, attribute, None)
        elif value is not None:
            setattr(feed, attribute, value)

    # NULL means "inherit the global", so clearing needs an explicit flag rather than
    # being inferred from an omitted field.
    if body.clear_retention_mode:
        feed.retention_mode = None
    elif body.retention_mode is not None:
        feed.retention_mode = body.retention_mode

    if body.clear_retention_days:
        feed.retention_days = None
    elif body.retention_days is not None:
        feed.retention_days = body.retention_days

    if body.clear_playback_rate:
        feed.playback_rate = None
    elif body.playback_rate is not None:
        feed.playback_rate = body.playback_rate

    await session.commit()

    # Turning auto-download on has to do something now. It is otherwise only acted on
    # during a refresh, so a feed that was just fetched would sit there for up to a full
    # refresh interval with the setting saved and nothing on disk -- indistinguishable,
    # from the outside, from the setting not working.
    touched_auto_download = (
        body.auto_download_count is not None or body.clear_auto_download_count
    )
    if touched_auto_download:
        await apply_auto_download_window(session, feed)
        await session.commit()

    await session.refresh(feed)
    return await _feed_out(session, feed, user.id)


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed(
    feed_id: int,
    purge: bool = Query(default=False),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Unsubscribe. ``?purge=true`` also deletes the downloaded audio.

    This is the one path that does delete episode rows, and it is explicit and
    user-initiated -- unlike retention, which never does.
    """
    feed = await _get_feed_or_404(session, feed_id)

    if purge:
        episodes = (
            await session.execute(
                select(Episode).where(Episode.feed_id == feed.id).where(Episode.local_path.is_not(None))
            )
        ).scalars().all()
        for episode in episodes:
            await purge_episode(session, episode, reason="manual")

    # Leave a tombstone before the row goes, or a synced client has no way to learn this
    # happened -- the feed just stops appearing in deltas, which looks like "unchanged".
    #
    # Refresh an existing one rather than inserting blindly. Postgres does not reuse ids in
    # normal operation, but a restore or a reset sequence can, and a delete that 500s on a
    # primary key collision would be a baffling way to find that out.
    tombstone = await session.get(DeletedFeed, feed.id)
    if tombstone is None:
        session.add(DeletedFeed(feed_id=feed.id, feed_url=feed.feed_url))
    else:
        tombstone.feed_url = feed.feed_url
        tombstone.deleted_at = datetime.now(UTC)
    await session.delete(feed)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/seen", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_seen(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Clear every show's new-episode count at once. The inbox calls this when opened.

    Declared before the /{feed_id}/... routes so "seen" is never read as a feed id.

    This necessarily zeroes the library tiles too: the nav badge is the sum of them, so
    there is no clearing one without the other. As with the per-show version, nothing is
    marked played -- seen and played are different claims.
    """
    await mark_all_feeds_seen(session, user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{feed_id}/seen", response_model=FeedOut)
async def mark_seen(
    feed_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> FeedOut:
    """Clear the show's new-episode count.

    Opening the show's page is what calls this. Playing an episode already removes that one
    from the count, but a show you dip into rather than follow needs a way to say "I have
    looked, I am not taking the rest" without marking anything played.
    """
    feed = await _get_feed_or_404(session, feed_id)
    await mark_feed_seen(session, user.id, feed.id)
    await session.commit()
    return await _feed_out(session, feed, user.id)


@router.post("/{feed_id}/played", response_model=FeedOut)
async def mark_all_played(
    feed_id: int,
    played: bool = Query(default=True),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> FeedOut:
    """Mark every episode of a show played, or unplayed.

    The case this exists for is subscribing to a show with a long archive: a 2,700-episode
    back catalogue is not 2,700 obligations, and without this the only way to clear it is
    one episode at a time.

    Deliberately does not touch position_seconds. Marking played is a statement about the
    backlog, and wiping the position of the one episode you were halfway through would be a
    surprising thing for a bulk action to do -- the resume list already ignores anything
    played, so it drops out either way and the position is still there if you come back.
    """
    feed = await _get_feed_or_404(session, feed_id)

    episode_ids = (
        await session.execute(select(Episode.id).where(Episode.feed_id == feed.id))
    ).scalars().all()

    existing: dict[int, EpisodeState] = {}
    if episode_ids:
        rows = (
            await session.execute(
                select(EpisodeState)
                .where(EpisodeState.user_id == user.id)
                .where(EpisodeState.episode_id.in_(episode_ids))
            )
        ).scalars()
        existing = {state.episode_id: state for state in rows}

    now = datetime.now(UTC)
    changed: list[int] = []
    for episode_id in episode_ids:
        state = existing.get(episode_id)
        if state is None:
            if not played:
                # Nothing recorded is already not played; writing a row would say nothing.
                continue
            state = EpisodeState(user_id=user.id, episode_id=episode_id)
            session.add(state)
        if state.played == played:
            # Already in the wanted state, so nothing to write -- but if it is played and
            # still in the queue, it should not be. Cheap to include, and it means the
            # bulk action clears the queue of a show's backlog completely.
            if played:
                changed.append(episode_id)
            continue
        # completed_at is what after_played retention measures from, so it moves with the
        # flag here exactly as it does for a single episode.
        state.completed_at = now if played else None
        state.played = played
        changed.append(episode_id)

    # Clearing a show's backlog should clear it out of the queue too, on the same
    # reasoning as a single episode: what is played is not what plays next.
    if played and changed:
        await drop_from_queue(session, user.id, changed)

    # Marking the backlog played is also a way of saying you have looked at the show.
    await mark_feed_seen(session, user.id, feed.id)
    await session.commit()
    return await _feed_out(session, feed, user.id)


@router.post("/{feed_id}/refresh", response_model=FeedOut)
async def force_refresh(
    feed_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> FeedOut:
    feed = await _get_feed_or_404(session, feed_id)
    app_settings = await get_app_settings(session)
    # A manual refresh should not be blocked by a backoff the user is trying to escape.
    feed.fetch_error_count = 0
    await refresh_feed(session, feed, user_agent=app_settings.user_agent)
    await session.refresh(feed)
    return await _feed_out(session, feed, user.id)
