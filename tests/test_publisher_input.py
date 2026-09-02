"""What a publisher controls, and what this server does with it.

Once a feed is subscribed, everything in it is the publisher's: the media types on the
enclosure and on the responses, the bytes behind the artwork URL, the strings in every
tag. Each of these ends up either on this origin or in a fixed-width column, and these
tests pin what is allowed to get there.
"""

import httpx
import pytest
import respx

from podarium.auth import current_user
from podarium.clients.feedfetch import LANGUAGE_MAX, MIME_MAX, parse_feed_bytes
from podarium.jobs.artwork import ensure_artwork, served_image_type
from podarium.main import app
from podarium.models import ArtworkCache, Episode, Feed
from podarium.streaming import safe_audio_type

JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestStreamMediaTypes:
    @pytest.mark.parametrize(
        ("declared", "served"),
        [
            ("audio/mpeg", "audio/mpeg"),
            ("audio/x-m4a; charset=binary", "audio/x-m4a"),
            ("video/mp4", "video/mp4"),
            ("application/ogg", "application/ogg"),
            ("text/html", "audio/mpeg"),
            ("image/svg+xml", "audio/mpeg"),
            (None, "audio/mpeg"),
            ("", "audio/mpeg"),
        ],
    )
    def test_only_media_types_reach_the_response(self, declared, served):
        assert safe_audio_type(declared) == served

    @respx.mock
    async def test_a_proxied_episode_does_not_take_the_publishers_content_type(
        self, client, session
    ):
        """An undownloaded episode is streamed through from the publisher, on this origin.
        A publisher answering text/html would otherwise have /api/stream serve a page on
        the same origin as the session cookie."""
        feed = Feed(feed_url="https://publisher.example/feed.xml", title="Show")
        session.add(feed)
        await session.flush()
        episode = Episode(
            feed_id=feed.id,
            guid="ep-1",
            enclosure_url="https://cdn.publisher.example/ep1.mp3",
            enclosure_type="audio/mpeg",
        )
        session.add(episode)
        await session.commit()

        respx.get("https://cdn.publisher.example/ep1.mp3").mock(
            return_value=httpx.Response(
                200, content=b"<html>not audio</html>", headers={"Content-Type": "text/html"}
            )
        )

        response = await client.get(f"/api/stream/{episode.id}")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/mpeg")


class TestArtworkIsCheckedByItsBytes:
    @respx.mock
    async def test_a_jpeg_served_as_text_plain_is_kept_as_a_jpeg(self, session, tmp_media_root):
        # Publishers do this. The header is wrong and the image is fine.
        url = "https://cdn.publisher.example/wrong-header.jpg"
        respx.get(url).mock(
            return_value=httpx.Response(200, content=JPEG_HEAD, headers={"Content-Type": "text/plain"})
        )

        entry = await ensure_artwork(session, url, user_agent="test")

        assert entry.fetch_error is None
        assert entry.content_type == "image/jpeg"
        assert entry.local_path.endswith(".jpg")

    @respx.mock
    async def test_a_document_claiming_to_be_an_image_is_refused(self, session, tmp_media_root):
        # SVG carries script and would be served from this origin. The signature check
        # does not know SVG, and the header is not believed on its own.
        url = "https://cdn.publisher.example/art.svg"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                content=b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>",
                headers={"Content-Type": "image/svg+xml"},
            )
        )

        entry = await ensure_artwork(session, url, user_agent="test")

        assert entry.local_path is None
        assert "not an image" in entry.fetch_error

    @respx.mock
    async def test_an_html_error_page_with_an_image_header_is_refused(self, session, tmp_media_root):
        url = "https://cdn.publisher.example/gone.png"
        respx.get(url).mock(
            return_value=httpx.Response(
                200, content=b"<html>gone</html>", headers={"Content-Type": "text/html"}
            )
        )

        entry = await ensure_artwork(session, url, user_agent="test")

        assert entry.local_path is None
        assert "not an image" in entry.fetch_error

    @respx.mock
    async def test_the_signature_wins_over_the_header(self, session, tmp_media_root):
        url = "https://cdn.publisher.example/actually-png.jpg"
        respx.get(url).mock(
            return_value=httpx.Response(200, content=PNG_HEAD, headers={"Content-Type": "image/jpeg"})
        )

        entry = await ensure_artwork(session, url, user_agent="test")

        assert entry.content_type == "image/png"

    def test_a_row_written_before_the_check_is_sniffed_when_served(self, tmp_path):
        # Rows from before this existed hold whatever the publisher sent.
        image = tmp_path / "old.img"
        image.write_bytes(JPEG_HEAD)
        entry = ArtworkCache(
            url_hash="x" * 64, source_url="u", local_path=str(image), content_type="text/plain"
        )
        assert served_image_type(entry) == "image/jpeg"

    def test_a_row_that_is_not_an_image_is_served_as_nothing_renderable(self, tmp_path):
        blob = tmp_path / "old.img"
        blob.write_bytes(b"<html>")
        entry = ArtworkCache(
            url_hash="y" * 64, source_url="u", local_path=str(blob), content_type="text/html"
        )
        assert served_image_type(entry) == "application/octet-stream"


class TestFeedStringsFitTheirColumns:
    """A value past the column width failed the whole refresh at commit, every hour."""

    def test_language_and_mime_types_are_cut_to_fit(self):
        long_language = "x" * (LANGUAGE_MAX + 40)
        long_mime = "audio/" + "y" * MIME_MAX
        document = f"""<?xml version="1.0"?>
        <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
          <channel>
            <title>Show</title>
            <language>{long_language}</language>
            <item>
              <title>Ep</title>
              <guid>g1</guid>
              <enclosure url="https://cdn.publisher.example/1.mp3" type="{long_mime}" length="1"/>
              <podcast:transcript url="https://cdn.publisher.example/1.vtt" type="{long_mime}"/>
            </item>
          </channel>
        </rss>""".encode()

        parsed = parse_feed_bytes(document)

        assert len(parsed.language) == LANGUAGE_MAX
        (episode,) = parsed.episodes
        assert len(episode.enclosure_type) == MIME_MAX
        assert len(episode.transcript_type) == MIME_MAX
