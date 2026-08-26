"""What wins when two devices disagree.

A client that queues writes while offline flushes them later, so arrival order stops
meaning anything. The realistic loss: forty minutes listened on the phone offline, then the
episode finished in the browser, then the phone reconnects and its hours-old position lands
last and wins -- silently undoing the newer work.

So a write carries when it was made, and one describing an already-overtaken moment is
declined. Writes without a timestamp are treated as happening now, which keeps every
existing caller working unchanged.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, Feed


@pytest.fixture
async def client(session, user):
    feed = Feed(feed_url="https://a.example/f.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    episode = Episode(feed_id=feed.id, guid="ep-1", title="Episode 1")
    session.add(episode)
    await session.commit()
    await session.refresh(episode)

    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.episode_id = episode.id
        yield c
    app.dependency_overrides.clear()


async def put(client, **body) -> dict:
    response = await client.put(f"/api/episodes/{client.episode_id}/state", json=body)
    assert response.status_code == 200
    return response.json()


async def test_a_stale_offline_flush_does_not_undo_newer_work(client):
    """The case this exists for."""
    long_ago = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

    # The browser finishes the episode now.
    await put(client, played=True, position_seconds=3600)

    # The phone reconnects and flushes what it recorded three hours ago.
    result = await put(client, played=False, position_seconds=2400, changed_at=long_ago)

    assert result["played"] is True, "the newer state stands"
    assert result["position_seconds"] == 3600
    assert result["played"] is not False


async def test_the_client_gets_the_current_state_back(client):
    """Returning the truth rather than an error lets a client self-correct."""
    await put(client, position_seconds=3600)
    stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

    result = await put(client, position_seconds=10, changed_at=stale)

    assert result["position_seconds"] == 3600


async def test_a_current_write_still_applies(client):
    """Declining stale writes must not decline every timestamped write."""
    await put(client, position_seconds=100)

    result = await put(client, position_seconds=900, changed_at=datetime.now(UTC).isoformat())

    assert result["position_seconds"] == 900


async def test_a_slightly_slow_clock_is_tolerated(client):
    """Devices do not agree to the second, and an ordinary write must not be declined for
    being a few seconds behind whatever touched the episode last."""
    await put(client, position_seconds=100)
    slightly_behind = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()

    result = await put(client, position_seconds=900, changed_at=slightly_behind)

    assert result["position_seconds"] == 900, "20s of skew is concurrent, not stale"


async def test_the_tolerance_does_not_swallow_a_real_flush(client):
    """Minutes behind is past any plausible skew and is the case worth catching."""
    await put(client, position_seconds=100)
    well_behind = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

    result = await put(client, position_seconds=900, changed_at=well_behind)

    assert result["position_seconds"] == 100


async def test_a_write_without_a_timestamp_still_applies(client):
    """Every existing caller omits it, and must keep working."""
    await put(client, position_seconds=500)
    result = await put(client, position_seconds=20)
    assert result["position_seconds"] == 20


async def test_a_first_write_is_never_stale(client):
    """There is nothing for it to be older than."""
    ancient = (datetime.now(UTC) - timedelta(days=30)).isoformat()

    result = await put(client, played=True, changed_at=ancient)

    assert result["played"] is True


async def test_a_future_timestamp_cannot_win_forever(client):
    """A device with a fast clock would otherwise beat every later write it ever met."""
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    await put(client, position_seconds=111, changed_at=tomorrow)

    # A perfectly ordinary write made now, which must not lose to that.
    result = await put(client, position_seconds=222)

    assert result["position_seconds"] == 222


async def test_starring_offline_is_declined_if_overtaken(client):
    """The rule is about the write as a whole, not only playback position."""
    await put(client, starred=True)
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    result = await put(client, starred=False, changed_at=stale)

    assert result["starred"] is True
