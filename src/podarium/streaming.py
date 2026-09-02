"""Which copy of an episode's audio gets served, and under which URL.

An episode can have two files on disk: the publisher's original, and the trimmed and
levelled copy made from it. The processed one is preferred once it exists -- it is what
the show asked for -- and the original is kept, because processing can be switched off.

The subtlety is that the processed copy appears *while people are listening*. Downloading
and processing are separate stages, and an episode is playable as soon as the first
finishes. If both copies answer to one URL, the bytes behind that URL change mid-playback,
and they are not interchangeable: trimming removes better than a tenth of the runtime, so
the byte-to-time mapping is entirely different.

What that does to a player is not subtle. It has read the original's length, so it asks
for a byte offset near the end of 135 MB; the server, now serving the 92 MB copy, has no
such byte and answers 416. Observed on iOS: it does not re-read the length and correct
itself, it reissues the same doomed request in a tight loop, sixty-three bytes further on
each time, and playback stops where it stood. It sends no If-Range, so there is no way to
tell it the resource changed.

So the URL names the copy. A player that started on the original keeps being handed the
original until it loads the episode afresh, at which point the API gives it the new URL and
it reads the trimmed file from the beginning. Nothing changes underneath anybody.
"""

from __future__ import annotations

from pathlib import Path

from podarium.jobs.audio import PROCESSED_MEDIA_TYPE
from podarium.models import Episode

#: Query parameter naming the copy. Short because it is on every media URL.
VERSION_PARAM = "v"

_PROCESSED = "p"
_ORIGINAL = "o"


_FALLBACK_MEDIA_TYPE = "audio/mpeg"
_NON_AUDIO_TYPES_ALLOWED = {"application/ogg", "application/octet-stream"}


def safe_audio_type(declared: str | None) -> str:
    """A media type this server is willing to put on an audio response.

    The declared type is the publisher's -- from the feed's <enclosure>, or from the
    upstream response when an episode is proxied -- and it is served from this origin. A
    feed that declares text/html would otherwise have /api/stream answer as a web page on
    the same origin as the session cookie. Anything that is not audio (or video, which
    some video podcasts declare honestly) is served as audio: if the bytes are audio the
    player copes, and if they are not, nothing renders them.
    """
    base = (declared or "").split(";")[0].strip().lower()
    if base.startswith(("audio/", "video/")) or base in _NON_AUDIO_TYPES_ALLOWED:
        return base
    return _FALLBACK_MEDIA_TYPE


def _media_type(episode: Episode) -> str:
    return safe_audio_type(episode.enclosure_type)


def preferred_copy(episode: Episode) -> tuple[Path, str, str] | None:
    """The copy to serve when nothing specific was asked for: path, media type, token.

    The processed file wins where it exists -- it is the one the show's settings ask for --
    and a processing failure costs the feature rather than the episode.
    """
    for path_value, media_type, token in (
        (episode.processed_path, PROCESSED_MEDIA_TYPE, _PROCESSED),
        (episode.local_path, _media_type(episode), _ORIGINAL),
    ):
        if not path_value:
            continue
        path = Path(path_value)
        if path.exists():
            return path, media_type, token
    return None


def copy_for_token(episode: Episode, token: str | None) -> tuple[Path, str] | None:
    """The copy a URL asked for by name, or None to fall back to the preferred one.

    An unrecognised token -- a link from before the file was replaced, or a copy since
    deleted -- falls back rather than failing. The caller then serves the current file,
    and the unsatisfiable-range handling in the media route keeps a stale client from
    getting stuck on it.
    """
    if token == _PROCESSED and episode.processed_path:
        path = Path(episode.processed_path)
        if path.exists():
            return path, PROCESSED_MEDIA_TYPE
    if token == _ORIGINAL and episode.local_path:
        path = Path(episode.local_path)
        if path.exists():
            return path, _media_type(episode)
    return None


def stream_url(episode: Episode) -> str:
    """The URL a client should use for this episode's audio.

    Carries the token for whichever copy exists now, so that when the processed copy
    lands the URL changes and clients pick it up as new rather than as more of the file
    they were already reading.
    """
    base = f"/api/stream/{episode.id}"
    chosen = preferred_copy(episode)
    if chosen is None:
        # Nothing on disk: the route proxies the publisher, and there is no local copy to
        # name. The URL stays bare so it does not churn while a download is pending.
        return base
    return f"{base}?{VERSION_PARAM}={chosen[2]}"


def audio_duration_seconds(episode: Episode) -> int | None:
    """How long the audio at the stream URL actually is.

    The feed's own figure describes the publisher's file, and once trimming is switched on
    that is no longer the file we serve -- a show with long pauses can lose more than a
    tenth of its length. A player told the wrong length draws the wrong scrubber, and,
    worse, cannot tell "the episode ended" from "the stream ran out": both look like audio
    stopping earlier than the duration said it would. So this reports the length of
    whichever copy would be served, and falls back to the feed's claim only when we have
    not measured the file ourselves.
    """
    if episode.processed_path and episode.processed_duration_seconds:
        return round(episode.processed_duration_seconds)
    if episode.local_path and episode.source_duration_seconds:
        return round(episode.source_duration_seconds)
    return episode.duration_seconds
