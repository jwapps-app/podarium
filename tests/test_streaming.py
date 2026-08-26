"""Range support. AVPlayer seeks by byte range; a server that answers 200 breaks scrubbing."""

import httpx
import pytest
from fastapi import HTTPException

from podarium.api.media_routes import parse_range
from podarium.auth import current_user
from podarium.config import get_settings
from podarium.main import app
from podarium.models import Episode, Feed, User

CONTENT = bytes(range(256)) * 40  # 10240 bytes, every offset distinguishable


def test_parse_range_forms():
    size = 1000
    assert parse_range(None, size) is None
    assert parse_range("bytes=0-99", size) == (0, 99)
    assert parse_range("bytes=100-", size) == (100, 999)
    assert parse_range("bytes=-200", size) == (800, 999)
    # An end past EOF is clamped rather than rejected -- players routinely over-ask.
    assert parse_range("bytes=900-99999", size) == (900, 999)
    # Unparseable or multi-range: fall back to the whole body.
    assert parse_range("bytes=abc", size) is None
    assert parse_range("bytes=0-10,20-30", size) is None


def test_parse_range_rejects_unsatisfiable():
    with pytest.raises(HTTPException) as exc:
        parse_range("bytes=5000-", 1000)
    assert exc.value.status_code == 416


@pytest.fixture
async def client(session):
    feed = Feed(feed_url="https://example.com/feed.xml")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    path = get_settings().download_dir / str(feed.id) / "audio.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CONTENT)

    episode = Episode(
        feed_id=feed.id, guid="ep-1", local_path=str(path), local_bytes=len(CONTENT),
        enclosure_type="audio/mpeg",
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode)

    app.dependency_overrides[current_user] = lambda: User(id=1, username="t", password_hash="x")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.episode_id = episode.id
        yield c
    app.dependency_overrides.clear()


async def test_full_request_advertises_range_support(client):
    response = await client.get(f"/api/stream/{client.episode_id}")
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert int(response.headers["content-length"]) == len(CONTENT)
    assert response.content == CONTENT


async def test_partial_request_returns_exact_bytes(client):
    response = await client.get(
        f"/api/stream/{client.episode_id}", headers={"Range": "bytes=100-199"}
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-199/{len(CONTENT)}"
    assert int(response.headers["content-length"]) == 100
    assert response.content == CONTENT[100:200]


async def test_open_ended_range(client):
    start = len(CONTENT) - 50
    response = await client.get(
        f"/api/stream/{client.episode_id}", headers={"Range": f"bytes={start}-"}
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{len(CONTENT) - 1}/{len(CONTENT)}"
    assert response.content == CONTENT[start:]


async def test_suffix_range(client):
    response = await client.get(
        f"/api/stream/{client.episode_id}", headers={"Range": "bytes=-64"}
    )
    assert response.status_code == 206
    assert response.content == CONTENT[-64:]


async def test_unsatisfiable_range_returns_416(client):
    response = await client.get(
        f"/api/stream/{client.episode_id}", headers={"Range": "bytes=999999-"}
    )
    assert response.status_code == 416
