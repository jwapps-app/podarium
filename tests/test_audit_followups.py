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


class TestAProcessedFileRemembersItsRecipe:
    async def test_a_changed_setting_rebuilds_the_file(self, session, monkeypatch):
        settings_row = await get_app_settings(session)
        settings_row.global_trim_silence = False
        settings_row.global_normalize_audio = True
        feed = await _feed(session)
        source = _file(feed, "1.mp3", 10)
        old = _file(feed, "1.processed.mp3", 9)
        episode = Episode(
            feed_id=feed.id, guid="ep-1", local_path=str(source), local_bytes=10,
            processed_path=str(old), processed_bytes=9, processed_recipe=audio.recipe(True, False),
            source_duration_seconds=10.0, processed_duration_seconds=9.0,
        )
        session.add(episode)
        await session.commit()

        attempted: list[int] = []

        async def record(session_, episode_, feed_, app_settings):
            attempted.append(episode_.id)
            return True

        monkeypatch.setattr(audio, "ffmpeg_available", lambda: True)
        monkeypatch.setattr(audio, "process_episode", record)
        monkeypatch.setattr(audio, "_failed_until", {})
        await audio.reconcile_processing(session)

        assert not old.exists(), "the trimmed file is not the levelled one that was asked for"
        assert attempted == [episode.id]

    def test_recipes_are_named(self):
        assert audio.recipe(True, False) == "trim2"
        assert audio.recipe(False, True) == "normalize"
        assert audio.recipe(True, True) == "trim2+normalize"
        assert audio.recipe(False, False) is None


class TestOneLiveJobPerEpisode:
    async def test_a_racing_enqueue_gets_the_other_job(self, session):
        """Two enqueues that both passed the existence check: the index refuses the
        second insert, and the caller gets the first job rather than a 500."""
        from podarium.db import get_sessionmaker

        feed = await _feed(session)
        episode = Episode(feed_id=feed.id, guid="ep-1", enclosure_url="https://cdn.example/1.mp3")
        session.add(episode)
        await session.commit()

        first = await enqueue_download(session, episode, JobSource.auto)
        await session.commit()

        async with get_sessionmaker()() as other:
            other_episode = await other.get(Episode, episode.id)
            # Bypass the check, as a transaction that read before the first committed would.
            other.add(
                DownloadJob(
                    episode_id=other_episode.id, source=JobSource.manual,
                    state=JobState.queued, next_attempt_at=datetime.now(UTC),
                )
            )
            with pytest.raises(Exception):
                await other.commit()

        assert first is not None


class TestArtworkIsAskedForAgain:
    async def test_a_week_old_cover_is_refetched_and_a_new_one_kept(self, session):
        import httpx
        import respx
        from sqlalchemy import select

        from podarium.jobs.artwork import REVALIDATE_AFTER, ensure_artwork
        from podarium.models import ArtworkCache

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        newer = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
        url = "https://cdn.example/cover.png"

        with respx.mock:
            route = respx.get(url).mock(return_value=httpx.Response(200, content=png, headers={"Content-Type": "image/png"}))
            entry = await ensure_artwork(session, url, user_agent="test")
            assert entry is not None and entry.local_path
            from pathlib import Path
            assert Path(entry.local_path).read_bytes() == png

            # Asked again straight away: nothing happens.
            await ensure_artwork(session, url, user_agent="test")
            assert route.call_count == 1

            # A week later the publisher has swapped the cover behind the same address.
            entry.fetched_at = datetime.now(UTC) - REVALIDATE_AFTER - timedelta(hours=1)
            await session.commit()
            route.mock(return_value=httpx.Response(200, content=newer, headers={"Content-Type": "image/png"}))
            entry = await ensure_artwork(session, url, user_agent="test")
            assert route.call_count == 2
            assert Path(entry.local_path).read_bytes() == newer

            # And a failed refetch keeps what it had.
            entry.fetched_at = datetime.now(UTC) - REVALIDATE_AFTER - timedelta(hours=1)
            await session.commit()
            route.mock(return_value=httpx.Response(503))
            entry = await ensure_artwork(session, url, user_agent="test")
            assert Path(entry.local_path).read_bytes() == newer
            assert (await session.execute(select(ArtworkCache))).scalar_one().local_path == entry.local_path
