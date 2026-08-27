"""The optimisations that keep a phone's radio quiet.

Each of these encodes a measured finding rather than a hunch: the numbers are in the
commit that introduced them.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from podarium.auth import current_user, generate_api_token
from podarium.main import app
from podarium.models import ApiToken, Episode, Feed


@pytest.fixture
async def library(session):
    feed = Feed(feed_url="https://example.com/feed.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    for index in range(3):
        session.add(
            Episode(
                feed_id=feed.id,
                guid=f"ep-{index}",
                title=f"Episode {index}",
                description_html="<p>" + "notes " * 200 + "</p>",
            )
        )
    await session.commit()
    return feed


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_lists_can_omit_show_notes(client, library):
    """Measured at 58% of a typical inbox page, rendered only when a row is expanded."""
    slim = (await client.get("/api/episodes", params={"notes": "false"})).json()["items"]

    assert all(item["description_html"] is None for item in slim)
    # Everything else is intact -- this trims one field, it does not change the shape.
    assert all(item["title"] for item in slim)


async def test_notes_are_included_by_default(client, library):
    """The existing contract: any client not passing the flag sees no change."""
    full = (await client.get("/api/episodes")).json()["items"]

    assert all(item["description_html"] for item in full)


async def test_the_single_episode_endpoint_always_carries_notes(client, library):
    """This is what a row fetches on expand, so it must never be trimmed."""
    episode_id = (await client.get("/api/episodes")).json()["items"][0]["id"]

    body = (await client.get(f"/api/episodes/{episode_id}")).json()

    assert body["description_html"]


async def test_bearer_auth_does_not_write_on_every_request(session, user):
    """A phone syncing on a timer would otherwise commit a row update per API call,
    thousands of writes a day recording nothing anyone reads at that granularity."""
    plaintext, token_hash = generate_api_token()
    recent = datetime.now(UTC) - timedelta(minutes=1)
    token = ApiToken(user_id=user.id, token_hash=token_hash, name="phone", last_used_at=recent)
    session.add(token)
    await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {plaintext}"},
    ) as client:
        assert (await client.get("/api/feeds")).status_code == 200

    await session.refresh(token)
    assert token.last_used_at == recent  # untouched: it was fresh

    # A genuinely stale stamp is refreshed -- the field still means something.
    token.last_used_at = datetime.now(UTC) - timedelta(hours=2)
    await session.commit()
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {plaintext}"},
    ) as client:
        assert (await client.get("/api/feeds")).status_code == 200
    await session.refresh(token)
    assert datetime.now(UTC) - token.last_used_at.replace(tzinfo=UTC) < timedelta(minutes=1)
