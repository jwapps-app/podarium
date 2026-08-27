"""Looking at a show before subscribing to it.

The point is that nothing is written. Subscribing and then unsubscribing to have a look
leaves a tombstone behind -- deliberately, so other clients learn the feed is gone -- which
makes it a poor way to browse. A preview reads the feed and throws it away.
"""

import httpx
import pytest
import respx

from podarium.auth import current_user
from podarium.main import app
from podarium.models import DeletedFeed, Episode, Feed

FEED_URL = "https://publisher.example/show.xml"

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>A Show</title>
    <itunes:author>Someone</itunes:author>
    <description>What it is about.</description>
    <link>https://publisher.example</link>
    <itunes:image href="https://publisher.example/art.jpg"/>
    <item>
      <guid>old</guid><title>Older episode</title>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <itunes:duration>1800</itunes:duration>
      <enclosure url="https://publisher.example/old.mp3" type="audio/mpeg" length="100"/>
    </item>
    <item>
      <guid>new</guid><title>Newer episode</title>
      <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
      <itunes:duration>3600</itunes:duration>
      <enclosure url="https://publisher.example/new.mp3" type="audio/mpeg" length="200"/>
    </item>
  </channel>
</rss>"""


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def preview(client, url: str = FEED_URL) -> httpx.Response:
    return await client.get("/api/search/preview", params={"url": url})


@respx.mock
async def test_returns_the_show_and_its_recent_episodes(client):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    body = (await preview(client)).json()

    assert body["title"] == "A Show"
    assert body["author"] == "Someone"
    assert body["episode_count"] == 2
    assert [e["title"] for e in body["episodes"]] == ["Newer episode", "Older episode"]


@respx.mock
async def test_newest_first_whatever_order_the_feed_is_in(client):
    """A feed is conventionally newest-first, and conventionally is not always. A preview
    opening on a 2024 episode reads as a dead show."""
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    body = (await preview(client)).json()

    assert body["episodes"][0]["title"] == "Newer episode"


@respx.mock
async def test_nothing_is_written(client, session):
    """The whole point: browsing a dozen shows leaves the library exactly as it was."""
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    await preview(client)

    assert (await session.execute(Feed.__table__.select())).all() == []
    assert (await session.execute(Episode.__table__.select())).all() == []
    assert (await session.execute(DeletedFeed.__table__.select())).all() == []


@respx.mock
async def test_no_publisher_url_reaches_the_client_except_the_feed_itself(client):
    """Artwork is proxied and enclosures are omitted outright. A preview that leaked
    enclosure URLs would have the browser fetching from a publisher for a show this server
    has not even subscribed to."""
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    body = (await preview(client)).json()

    assert body["image_url"].startswith("/api/images/cache/")
    assert "art.jpg" not in str(body)
    assert ".mp3" not in str(body)
    # feed_url is the exception, and has to be: it is what the client posts back to
    # subscribe. It is data the client sends us, not something it fetches.
    assert body["feed_url"] == FEED_URL


@respx.mock
async def test_a_show_already_subscribed_says_so(client, session):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))
    session.add(Feed(feed_url=FEED_URL, title="A Show"))
    await session.commit()

    assert (await preview(client)).json()["already_subscribed"] is True


@respx.mock
async def test_an_unreachable_feed_is_a_clean_404(client):
    respx.get(FEED_URL).mock(side_effect=httpx.ConnectError("no route"))

    response = await preview(client)

    assert response.status_code == 404
    assert "Could not fetch" in response.json()["error"]["message"]


@respx.mock
async def test_a_page_that_is_not_a_feed_is_rejected(client):
    """Pasting a show's web page rather than its feed is the common mistake."""
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text="<html>Not a feed</html>"))

    assert (await preview(client)).status_code == 422
