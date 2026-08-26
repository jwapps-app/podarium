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
from podarium.config import get_settings
from podarium.models import DownloadJob, Episode, Feed, JobSource, JobState, QueueItem


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


async def test_lowering_the_global_does_not_cancel_in_flight_jobs(session, client):
    """Trimming removes files that are already on disk; it does not abort work in progress.

    A job that is mid-download will finish and then be trimmed on the next pass if it is
    still outside the window, which is simpler than tearing down a partial transfer.
    """
    feed = await session.get(Feed, client.feed_id)
    feed.auto_download_count = None
    await session.commit()
    await _set_global(client, 3)
    assert len(await _jobs(session)) == 3

    await _set_global(client, 1)

    assert len(await _jobs(session)) == 3


async def test_an_inactive_feed_is_left_alone(session, client):
    feed = await session.get(Feed, client.feed_id)
    feed.auto_download_count = None
    feed.active = False
    await session.commit()

    await _set_global(client, 3)

    assert await _jobs(session) == [], "an unsubscribed feed must not start downloading"


# --- trimming -----------------------------------------------------------------
#
# The setting describes a target, not a floor. "Keep the 3 newest" means three: without a
# removal half, lowering the number would silently do nothing and every new episode would
# grow the directory forever.


async def _download(session, episode: Episode, *, source=JobSource.auto) -> Episode:
    """Put an episode on disk as though the worker had fetched it."""
    path = get_settings().download_dir / str(episode.feed_id) / f"{episode.id}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    episode.local_path = str(path)
    episode.local_bytes = 5
    episode.downloaded_at = datetime.now(UTC)
    session.add(
        DownloadJob(
            episode_id=episode.id,
            source=source,
            state=JobState.done,
            next_attempt_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return episode


async def _on_disk(session, feed_id: int) -> set[int]:
    return set(
        (
            await session.execute(
                select(Episode.id)
                .where(Episode.feed_id == feed_id)
                .where(Episode.local_path.is_not(None))
            )
        ).scalars()
    )


async def _newest(session, feed_id: int, n: int) -> list[Episode]:
    return list(
        (
            await session.execute(
                select(Episode)
                .where(Episode.feed_id == feed_id)
                .order_by(Episode.published_at.desc(), Episode.id.desc())
                .limit(n)
            )
        ).scalars()
    )


async def test_lowering_the_count_removes_the_excess(session, client):
    for episode in await _newest(session, client.feed_id, 5):
        await _download(session, episode)
    assert len(await _on_disk(session, client.feed_id)) == 5

    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 2})

    remaining = await _on_disk(session, client.feed_id)
    assert len(remaining) == 2
    assert remaining == {e.id for e in await _newest(session, client.feed_id, 2)}


async def test_setting_it_to_zero_reclaims_everything(session, client):
    for episode in await _newest(session, client.feed_id, 4):
        await _download(session, episode)

    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 0})

    assert await _on_disk(session, client.feed_id) == set()


async def test_trimming_keeps_the_row_and_marks_it_purged(session, client):
    """Same contract as retention: the file goes, the row and its GUID stay."""
    episodes = await _newest(session, client.feed_id, 3)
    for episode in episodes:
        await _download(session, episode)
    oldest = episodes[-1]

    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 1})

    await session.refresh(oldest)
    assert oldest.local_path is None
    assert oldest.purged_at is not None
    assert oldest.guid is not None


async def test_a_queued_episode_is_never_trimmed(session, client, user):
    episodes = await _newest(session, client.feed_id, 3)
    for episode in episodes:
        await _download(session, episode)
    protected = episodes[-1]
    session.add(QueueItem(user_id=user.id, episode_id=protected.id, position=0))
    await session.commit()

    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 1})

    assert protected.id in await _on_disk(session, client.feed_id)


async def test_a_manual_download_is_never_trimmed(session, client):
    """Auto-download manages its own window. What you asked for yourself is not its to take."""
    episodes = await _newest(session, client.feed_id, 3)
    for episode in episodes[:2]:
        await _download(session, episode)
    manual = episodes[-1]
    await _download(session, manual, source=JobSource.manual)

    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 1})

    assert manual.id in await _on_disk(session, client.feed_id)


async def test_keep_forever_vetoes_trimming(session, client):
    """"Never delete" is an explicit instruction and outranks the window."""
    for episode in await _newest(session, client.feed_id, 4):
        await _download(session, episode)

    await client.patch(
        f"/api/feeds/{client.feed_id}",
        json={"auto_download_count": 1, "retention_mode": "never"},
    )

    assert len(await _on_disk(session, client.feed_id)) == 4


async def test_a_new_episode_pushes_the_oldest_out_of_the_window(session, client):
    """The window has to stay true as episodes arrive, not just when the setting changes."""
    from podarium.jobs.refresh import apply_auto_download_window

    episodes = await _newest(session, client.feed_id, 2)
    await client.patch(f"/api/feeds/{client.feed_id}", json={"auto_download_count": 2})
    for episode in episodes:
        await _download(session, episode)

    arrival = Episode(
        feed_id=client.feed_id,
        guid="brand-new",
        title="Brand new",
        published_at=datetime.now(UTC),
        enclosure_url="https://cdn.example.com/new.mp3",
    )
    session.add(arrival)
    await session.commit()

    feed = await session.get(Feed, client.feed_id)
    # The PATCH above wrote through the request's own session, so this one still holds the
    # fixture's value for the feed.
    await session.refresh(feed)
    await apply_auto_download_window(session, feed)
    await session.commit()

    on_disk = await _on_disk(session, client.feed_id)
    assert episodes[-1].id not in on_disk, "the oldest should fall out of a full window"
    assert len(on_disk) == 1
