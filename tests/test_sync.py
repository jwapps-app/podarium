"""Delta sync, and the paging that keeps a large first sync honest.

The failure this guards against is quiet: a client takes a truncated page, adopts the
server's `now` as its next `since`, and the episodes that did not fit are never mentioned
again. They are not late -- they are permanently invisible.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, EpisodeState, Feed, User

TOTAL_EPISODES = 25


@pytest.fixture
async def client(session, user):
    feed = Feed(feed_url="https://example.com/feed.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    base = datetime.now(UTC) - timedelta(days=10)
    for index in range(TOTAL_EPISODES):
        session.add(
            Episode(
                feed_id=feed.id,
                guid=f"ep-{index:03d}",
                title=f"Episode {index}",
                first_seen_at=base + timedelta(minutes=index),
            )
        )
    await session.commit()

    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.feed_id = feed.id
        yield c
    app.dependency_overrides.clear()


async def _drain(client, *, since=None, limit=10):
    """Page through a full sync the way a client is meant to, collecting every episode."""
    params = {"limit": limit}
    if since:
        params["since"] = since
    seen, pages, cursor = [], 0, None

    while True:
        page = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get("/api/sync", params=page)
        assert response.status_code == 200
        payload = response.json()
        seen.extend(payload["episodes"])
        pages += 1
        cursor = payload["next_cursor"]
        if not cursor:
            return seen, pages, payload
        assert pages < 50, "cursor is not advancing"


async def test_a_full_sync_returns_every_episode_across_pages(client):
    seen, pages, final = await _drain(client, limit=10)

    ids = [episode["id"] for episode in seen]
    assert len(ids) == TOTAL_EPISODES, "paging must not drop episodes"
    assert len(set(ids)) == TOTAL_EPISODES, "paging must not repeat episodes"
    assert pages == 3
    assert final["next_cursor"] is None


async def test_a_truncated_page_advertises_a_cursor(client):
    response = await client.get("/api/sync", params={"limit": 10})
    payload = response.json()

    assert len(payload["episodes"]) == 10
    assert payload["next_cursor"], "a truncated sync must say so, or the rest is lost"


async def test_a_complete_page_has_no_cursor(client):
    payload = (await client.get("/api/sync", params={"limit": 100})).json()

    assert len(payload["episodes"]) == TOTAL_EPISODES
    assert payload["next_cursor"] is None


async def test_feeds_and_queue_come_once_not_on_every_page(client):
    first = (await client.get("/api/sync", params={"limit": 10})).json()
    assert len(first["feeds"]) == 1

    second = (
        await client.get("/api/sync", params={"limit": 10, "cursor": first["next_cursor"]})
    ).json()
    assert second["feeds"] == []


async def test_cursor_from_a_current_sync_yields_nothing(client):
    full = (await client.get("/api/sync", params={"limit": 100})).json()

    delta = (await client.get("/api/sync", params={"since": full["now"]})).json()
    assert delta["episodes"] == []
    assert delta["next_cursor"] is None


async def test_a_state_only_change_appears_in_the_delta(session, client, user):
    """An episode row untouched since before `since`, whose state just changed.

    Ordering by the episode's own updated_at would sort this by a stale timestamp, and
    paging would step straight over it.
    """
    full = (await client.get("/api/sync", params={"limit": 100})).json()
    cursor_time = full["now"]
    target = full["episodes"][0]["id"]

    session.add(EpisodeState(user_id=user.id, episode_id=target, played=True))
    await session.commit()

    delta = (await client.get("/api/sync", params={"since": cursor_time})).json()

    assert [episode["id"] for episode in delta["episodes"]] == [target]
    assert delta["episodes"][0]["played"] is True


async def test_paging_survives_a_state_change_mid_run(session, client, user):
    """Marking an episode played moves it to the end of the ordering.

    Keyset paging tolerates that -- the row is re-sorted past the cursor and appears on a
    later page. What must not happen is an episode vanishing entirely.
    """
    first = (await client.get("/api/sync", params={"limit": 10})).json()
    assert first["next_cursor"]

    later = first["episodes"][-1]["id"]
    session.add(EpisodeState(user_id=user.id, episode_id=later, played=True))
    await session.commit()

    seen = list(first["episodes"])
    cursor = first["next_cursor"]
    for _ in range(10):
        page = (await client.get("/api/sync", params={"limit": 10, "cursor": cursor})).json()
        seen.extend(page["episodes"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    ids = {episode["id"] for episode in seen}
    assert len(ids) == TOTAL_EPISODES, "no episode may be skipped by a concurrent edit"


async def test_a_malformed_cursor_is_rejected(client):
    response = await client.get("/api/sync", params={"cursor": "not-a-cursor"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "http_400"


async def test_default_playback_rate_round_trips(client):
    """Stored server-side so the iOS client starts at the same speed as the web player."""
    assert (await client.get("/api/settings")).json()["default_playback_rate"] == 1.0

    updated = await client.put("/api/settings", json={"default_playback_rate": 1.1})
    assert updated.status_code == 200
    assert updated.json()["default_playback_rate"] == 1.1

    assert (await client.get("/api/settings")).json()["default_playback_rate"] == 1.1


async def test_playback_rate_is_bounded(client):
    """Outside this range playback is unintelligible or silently clamped by the platform."""
    for bad in [0, 0.1, 5, 100, -1]:
        response = await client.put("/api/settings", json={"default_playback_rate": bad})
        assert response.status_code == 422, f"{bad} should be rejected"


async def test_other_settings_survive_a_rate_change(client):
    await client.put("/api/settings", json={"global_retention_days": 21})
    await client.put("/api/settings", json={"default_playback_rate": 1.5})

    settings = (await client.get("/api/settings")).json()
    assert settings["global_retention_days"] == 21
    assert settings["default_playback_rate"] == 1.5


async def test_the_cursor_comes_from_the_database_clock(session, client):
    """`now` and `updated_at` must be stamped by the same clock.

    Every updated_at in the delta comes from Postgres. A cursor read from the API process's
    clock compares against a different one, and any drift breaks sync silently: running
    behind drops changes into a gap the client never revisits, running ahead re-sends the
    whole library on every call.
    """
    from sqlalchemy import func, select

    payload = (await client.get("/api/sync", params={"limit": 1})).json()
    reported = datetime.fromisoformat(payload["now"].replace("Z", "+00:00"))

    db_now = (await session.execute(select(func.now()))).scalar_one()

    # Same clock means the two readings are essentially the same instant, whatever the
    # host's clock happens to say.
    assert abs((db_now - reported).total_seconds()) < 2


async def test_bootstrap_says_so_when_it_skips(session, caplog):
    """A silent skip is a dead end: the server starts, serves a login page, and no
    credentials work, with nothing in the log saying why."""
    import logging

    from podarium.auth import bootstrap_user
    from podarium.config import Settings

    settings = Settings(podarium_username=None, podarium_password=None)

    with caplog.at_level(logging.WARNING):
        await bootstrap_user(session, settings)

    assert any("no account was created" in r.message for r in caplog.records)
