"""Unsubscribing removes the audio with the rows.

A row that is gone cannot be counted, served, or swept, so a file left behind it is
lost to every accounting the server does: not in the storage panel, not under the
ceiling, never reclaimed. ``purge=false`` used to produce exactly that.
"""

from datetime import UTC, datetime

import httpx
import pytest

from podarium.auth import current_user
from podarium.config import get_settings
from podarium.main import app
from podarium.models import Episode, Feed


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _downloaded_feed(session):
    feed = Feed(feed_url="https://a.example/f.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    path = get_settings().download_dir / str(feed.id) / "1.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    session.add(
        Episode(
            feed_id=feed.id, guid="ep-1", title="Episode 1",
            enclosure_url="https://cdn.example/1.mp3", local_path=str(path), local_bytes=100,
            downloaded_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return feed, path


@pytest.mark.parametrize("purge", [None, "false", "true"])
async def test_the_audio_goes_with_the_rows(session, client, purge):
    feed, path = await _downloaded_feed(session)
    feed_id = feed.id
    params = {} if purge is None else {"purge": purge}

    assert (await client.delete(f"/api/feeds/{feed_id}", params=params)).status_code == 204

    assert not path.exists(), "a file with no row is unreachable and uncounted"
    session.expire_all()
    assert await session.get(Feed, feed_id) is None
