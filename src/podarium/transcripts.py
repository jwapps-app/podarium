"""podcast:transcript, fetched by the server and kept so the library can be searched.

The interesting half is not showing a transcript, it is searching one. A commercial app
cannot do this well: it does not hold your library, so it would have to index everyone's.
This server holds 3,000-odd episodes in one Postgres database, which makes "which episode
was the bit about X" a query rather than a memory exercise.

Fetched like chapters and artwork -- by the server, never by the client -- and stripped to
plain text on the way in, because a timestamped caption file is not what a full-text index
wants and is not what anyone wants to read.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.clients.http import build_client
from podarium.models import Episode

log = logging.getLogger("podarium")

RETRY_FAILED_AFTER = timedelta(hours=6)

# Transcripts are text. A three-hour episode runs perhaps 200 KB; anything past this is not
# a transcript, and it is going into a database column.
MAX_BYTES = 5 * 1024 * 1024

# WEBVTT/SRT scaffolding: cue numbers, timestamp ranges, and the header.
_CUE_NUMBER = re.compile(r"^\d+$")
_TIMESTAMP = re.compile(r"^[\d:.,]+\s*-->\s*[\d:.,]+")
_VTT_HEADER = re.compile(r"^WEBVTT", re.IGNORECASE)
# Inline speaker and styling tags a VTT file may carry.
_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def to_plain_text(raw: str) -> str:
    """Strip caption scaffolding down to what was actually said.

    SRT and VTT are the common formats and both interleave the words with cue numbers and
    timestamps. Left in, every search would match against timecodes, and the stored text
    would be several times larger than the speech it contains.

    Consecutive duplicate lines are collapsed because rolling captions repeat each line as
    it scrolls, which would otherwise triple the text and skew relevance.
    """
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _VTT_HEADER.match(stripped) or _CUE_NUMBER.match(stripped):
            continue
        if _TIMESTAMP.match(stripped):
            continue
        cleaned = _TAGS.sub("", stripped).strip()
        if not cleaned:
            continue
        if lines and lines[-1] == cleaned:
            continue
        lines.append(cleaned)

    return _WHITESPACE.sub(" ", " ".join(lines)).strip()


async def ensure_transcript(
    session: AsyncSession, episode: Episode, *, user_agent: str
) -> str | None:
    """Fetch and store the transcript on first use. Returns the text, or None."""
    if not episode.transcript_url:
        return None
    if episode.transcript_text is not None:
        return episode.transcript_text

    last_try = episode.transcript_fetched_at
    if last_try is not None:
        if last_try.tzinfo is None:
            last_try = last_try.replace(tzinfo=UTC)
        if datetime.now(UTC) - last_try < RETRY_FAILED_AFTER:
            return None

    episode.transcript_fetched_at = datetime.now(UTC)

    try:
        async with build_client(user_agent) as client:
            async with client.stream("GET", episode.transcript_url) as response:
                response.raise_for_status()
                buffer = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    buffer.extend(chunk)
                    if len(buffer) > MAX_BYTES:
                        raise ValueError(f"transcript exceeds {MAX_BYTES} bytes")
                raw = bytes(buffer).decode("utf-8", errors="replace")
    except (httpx.HTTPError, ValueError) as exc:
        log.info("transcript fetch failed for %s: %s", episode.transcript_url, exc)
        await session.commit()
        return None

    text = to_plain_text(raw)
    if not text:
        log.info("transcript at %s parsed to nothing", episode.transcript_url)
        await session.commit()
        return None

    episode.transcript_text = text
    await session.commit()
    return text
