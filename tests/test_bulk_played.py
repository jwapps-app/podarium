"""Marking a show's whole backlog played.

The case is subscribing to something with a long archive. A 2,700-episode back catalogue is
not 2,700 obligations, and one episode at a time is not a way to say so.
"""

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, EpisodeState, Feed


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def feed(session, user):
    row = Feed(feed_url="https://example.com/feed.xml", title="Archive")
    other = Feed(feed_url="https://example.com/other.xml", title="Untouched")
    session.add_all([row, other])
    await session.commit()
    await session.refresh(row)
    await session.refresh(other)

    for index in range(5):
        session.add(Episode(feed_id=row.id, guid=f"ep-{index}", title=f"Episode {index}"))
    session.add(Episode(feed_id=other.id, guid="other-1", title="Other show"))
    await session.commit()
    return row


async def test_marks_every_episode_played(client, feed, session):
    await client.post(f"/api/feeds/{feed.id}/played")

    body = (await client.get("/api/episodes", params={"feed_id": feed.id})).json()
    assert all(item["played"] for item in body["items"])
    assert len(body["items"]) == 5


async def test_leaves_other_shows_alone(client, feed):
    await client.post(f"/api/feeds/{feed.id}/played")

    body = (await client.get("/api/episodes", params={"unplayed": "true"})).json()
    assert [item["title"] for item in body["items"]] == ["Other show"]


async def test_reverses_with_played_false(client, feed):
    await client.post(f"/api/feeds/{feed.id}/played")
    await client.post(f"/api/feeds/{feed.id}/played", params={"played": "false"})

    body = (await client.get("/api/episodes", params={"feed_id": feed.id})).json()
    assert not any(item["played"] for item in body["items"])


async def test_does_not_wipe_where_you_were(client, feed, session, user):
    """Bulk-marking the backlog is a claim about the backlog, not an instruction to forget
    the one episode you were halfway through. The resume list drops it either way."""
    episode = (await client.get("/api/episodes", params={"feed_id": feed.id})).json()["items"][0]
    await client.put(f"/api/episodes/{episode['id']}/state", json={"position_seconds": 600})

    await client.post(f"/api/feeds/{feed.id}/played")

    state = await session.get(
        EpisodeState, {"user_id": user.id, "episode_id": episode["id"]}
    )
    await session.refresh(state)
    assert state.position_seconds == 600
    assert state.played is True


async def test_completed_at_is_stamped_so_retention_can_measure_from_it(
    client, feed, session, user
):
    """after_played retention counts days from completed_at; without it the sweep has no
    clock and a bulk-marked archive would never be reclaimed."""
    await client.post(f"/api/feeds/{feed.id}/played")

    episode = (await client.get("/api/episodes", params={"feed_id": feed.id})).json()["items"][0]
    state = await session.get(
        EpisodeState, {"user_id": user.id, "episode_id": episode["id"]}
    )
    assert state.completed_at is not None


async def test_unplayed_does_not_create_rows_for_untouched_episodes(
    client, feed, session, user
):
    """Nothing recorded already means not played; a row saying so is pure noise, and it
    would push every episode into every client's next sync delta."""
    await client.post(f"/api/feeds/{feed.id}/played", params={"played": "false"})

    rows = (
        await session.execute(
            EpisodeState.__table__.select().where(EpisodeState.user_id == user.id)
        )
    ).all()
    assert rows == []


async def test_marking_played_also_clears_the_new_badge(client, feed):
    """Saying you are not taking the backlog is also saying you have looked at the show."""
    body = (await client.get(f"/api/feeds/{feed.id}")).json()
    assert body["new_episode_count"] > 0

    after = (await client.post(f"/api/feeds/{feed.id}/played")).json()
    assert after["new_episode_count"] == 0
