"""Time listened is measured, not inferred.

The obvious implementation counts the duration of everything marked played. That describes
tidying rather than listening: marking played is how an inbox gets cleared, and it usually
means "I am not going to listen to this". A library cleared of skipped shows would report
hundreds of hours nobody heard.
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
async def episodes(session):
    feed = Feed(feed_url="https://example.com/f.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    rows = [
        Episode(feed_id=feed.id, guid=f"ep-{i}", title=f"Episode {i}", duration_seconds=3600)
        for i in range(3)
    ]
    session.add_all(rows)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


async def stats(client) -> dict:
    return (await client.get("/api/stats")).json()


async def test_clearing_the_inbox_is_not_listening(client, episodes):
    """The whole point. Three hour-long episodes marked played to clear them must not
    report three hours listened."""
    for episode in episodes:
        await client.put(f"/api/episodes/{episode.id}/state", json={"played": True})

    body = await stats(client)

    assert body["seconds_listened"] == 0
    assert body["episodes_listened"] == 0
    # ...but the clearing itself is still reported, under its own name.
    assert body["episodes_marked_played"] == 3


async def test_listening_is_counted(client, episodes):
    await client.put(
        f"/api/episodes/{episodes[0].id}/state",
        json={"position_seconds": 600, "listened_delta": 600},
    )

    body = await stats(client)

    assert body["seconds_listened"] == 600
    assert body["episodes_listened"] == 1


async def test_deltas_accumulate_rather_than_overwrite(client, episodes):
    """Reports arrive every few seconds, and two devices can play the same episode. A total
    would overwrite; a delta adds up."""
    for _ in range(4):
        await client.put(
            f"/api/episodes/{episodes[0].id}/state", json={"listened_delta": 30}
        )

    assert (await stats(client))["seconds_listened"] == 120


async def test_a_stale_report_still_counts_its_listening(client, episodes, session, user):
    """Every other field is a claim about the present, so an overtaken one is discarded.
    This is a fact about audio that really played, and stays true however late it arrives
    from a device that was offline."""
    await client.put(
        f"/api/episodes/{episodes[0].id}/state",
        json={"position_seconds": 900, "listened_delta": 900},
    )

    await client.put(
        f"/api/episodes/{episodes[0].id}/state",
        json={
            "position_seconds": 5,
            "listened_delta": 45,
            "changed_at": "2020-01-01T00:00:00Z",
        },
    )

    state = await session.get(
        EpisodeState, {"user_id": user.id, "episode_id": episodes[0].id}
    )
    await session.refresh(state)
    # The stale position was rejected; the listening it reported was not.
    assert state.position_seconds == 900
    assert state.listened_seconds == 945


async def test_a_moment_of_sampling_is_not_an_episode_listened(client, episodes):
    """Pressing play to hear what a show sounds like should not count as an episode."""
    await client.put(f"/api/episodes/{episodes[0].id}/state", json={"listened_delta": 20})

    body = await stats(client)

    assert body["seconds_listened"] == 20
    assert body["episodes_listened"] == 0


async def test_the_two_counts_are_reported_separately_per_show(client, episodes):
    await client.put(
        f"/api/episodes/{episodes[0].id}/state",
        json={"played": True, "listened_delta": 1800},
    )
    await client.put(f"/api/episodes/{episodes[1].id}/state", json={"played": True})

    show = (await stats(client))["shows"][0]

    assert show["episodes_listened"] == 1
    assert show["episodes_marked_played"] == 2
    assert show["seconds_listened"] == 1800
