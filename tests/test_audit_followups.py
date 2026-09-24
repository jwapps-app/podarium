"""Regression tests for the storage, chapter, request-intent and processing findings."""

import json
import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from podarium.chapters import parse_chapters
from podarium.config import get_settings
from podarium.jobs import audio
from podarium.jobs.retention import sweep
from podarium.models import DownloadJob, Episode, Feed, JobSource, JobState, RetentionMode
from podarium.services import enqueue_download, get_app_settings


async def _feed(session, url="https://a.example/f.xml") -> Feed:
    feed = Feed(feed_url=url, title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


def _file(feed, name, size):
    path = get_settings().download_dir / str(feed.id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


class TestTheCeilingCountsBothCopies:
    async def test_a_processed_copy_counts_against_the_ceiling(self, session, user):
        settings_row = await get_app_settings(session)
        settings_row.global_retention_mode = RetentionMode.never
        settings_row.download_dir_max_bytes = 150
        feed = await _feed(session)
        original = _file(feed, "1.mp3", 100)
        processed = _file(feed, "1.processed.mp3", 100)
        session.add(
            Episode(
                feed_id=feed.id, guid="ep-1", local_path=str(original), local_bytes=100,
                processed_path=str(processed), processed_bytes=100,
                downloaded_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await session.commit()

        assert await sweep(session) == 1
        assert not original.exists() and not processed.exists()


class TestChapterFilesAreCheckedForShape:
    def test_a_number_where_the_list_should_be_is_no_chapters(self):
        assert parse_chapters(json.dumps({"chapters": 123})) == []
        assert parse_chapters(json.dumps({"chapters": "abc"})) == []

    def test_nonfinite_and_negative_starts_are_dropped(self):
        raw = '{"chapters": [{"startTime": NaN}, {"startTime": Infinity}, {"startTime": -5}, {"startTime": 12, "title": "ok"}]}'
        chapters = parse_chapters(raw)
        assert [c.start_seconds for c in chapters] == [12]
        assert all(math.isfinite(c.start_seconds) for c in chapters)


class TestARequestIsRecordedEvenWhenNothingIsFetched:
    async def test_asking_for_a_downloaded_episode_leaves_a_claim(self, session):
        feed = await _feed(session)
        path = _file(feed, "1.mp3", 10)
        episode = Episode(
            feed_id=feed.id, guid="ep-1", enclosure_url="https://cdn.example/1.mp3",
            local_path=str(path), local_bytes=10,
        )
        session.add(episode)
        await session.commit()

        assert await enqueue_download(session, episode, JobSource.manual) is None
        await session.commit()

        jobs = (await session.execute(select(DownloadJob))).scalars().all()
        assert [(j.source, j.state) for j in jobs] == [(JobSource.manual, JobState.done)]

    async def test_asking_by_hand_upgrades_an_automatic_job(self, session):
        feed = await _feed(session)
        episode = Episode(feed_id=feed.id, guid="ep-1", enclosure_url="https://cdn.example/1.mp3")
        session.add(episode)
        await session.commit()

        auto = await enqueue_download(session, episode, JobSource.auto)
        await session.commit()
        assert auto is not None and auto.source is JobSource.auto

        same = await enqueue_download(session, episode, JobSource.queue)
        assert same is auto
        assert auto.source is JobSource.queue, "the file is now theirs, not auto-download's"

    async def test_a_lost_file_is_fetched_again(self, session):
        feed = await _feed(session)
        episode = Episode(
            feed_id=feed.id, guid="ep-1", enclosure_url="https://cdn.example/1.mp3",
            local_path=str(get_settings().download_dir / "gone.mp3"), local_bytes=10,
        )
        session.add(episode)
        await session.commit()

        job = await enqueue_download(session, episode, JobSource.manual)

        assert job is not None and job.state is JobState.queued
        assert episode.local_path is None and episode.local_bytes is None


class TestAFailingEncodeDoesNotBlockTheBacklog:
    async def test_the_next_candidate_is_tried_on_the_next_pass(self, session, monkeypatch):
        settings_row = await get_app_settings(session)
        settings_row.global_trim_silence = True
        feed = await _feed(session)
        for n in (1, 2):
            path = _file(feed, f"{n}.mp3", 10)
            session.add(Episode(feed_id=feed.id, guid=f"ep-{n}", local_path=str(path), local_bytes=10))
        await session.commit()

        attempted: list[int] = []

        async def failing(session_, episode, feed_, app_settings):
            attempted.append(episode.id)
            return False

        monkeypatch.setattr(audio, "ffmpeg_available", lambda: True)
        monkeypatch.setattr(audio, "process_episode", failing)
        monkeypatch.setattr(audio, "_failed_until", {})

        for _ in range(3):
            await audio.reconcile_processing(session)

        assert len(set(attempted)) == 2, "both were tried, not the first one three times"
        assert len(attempted) == 2, "and neither was retried inside the backoff"
