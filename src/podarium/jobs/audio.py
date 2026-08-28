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
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.db import get_sessionmaker
from podarium.models import AppSettings, Episode, Feed
from podarium.services import get_app_settings

log = logging.getLogger("podarium")

# Anything quieter than this, for longer than this, is dead air rather than a pause for
# breath. Chosen conservatively: too aggressive and speech starts to sound clipped
# together, which is worse than the silence it removed.
SILENCE_THRESHOLD_DB = -35
MIN_SILENCE_SECONDS = 0.6
# What is left where silence was removed. Cutting to nothing makes conversation sound
# unnaturally rushed; leaving a beat keeps the rhythm of speech.
SILENCE_KEEP_SECONDS = 0.25

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


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _filters(*, trim: bool, normalize: bool) -> str:
    """The filter chain, in the order that matters.

    Silence removal first: it changes what the file contains, and measuring loudness before
    removing a third of the runtime would target the wrong thing.
    """
    chain: list[str] = []
    if trim:
        chain.append(
            f"silenceremove=stop_periods=-1"
            f":stop_duration={SILENCE_KEEP_SECONDS}"
            f":stop_threshold={SILENCE_THRESHOLD_DB}dB"
        )
    if normalize:
        chain.append(
            f"loudnorm=I={LOUDNESS_TARGET_LUFS}:LRA={LOUDNESS_RANGE}:TP={TRUE_PEAK_DB}"
        )
    return ",".join(chain)


def wanted(feed: Feed, app_settings: AppSettings) -> tuple[bool, bool]:
    """(trim, normalize) for a feed, resolving NULL against the globals."""
    trim = feed.trim_silence if feed.trim_silence is not None else app_settings.global_trim_silence
    normalize = (
        feed.normalize_audio
        if feed.normalize_audio is not None
        else app_settings.global_normalize_audio
    )
    return bool(trim), bool(normalize)


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
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=PROCESS_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            raise ValueError(f"ffmpeg exceeded {PROCESS_TIMEOUT_SECONDS}s") from None

        if process.returncode != 0:
            raise ValueError((stderr or b"").decode(errors="replace")[:300])

        size = partial.stat().st_size
        if size == 0:
            raise ValueError("ffmpeg produced an empty file")

        # Trimming silence removes a few percent of a talk show and rather more of a badly
        # edited one, but it never removes most of it. A result that small means the wrong
        # stream was encoded, which is exactly the failure this catches -- and it is
        # invisible without a check, because the file is valid audio, just the wrong audio.
        original = episode.local_bytes or source.stat().st_size
        if original and size < original * MIN_PLAUSIBLE_RATIO:
            raise ValueError(
                f"implausible output: {size} bytes from {original}; refusing to use it"
            )

        partial.replace(target)
        episode.processed_path = str(target)
        episode.processed_bytes = size
        episode.processed_at = datetime.now(UTC)
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
    except Exception as exc:  # noqa: BLE001 - the original still plays
        partial.unlink(missing_ok=True)
        log.warning("could not process episode %s: %s", episode.id, exc)
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
            .join(Feed, Feed.id == Episode.feed_id)
            .where(Episode.local_path.is_not(None))
        )
    ).all()

    # The cheap direction first, and without a cap: deleting a file nobody should be served
    # is instant, and leaving even one behind means hearing audio the setting says you
    # turned off.
    reclaimed = 0
    pending: list[tuple[Episode, Feed]] = []
    for episode, feed in rows:
        trim, normalize = wanted(feed, app_settings)
        if trim or normalize:
            if not episode.processed_path:
                pending.append((episode, feed))
        elif episode.processed_path:
            drop_processed(episode)
            reclaimed += 1

    if reclaimed:
        await session.commit()
        log.info("removed %s processed copies no longer wanted", reclaimed)

    done = 0
    for episode, feed in pending[:limit]:
        if await process_episode(session, episode, feed, app_settings):
            done += 1

    return done + reclaimed


async def processing_loop(stop: asyncio.Event, idle_seconds: int = 300) -> None:
    """Keep processed audio in step with the settings.

    Polls quickly while there is work and slowly when there is none, so turning a setting
    on is acted upon within minutes rather than at the next download.
    """
    if not ffmpeg_available():
        log.info("ffmpeg not installed; audio processing disabled")
        return

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


def drop_processed(episode: Episode) -> None:
    """Forget the processed copy, so a changed setting takes effect on the next download."""
    if episode.processed_path:
        Path(episode.processed_path).unlink(missing_ok=True)
    episode.processed_path = None
    episode.processed_bytes = None
    episode.processed_at = None
