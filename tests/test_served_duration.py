"""The duration a client is told must describe the audio the server will actually send.

Trimming silence takes better than a tenth of the runtime off a conversational show. While
the API kept reporting the publisher's figure, every client was told an episode was longer
than the file it received -- and a player cannot then tell "this episode ended" from "this
stream was cut off", because both look like audio stopping before the duration said it
would. On iOS that surfaced as playback stopping near the end and the queue jumping on.
"""

from podarium.models import Episode
from podarium.schemas import audio_duration_seconds


def episode(**kwargs) -> Episode:
    return Episode(feed_id=1, guid="g", duration_seconds=9693, **kwargs)


class TestServedDuration:
    def test_the_processed_copy_wins_because_it_is_what_gets_served(self):
        # media_routes.stream_episode prefers the processed file, so its length is the
        # one the client will measure. 11.6% shorter is a real figure, not a contrived one.
        assert (
            audio_duration_seconds(
                episode(
                    local_path="/d/1.mp3",
                    source_duration_seconds=9693.0,
                    processed_path="/d/1.processed.mp3",
                    processed_duration_seconds=8568.0,
                )
            )
            == 8568
        )

    def test_the_measured_source_wins_over_the_feed_when_nothing_is_processed(self):
        assert (
            audio_duration_seconds(
                episode(local_path="/d/1.mp3", source_duration_seconds=9650.4)
            )
            == 9650
        )

    def test_the_feed_is_the_fallback_for_an_episode_we_have_not_measured(self):
        # Streamed straight from the publisher: no file here, so nothing to measure.
        assert audio_duration_seconds(episode()) == 9693

    def test_a_processed_row_without_a_measurement_does_not_report_zero(self):
        # Written before durations were measured. Reporting None or 0 would be worse than
        # the feed's approximation.
        assert (
            audio_duration_seconds(
                episode(local_path="/d/1.mp3", processed_path="/d/1.processed.mp3")
            )
            == 9693
        )

    def test_a_purged_episode_falls_back_to_the_feed(self):
        # Retention unlinks the file and nulls the path; the row and its measurement stay.
        assert (
            audio_duration_seconds(episode(source_duration_seconds=9650.0, local_path=None))
            == 9693
        )
