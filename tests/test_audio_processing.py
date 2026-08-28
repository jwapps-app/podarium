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
