"""A finished episode leaves the queue.

The queue is what plays next. An episode that has been heard is not that, and leaving it
there turns the queue into a history of what you already listened to -- which the played
flag already records.
"""

import httpx
import pytest
from sqlalchemy import select

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, Feed, QueueItem
from podarium.services import drop_from_queue


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def a_queue(session, user, count: int):
    feed = Feed(feed_url="https://example.com/s.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    episodes = []
    for index in range(count):
        episode = Episode(feed_id=feed.id, guid=f"e{index}", title=f"Episode {index}")
        session.add(episode)
        await session.commit()
        await session.refresh(episode)
        session.add(
            QueueItem(user_id=user.id, episode_id=episode.id, position=index)
        )
        episodes.append(episode)
    await session.commit()
    return feed, episodes


async def queue_ids(session, user) -> list[int]:
    rows = (
        await session.execute(
            select(QueueItem)
            .where(QueueItem.user_id == user.id)
            .order_by(QueueItem.position, QueueItem.id)
        )
    ).scalars().all()
    return [row.episode_id for row in rows]


class TestDropFromQueue:
    async def test_the_finished_episode_goes_and_the_rest_stay_in_order(
        self, session, user
    ):
        _, episodes = await a_queue(session, user, 3)

        await drop_from_queue(session, user.id, [episodes[0].id])
        await session.commit()

        assert await queue_ids(session, user) == [episodes[1].id, episodes[2].id]

    async def test_positions_stay_dense(self, session, user):
        # The queue is renumbered 0..n-1 everywhere else; a gap here would be a slow leak
        # into reorder and insert-at-position, which both count on it.
        _, episodes = await a_queue(session, user, 3)

        await drop_from_queue(session, user.id, [episodes[1].id])
        await session.commit()

        rows = (
            await session.execute(
                select(QueueItem).where(QueueItem.user_id == user.id)
                .order_by(QueueItem.position)
            )
        ).scalars().all()
        assert [row.position for row in rows] == [0, 1]

    async def test_an_episode_that_was_never_queued_is_not_an_error(
        self, session, user
    ):
        # Most episodes are played without ever being queued.
        _, episodes = await a_queue(session, user, 1)
        feed = Feed(feed_url="https://example.com/other.xml")
        session.add(feed)
        await session.commit()
        await session.refresh(feed)
        loose = Episode(feed_id=feed.id, guid="loose", title="Loose")
        session.add(loose)
        await session.commit()
        await session.refresh(loose)

        assert await drop_from_queue(session, user.id, [loose.id]) == 0
        assert await queue_ids(session, user) == [episodes[0].id]

    async def test_one_persons_queue_is_not_another_persons(self, session, user):
        _, episodes = await a_queue(session, user, 1)
        await drop_from_queue(session, user.id + 999, [episodes[0].id])
        await session.commit()

        assert await queue_ids(session, user) == [episodes[0].id]


class TestMarkingPlayedClearsIt:
    async def test_finishing_an_episode_takes_it_out_of_the_queue(
        self, client, session, user
    ):
        _, episodes = await a_queue(session, user, 2)

        response = await client.put(
            f"/api/episodes/{episodes[0].id}/state", json={"played": True}
        )
        assert response.status_code == 200

        assert await queue_ids(session, user) == [episodes[1].id]

    async def test_marking_it_unplayed_does_not_put_it_back(
        self, client, session, user
    ):
        # Re-queueing is a deliberate act. Guessing at it would resurrect episodes that
        # were cleared on purpose.
        _, episodes = await a_queue(session, user, 2)
        await client.put(f"/api/episodes/{episodes[0].id}/state", json={"played": True})
        await client.put(f"/api/episodes/{episodes[0].id}/state", json={"played": False})

        assert await queue_ids(session, user) == [episodes[1].id]

    async def test_the_next_episode_is_still_reachable_after_the_removal(
        self, client, session, user
    ):
        # The player asks the queue for what follows the episode that just ended. Removing
        # the finished one must leave the following one at the head, or the queue stops
        # advancing -- the failure this change could most easily cause.
        _, episodes = await a_queue(session, user, 3)

        await client.put(f"/api/episodes/{episodes[0].id}/state", json={"played": True})

        remaining = await client.get("/api/queue")
        ids = [item["episode_id"] for item in remaining.json()]
        assert ids == [episodes[1].id, episodes[2].id]
