"""The storage report is what makes the download ceiling settable.

The number that matters is not the total but the reclaimable share. Starred and queued
episodes are exempt from retention and from the ceiling, so a ceiling set below what they
occupy is a ceiling that can never be met -- the sweep would run forever with nothing it is
allowed to delete. The report separates the two so that ceiling can be chosen from
measurement.
"""

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import AppSettings, Episode, EpisodeState, Feed, QueueItem, User


@pytest.fixture
async def library(session, user):
    """Two shows. On disk: one plain, one starred, one queued, plus one never downloaded."""
    show = Feed(feed_url="https://example.com/a.xml", title="Show A")
    other = Feed(feed_url="https://example.com/b.xml", title="Show B")
    session.add_all([show, other])
    await session.commit()
    await session.refresh(show)
    await session.refresh(other)

    def episode(feed, guid, local_bytes, downloaded=True):
        return Episode(
            feed_id=feed.id,
            guid=guid,
            title=guid,
            local_path=f"/downloads/{feed.id}/{guid}.mp3" if downloaded else None,
            local_bytes=local_bytes if downloaded else None,
        )

    plain = episode(show, "plain", 100)
    starred = episode(show, "starred", 200)
    queued = episode(other, "queued", 400)
    absent = episode(other, "absent", 800, downloaded=False)
    session.add_all([plain, starred, queued, absent])
    await session.commit()
    for row in (plain, starred, queued, absent):
        await session.refresh(row)

    session.add(EpisodeState(user_id=user.id, episode_id=starred.id, starred=True))
    session.add(QueueItem(user_id=user.id, episode_id=queued.id, position=1))
    await session.commit()

    return {"show": show, "other": other, "plain": plain, "starred": starred, "queued": queued}


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_counts_only_what_is_on_disk(client, library):
    """An episode with no local_path occupies nothing, whatever its enclosure claimed."""
    body = (await client.get("/api/storage")).json()

    assert body["total_bytes"] == 700
    assert body["episodes"] == 3


async def test_starred_and_queued_are_reported_as_protected(client, library):
    """Both are exempt from retention and the ceiling, so both count against headroom."""
    body = (await client.get("/api/storage")).json()

    assert body["protected_bytes"] == 600
    assert body["protected_episodes"] == 2
    assert body["reclaimable_bytes"] == 100
    assert body["protected_bytes"] + body["reclaimable_bytes"] == body["total_bytes"]


async def test_breakdown_is_per_feed_largest_first(client, library):
    """Sorted by size because the point is finding what to trim."""
    body = (await client.get("/api/storage")).json()

    assert [(f["title"], f["bytes"], f["episodes"]) for f in body["feeds"]] == [
        ("Show B", 400, 1),
        ("Show A", 300, 2),
    ]


async def test_another_users_stars_protect_the_shared_disk(client, session, library):
    """The sweep will not delete what anyone starred, so the report must not call it free.

    Protection here is deliberately global rather than per-user. The disk and the ceiling
    are shared; reporting a stranger's starred episode as reclaimable would promise space
    retention is going to refuse to take back.
    """
    stranger = User(username="stranger", password_hash="x")
    session.add(stranger)
    await session.commit()
    await session.refresh(stranger)
    # The one episode nothing of mine protects.
    session.add(EpisodeState(user_id=stranger.id, episode_id=library["plain"].id, starred=True))
    await session.commit()

    body = (await client.get("/api/storage")).json()

    assert body["reclaimable_bytes"] == 0
    assert body["protected_bytes"] == 700


async def test_reports_the_ceiling_it_is_measured_against(client, session, library):
    settings_row = await session.get(AppSettings, 1)
    if settings_row is None:
        settings_row = AppSettings(id=1)
        session.add(settings_row)
    settings_row.download_dir_max_bytes = 5000
    await session.commit()

    body = (await client.get("/api/storage")).json()

    assert body["ceiling_bytes"] == 5000
