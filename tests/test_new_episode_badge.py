"""The library badge counts what has arrived since you last looked, not what is unplayed.

Unplayed counts a backlog nobody intends to finish -- subscribing to a show with thousands
of episodes does not create thousands of obligations -- so a badge built on it reads 99+
forever and stops meaning anything. Unseen is the actionable number.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, EpisodeState, Feed, FeedState


@pytest.fixture
async def client(session, user):
    feed = Feed(feed_url="https://example.com/feed.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    # A back catalogue that arrived with the subscription.
    subscribed_at = datetime.now(UTC) - timedelta(days=30)
    for index in range(50):
        session.add(
            Episode(
                feed_id=feed.id,
                guid=f"old-{index}",
                title=f"Old {index}",
                first_seen_at=subscribed_at,
            )
        )
    session.add(FeedState(user_id=user.id, feed_id=feed.id, last_seen_at=subscribed_at))
    await session.commit()

    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.feed_id = feed.id
        yield c
    app.dependency_overrides.clear()


async def _add_new_episodes(session, feed_id: int, count: int) -> list[Episode]:
    episodes = []
    for index in range(count):
        episode = Episode(
            feed_id=feed_id,
            guid=f"new-{index}-{datetime.now(UTC).timestamp()}",
            title=f"New {index}",
            first_seen_at=datetime.now(UTC),
        )
        session.add(episode)
        episodes.append(episode)
    await session.commit()
    return episodes


async def _badge(client) -> int:
    body = (await client.get(f"/api/feeds/{client.feed_id}")).json()
    return body["new_episode_count"]


async def test_a_backlog_is_not_new(client):
    """The whole point: 50 unplayed episodes, none of them new."""
    body = (await client.get(f"/api/feeds/{client.feed_id}")).json()

    assert body["unplayed_count"] == 50
    assert body["new_episode_count"] == 0


async def test_episodes_arriving_later_are_new(session, client):
    await _add_new_episodes(session, client.feed_id, 3)
    assert await _badge(client) == 3


async def test_playing_an_episode_removes_it_from_the_count(session, client, user):
    episodes = await _add_new_episodes(session, client.feed_id, 3)

    session.add(EpisodeState(user_id=user.id, episode_id=episodes[0].id, played=True))
    await session.commit()

    assert await _badge(client) == 2


async def test_viewing_the_show_clears_it(session, client):
    """A show you dip into needs a way to say "I looked, I am not taking the rest"."""
    await _add_new_episodes(session, client.feed_id, 4)
    assert await _badge(client) == 4

    response = await client.post(f"/api/feeds/{client.feed_id}/seen")

    assert response.status_code == 200
    assert response.json()["new_episode_count"] == 0
    assert await _badge(client) == 0


async def test_clearing_does_not_mark_anything_played(session, client):
    """Seen and played are different claims. Clearing the badge must not fake listening."""
    await _add_new_episodes(session, client.feed_id, 4)
    await client.post(f"/api/feeds/{client.feed_id}/seen")

    body = (await client.get(f"/api/feeds/{client.feed_id}")).json()
    assert body["unplayed_count"] == 54
    assert (await session.execute(select(EpisodeState))).scalars().all() == []


async def test_episodes_after_clearing_count_again(session, client):
    await _add_new_episodes(session, client.feed_id, 2)
    await client.post(f"/api/feeds/{client.feed_id}/seen")
    assert await _badge(client) == 0

    await _add_new_episodes(session, client.feed_id, 1)
    assert await _badge(client) == 1


async def test_subscribing_does_not_light_up_the_badge(session, user):
    """A newly subscribed show arrives with its whole archive; none of it is new to you."""
    import respx
    from tests.feeds import build_feed

    url = "https://example.com/fresh.xml"
    xml = build_feed(
        items=[
            {"guid": f"ep-{i}", "title": f"Episode {i}", "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT"}
            for i in range(12)
        ]
    )

    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, content=xml))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            created = await c.post("/api/feeds", json={"feed_url": url})
            assert created.status_code == 201
            body = created.json()

    app.dependency_overrides.clear()

    assert body["episode_count"] == 12
    assert body["new_episode_count"] == 0, "a new subscription's archive is not 12 new episodes"


async def test_a_feed_with_no_seen_row_falls_back_to_its_creation_time(session, client, user):
    """Subscriptions made before this existed must not report their whole archive."""
    state = await session.get(FeedState, {"user_id": user.id, "feed_id": client.feed_id})
    await session.delete(state)
    await session.commit()

    assert await _badge(client) == 0


# --- clearing everything at once ----------------------------------------------
#
# Opening the inbox clears the badge. The nav badge is the sum of the library tiles, so
# there is no clearing one without the other.


async def test_marking_all_seen_clears_every_show(session, client, user):
    from podarium.models import Feed

    second = Feed(feed_url="https://other.example/feed.xml", title="Other")
    session.add(second)
    await session.commit()
    await session.refresh(second)

    await _add_new_episodes(session, client.feed_id, 3)
    await _add_new_episodes(session, second.id, 2)

    response = await client.post("/api/feeds/seen")
    assert response.status_code == 204

    feeds = (await client.get("/api/feeds")).json()
    assert [f["new_episode_count"] for f in feeds] == [0, 0]


async def test_marking_all_seen_does_not_mark_anything_played(session, client):
    """Seen and played are different claims, here as everywhere else."""
    await _add_new_episodes(session, client.feed_id, 3)
    before = (await client.get(f"/api/feeds/{client.feed_id}")).json()["unplayed_count"]

    await client.post("/api/feeds/seen")

    after = (await client.get(f"/api/feeds/{client.feed_id}")).json()
    assert after["unplayed_count"] == before
    assert after["new_episode_count"] == 0


async def test_marking_all_seen_leaves_unsubscribed_shows_alone(session, client, user):
    """A soft-unsubscribed show is not something the inbox is offering you."""
    from podarium.models import Feed, FeedState

    inactive = Feed(feed_url="https://gone.example/feed.xml", title="Gone", active=False)
    session.add(inactive)
    await session.commit()
    await session.refresh(inactive)
    marker = datetime.now(UTC) - timedelta(days=30)
    session.add(FeedState(user_id=user.id, feed_id=inactive.id, last_seen_at=marker))
    await session.commit()

    await client.post("/api/feeds/seen")

    state = await session.get(FeedState, {"user_id": user.id, "feed_id": inactive.id})
    assert state.last_seen_at == marker, "an inactive feed's marker must not move"


async def test_seen_is_not_read_as_a_feed_id(client):
    """The literal path must not be captured by the /{feed_id}/ routes."""
    response = await client.post("/api/feeds/seen")
    assert response.status_code == 204
