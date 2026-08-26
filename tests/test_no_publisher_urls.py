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
