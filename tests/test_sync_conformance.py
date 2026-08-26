"""Sync as a client actually uses it.

/api/sync is the one endpoint nothing has ever consumed -- the web UI does not use it, so
unlike every other route it has never been exercised by a real client over time. These
tests stand in for that client: they hold a local mirror, sync repeatedly, and assert the
mirror matches the server after each round.

Three bugs were found writing them, each invisible to a single-call test: deletions were
unknowable, badge changes never entered the delta, and feeds arrived without their counts.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, EpisodeState, Feed, FeedState, QueueItem


class Mirror:
    """What a client keeps locally, rebuilt only from what sync hands it."""

    def __init__(self) -> None:
        self.feeds: dict[int, dict] = {}
        self.episodes: dict[int, dict] = {}
        self.queue: list[int] = []
        self.cursor: str | None = None

    def apply(self, payload: dict) -> None:
        for feed in payload["feeds"]:
            self.feeds[feed["id"]] = feed
        for episode in payload["episodes"]:
            self.episodes[episode["id"]] = episode
        for feed_id in payload["deleted_feed_ids"]:
            self.feeds.pop(feed_id, None)
            for eid in [e for e, v in self.episodes.items() if v["feed_id"] == feed_id]:
                self.episodes.pop(eid)
        # The queue arrives whole on the first page, so removals are implicit.
        if not payload.get("_was_continuation"):
            self.queue = [item["episode_id"] for item in payload["queue"]]


async def sync(client, mirror: Mirror, *, limit: int = 500) -> dict:
    """Drain every page the way a client must, then adopt the new cursor."""
    params: dict = {"limit": limit}
    if mirror.cursor:
        params["since"] = mirror.cursor
    page_cursor = None
    pages = 0
    last: dict = {}

    while True:
        page = {**params, **({"cursor": page_cursor} if page_cursor else {})}
        response = await client.get("/api/sync", params=page)
        assert response.status_code == 200
        last = response.json()
        last["_was_continuation"] = page_cursor is not None
        mirror.apply(last)
        pages += 1
        page_cursor = last["next_cursor"]
        if not page_cursor:
            break
        assert pages < 50, "cursor is not advancing"

    # `now` is adopted only once the pages run out; doing it earlier loses the remainder.
    mirror.cursor = last["now"]
    return last


@pytest.fixture
async def client(session, user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _feed(session, url: str, title: str, *, episodes: int = 0) -> Feed:
    feed = Feed(feed_url=url, title=title)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    for index in range(episodes):
        session.add(
            Episode(
                feed_id=feed.id,
                guid=f"{title}-{index}",
                title=f"{title} {index}",
                published_at=datetime.now(UTC) - timedelta(days=index),
            )
        )
    await session.commit()
    return feed


async def test_a_first_sync_mirrors_the_whole_library(session, client):
    await _feed(session, "https://a.example/f.xml", "Alpha", episodes=3)
    await _feed(session, "https://b.example/f.xml", "Beta", episodes=2)

    mirror = Mirror()
    await sync(client, mirror)

    assert len(mirror.feeds) == 2
    assert len(mirror.episodes) == 5


async def test_a_second_sync_with_no_changes_returns_nothing(session, client):
    await _feed(session, "https://a.example/f.xml", "Alpha", episodes=3)
    mirror = Mirror()
    await sync(client, mirror)

    payload = await sync(client, mirror)

    assert payload["episodes"] == []
    assert payload["feeds"] == []
    assert payload["deleted_feed_ids"] == []


async def test_feeds_arrive_with_their_counts(session, client):
    """Without these a client holds feeds it cannot display, and must re-fetch each one."""
    await _feed(session, "https://a.example/f.xml", "Alpha", episodes=4)

    mirror = Mirror()
    await sync(client, mirror)
    feed = next(iter(mirror.feeds.values()))

    assert feed["episode_count"] == 4
    assert feed["unplayed_count"] == 4
    assert feed["new_episode_count"] is not None


async def test_a_deleted_feed_is_reported_and_removed(session, client):
    """The gap that made deleted_feed_ids a permanent empty list."""
    keep = await _feed(session, "https://a.example/f.xml", "Alpha", episodes=2)
    drop = await _feed(session, "https://b.example/f.xml", "Beta", episodes=2)

    mirror = Mirror()
    await sync(client, mirror)
    assert len(mirror.feeds) == 2

    assert (await client.delete(f"/api/feeds/{drop.id}")).status_code == 204
    payload = await sync(client, mirror)

    assert payload["deleted_feed_ids"] == [drop.id]
    assert set(mirror.feeds) == {keep.id}, "the client dropped it from its mirror"
    assert all(e["feed_id"] == keep.id for e in mirror.episodes.values())


async def test_clearing_a_badge_reaches_the_client(session, client):
    """feed_state writes never touched Feed.updated_at, so this never entered the delta."""
    feed = await _feed(session, "https://a.example/f.xml", "Alpha", episodes=3)
    session.add(
        FeedState(
            user_id=1, feed_id=feed.id, last_seen_at=datetime.now(UTC) - timedelta(days=9)
        )
    )
    await session.commit()

    mirror = Mirror()
    await sync(client, mirror)
    assert mirror.feeds[feed.id]["new_episode_count"] == 3

    assert (await client.post(f"/api/feeds/{feed.id}/seen")).status_code == 200
    await sync(client, mirror)

    assert mirror.feeds[feed.id]["new_episode_count"] == 0, "badge went stale on the client"


async def test_playing_an_episode_reaches_the_client(session, client):
    feed = await _feed(session, "https://a.example/f.xml", "Alpha", episodes=3)
    mirror = Mirror()
    await sync(client, mirror)
    target = next(iter(mirror.episodes))
    assert mirror.episodes[target]["played"] is False

    await client.put(f"/api/episodes/{target}/state", json={"played": True, "position_seconds": 91})
    payload = await sync(client, mirror)

    assert [e["id"] for e in payload["episodes"]] == [target]
    assert mirror.episodes[target]["played"] is True
    assert mirror.episodes[target]["position_seconds"] == 91


async def test_queue_changes_reach_the_client(session, client):
    feed = await _feed(session, "https://a.example/f.xml", "Alpha", episodes=3)
    mirror = Mirror()
    await sync(client, mirror)
    first, second = sorted(mirror.episodes)[:2]

    await client.post("/api/queue", json={"episode_id": first})
    await client.post("/api/queue", json={"episode_id": second})
    await sync(client, mirror)
    assert mirror.queue == [first, second]

    await client.delete(f"/api/queue/{first}")
    await sync(client, mirror)
    assert mirror.queue == [second], "a removal has to reach the client too"


async def test_a_backfill_larger_than_a_page_arrives_complete(session, client):
    """The truncation case, driven the way a client drains it."""
    await _feed(session, "https://a.example/f.xml", "Alpha", episodes=25)

    mirror = Mirror()
    await sync(client, mirror, limit=4)

    assert len(mirror.episodes) == 25
    assert (await sync(client, mirror, limit=4))["episodes"] == []


async def test_offline_edits_flush_without_loss(session, client):
    """What the iOS client will actually do: queue writes offline, then flush and re-sync."""
    feed = await _feed(session, "https://a.example/f.xml", "Alpha", episodes=5)
    mirror = Mirror()
    await sync(client, mirror)

    offline = sorted(mirror.episodes)[:3]
    for index, episode_id in enumerate(offline):
        await client.put(
            f"/api/episodes/{episode_id}/state",
            json={"played": True, "position_seconds": 10 * (index + 1)},
        )

    payload = await sync(client, mirror)

    assert sorted(e["id"] for e in payload["episodes"]) == sorted(offline)
    for index, episode_id in enumerate(offline):
        assert mirror.episodes[episode_id]["played"] is True
        assert mirror.episodes[episode_id]["position_seconds"] == 10 * (index + 1)


async def test_the_mirror_matches_the_server_after_a_mixed_round(session, client, user):
    """One round with every kind of change at once, then compare against /api/episodes."""
    alpha = await _feed(session, "https://a.example/f.xml", "Alpha", episodes=4)
    beta = await _feed(session, "https://b.example/f.xml", "Beta", episodes=3)

    mirror = Mirror()
    await sync(client, mirror)

    played = sorted(mirror.episodes)[0]
    starred = sorted(mirror.episodes)[1]
    queued = sorted(mirror.episodes)[2]
    await client.put(f"/api/episodes/{played}/state", json={"played": True})
    await client.put(f"/api/episodes/{starred}/state", json={"starred": True})
    await client.post("/api/queue", json={"episode_id": queued})
    await client.delete(f"/api/feeds/{beta.id}")
    await _feed(session, "https://c.example/f.xml", "Gamma", episodes=2)

    await sync(client, mirror)

    listed = (await client.get("/api/episodes", params={"limit": 200})).json()["items"]
    assert set(mirror.episodes) == {e["id"] for e in listed}
    assert set(mirror.feeds) == {alpha.id} | {
        f["id"] for f in (await client.get("/api/feeds")).json() if f["id"] != alpha.id
    }
    assert mirror.episodes[played]["played"] is True
    assert mirror.episodes[starred]["starred"] is True
    assert mirror.queue == [queued]
