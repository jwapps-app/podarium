"""Chapters, fetched by the server like everything else.

The chapters file sits on the publisher's host, so handing a client its URL would leak
exactly the request the design exists to prevent -- and would leak it on every episode
opened, which is a good deal worse than the artwork case it resembles.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from podarium.auth import current_user
from podarium.chapters import RETRY_FAILED_AFTER, parse_chapters
from podarium.clients.feedfetch import parse_feed_bytes
from podarium.main import app
from podarium.models import Episode, Feed

CHAPTERS_URL = "https://publisher.example/ep1/chapters.json"

BODY = """{
  "version": "1.2.0",
  "chapters": [
    {"startTime": 0, "title": "Cold open"},
    {"startTime": 95.5, "title": "Sponsor", "img": "https://publisher.example/ad.png"},
    {"startTime": 300, "title": "Interview"}
  ]
}"""


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def episode(session):
    feed = Feed(feed_url="https://example.com/feed.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    row = Episode(feed_id=feed.id, guid="ep1", title="Episode", chapters_url=CHAPTERS_URL)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def test_chapters_are_read_from_the_feed():
    """podcast:chapters has no feedparser mapping, so the element arrives generically."""
    xml = b"""<?xml version="1.0"?>
    <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
      <channel><title>Show</title>
        <item>
          <guid>ep1</guid><title>Episode</title>
          <podcast:chapters url="https://publisher.example/c.json" type="application/json+chapters"/>
        </item>
      </channel>
    </rss>"""

    parsed = parse_feed_bytes(xml)

    assert parsed.episodes[0].chapters_url == "https://publisher.example/c.json"


def test_only_the_rendered_fields_survive_parsing():
    """A chapter image would be a publisher URL smuggled into a client response."""
    chapters = parse_chapters(BODY)

    assert [(c.start_seconds, c.title) for c in chapters] == [
        (0.0, "Cold open"),
        (95.5, "Sponsor"),
        (300.0, "Interview"),
    ]


def test_malformed_json_yields_no_chapters_rather_than_an_error():
    """A broken file is the publisher's problem; it must not break the episode."""
    assert parse_chapters("not json at all") == []
    assert parse_chapters('{"chapters": "nonsense"}') == []


def test_chapters_hidden_from_the_table_of_contents_are_kept_and_flagged():
    """toc:false is a publisher saying "do not list this", which in practice is almost
    always an ad break. Dropping it lost the one thing worth skipping -- and skipping needs
    to know where the break ends, which only the next chapter's start tells you."""
    chapters = parse_chapters(
        '{"chapters": [{"startTime": 0, "title": "Keep"},'
        ' {"startTime": 5, "title": "Hide", "toc": false}]}'
    )

    assert [c.title for c in chapters] == ["Keep", "Hide"]
    assert [c.sponsor for c in chapters] == [False, True]


def test_a_chapter_that_says_it_is_an_ad_is_flagged_too():
    """Plenty of shows label the break and leave it in the table of contents."""
    chapters = parse_chapters(
        '{"chapters": [{"startTime": 0, "title": "Interview"},'
        ' {"startTime": 5, "title": "Sponsor: Acme"},'
        ' {"startTime": 9, "title": "Ad break"}]}'
    )

    assert [c.sponsor for c in chapters] == [False, True, True]


def test_ordinary_words_containing_ad_are_not_treated_as_ads():
    """Matched on word boundaries, not substrings. "ads" inside "threads" or "downloads"
    would otherwise skip real content, and skipping something you wanted to hear is a far
    worse failure than sitting through an advert."""
    titles = ["Reading the threads", "Back roads of Kansas", "Downloads and mirrors"]
    document = '{"chapters": [' + ", ".join(
        f'{{"startTime": {i}, "title": "{t}"}}' for i, t in enumerate(titles)
    ) + "]}"

    assert [c.sponsor for c in parse_chapters(document)] == [False, False, False]


def test_chapters_without_a_start_time_are_skipped():
    """startTime is the only required field; without it there is nothing to seek to."""
    assert parse_chapters('{"chapters": [{"title": "Nowhere"}]}') == []


@respx.mock
async def test_fetched_once_and_served_from_the_cache(client, episode, session):
    route = respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(200, text=BODY))

    first = (await client.get(f"/api/episodes/{episode.id}/chapters")).json()
    second = (await client.get(f"/api/episodes/{episode.id}/chapters")).json()

    assert route.call_count == 1
    assert len(first["chapters"]) == 3
    assert second == first


@respx.mock
async def test_an_episode_without_chapters_is_an_empty_list_not_an_error(client, session):
    feed = Feed(feed_url="https://example.com/b.xml", title="No chapters")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    row = Episode(feed_id=feed.id, guid="plain", title="Plain")
    session.add(row)
    await session.commit()
    await session.refresh(row)

    response = await client.get(f"/api/episodes/{row.id}/chapters")

    assert response.status_code == 200
    assert response.json() == {"chapters": []}


@respx.mock
async def test_a_failed_fetch_backs_off_rather_than_retrying_every_open(
    client, episode, session
):
    """A 404 here is usually permanent, and every episode open would otherwise re-ask."""
    route = respx.get(CHAPTERS_URL).mock(return_value=httpx.Response(404))

    await client.get(f"/api/episodes/{episode.id}/chapters")
    await client.get(f"/api/episodes/{episode.id}/chapters")

    assert route.call_count == 1

    # ...but it is a backoff, not a tombstone: publishers do fix these.
    await session.refresh(episode)
    episode.chapters_fetched_at = datetime.now(UTC) - RETRY_FAILED_AFTER - timedelta(minutes=1)
    await session.commit()

    await client.get(f"/api/episodes/{episode.id}/chapters")
    assert route.call_count == 2
