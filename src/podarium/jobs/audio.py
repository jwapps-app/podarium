"""Post-processing downloaded audio: trimming silence, levelling loudness.

Every commercial podcast app does this on the phone, re-analysing the same audio on every
playback and spending battery to do it. This server already holds the file, so it can do
the work once and hand every client -- the web player now, the iOS app later -- a file that
needs nothing done to it.

The original is never replaced. Processing is lossy and both settings can be turned back
off, which would otherwise mean re-downloading a library to undo a preference.
"""

from __future__ import annotations

import asyncio
import re
import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.db import get_sessionmaker
from podarium.models import AppSettings, Episode, EpisodeState, Feed
from podarium.services import get_app_settings, without_large_text

log = logging.getLogger("podarium")

# Anything quieter than this, for longer than this, is dead air rather than a pause for
# breath. Chosen conservatively: too aggressive and speech starts to sound clipped
# together, which is worse than the silence it removed.
SILENCE_THRESHOLD_DB = -35
MIN_SILENCE_SECONDS = 0.6
# What is left where silence was removed. Cutting to nothing makes conversation sound
# unnaturally rushed; leaving a beat keeps the rhythm of speech.
SILENCE_KEEP_SECONDS = 0.25
# silenceremove judges silence over windows of this length (its default). It shows up as
# an extra window's worth of each silence surviving the cut.
DETECTION_WINDOW_SECONDS = 0.02

# EBU R128, the broadcast standard, and what every levelling tool targets.
LOUDNESS_TARGET_LUFS = -16
LOUDNESS_RANGE = 11
TRUE_PEAK_DB = -1.5

# A three-hour episode is a lot of audio to push through a filter graph. Generous, because
# the alternative to finishing slowly is failing.
PROCESS_TIMEOUT_SECONDS = 30 * 60

# Below this fraction of the original, the output is not trimmed audio -- it is the wrong
# stream, a decode failure, or a truncated encode. Deliberately generous: re-encoding to a
# smaller bitrate legitimately shrinks a file a lot, and this only needs to catch disasters.
MIN_PLAUSIBLE_RATIO = 0.2

# How many already-processed episodes to measure per pass. Far higher than the encoding
# cap, because this is two header reads rather than an encode.
MEASURE_BATCH = 25

# An episode whose encode failed is left alone for this long. Without it the same
# broken file was chosen on every pass -- the pass takes the first pending row and the
# failure changed nothing about it -- and nothing behind it was ever attempted. Kept in
# memory: a restart is a fair moment to try again.
FAILURE_BACKOFF = timedelta(hours=6)
_failed_until: dict[int, datetime] = {}


async def _run(command: list[str], *, timeout: float, stderr) -> tuple[int | None, bytes, bytes]:
    """Run a child to completion, and never leave one behind.

    A timeout used to return with ffprobe still running, and the ffmpeg path killed
    without waiting. Cancellation -- shutdown -- did neither. Whatever ends the wait,
    the child is killed and reaped before this returns or raises.
    """
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=stderr
    )
    try:
        stdout, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return process.returncode, stdout, err or b""


async def measure_duration(path: Path) -> float | None:
    """Seconds of audio in a file, from ffprobe.

    Measured rather than taken from the feed: the publisher's duration is frequently wrong
    and sometimes missing, and the whole point of storing this is to subtract one duration
    from another. An approximation on either side turns the answer into fiction.
    """
    try:
        returncode, stdout, _ = await _run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout=60,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if returncode != 0:
            return None
        return float(stdout.decode().strip())
    except (TimeoutError, ValueError, OSError):
        return None


# The silenceremove filter was rewritten between ffmpeg 5.1 and 7. On the old one the
# options this code sends mean something else entirely: every pause in speech, however
# short, is cut down to a quarter of a second, and there is no combination of options
# that makes it keep a pause whole. Discovered the hard way, on a server whose image
# carried Debian's 5.1 while the filter had been measured on 9.
MIN_FFMPEG_MAJOR = 7
_ffmpeg_major: int | None = None


def ffmpeg_major_version() -> int | None:
    """The installed ffmpeg's major version, or None when it is missing or unreadable."""
    global _ffmpeg_major
    if _ffmpeg_major is not None:
        return _ffmpeg_major
    path = shutil.which("ffmpeg")
    if path is None:
        return None
    try:
        banner = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"ffmpeg version (?:n)?(\d+)\.", banner)
    if not match:
        return None
    _ffmpeg_major = int(match.group(1))
    return _ffmpeg_major


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def trimming_available() -> bool:
    """Whether silence may be trimmed here: an ffmpeg whose filter means what this code
    says. Levelling is fine on any version; loudnorm has not changed."""
    major = ffmpeg_major_version()
    return major is not None and major >= MIN_FFMPEG_MAJOR


def _filters(*, trim: bool, normalize: bool) -> str:
    """The filter chain, in the order that matters.

    Silence removal first: it changes what the file contains, and measuring loudness before
    removing a third of the runtime would target the wrong thing.
    """
    chain: list[str] = []
    if trim:
        chain.append(
            # stop_duration is how long a silence must last to count as one; stop_silence
            # is how much of it is left in. They were one parameter for a while, which
            # removed every quarter-second pause outright and left nothing where it was.
            #
            # detection=peak, per sample, because that is how silencedetect judges silence
            # and the map between the two clocks is built from what silencedetect finds.
            # With the default RMS the two disagreed on where silence was, and the map
            # was wrong by a third.
            f"silenceremove=stop_periods=-1"
            f":stop_duration={MIN_SILENCE_SECONDS}"
            f":stop_threshold={SILENCE_THRESHOLD_DB}dB"
            f":stop_silence={SILENCE_KEEP_SECONDS}"
            f":detection=peak"
        )
    if normalize:
        chain.append(
            f"loudnorm=I={LOUDNESS_TARGET_LUFS}:LRA={LOUDNESS_RANGE}:TP={TRUE_PEAK_DB}"
        )
    return ",".join(chain)


def wanted(feed: Feed, app_settings: AppSettings) -> tuple[bool, bool]:
    """(trim, normalize) for a feed, resolving NULL against the globals.

    Trimming is only ever wanted where the installed ffmpeg can do it as meant; on an
    older one the setting stands but produces nothing, and the original is served.
    """
    trim = feed.trim_silence if feed.trim_silence is not None else app_settings.global_trim_silence
    if trim and not trimming_available():
        trim = False
    normalize = (
        feed.normalize_audio
        if feed.normalize_audio is not None
        else app_settings.global_normalize_audio
    )
    return bool(trim), bool(normalize)


def recipe(trim: bool, normalize: bool) -> str | None:
    """The name a processed file is stamped with, so a changed setting is noticed.

    The number moves whenever the trim changes meaning, and files cut under the old
    meaning are rebuilt by not matching: "trim2" was per-sample detection, "trim3" is
    the first cut by an ffmpeg whose filter keeps pauses whole.
    """
    parts = [name for name, on in (("trim3", trim), ("normalize", normalize)) if on]
    return "+".join(parts) or None


# Everything is re-encoded to MP3: the filters rule out a stream copy, and one output
# format keeps the served content type honest whatever the publisher shipped.
PROCESSED_SUFFIX = ".processed.mp3"
PROCESSED_MEDIA_TYPE = "audio/mpeg"


def processed_path_for(source: Path) -> Path:
    """Sits beside the original. Named for what it is, not for what the source was -- an
    m4a processed into MP3 must not keep the m4a extension."""
    return source.with_suffix(PROCESSED_SUFFIX)


async def process_episode(
    session: AsyncSession, episode: Episode, feed: Feed, app_settings: AppSettings
) -> bool:
    """Produce the processed copy if the show asks for one. True if a file was written.

    Failure is never fatal: the original is already on disk and plays perfectly well, so a
    missing processed copy costs the feature for that episode and nothing else.
    """
    trim, normalize = wanted(feed, app_settings)
    if not (trim or normalize):
        return False
    if not episode.local_path:
        return False
    if episode.processed_path and Path(episode.processed_path).exists():
        return False

    if not ffmpeg_available():
        log.warning("ffmpeg is not installed; audio processing is unavailable")
        return False

    source = Path(episode.local_path)
    if not source.exists():
        return False

    target = processed_path_for(source)
    partial = target.with_suffix(target.suffix + ".part")

    command = [
        # Niced, because a backlog of long episodes is hours of CPU and this shares a host
        # with the API, Postgres, and whatever else the machine is doing. Slower processing
        # is invisible; a sluggish player is not.
        "nice",
        "-n",
        "10",
        "ffmpeg",
        "-nostdin",
        "-loglevel", "error",
        "-i", str(source),
        # Most podcast MP3s embed cover art as a second stream, and ffmpeg left to itself
        # will happily pick that as the output -- producing a file that is the artwork, a
        # few kilobytes long, in about a second. Map the first audio stream and nothing
        # else. This is why the filter below appeared to save 100% of a four-hour episode.
        "-map", "0:a:0",
        "-vn",
        "-af", _filters(trim=trim, normalize=normalize),
        # Re-encode at a bitrate that is transparent for speech; the filters make copying
        # impossible, and matching the source exactly is not worth the complexity.
        "-c:a", "libmp3lame",
        "-q:a", "5",
        # Stated explicitly because the target is written to a .part name first, and ffmpeg
        # picks its muxer from the extension unless told otherwise.
        "-f", "mp3",
        "-y", str(partial),
    ]

    started = datetime.now(UTC)
    try:
        try:
            returncode, _, stderr = await _run(
                command, timeout=PROCESS_TIMEOUT_SECONDS, stderr=asyncio.subprocess.PIPE
            )
        except TimeoutError:
            raise ValueError(f"ffmpeg exceeded {PROCESS_TIMEOUT_SECONDS}s") from None

        if returncode != 0:
            raise ValueError(stderr.decode(errors="replace")[:300])

        size = partial.stat().st_size
        if size == 0:
            raise ValueError("ffmpeg produced an empty file")

        # Both sides, so the time trimming actually saved is a subtraction rather than an
        # estimate. Measured before the file is accepted, because the plausibility check
        # below is a comparison of durations.
        source_duration = await measure_duration(source)
        processed_duration = await measure_duration(partial)

        # Trimming silence removes a few percent of a talk show and rather more of a badly
        # edited one, but it never removes most of it. A result that small means the wrong
        # stream was encoded, which is exactly the failure this catches -- and it is
        # invisible without a check, because the file is valid audio, just the wrong audio.
        #
        # Compared by duration where both could be measured. Bytes were the measure once,
        # and a lossless or high-bitrate source re-encoded to speech MP3 legitimately
        # lands under a fifth of its size; bytes remain the fallback when ffprobe cannot
        # read one of the files.
        if source_duration and processed_duration is not None:
            if processed_duration < source_duration * MIN_PLAUSIBLE_RATIO:
                raise ValueError(
                    f"implausible output: {processed_duration:.0f}s from "
                    f"{source_duration:.0f}s; refusing to use it"
                )
        else:
            original = episode.local_bytes or source.stat().st_size
            if original and size < original * MIN_PLAUSIBLE_RATIO:
                raise ValueError(
                    f"implausible output: {size} bytes from {original}; refusing to use it"
                )

        partial.replace(target)
        episode.processed_path = str(target)
        episode.processed_bytes = size
        episode.processed_recipe = recipe(trim, normalize)
        episode.processed_at = datetime.now(UTC)
        # Failure to measure is not failure to process -- the file is good, the saving
        # simply goes unreported for that episode.
        episode.source_duration_seconds = source_duration
        episode.processed_duration_seconds = processed_duration
        # The map between the two clocks. Positions are stored on the original's clock
        # whatever is playing (see timeline.py), so nothing here moves them; this is what
        # translates them for the trimmed copy, exactly rather than proportionally.
        removed = await measure_removed(source) if trim else []
        if removed is not None and map_is_credible(removed, source_duration, processed_duration):
            episode.trim_map_json = json.dumps([[round(s, 3), round(e, 3)] for s, e in removed])
        else:
            episode.trim_map_json = None
            if trim:
                log.info("episode %s: silence map not credible; positions scale by ratio", episode.id)
        await session.commit()

        elapsed = (datetime.now(UTC) - started).total_seconds()
        saved = (episode.local_bytes or 0) - size
        log.info(
            "processed episode %s in %.0fs (%s -> %s bytes, %+d)",
            episode.id,
            elapsed,
            episode.local_bytes,
            size,
            -saved,
        )
        return True
    except asyncio.CancelledError:
        # Shutdown mid-encode. Nothing here is worth keeping, and the row is untouched.
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - the original still plays
        partial.unlink(missing_ok=True)
        # Discard whatever was assigned before the failure. The fields above are set one
        # at a time on a live session, so an exception part way through leaves the row
        # half written -- and the next commit anywhere in this loop would persist it. A
        # row carrying processed_path with no processed_duration_seconds is the worst of
        # those states: the stream endpoint then serves a trimmed file while the API
        # reports the feed's much longer figure, which is exactly the mismatch that made
        # trimmed episodes look like truncated streams.
        #
        # Read before the rollback: it expires every loaded attribute, and reading one
        # back is database IO, which is not something to attempt from an error path.
        episode_id = episode.id
        await session.rollback()
        log.warning("could not process episode %s: %s", episode_id, exc)
        return False


async def reconcile_processing(session: AsyncSession, *, limit: int = 1) -> int:
    """Bring existing downloads in line with the current settings. Returns work done.

    Processing at download time alone is not enough, and it fails in both directions.
    Turning trimming on left every episode already on disk untouched, so the setting
    appeared to do nothing until the next download -- possibly days. And turning it off
    left the trimmed copies in place *and still being served*, because the stream endpoint
    prefers them, so you would go on hearing processed audio after switching it off.

    So the state is reconciled rather than assumed: anything that should have a processed
    copy and does not gets one, and anything that has one and should not loses it.

    Bounded per pass. A backlog of long episodes is hours of encoding, and it should trickle
    rather than seize the machine.
    """
    app_settings = await get_app_settings(session)

    rows = (
        await session.execute(
            select(Episode, Feed)
            .options(*without_large_text())
            .join(Feed, Feed.id == Episode.feed_id)
            .where(Episode.local_path.is_not(None))
        )
    ).all()

    # The cheap direction first, and without a cap: deleting a file nobody should be served
    # is instant, and leaving even one behind means hearing audio the setting says you
    # turned off.
    reclaimed = 0
    pending: list[tuple[Episode, Feed]] = []
    unmeasured: list[Episode] = []
    for episode, feed in rows:
        trim, normalize = wanted(feed, app_settings)
        if trim or normalize:
            if episode.processed_path and episode.processed_recipe != recipe(trim, normalize):
                # Made under different settings, or before the recipe was recorded. Either
                # way it is not the file the settings ask for.
                drop_processed(episode)
                reclaimed += 1
            if not episode.processed_path:
                pending.append((episode, feed))
            elif (
                episode.processed_duration_seconds is None
                or episode.source_duration_seconds is None
            ):
                unmeasured.append(episode)
        elif episode.processed_path:
            drop_processed(episode)
            reclaimed += 1

    if reclaimed:
        await session.commit()
        log.info("removed %s processed copies no longer wanted", reclaimed)

    # Episodes trimmed before the durations were recorded, and any measurement that failed
    # at the time. Backfilled rather than written off: ffprobe reads a header in
    # milliseconds, so recovering the figure costs nothing next to re-encoding, and without
    # it those episodes would never contribute to what trimming saved.
    measured = 0
    for episode in unmeasured[:MEASURE_BATCH]:
        source = Path(episode.local_path) if episode.local_path else None
        target = Path(episode.processed_path) if episode.processed_path else None
        if not source or not target or not source.exists() or not target.exists():
            continue
        episode.source_duration_seconds = await measure_duration(source)
        episode.processed_duration_seconds = await measure_duration(target)
        measured += 1

    if measured:
        await session.commit()
        log.info("measured durations for %s already-processed episodes", measured)

    done = 0
    now = datetime.now(UTC)
    for episode_id in [k for k, until in _failed_until.items() if until <= now]:
        del _failed_until[episode_id]
    pending = [(e, f) for e, f in pending if e.id not in _failed_until]

    for episode, feed in pending[:limit]:
        if await process_episode(session, episode, feed, app_settings):
            done += 1
        else:
            _failed_until[episode.id] = now + FAILURE_BACKOFF

    return done + reclaimed + measured


async def processing_loop(stop: asyncio.Event, idle_seconds: int = 300) -> None:
    """Keep processed audio in step with the settings.

    Polls quickly while there is work and slowly when there is none, so turning a setting
    on is acted upon within minutes rather than at the next download.
    """
    if not ffmpeg_available():
        log.info("ffmpeg not installed; audio processing disabled")
        return
    if not trimming_available():
        log.error(
            "ffmpeg %s is too old to trim silence as this server means it (needs %s or newer); "
            "trimming is off until it is upgraded, levelling still works",
            ffmpeg_major_version(), MIN_FFMPEG_MAJOR,
        )

    sessionmaker = get_sessionmaker()
    while not stop.is_set():
        worked = 0
        try:
            async with sessionmaker() as session:
                worked = await reconcile_processing(session)
        except Exception:  # noqa: BLE001 - a bad episode must not kill the loop
            log.exception("audio processing pass failed")

        try:
            await asyncio.wait_for(stop.wait(), timeout=5 if worked else idle_seconds)
        except TimeoutError:
            pass


_SILENCE_LINE = re.compile(r"silence_(start|end): ([0-9.]+)")


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """(start, end) of every silence ffmpeg's silencedetect reported, in file seconds."""
    silences: list[tuple[float, float]] = []
    open_start: float | None = None
    for kind, value in _SILENCE_LINE.findall(stderr):
        if kind == "start":
            open_start = float(value)
        elif open_start is not None:
            silences.append((open_start, float(value)))
            open_start = None
    return silences


# What survives of every silence the filter cuts: the stop_duration it copied while
# deciding, the beat it keeps, and one detection window. Measured, not read from the
# documentation: a synthetic file with known gaps run through the filter keeps exactly
# this much of each gap longer than it, and gaps shorter than it entirely.
SILENCE_SURVIVES_SECONDS = MIN_SILENCE_SECONDS + SILENCE_KEEP_SECONDS + DETECTION_WINDOW_SECONDS


def removed_from_silences(silences: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """What silenceremove cuts from each detected silence.

    A silence at the very start of the file is left whole -- stop mode only acts once
    audio has been heard -- and every other one loses whatever is past what survives.
    """
    return [
        (start + SILENCE_SURVIVES_SECONDS, end)
        for start, end in silences
        if start > DETECTION_WINDOW_SECONDS and end - start > SILENCE_SURVIVES_SECONDS
    ]


async def measure_removed(source: Path) -> list[tuple[float, float]] | None:
    """The stretches the silence filter removes from a file, by asking silencedetect with
    the filter's own threshold and minimum. None if ffmpeg could not say."""
    try:
        returncode, _, stderr = await _run(
            [
                "ffmpeg", "-nostats", "-hide_banner",
                "-i", str(source),
                "-af", f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:d={MIN_SILENCE_SECONDS}",
                "-f", "null", "-",
            ],
            timeout=PROCESS_TIMEOUT_SECONDS,
            stderr=asyncio.subprocess.PIPE,
        )
    except (TimeoutError, OSError):
        return None
    if returncode != 0:
        return None
    return removed_from_silences(parse_silences(stderr.decode(errors="replace")))


def map_is_credible(
    removed: list[tuple[float, float]], source_duration: float | None, processed_duration: float | None
) -> bool:
    """Whether the detected cuts account for the length that actually went. Detection and
    removal are two filters with the same settings, but they are two filters; when they
    disagree by more than a couple of seconds the proportional estimate is the safer map."""
    if not source_duration or processed_duration is None:
        return False
    expected = source_duration - sum(end - start for start, end in removed)
    return abs(expected - processed_duration) <= max(2.0, 0.02 * source_duration)


def drop_processed(episode: Episode) -> None:
    """Forget the processed copy, so a changed setting takes effect on the next download."""
    if episode.processed_path:
        Path(episode.processed_path).unlink(missing_ok=True)
    episode.processed_path = None
    episode.processed_bytes = None
    episode.processed_recipe = None
    episode.trim_map_json = None
    episode.processed_at = None
    episode.processed_duration_seconds = None
