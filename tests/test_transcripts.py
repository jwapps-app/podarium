"""Transcripts: fetched by the server, stripped to speech, and searchable.

The point is not displaying a transcript, it is searching one. A commercial app cannot do
this well because it does not hold your library; this server holds every episode in one
database, which turns "which episode was the bit about X" into a query.
"""

import httpx
import pytest
import respx

from podarium.auth import current_user
from podarium.clients.feedfetch import parse_feed_bytes
from podarium.main import app
from podarium.models import Episode, Feed
from podarium.transcripts import to_plain_text

URL = "https://publisher.example/ep1.vtt"

VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<v Host>Welcome to the show.

2
00:00:04.000 --> 00:00:07.500
Welcome to the show.
Today we discuss orbital mechanics.
"""


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def episode(session):
    feed = Feed(feed_url="https://example.com/f.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    row = Episode(feed_id=feed.id, guid="ep1", title="Episode One", transcript_url=URL)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


class TestStripping:
    def test_caption_scaffolding_is_removed(self):
        """Left in, every search would match timecodes and the stored text would be several
        times larger than the speech it contains."""
        text = to_plain_text(VTT)

        assert "WEBVTT" not in text
        assert "00:00:01" not in text
        assert "-->" not in text
        assert "<v Host>" not in text
        assert "Today we discuss orbital mechanics." in text

    def test_rolling_caption_repeats_are_collapsed(self):
        """Rolling captions repeat each line as it scrolls, which would otherwise triple the
        text and skew relevance."""
        assert to_plain_text(VTT).count("Welcome to the show.") == 1

    def test_an_empty_or_useless_file_yields_nothing(self):
        assert to_plain_text("") == ""
        assert to_plain_text("WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n") == ""


class TestFeedParsing:
    def test_the_transcript_link_is_read_from_the_feed(self):
        xml = b"""<?xml version="1.0"?>
        <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
          <channel><title>Show</title><item><guid>ep1</guid>
            <podcast:transcript url="https://p.example/a.vtt" type="text/vtt"/>
          </item></channel>
        </rss>"""

        parsed = parse_feed_bytes(xml)

        assert parsed.episodes[0].transcript_url == "https://p.example/a.vtt"
        assert parsed.episodes[0].transcript_type == "text/vtt"

    def test_the_most_searchable_format_wins(self):
        """Shows commonly publish the same transcript several ways. HTML and JSON need more
        mangling to become searchable text, so plain text is preferred."""
        xml = b"""<?xml version="1.0"?>
        <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
          <channel><title>Show</title><item><guid>ep1</guid>
            <podcast:transcript url="https://p.example/a.html" type="text/html"/>
            <podcast:transcript url="https://p.example/a.txt" type="text/plain"/>
          </item></channel>
        </rss>"""

        assert parse_feed_bytes(xml).episodes[0].transcript_url == "https://p.example/a.txt"


class TestEndpoint:
    @respx.mock
    async def test_it_is_fetched_once_and_cached(self, client, episode):
        route = respx.get(URL).mock(return_value=httpx.Response(200, text=VTT))

        first = (await client.get(f"/api/episodes/{episode.id}/transcript")).json()
        second = (await client.get(f"/api/episodes/{episode.id}/transcript")).json()

        assert route.call_count == 1
        assert first["available"] is True
        assert "orbital mechanics" in first["text"]
        assert second == first

    async def test_an_episode_without_one_says_so_rather_than_404ing(self, client, session):
        feed = Feed(feed_url="https://example.com/b.xml", title="No transcripts")
        session.add(feed)
        await session.commit()
        await session.refresh(feed)
        row = Episode(feed_id=feed.id, guid="plain", title="Plain")
        session.add(row)
        await session.commit()
        await session.refresh(row)

        response = await client.get(f"/api/episodes/{row.id}/transcript")

        assert response.status_code == 200
        assert response.json() == {"available": False, "text": None}


class TestSearch:
    @respx.mock
    async def test_the_library_can_be_searched_by_what_was_said(self, client, episode):
        """The whole point: the words are in the audio, not in the title."""
        respx.get(URL).mock(return_value=httpx.Response(200, text=VTT))
        await client.get(f"/api/episodes/{episode.id}/transcript")

        found = (await client.get("/api/episodes", params={"q": "orbital mechanics"})).json()

        assert [item["id"] for item in found["items"]] == [episode.id]

    @respx.mock
    async def test_a_word_in_neither_title_nor_transcript_finds_nothing(self, client, episode):
        respx.get(URL).mock(return_value=httpx.Response(200, text=VTT))
        await client.get(f"/api/episodes/{episode.id}/transcript")

        found = (await client.get("/api/episodes", params={"q": "submarines"})).json()

        assert found["items"] == []

    async def test_title_search_still_works_without_a_transcript(self, client, episode):
        found = (await client.get("/api/episodes", params={"q": "Episode One"})).json()

        assert [item["id"] for item in found["items"]] == [episode.id]
