"""Every stored second is on the original's clock; the API translates at its edges.

Trimming makes a second copy with a different clock, and the difference is not uniform:
it is however much silence sat before a given moment. Positions and bookmarks arrive
from whichever copy a player has, chapters come from the publisher on the original's
clock, and all of them are handed back on the clock of the copy served today.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from podarium import timeline
from podarium.auth import current_user
from podarium.jobs import audio
from podarium.jobs.audio import map_is_credible, parse_silences, removed_from_silences
from podarium.main import app
from podarium.models import Episode, EpisodeState, Feed

# Three cuts: 10s at 100, 5s at 300, 20s at 600 (original clock).
CUTS = [(100.0, 110.0), (300.0, 305.0), (600.0, 620.0)]


class TestTheMap:
    def test_before_any_cut_the_clocks_agree(self):
        assert timeline.to_processed(50, CUTS) == 50
        assert timeline.to_original(50, CUTS) == 50

    def test_after_a_cut_the_trimmed_clock_runs_behind_by_its_length(self):
        assert timeline.to_processed(200, CUTS) == 190
        assert timeline.to_processed(400, CUTS) == 385
        assert timeline.to_processed(1000, CUTS) == 965

    def test_the_two_directions_agree_outside_the_cuts(self):
        # Not 110, 305 or 620: the end of a cut and its start are the same trimmed moment.
        for seconds in (0, 99, 100, 111, 250, 306, 599, 621, 3600):
            trimmed = timeline.to_processed(seconds, CUTS)
            assert timeline.to_original(trimmed, CUTS) == pytest.approx(seconds)

    def test_a_moment_inside_a_cut_lands_where_the_cut_was(self):
        assert timeline.to_processed(105, CUTS) == 100
        assert timeline.to_original(100, CUTS) == 100

    def test_silences_become_cuts_less_the_kept_beat(self):
        stderr = (
            "[silencedetect @ 0x1] silence_start: 100\n"
            "[silencedetect @ 0x1] silence_end: 110 | silence_duration: 10\n"
            "[silencedetect @ 0x1] silence_start: 300.5\n"
            "[silencedetect @ 0x1] silence_end: 300.6 | silence_duration: 0.1\n"
        )
        silences = parse_silences(stderr)
        assert silences == [(100.0, 110.0), (300.5, 300.6)]
        assert removed_from_silences(silences) == [(100.87, 110.0)]

    def test_a_leading_silence_and_a_short_one_are_not_cuts(self):
        assert removed_from_silences([(0.0, 5.0), (50.0, 50.8), (60.0, 61.0)]) == [(60.87, 61.0)]

    def test_a_map_that_does_not_add_up_is_not_trusted(self):
        removed = [(100.0, 110.0)]
        assert map_is_credible(removed, 1000.0, 990.0)
        assert map_is_credible(removed, 1000.0, 991.5)
        assert not map_is_credible(removed, 1000.0, 900.0)
        assert not map_is_credible(removed, None, 990.0)


@pytest.fixture
async def trimmed(session, user):
    """An episode with a trimmed copy and a real map."""
    feed = Feed(feed_url="https://a.example/f.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    episode = Episode(
        feed_id=feed.id, guid="ep-1", title="One", duration_seconds=1000,
        local_path="/d/1.mp3", local_bytes=10,
        processed_path="/d/1.processed.mp3", processed_bytes=9, processed_recipe=audio.recipe(True, False),
        source_duration_seconds=1000.0, processed_duration_seconds=965.0,
        trim_map_json=json.dumps(CUTS), processed_at=datetime.now(UTC),
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode)
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.episode_id = episode.id
        yield client, episode
    app.dependency_overrides.clear()


class TestTheEdges:
    async def test_a_position_heard_on_the_trimmed_copy_is_stored_on_the_original_clock(self, session, trimmed):
        client, episode = trimmed
        response = await client.put(
            f"/api/episodes/{episode.id}/state",
            json={"position_seconds": 385, "audio_version": "p"},
        )
        assert response.status_code == 200
        assert response.json()["position_seconds"] == 385, "handed back on the served clock"
        assert response.json()["audio_version"] == "p"
        state = await session.get(EpisodeState, {"user_id": 1, "episode_id": episode.id})
        await session.refresh(state)
        assert state.position_seconds == 400, "stored on the original's"

    async def test_a_player_still_on_the_original_is_read_on_its_own_clock(self, session, trimmed):
        """The case this exists for: trimming finished under a player that kept the
        original, and its seconds used to be read as trimmed ones."""
        client, episode = trimmed
        await client.put(
            f"/api/episodes/{episode.id}/state",
            json={"position_seconds": 400, "audio_version": "o"},
        )
        state = await session.get(EpisodeState, {"user_id": 1, "episode_id": episode.id})
        await session.refresh(state)
        assert state.position_seconds == 400
        listing = (await client.get(f"/api/episodes/{episode.id}")).json()
        assert listing["position_seconds"] == 385, "and handed to the trimmed copy translated"

    async def test_no_version_means_the_copy_served_today(self, session, trimmed):
        client, episode = trimmed
        await client.put(f"/api/episodes/{episode.id}/state", json={"position_seconds": 385})
        state = await session.get(EpisodeState, {"user_id": 1, "episode_id": episode.id})
        await session.refresh(state)
        assert state.position_seconds == 400

    async def test_bookmarks_translate_both_ways(self, trimmed):
        client, episode = trimmed
        made = await client.post(
            "/api/bookmarks",
            json={"episode_id": episode.id, "position_seconds": 190, "audio_version": "p", "note": "here"},
        )
        assert made.status_code == 201
        assert made.json()["position_seconds"] == 190
        listed = (await client.get("/api/bookmarks", params={"episode_id": episode.id})).json()
        assert [b["position_seconds"] for b in listed] == [190]

    async def test_chapters_are_handed_out_on_the_served_clock(self, session, trimmed, monkeypatch):
        client, episode = trimmed
        from podarium.chapters import Chapter
        from podarium.api import episode_routes

        async def fake(session_, episode_, *, user_agent):
            return [Chapter(start_seconds=50.0, title="Intro", sponsor=False),
                    Chapter(start_seconds=400.0, title="Main", sponsor=False)]

        monkeypatch.setattr(episode_routes, "ensure_chapters", fake)
        chapters = (await client.get(f"/api/episodes/{episode.id}/chapters")).json()["chapters"]
        assert [c["start_seconds"] for c in chapters] == [50.0, 385.0]

    async def test_without_a_map_the_proportion_stands_in(self, session, trimmed):
        client, episode = trimmed
        episode.trim_map_json = None
        await session.commit()
        await client.put(f"/api/episodes/{episode.id}/state", json={"position_seconds": 965, "audio_version": "p"})
        state = await session.get(EpisodeState, {"user_id": 1, "episode_id": episode.id})
        await session.refresh(state)
        assert state.position_seconds == 1000
