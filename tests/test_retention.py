"""Retention removes files, never rows -- and a purged episode must stay purged."""

from datetime import UTC, datetime, timedelta

import httpx
import respx
from sqlalchemy import select

from podarium.jobs.refresh import refresh_feed
from podarium.jobs.retention import sweep
from podarium.models import (
    AppSettings,
    Episode,
    EpisodeState,
    Feed,
    QueueItem,
    RetentionMode,
)
from podarium.services import get_app_settings
from tests.feeds import build_feed

FEED_URL = "https://example.com/feed.xml"
FEED_XML = build_feed(
    items=[{"guid": "ep-1", "title": "Episode 1", "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT"}]
)


async def _downloaded_episode(session, tmp_path_factory=None, *, size=1000) -> tuple[Feed, Episode]:
    feed = Feed(feed_url=FEED_URL)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    settings = __import__("podarium.config", fromlist=["get_settings"]).get_settings()
    path = settings.download_dir / str(feed.id) / "1.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)

    episode = Episode(
        feed_id=feed.id,
        guid="ep-1",
        title="Episode 1",
        enclosure_url="https://cdn.example.com/ep-1.mp3",
        local_path=str(path),
        local_bytes=size,
        downloaded_at=datetime.now(UTC) - timedelta(days=30),
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode)
    return feed, episode


async def test_purge_keeps_the_row_and_the_played_state(session, user):
    feed, episode = await _downloaded_episode(session)
    settings_row = await get_app_settings(session)
    settings_row.global_retention_mode = RetentionMode.after_played
    settings_row.global_retention_days = 7
    session.add(
        EpisodeState(
            user_id=user.id,
            episode_id=episode.id,
            played=True,
            position_seconds=123,
            completed_at=datetime.now(UTC) - timedelta(days=30),
        )
    )
    await session.commit()

    assert await sweep(session) == 1

    await session.refresh(episode)
    assert episode.local_path is None
    assert episode.purged_at is not None
    assert episode.guid == "ep-1", "the row and its dedup key must survive"

    state = await session.get(EpisodeState, {"user_id": user.id, "episode_id": episode.id})
    assert state.played is True
    assert state.position_seconds == 123


@respx.mock
async def test_refresh_does_not_resurrect_a_purged_episode(session, user):
    """The failure this prevents: purge deletes the row, refresh re-adds it as new, and the
    episode the user deliberately removed downloads itself again, forever."""
    feed, episode = await _downloaded_episode(session)
    settings_row = await get_app_settings(session)
    settings_row.global_retention_mode = RetentionMode.after_download
    settings_row.global_retention_days = 7
    feed.auto_download_count = 5
    await session.commit()

    assert await sweep(session) == 1
    await session.refresh(episode)
    purged_at = episode.purged_at
    assert purged_at is not None

    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=FEED_XML))
    outcome = await refresh_feed(session, feed, user_agent="test")

    assert outcome.new_episodes == 0
    assert len((await session.execute(select(Episode))).scalars().all()) == 1

    await session.refresh(episode)
    assert episode.local_path is None
    assert episode.purged_at == purged_at

    # And auto-download must not undo the purge either.
    from podarium.models import DownloadJob

    jobs = (await session.execute(select(DownloadJob))).scalars().all()
    assert jobs == []


async def test_queued_episodes_are_never_purged(session, user):
    feed, episode = await _downloaded_episode(session)
    settings_row = await get_app_settings(session)
    settings_row.global_retention_mode = RetentionMode.after_download
    settings_row.global_retention_days = 1
    session.add(QueueItem(user_id=user.id, episode_id=episode.id, position=0))
    await session.commit()

    assert await sweep(session) == 0
    await session.refresh(episode)
    assert episode.local_path is not None


async def test_never_mode_keeps_everything(session, user):
    feed, episode = await _downloaded_episode(session)
    feed.retention_mode = RetentionMode.never
    settings_row = await get_app_settings(session)
    settings_row.global_retention_mode = RetentionMode.after_download
    settings_row.global_retention_days = 1
    await session.commit()

    assert await sweep(session) == 0
    await session.refresh(episode)
    assert episode.local_path is not None


async def test_unplayed_survives_after_played_mode(session, user):
    feed, episode = await _downloaded_episode(session)
    settings_row = await get_app_settings(session)
    settings_row.global_retention_mode = RetentionMode.after_played
    settings_row.global_retention_days = 1
    await session.commit()

    # Downloaded 30 days ago but never played: after_played must leave it alone.
    assert await sweep(session) == 0
    await session.refresh(episode)
    assert episode.local_path is not None


async def test_disk_ceiling_purges_played_first(session, user):
    settings_row = await get_app_settings(session)
    settings_row.global_retention_mode = RetentionMode.never
    settings_row.download_dir_max_bytes = 1500

    feed = Feed(feed_url=FEED_URL)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    from podarium.config import get_settings

    episodes = []
    for index in range(3):
        path = get_settings().download_dir / str(feed.id) / f"{index}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1000)
        episode = Episode(
            feed_id=feed.id,
            guid=f"ep-{index}",
            local_path=str(path),
            local_bytes=1000,
            downloaded_at=datetime.now(UTC) - timedelta(days=index),
        )
        session.add(episode)
        episodes.append(episode)
    await session.commit()

    # Mark the newest one played; it should go before the older unplayed ones.
    session.add(EpisodeState(user_id=user.id, episode_id=episodes[0].id, played=True))
    await session.commit()

    purged = await sweep(session)
    assert purged >= 2

    await session.refresh(episodes[0])
    assert episodes[0].local_path is None, "played episodes are purged first under the ceiling"

    remaining = sum(
        1
        for e in (await session.execute(select(Episode))).scalars()
        if e.local_path is not None
    )
    assert remaining * 1000 <= 1500
