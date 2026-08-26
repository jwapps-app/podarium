"""Artwork is validated, not cached blind.

/api/images/feed/2 is a stable URL over changing content: a publisher can swap their cover
art at any time, and the same URL then has to start returning a different image. Caching it
for a day would leave the wrong artwork on screen with no way to correct it.
"""

from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select

from podarium.auth import current_user
from podarium.config import get_settings
from podarium.jobs.artwork import url_hash
from podarium.main import app
from podarium.models import ArtworkCache, Feed, User

IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
SOURCE = "https://cdn.publisher.example/cover.jpg"


@pytest.fixture
async def client(session):
    feed = Feed(feed_url="https://example.com/feed.xml", image_url=SOURCE)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    digest = url_hash(SOURCE)
    path = get_settings().artwork_dir / f"{digest}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(IMAGE)

    session.add(
        ArtworkCache(
            url_hash=digest,
            source_url=SOURCE,
            local_path=str(path),
            content_type="image/jpeg",
        )
    )
    await session.commit()

    app.dependency_overrides[current_user] = lambda: User(id=1, username="t", password_hash="x")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.feed_id = feed.id
        c.digest = digest
        yield c
    app.dependency_overrides.clear()


async def test_artwork_is_served_with_a_validating_etag(client):
    response = await client.get(f"/api/images/feed/{client.feed_id}")

    assert response.status_code == 200
    assert response.content == IMAGE
    assert response.headers["etag"] == f'"{client.digest}"'

    cache_control = response.headers["cache-control"]
    assert "must-revalidate" in cache_control
    # The endpoint is behind auth, so a shared proxy must not hold the response.
    assert "public" not in cache_control


async def test_matching_etag_returns_304(client):
    etag = f'"{client.digest}"'
    response = await client.get(
        f"/api/images/feed/{client.feed_id}", headers={"If-None-Match": etag}
    )

    assert response.status_code == 304
    assert response.content == b""


async def test_changed_artwork_produces_a_new_etag(session, client):
    """A publisher swapping their cover art must invalidate the old response."""
    original = (await client.get(f"/api/images/feed/{client.feed_id}")).headers["etag"]

    feed = await session.get(Feed, client.feed_id)
    feed.image_url = "https://cdn.publisher.example/new-cover.jpg"
    new_digest = url_hash(feed.image_url)
    path = get_settings().artwork_dir / f"{new_digest}.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0different")
    session.add(
        ArtworkCache(
            url_hash=new_digest,
            source_url=feed.image_url,
            local_path=str(path),
            content_type="image/jpeg",
        )
    )
    await session.commit()

    updated = await client.get(f"/api/images/feed/{client.feed_id}")
    assert updated.headers["etag"] != original

    # The browser's stale validator no longer matches, so it gets fresh bytes.
    revalidated = await client.get(
        f"/api/images/feed/{client.feed_id}", headers={"If-None-Match": original}
    )
    assert revalidated.status_code == 200
    assert revalidated.content == b"\xff\xd8\xff\xe0different"


# --- failed fetches ------------------------------------------------------------
#
# Some feeds carry artwork URLs that simply do not work. One in this library points at a
# bare directory and answers 403 every time, and without a pause every library page load
# fired another request at a host already refusing us.


@respx.mock
async def test_a_failed_fetch_is_not_retried_immediately(session):
    from podarium.jobs.artwork import ensure_artwork

    broken = "https://feeds.example.com/~images/"
    route = respx.get(broken).mock(return_value=httpx.Response(403))

    await ensure_artwork(session, broken, user_agent="test")
    await ensure_artwork(session, broken, user_agent="test")
    await ensure_artwork(session, broken, user_agent="test")

    assert route.call_count == 1, "one attempt, not one per page load"


@respx.mock
async def test_it_is_retried_once_the_pause_is_over(session):
    from datetime import timedelta

    from podarium.jobs.artwork import RETRY_FAILED_AFTER, ensure_artwork, url_hash

    broken = "https://feeds.example.com/~images/"
    route = respx.get(broken).mock(return_value=httpx.Response(403))
    await ensure_artwork(session, broken, user_agent="test")

    entry = (
        await session.execute(
            select(ArtworkCache).where(ArtworkCache.url_hash == url_hash(broken))
        )
    ).scalar_one()
    entry.fetched_at = datetime.now(UTC) - RETRY_FAILED_AFTER - timedelta(minutes=1)
    await session.commit()

    await ensure_artwork(session, broken, user_agent="test")

    assert route.call_count == 2, "publishers do fix these"


@respx.mock
async def test_a_recovered_image_is_stored(session):
    """The point of retrying at all."""
    from datetime import timedelta

    from podarium.jobs.artwork import RETRY_FAILED_AFTER, ensure_artwork, url_hash

    url = "https://feeds.example.com/art.jpg"
    route = respx.get(url)
    route.side_effect = [httpx.Response(403), httpx.Response(200, content=IMAGE)]

    await ensure_artwork(session, url, user_agent="test")
    entry = (
        await session.execute(
            select(ArtworkCache).where(ArtworkCache.url_hash == url_hash(url))
        )
    ).scalar_one()
    assert entry.local_path is None
    entry.fetched_at = datetime.now(UTC) - RETRY_FAILED_AFTER - timedelta(minutes=1)
    await session.commit()

    recovered = await ensure_artwork(session, url, user_agent="test")

    assert recovered.local_path is not None
    assert recovered.fetch_error is None
