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
from podarium.models import Episode, EpisodeState, Feed, FeedState, User
from podarium.schemas import FeedCreateRequest, FeedOut, FeedUpdateRequest, feed_out
from podarium.services import get_app_settings, mark_all_feeds_seen, mark_feed_seen
from podarium.subscribe import subscribe_feed

router = APIRouter(prefix="/api/feeds", tags=["feeds"])


async def _counts(
    session: AsyncSession, user_id: int, feed_ids: list[int]
) -> dict[int, tuple[int, int, int]]:
    """Per-feed (total, unplayed, new) counts.

    "New" means first seen since the show was last looked at, and not already played.
    Falling back to the feed's created_at keeps a subscription made before feed_state
    existed from reporting its whole back catalogue as new.
    """
    if not feed_ids:
        return {}

    seen_at = func.coalesce(FeedState.last_seen_at, Feed.created_at)

    rows = (
        await session.execute(
            select(
                Episode.feed_id,
                func.count(Episode.id),
                func.count(Episode.id).filter(
                    func.coalesce(EpisodeState.played, False).is_(False)
                ),
                func.count(Episode.id).filter(
                    (Episode.first_seen_at > seen_at)
                    & func.coalesce(EpisodeState.played, False).is_(False)
                ),
            )
            .select_from(Episode)
            .join(Feed, Feed.id == Episode.feed_id)
            .outerjoin(
                EpisodeState,
                (EpisodeState.episode_id == Episode.id) & (EpisodeState.user_id == user_id),
            )
            .outerjoin(
                FeedState,
                (FeedState.feed_id == Episode.feed_id) & (FeedState.user_id == user_id),
            )
            .where(Episode.feed_id.in_(feed_ids))
            .group_by(Episode.feed_id)
        )
    ).all()
    return {feed_id: (total, unplayed, new) for feed_id, total, unplayed, new in rows}


async def _feed_out(session: AsyncSession, feed: Feed, user_id: int) -> FeedOut:
    app_settings = await get_app_settings(session)
    total, unplayed, new = (await _counts(session, user_id, [feed.id])).get(feed.id, (0, 0, 0))
    return feed_out(
        feed,
        episode_count=total,
        unplayed_count=unplayed,
        new_episode_count=new,
        global_auto_download_count=app_settings.global_auto_download_count,
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
    counts = await _counts(session, user.id, [f.id for f in feeds])
    app_settings = await get_app_settings(session)
    return [
        feed_out(
            f,
            episode_count=counts.get(f.id, (0, 0, 0))[0],
            unplayed_count=counts.get(f.id, (0, 0, 0))[1],
            new_episode_count=counts.get(f.id, (0, 0, 0))[2],
            global_auto_download_count=app_settings.global_auto_download_count,
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
            status.HTTP_422_UNPROCESSABLE_ENTITY,
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
