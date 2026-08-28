"""Trimming silence and levelling loudness, on the server rather than the phone.

Every commercial app does this on-device, re-analysing the same audio on every playback.
This server holds the file, so it does the work once and every client gets a file that
needs nothing done to it.
"""

from pathlib import Path

import pytest

from podarium.jobs.audio import (
    MIN_PLAUSIBLE_RATIO,
    PROCESSED_SUFFIX,
    _filters,
    processed_path_for,
    wanted,
)
from podarium.models import AppSettings, Feed


class TestSettingsResolution:
    """NULL on the feed inherits the global, the shape every other feed setting uses."""

    def test_a_show_inherits_when_it_says_nothing(self):
        feed = Feed(feed_url="x")
        globals_on = AppSettings(id=1, global_trim_silence=True, global_normalize_audio=True)

        assert wanted(feed, globals_on) == (True, True)

    def test_a_show_can_override_in_both_directions(self):
        globals_on = AppSettings(id=1, global_trim_silence=True, global_normalize_audio=True)
        off = Feed(feed_url="x", trim_silence=False, normalize_audio=False)
        assert wanted(off, globals_on) == (False, False)

        globals_off = AppSettings(id=1, global_trim_silence=False, global_normalize_audio=False)
        on = Feed(feed_url="x", trim_silence=True, normalize_audio=True)
        assert wanted(on, globals_off) == (True, True)

    def test_the_two_settings_are_independent(self):
        settings = AppSettings(id=1, global_trim_silence=False, global_normalize_audio=False)
        feed = Feed(feed_url="x", trim_silence=True)

        assert wanted(feed, settings) == (True, False)


class TestFilterChain:
    def test_silence_removal_comes_before_levelling(self):
        """Measuring loudness before removing a third of the runtime targets the wrong
        thing -- the dead air is part of what gets measured."""
        chain = _filters(trim=True, normalize=True)

        assert chain.index("silenceremove") < chain.index("loudnorm")

    def test_each_filter_appears_only_when_asked_for(self):
        assert "loudnorm" not in _filters(trim=True, normalize=False)
        assert "silenceremove" not in _filters(trim=False, normalize=True)
        assert _filters(trim=False, normalize=False) == ""


class TestProcessedFile:
    def test_it_sits_beside_the_original(self):
        assert processed_path_for(Path("/downloads/5/12.mp3")).parent == Path("/downloads/5")

    def test_it_is_named_for_what_it_is_not_what_the_source_was(self):
        """Everything is re-encoded to MP3, so an m4a source must not keep its extension --
        the file would be MP3 data wearing the wrong name, and served with the wrong type."""
        assert processed_path_for(Path("/d/12.m4a")).name == f"12{PROCESSED_SUFFIX}"
        assert processed_path_for(Path("/d/12.mp3")).name == f"12{PROCESSED_SUFFIX}"

    def test_the_plausibility_floor_leaves_room_for_a_real_re_encode(self):
        """It exists to catch encoding the wrong stream -- podcast MP3s embed cover art, and
        ffmpeg left to itself will happily encode the artwork instead of the audio, in about
        a second. That output is valid audio, just the wrong audio, so only size catches it.
        A legitimate trim plus re-encode lands far above this."""
        assert 0 < MIN_PLAUSIBLE_RATIO < 0.5


class TestReconciliation:
    """Processing at download time alone fails in both directions.

    Turning trimming on left everything already on disk untouched, so the setting appeared
    to do nothing until the next download. Turning it off left the trimmed copies in place
    *and still being served*, since the stream endpoint prefers them -- so you would go on
    hearing processed audio after switching it off.
    """

    @pytest.fixture
    async def library(self, session, tmp_media_root):
        from podarium.models import Episode, Feed

        feed = Feed(feed_url="https://example.com/f.xml", title="Show")
        session.add(feed)
        await session.commit()
        await session.refresh(feed)

        audio = tmp_media_root / "downloads" / str(feed.id)
        audio.mkdir(parents=True, exist_ok=True)
        path = audio / "1.mp3"
        path.write_bytes(b"not really audio")

        episode = Episode(
            feed_id=feed.id, guid="ep-1", title="One", local_path=str(path), local_bytes=16
        )
        session.add(episode)
        await session.commit()
        await session.refresh(episode)
        return feed, episode, path

    async def test_a_processed_copy_is_removed_when_the_setting_goes_off(
        self, session, library
    ):
        from podarium.jobs.audio import reconcile_processing

        feed, episode, path = library
        processed = path.with_suffix(".processed.mp3")
        processed.write_bytes(b"trimmed")
        episode.processed_path = str(processed)
        episode.processed_bytes = 7
        feed.trim_silence = False
        await session.commit()

        await reconcile_processing(session)
        await session.refresh(episode)

        assert episode.processed_path is None
        assert not processed.exists(), "still on disk, and the stream endpoint prefers it"

    async def test_nothing_is_removed_while_the_setting_is_on(self, session, library):
        from podarium.jobs.audio import reconcile_processing

        feed, episode, path = library
        processed = path.with_suffix(".processed.mp3")
        processed.write_bytes(b"trimmed")
        episode.processed_path = str(processed)
        feed.trim_silence = True
        await session.commit()

        await reconcile_processing(session)
        await session.refresh(episode)

        assert episode.processed_path == str(processed)
        assert processed.exists()

    async def test_an_episode_with_no_file_is_left_alone(self, session, library):
        """Purged episodes keep their row; there is nothing on disk to reconcile."""
        from podarium.jobs.audio import reconcile_processing

        feed, episode, _ = library
        episode.local_path = None
        feed.trim_silence = True
        await session.commit()

        assert await reconcile_processing(session) == 0


class TestDurationBackfill:
    """Episodes trimmed before durations were recorded still need measuring.

    Without it they would never contribute to what trimming saved -- and since they are the
    ones already on disk, that is exactly the audio you have been listening to.
    """

    @pytest.fixture
    async def processed_without_durations(self, session, tmp_media_root):
        from podarium.models import Episode, Feed

        feed = Feed(feed_url="https://example.com/m.xml", title="Show", trim_silence=True)
        session.add(feed)
        await session.commit()
        await session.refresh(feed)

        directory = tmp_media_root / "downloads" / str(feed.id)
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "1.mp3"
        target = directory / "1.processed.mp3"
        source.write_bytes(b"original")
        target.write_bytes(b"trimmed")

        episode = Episode(
            feed_id=feed.id,
            guid="ep-1",
            title="One",
            local_path=str(source),
            local_bytes=8,
            processed_path=str(target),
            processed_bytes=7,
            # The state an episode processed by the earlier build is left in.
            source_duration_seconds=None,
            processed_duration_seconds=None,
        )
        session.add(episode)
        await session.commit()
        await session.refresh(episode)
        return episode

    async def test_it_attempts_to_measure_them(self, session, processed_without_durations, monkeypatch):
        from podarium.jobs import audio

        seen: list[str] = []

        async def fake_measure(path):
            seen.append(path.name)
            return 100.0 if "processed" not in path.name else 90.0

        monkeypatch.setattr(audio, "measure_duration", fake_measure)

        await audio.reconcile_processing(session)
        await session.refresh(processed_without_durations)

        assert sorted(seen) == ["1.mp3", "1.processed.mp3"], "both sides are needed"
        assert processed_without_durations.source_duration_seconds == 100.0
        assert processed_without_durations.processed_duration_seconds == 90.0

    async def test_an_episode_already_measured_is_left_alone(
        self, session, processed_without_durations, monkeypatch
    ):
        from podarium.jobs import audio

        processed_without_durations.source_duration_seconds = 100.0
        processed_without_durations.processed_duration_seconds = 90.0
        await session.commit()

        async def fail(path):
            raise AssertionError("should not re-measure")

        monkeypatch.setattr(audio, "measure_duration", fail)

        await audio.reconcile_processing(session)

    async def test_a_missing_file_is_skipped_rather_than_measured(
        self, session, processed_without_durations, monkeypatch
    ):
        """Retention purges files and keeps rows; there is nothing to probe."""
        from podarium.jobs import audio

        Path(processed_without_durations.processed_path).unlink()

        async def fail(path):
            raise AssertionError("should not probe a missing file")

        monkeypatch.setattr(audio, "measure_duration", fail)

        await audio.reconcile_processing(session)


class TestPositionRescaling:
    """A saved position has to move when the file it refers to gets shorter.

    An episode is listenable as soon as it downloads, so someone can be part way through
    when trimming finishes. Left alone, a position recorded against the original points
    further through the trimmed content -- minutes skipped silently.
    """

    async def test_a_position_moves_with_the_trim(self, session, user):
        from podarium.jobs.audio import _rescale_positions
        from podarium.models import Episode, EpisodeState, Feed

        feed = Feed(feed_url="https://example.com/r.xml", title="Show")
        session.add(feed)
        await session.commit()
        await session.refresh(feed)

        # An hour that became fifty minutes: everything shifts to 5/6 of where it was.
        episode = Episode(
            feed_id=feed.id,
            guid="r-1",
            title="One",
            source_duration_seconds=3600.0,
            processed_duration_seconds=3000.0,
        )
        session.add(episode)
        await session.commit()
        await session.refresh(episode)

        session.add(
            EpisodeState(user_id=user.id, episode_id=episode.id, position_seconds=1200)
        )
        await session.commit()

        await _rescale_positions(session, episode)

        state = await session.get(
            EpisodeState, {"user_id": user.id, "episode_id": episode.id}
        )
        await session.refresh(state)
        assert state.position_seconds == 1000

    async def test_an_untouched_episode_is_left_alone(self, session, user):
        """Nothing was removed, so nothing should move."""
        from podarium.jobs.audio import _rescale_positions
        from podarium.models import Episode, EpisodeState, Feed

        feed = Feed(feed_url="https://example.com/s.xml", title="Show")
        session.add(feed)
        await session.commit()
        await session.refresh(feed)
        episode = Episode(
            feed_id=feed.id,
            guid="s-1",
            title="One",
            source_duration_seconds=3600.0,
            processed_duration_seconds=3600.0,
        )
        session.add(episode)
        await session.commit()
        await session.refresh(episode)
        session.add(
            EpisodeState(user_id=user.id, episode_id=episode.id, position_seconds=1200)
        )
        await session.commit()

        await _rescale_positions(session, episode)

        state = await session.get(
            EpisodeState, {"user_id": user.id, "episode_id": episode.id}
        )
        await session.refresh(state)
        assert state.position_seconds == 1200

    async def test_it_does_nothing_without_both_measurements(self, session, user):
        from podarium.jobs.audio import _rescale_positions
        from podarium.models import Episode, EpisodeState, Feed

        feed = Feed(feed_url="https://example.com/t2.xml", title="Show")
        session.add(feed)
        await session.commit()
        await session.refresh(feed)
        episode = Episode(feed_id=feed.id, guid="t2-1", title="One")
        session.add(episode)
        await session.commit()
        await session.refresh(episode)
        session.add(
            EpisodeState(user_id=user.id, episode_id=episode.id, position_seconds=900)
        )
        await session.commit()

        await _rescale_positions(session, episode)

        state = await session.get(
            EpisodeState, {"user_id": user.id, "episode_id": episode.id}
        )
        await session.refresh(state)
        assert state.position_seconds == 900
