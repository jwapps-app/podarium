"""The bytes behind a stream URL must never change.

An episode gains a second file part way through its life: the trimmed copy, made some
minutes after the download and better than a tenth shorter. While both answered to one
URL, a player that had read the original's length went on asking for offsets from it --
and on iOS a byte past the end is not read as "re-read the length", it is retried in a
tight loop while playback sits still.
"""

from pathlib import Path

from podarium.models import Episode
from podarium.streaming import copy_for_token, preferred_copy, stream_url


def episode(tmp_path: Path, *, original=True, processed=False) -> Episode:
    ep = Episode(feed_id=1, guid="g", duration_seconds=9828)
    ep.id = 7
    if original:
        source = tmp_path / "7.mp3"
        source.write_bytes(b"\0" * 4096)
        ep.local_path = str(source)
    if processed:
        target = tmp_path / "7.processed.mp3"
        target.write_bytes(b"\0" * 2048)
        ep.processed_path = str(target)
    return ep


class TestTheUrlNamesTheCopy:
    def test_the_url_changes_when_the_processed_copy_appears(self, tmp_path):
        before = stream_url(episode(tmp_path))
        after = stream_url(episode(tmp_path, processed=True))
        assert before != after, "a client would not notice the file had been replaced"

    def test_a_player_that_started_on_the_original_keeps_getting_it(self, tmp_path):
        # The processed copy now exists, but this URL asked for the original by name.
        ep = episode(tmp_path, processed=True)
        chosen = copy_for_token(ep, "o")
        assert chosen is not None
        assert chosen[0] == Path(ep.local_path)

    def test_the_processed_copy_is_served_when_named(self, tmp_path):
        ep = episode(tmp_path, processed=True)
        chosen = copy_for_token(ep, "p")
        assert chosen is not None
        assert chosen[0] == Path(ep.processed_path)

    def test_the_processed_copy_is_preferred_by_default(self, tmp_path):
        chosen = preferred_copy(episode(tmp_path, processed=True))
        assert chosen is not None
        assert chosen[0].name.endswith(".processed.mp3")

    def test_an_unknown_token_falls_back_rather_than_failing(self, tmp_path):
        # A link from before the copy was deleted. Falling back beats a dead URL.
        assert copy_for_token(episode(tmp_path), "p") is None
        assert copy_for_token(episode(tmp_path), "nonsense") is None

    def test_a_token_naming_a_deleted_copy_falls_back(self, tmp_path):
        ep = episode(tmp_path, processed=True)
        Path(ep.processed_path).unlink()
        assert copy_for_token(ep, "p") is None
        assert preferred_copy(ep)[0] == Path(ep.local_path)

    def test_an_episode_with_nothing_on_disk_gets_a_bare_url(self, tmp_path):
        # Proxied from the publisher; there is no local copy to name, and the URL should
        # not churn while the download is still pending.
        ep = episode(tmp_path, original=False)
        assert stream_url(ep) == "/api/stream/7"
        assert preferred_copy(ep) is None
