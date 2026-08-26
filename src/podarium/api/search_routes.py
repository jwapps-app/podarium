import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.clients import podcastindex
from podarium.clients.feedfetch import fetch_feed
from podarium.clients.podcastindex import PodcastIndexUnavailable
from podarium.db import get_session
from podarium.models import Feed, User
from podarium.schemas import SearchResultOut
from podarium.services import get_app_settings

router = APIRouter(prefix="/api/search", tags=["search"])


async def _subscribed_urls(session: AsyncSession) -> set[str]:
    return set((await session.execute(select(Feed.feed_url))).scalars().all())


def _unavailable(exc: PodcastIndexUnavailable) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.get("", response_model=list[SearchResultOut])
async def search(
    q: str = Query(min_length=1),
    limit: int = Query(default=40, ge=1, le=100),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SearchResultOut]:
    app_settings = await get_app_settings(session)
    try:
        results = await podcastindex.search_by_term(q, user_agent=app_settings.user_agent, limit=limit)
    except PodcastIndexUnavailable as exc:
        raise _unavailable(exc) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Podcast Index request failed: {exc}") from exc

    subscribed = await _subscribed_urls(session)
    return [
        SearchResultOut(
            podcast_index_id=r.podcast_index_id,
            title=r.title,
            author=r.author,
            description=r.description,
            feed_url=r.feed_url,
            image_url=r.image_url,
            episode_count=r.episode_count,
            already_subscribed=r.feed_url in subscribed,
        )
        for r in results
    ]


@router.get("/byfeedurl", response_model=SearchResultOut)
async def search_by_feed_url(
    url: str = Query(min_length=1),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> SearchResultOut:
    """Resolve an arbitrary feed URL.

    Podcast Index is tried first because it carries the richer metadata, but a feed it has
    never indexed still resolves: we fall back to fetching and parsing the RSS ourselves.
    That keeps 'paste a private feed URL' working with no credentials at all.
    """
    app_settings = await get_app_settings(session)
    subscribed = await _subscribed_urls(session)

    try:
        found = await podcastindex.podcast_by_feed_url(url, user_agent=app_settings.user_agent)
        if found:
            return SearchResultOut(
                podcast_index_id=found.podcast_index_id,
                title=found.title,
                author=found.author,
                description=found.description,
                feed_url=found.feed_url,
                image_url=found.image_url,
                episode_count=found.episode_count,
                already_subscribed=found.feed_url in subscribed,
            )
    except (PodcastIndexUnavailable, httpx.HTTPError):
        pass

    try:
        result = await fetch_feed(url, user_agent=app_settings.user_agent)
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Could not fetch feed: {exc}") from exc

    if result.parsed is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="URL is not a parseable feed")

    return SearchResultOut(
        podcast_index_id=None,
        title=result.parsed.title,
        author=result.parsed.author,
        description=result.parsed.description,
        feed_url=result.final_url or url,
        image_url=result.parsed.image_url,
        episode_count=len(result.parsed.episodes),
        already_subscribed=(result.final_url or url) in subscribed,
    )
