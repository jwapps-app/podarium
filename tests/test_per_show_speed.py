"""Per-show playback speed.

Speed is the setting that varies most by show -- a dense interview holds up at 1.5x where a
scripted narrative does not -- so it takes the same inherit-or-override shape as retention
and auto-download. NULL means inherit, which is a different thing from any particular value.
"""

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Feed
from podarium.services import get_app_settings


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def feed(session):
    row = Feed(feed_url="https://example.com/feed.xml", title="Show")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def test_a_new_show_inherits_the_global_default(client, feed, session):
    app_settings = await get_app_settings(session)
    app_settings.default_playback_rate = 1.25
    await session.commit()

    body = (await client.get(f"/api/feeds/{feed.id}")).json()

    assert body["playback_rate"] is None
    assert body["effective_playback_rate"] == 1.25


async def test_a_show_value_overrides_the_global(client, feed, session):
    app_settings = await get_app_settings(session)
    app_settings.default_playback_rate = 1.0
    await session.commit()

    body = (await client.patch(f"/api/feeds/{feed.id}", json={"playback_rate": 1.5})).json()

    assert body["playback_rate"] == 1.5
    assert body["effective_playback_rate"] == 1.5


async def test_clearing_returns_the_show_to_the_global(client, feed, session):
    app_settings = await get_app_settings(session)
    app_settings.default_playback_rate = 1.1
    await session.commit()
    await client.patch(f"/api/feeds/{feed.id}", json={"playback_rate": 2.0})

    body = (
        await client.patch(f"/api/feeds/{feed.id}", json={"clear_playback_rate": True})
    ).json()

    assert body["playback_rate"] is None
    assert body["effective_playback_rate"] == 1.1


async def test_an_omitted_field_is_not_a_clear(client, feed):
    """Patching something else must not silently reset the speed -- which is exactly why
    clearing needs its own flag rather than being inferred from a missing field."""
    await client.patch(f"/api/feeds/{feed.id}", json={"playback_rate": 1.75})

    body = (await client.patch(f"/api/feeds/{feed.id}", json={"active": True})).json()

    assert body["playback_rate"] == 1.75


async def test_a_nonsense_rate_is_rejected(client, feed):
    """Zero would stop playback and negative has no meaning; both are 422, not stored."""
    assert (await client.patch(f"/api/feeds/{feed.id}", json={"playback_rate": 0})).status_code == 422
    assert (await client.patch(f"/api/feeds/{feed.id}", json={"playback_rate": -1})).status_code == 422
    assert (await client.patch(f"/api/feeds/{feed.id}", json={"playback_rate": 99})).status_code == 422
