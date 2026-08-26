"""Turning auto-download on has to act immediately.

The setting is otherwise only consulted during a refresh, so a feed fetched a minute ago
would sit with the value saved and nothing on disk until the next scheduled pass -- up to a
full refresh interval later. From the outside that is indistinguishable from the setting
being broken.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from podarium.auth import current_user
from podarium.main import app
from podarium.models import DownloadJob, Episode, Feed, JobSource


@pytest.fixture
async def client(session, user):
    feed = Feed(feed_url="https://example.com/feed.xml", title="Show", auto_download_count=0)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    base = datetime.now(UTC) - timedelta(days=5)
    for index in range(5):
        session.add(
            Episode(
                feed_id=feed.id,
                guid=f"ep-{index}",
                title=f"Episode {index}",
                published_at=base + timedelta(days=index),
                enclosure_url=f"https://cdn.example.com/{index}.mp3",
                enclosure_type="audio/mpeg",
            )
        )
    await session.commit()

    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.feed_id = feed.id
        yield c
    app.dependency_overrides.clear()


async def _jobs(session) -> list[DownloadJob]:
    return list((await session.execute(select(DownloadJob))).scalars())


async def test_turning_auto_download_on_enqueues_immediately(session, client):
    assert await _jobs(session) == []

    response = await client.patch(
        f"/api/feeds/{client.feed_id}", json={"auto_download_count": 2}
    )
    assert response.status_code == 200

    jobs = await _jobs(session)
    assert len(jobs) == 2, "the newest N should be queued as soon as the setting is saved"
    assert all(job.source is JobSource.auto for job in jobs)


async def test_it_queues_the_newest_episodes(session, client):
    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 2})

    queued = {job.episode_id for job in await _jobs(session)}
    newest = (
        await session.execute(
            select(Episode.id).order_by(Episode.published_at.desc()).limit(2)
        )
    ).scalars().all()

    assert queued == set(newest)


async def test_setting_it_to_zero_queues_nothing(session, client):
    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 0})
    assert await _jobs(session) == []


async def test_saving_other_settings_does_not_enqueue(session, client):
    """Editing retention on a queue-only feed must not start downloading it."""
    await client.patch(f"/api/feeds/{client.feed_id}", json={"retention_days": 14})
    assert await _jobs(session) == []


async def test_repeated_saves_do_not_pile_up_jobs(session, client):
    for _ in range(3):
        await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 2})

    assert len(await _jobs(session)) == 2, "enqueueing is idempotent"


async def test_already_downloaded_episodes_are_not_requeued(session, client):
    episode = (
        await session.execute(select(Episode).order_by(Episode.published_at.desc()).limit(1))
    ).scalar_one()
    episode.local_path = "/somewhere/on/disk.mp3"
    await session.commit()

    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 2})

    queued = {job.episode_id for job in await _jobs(session)}
    assert episode.id not in queued


async def test_purged_episodes_are_not_resurrected(session, client):
    """Retention deleted this deliberately; turning auto-download on must not undo that."""
    episode = (
        await session.execute(select(Episode).order_by(Episode.published_at.desc()).limit(1))
    ).scalar_one()
    episode.purged_at = datetime.now(UTC)
    await session.commit()

    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 3})

    queued = {job.episode_id for job in await _jobs(session)}
    assert episode.id not in queued


# --- global default -----------------------------------------------------------
#
# Same shape as retention: NULL on the feed inherits the global, a value overrides it.
# That distinction is why the column had to become nullable -- an explicit 0 ("never
# pre-download this show") and "follow the global" are different intentions, and before
# this they were the same value.


async def _set_global(client, count: int):
    response = await client.put("/api/settings", json={"global_auto_download_count": count})
    assert response.status_code == 200
    return response.json()


async def test_a_feed_with_no_override_follows_the_global(session, client):
    feed = await session.get(Feed, client.feed_id)
    feed.auto_download_count = None
    await session.commit()

    await _set_global(client, 2)

    jobs = await _jobs(session)
    assert len(jobs) == 2, "raising the global must reach inheriting feeds immediately"


async def test_an_explicit_zero_is_not_the_same_as_inheriting(session, client):
    """The distinction the nullable column exists for."""
    feed = await session.get(Feed, client.feed_id)
    feed.auto_download_count = 0
    await session.commit()

    await _set_global(client, 3)

    assert await _jobs(session) == [], "an explicit 0 must override the global, not follow it"


async def test_a_feed_override_beats_the_global(session, client):
    await _set_global(client, 5)
    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 1})

    jobs = await _jobs(session)
    assert len(jobs) == 1


async def test_clearing_the_override_returns_the_feed_to_the_global(session, client):
    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 1})
    assert len(await _jobs(session)) == 1

    await _set_global(client, 3)
    response = await client.patch(
        f"/api/feeds/{client.feed_id}", json={"clear_auto_download_count": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["auto_download_count"] is None
    assert body["effective_auto_download_count"] == 3
    assert len(await _jobs(session)) == 3


async def test_the_feed_reports_what_is_actually_applied(session, client):
    """A client should not have to fetch settings to show the effective value."""
    feed = await session.get(Feed, client.feed_id)
    feed.auto_download_count = None
    await session.commit()
    await _set_global(client, 4)

    body = (await client.get(f"/api/feeds/{client.feed_id}")).json()
    assert body["auto_download_count"] is None
    assert body["effective_auto_download_count"] == 4


async def test_lowering_the_global_does_not_delete_anything(session, client):
    """Retention decides what leaves the disk, not this setting."""
    feed = await session.get(Feed, client.feed_id)
    feed.auto_download_count = None
    await session.commit()
    await _set_global(client, 3)
    assert len(await _jobs(session)) == 3

    await _set_global(client, 1)

    assert len(await _jobs(session)) == 3, "already-queued downloads must not be cancelled"


async def test_an_inactive_feed_is_left_alone(session, client):
    feed = await session.get(Feed, client.feed_id)
    feed.auto_download_count = None
    feed.active = False
    await session.commit()

    await _set_global(client, 3)

    assert await _jobs(session) == [], "an unsubscribed feed must not start downloading"
