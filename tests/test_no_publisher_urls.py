"""The invariant from spec 10: no client response may carry a publisher URL.

If a feed or episode serializer ever exposes ``enclosure_url`` or a raw ``image_url``, a
client could fetch it directly and the whole point of the server -- one IP address, the
server's -- is lost.
"""

from datetime import UTC, datetime

from podarium.models import Episode, Feed
from podarium.schemas import episode_out, feed_out

# These rows are built in memory, so the server-side timestamp defaults have not fired.
NOW = datetime.now(UTC)
STAMPS = {"created_at": NOW, "updated_at": NOW}
EPISODE_STAMPS = {"first_seen_at": NOW, "updated_at": NOW}

PUBLISHER_IMAGE = "https://cdn.publisher.example/art.jpg"
PUBLISHER_AUDIO = "https://cdn.publisher.example/ep-1.mp3"


def test_feed_output_rewrites_artwork_to_a_local_path():
    feed = Feed(
        id=7,
        feed_url="https://example.com/feed.xml",
        image_url=PUBLISHER_IMAGE,
        explicit=False,
        auto_download_count=0,
        fetch_error_count=0,
        active=True,
        # Set explicitly, like active above: column defaults are applied on flush, and this
        # feed is never flushed.
        notify=True,
        intro_skip_seconds=0,
        outro_skip_seconds=0,
        **STAMPS,
    )
    payload = feed_out(feed).model_dump()

    assert payload["image_url"] == "/api/images/feed/7"
    assert PUBLISHER_IMAGE not in str(payload)


def test_episode_output_hides_the_enclosure_and_rewrites_artwork():
    episode = Episode(
        id=3,
        feed_id=7,
        guid="ep-1",
        image_url=PUBLISHER_IMAGE,
        enclosure_url=PUBLISHER_AUDIO,
        enclosure_type="audio/mpeg",
        explicit=False,
        **EPISODE_STAMPS,
    )
    payload = episode_out(episode).model_dump()

    assert "enclosure_url" not in payload
    assert PUBLISHER_AUDIO not in str(payload)
    assert PUBLISHER_IMAGE not in str(payload)
    assert payload["image_url"] == "/api/images/episode/3"
    assert payload["stream_url"] == "/api/stream/3"


def test_episode_without_its_own_art_falls_back_to_the_feed_image_endpoint():
    episode = Episode(id=3, feed_id=7, guid="ep-1", explicit=False, **EPISODE_STAMPS)
    assert episode_out(episode).image_url == "/api/images/feed/7"


# --- search results ---------------------------------------------------------
#
# Search is the last place a publisher URL could reach a client, and the worst one: you
# scroll past dozens of shows you never subscribe to, and returning their artwork URLs raw
# would hand your IP to every one of their CDNs.

import httpx
import pytest
import respx

from podarium.auth import current_user
from podarium.main import app
from podarium.models import ArtworkCache, User

PUBLISHER_SEARCH_ART = "https://cdn.publisher.example/search-cover.jpg"

PI_RESPONSE = {
    "feeds": [
        {
            "id": 12345,
            "title": "Some Show",
            "author": "Someone",
            "description": "A show",
            "url": "https://example.com/feed.xml",
            "artwork": PUBLISHER_SEARCH_ART,
            "episodeCount": 10,
        }
    ]
}


@pytest.fixture
async def search_client(session, monkeypatch):
    from podarium.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "podcastindex_key", "k", raising=False)
    monkeypatch.setattr(settings, "podcastindex_secret", "s", raising=False)

    app.dependency_overrides[current_user] = lambda: User(id=1, username="t", password_hash="x")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@respx.mock
async def test_search_results_never_carry_a_publisher_image_url(search_client, session):
    respx.get(url__startswith="https://api.podcastindex.org/api/1.0/search/byterm").mock(
        return_value=httpx.Response(200, json=PI_RESPONSE)
    )

    response = await search_client.get("/api/search", params={"q": "some show"})

    assert response.status_code == 200
    payload = response.json()
    assert PUBLISHER_SEARCH_ART not in str(payload)
    assert payload[0]["image_url"].startswith("/api/images/cache/")

    # The server recorded the source URL so it can fetch it on the client's behalf.
    from sqlalchemy import select

    cached = (await session.execute(select(ArtworkCache))).scalars().all()
    assert [entry.source_url for entry in cached] == [PUBLISHER_SEARCH_ART]


async def test_cache_endpoint_rejects_anything_it_did_not_mint(search_client):
    """The hash is server-minted, which is what stops this being an open proxy."""
    for bogus in ["../../etc/passwd", "not-a-hash", "z" * 64, "a" * 63]:
        response = await search_client.get(f"/api/images/cache/{bogus}")
        assert response.status_code == 404
