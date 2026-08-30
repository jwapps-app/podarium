import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import current_user
from podarium.clients import podcastindex
from podarium.clients.feedfetch import fetch_feed
from podarium.clients.podcastindex import PodcastIndexUnavailable
from podarium.db import get_session
from podarium.jobs.artwork import register_artwork
from podarium.models import Feed, User
from podarium.schemas import PreviewEpisodeOut, PreviewOut, SearchResultOut
from podarium.services import get_app_settings
from podarium.urls import normalize_feed_url

router = APIRouter(prefix="/api/search", tags=["search"])


async def _subscribed_index(session: AsyncSession) -> tuple[set[str], set[int]]:
    """Normalised URLs and Podcast Index ids for everything already subscribed.

    Both are needed: Podcast Index reports a show's canonical URL, which is often not the
    hosting-platform URL a user originally subscribed with, so comparing raw strings marks
    shows you already have as available to add.
    """
    rows = (
        await session.execute(
            select(Feed.feed_url, Feed.resolved_url, Feed.podcast_index_id)
        )
    ).all()
    urls = {normalize_feed_url(url) for url, _, _ in rows}
    urls |= {normalize_feed_url(resolved) for _, resolved, _ in rows if resolved}
    ids = {index_id for _, _, index_id in rows if index_id is not None}
    return urls, ids


def _is_subscribed(
    result_url: str, result_index_id: int | None, urls: set[str], ids: set[int]
) -> bool:
    if result_index_id is not None and result_index_id in ids:
        return True
    return normalize_feed_url(result_url) in urls


def _require_feed(result) -> None:
    """Reject a document that parsed but is not a feed.

    feedparser is deliberately lenient -- it will accept a web page and hand back an empty
    feed rather than an error -- so `parsed is not None` is not the same question as "is
    this a feed". Pasting a show's home page instead of its RSS is the common mistake, and
    it would otherwise succeed into an untitled result with no episodes.

    Title *and* episodes both absent, because either alone is legitimate: a brand new show
    has a title and nothing published yet.
    """
    if result.parsed is None or (not result.parsed.title and not result.parsed.episodes):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That URL did not return a podcast feed. It may be the show's web page "
            "rather than its RSS.",
        )


async def _proxied_image(session: AsyncSession, image_url: str | None) -> str | None:
    """Rewrite a publisher image URL to one this server will serve.

    Search results are the last place a publisher URL could reach a client. Returning them
    raw would have the browser fetch from every result's CDN, which is exactly the traffic
    this server exists to absorb.
    """
    if not image_url:
        return None
    return f"/api/images/cache/{await register_artwork(session, image_url)}"


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

    urls, ids = await _subscribed_index(session)
    return [
        SearchResultOut(
            podcast_index_id=r.podcast_index_id,
            title=r.title,
            author=r.author,
            description=r.description,
            feed_url=r.feed_url,
            image_url=await _proxied_image(session, r.image_url),
            episode_count=r.episode_count,
            already_subscribed=_is_subscribed(r.feed_url, r.podcast_index_id, urls, ids),
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
    urls, ids = await _subscribed_index(session)

    try:
        found = await podcastindex.podcast_by_feed_url(url, user_agent=app_settings.user_agent)
        if found:
            return SearchResultOut(
                podcast_index_id=found.podcast_index_id,
                title=found.title,
                author=found.author,
                description=found.description,
                feed_url=found.feed_url,
                image_url=await _proxied_image(session, found.image_url),
                episode_count=found.episode_count,
                already_subscribed=_is_subscribed(
                    found.feed_url, found.podcast_index_id, urls, ids
                ),
            )
    except (PodcastIndexUnavailable, httpx.HTTPError):
        pass

    try:
        result = await fetch_feed(url, user_agent=app_settings.user_agent)
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Could not fetch feed: {exc}") from exc

    _require_feed(result)

    return SearchResultOut(
        podcast_index_id=None,
        title=result.parsed.title,
        author=result.parsed.author,
        description=result.parsed.description,
        feed_url=result.final_url or url,
        image_url=await _proxied_image(session, result.parsed.image_url),
        episode_count=len(result.parsed.episodes),
        already_subscribed=_is_subscribed(result.final_url or url, None, urls, ids),
    )


# How many episodes a preview carries. Enough to judge cadence and subject matter; not so
# many that looking at a show with a decade of archive downloads a decade of archive.
PREVIEW_EPISODES = 20


#: A BCP 47 tag as far as this needs to care: letters, then optional subtags.
_LANG_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$")

#: Enough for a device that lists a regional tag, its base, and a second language.
MAX_LANGUAGES = 6


def parse_languages(raw: str | None) -> list[str]:
    """Turn a client's language list into tags safe to forward.

    This is user-controlled input that ends up in a request to another service, so it is
    matched against a shape rather than trimmed and hoped for. Anything unrecognisable is
    dropped rather than rejected: a device offering one odd tag among several should still
    get the others, and the worst case is trending unfiltered, which is where it started.

    Regional tags bring their base along -- "en-US" alone would drop every feed that
    declares plain "en", which is most of them.
    """
    if not raw:
        return []

    tags: list[str] = []
    for candidate in raw.split(","):
        tag = candidate.strip()
        if not _LANG_TAG.match(tag):
            continue
        for form in (tag, tag.split("-")[0]):
            if form.lower() not in {t.lower() for t in tags}:
                tags.append(form)
    return tags[:MAX_LANGUAGES]


@router.get("/trending", response_model=list[SearchResultOut])
async def trending(
    category: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    lang: str | None = Query(
        default=None,
        max_length=200,
        description="Comma-separated BCP 47 tags, usually the browser's own list.",
    ),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SearchResultOut]:
    """Shows worth a look, with no search term.

    Search only finds what you can already name. This is the other half of discovery, and
    it goes through Podcast Index like everything else here -- Apple is never consulted.

    Trending is global unless told otherwise, and globally most podcasts are not in your
    language: the unfiltered list comes back a third Russian, German and French. The client
    sends the languages its device actually reads, which is a better answer than a fixed
    country because it follows whoever is holding the phone.
    """
    app_settings = await get_app_settings(session)
    try:
        results = await podcastindex.trending(
            user_agent=app_settings.user_agent,
            limit=limit,
            category=category,
            languages=parse_languages(lang),
        )
    except PodcastIndexUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    urls, ids = await _subscribed_index(session)
    return [
        SearchResultOut(
            podcast_index_id=result.podcast_index_id,
            title=result.title,
            author=result.author,
            description=result.description,
            feed_url=result.feed_url,
            image_url=await _proxied_image(session, result.image_url),
            episode_count=result.episode_count,
            already_subscribed=_is_subscribed(
                result.feed_url, result.podcast_index_id, urls, ids
            ),
        )
        for result in results
    ]


@router.get("/categories", response_model=list[str])
async def list_categories(
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    app_settings = await get_app_settings(session)
    try:
        return await podcastindex.categories(user_agent=app_settings.user_agent)
    except PodcastIndexUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/preview", response_model=PreviewOut)
async def preview(
    url: str = Query(..., description="Feed URL to look at without subscribing"),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PreviewOut:
    """Look at a show before deciding to subscribe.

    Nothing is written: no feed row, no episodes, no state. The feed is fetched and parsed
    in memory and thrown away, so browsing a dozen shows leaves the library exactly as it
    was -- which is the difference between this and subscribing then unsubscribing, since
    the latter leaves a tombstone behind by design.

    The fetch is the server's, as ever. The only thing that persists is the artwork cache
    entry, because the alternative is handing the client the publisher's image URL.
    """
    app_settings = await get_app_settings(session)

    try:
        result = await fetch_feed(url, user_agent=app_settings.user_agent)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Could not fetch feed: {exc}"
        ) from exc

    _require_feed(result)

    urls, ids = await _subscribed_index(session)
    parsed = result.parsed
    final_url = result.final_url or url

    # Newest first. A feed is conventionally in that order already, but conventionally is
    # not always, and a preview that opens on a 2011 episode reads as a dead show.
    episodes = sorted(
        parsed.episodes,
        key=lambda episode: (episode.published_at is not None, episode.published_at),
        reverse=True,
    )

    return PreviewOut(
        title=parsed.title,
        author=parsed.author,
        description=parsed.description,
        feed_url=final_url,
        image_url=await _proxied_image(session, parsed.image_url),
        link=parsed.link,
        episode_count=len(parsed.episodes),
        already_subscribed=_is_subscribed(final_url, None, urls, ids),
        episodes=[
            PreviewEpisodeOut(
                guid=episode.guid,
                title=episode.title,
                published_at=episode.published_at,
                duration_seconds=episode.duration_seconds,
                description_html=episode.description_html,
            )
            for episode in episodes[:PREVIEW_EPISODES]
        ],
    )
