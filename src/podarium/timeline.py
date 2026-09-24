"""One timeline for every stored second.

Trimming silence makes a second copy of an episode with a different clock: a position
that means "forty minutes in" on the original means somewhere earlier on the trimmed
file, and by an amount that depends on how much silence sat before it. Chapters come from
the publisher on the original's clock; positions and bookmarks arrive from whichever file
a player happens to be playing.

So every second stored here is on the **original's** clock, and the translation happens at
the edges: a write says which copy it was heard on (``audio_version``, defaulting to the
copy the server currently serves), and a read is translated to the clock of the copy the
server serves now. The map between the two clocks is the list of intervals trimming
removed, recorded when the processed copy is made. Where there is no map -- a copy made
before maps existed -- the durations give a proportional estimate, which is a fraction of
the removed silence out rather than all of it.
"""

from __future__ import annotations

import json

from podarium.models import Episode

PROCESSED = "p"
ORIGINAL = "o"

Interval = tuple[float, float]


def removed_intervals(episode: Episode) -> list[Interval] | None:
    """The stretches of the original that trimming cut, in original seconds, in order."""
    raw = episode.trim_map_json
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        intervals = [(float(a), float(b)) for a, b in parsed]
    except (ValueError, TypeError):
        return None
    return [(a, b) for a, b in sorted(intervals) if b > a >= 0]


def to_processed(seconds: float, intervals: list[Interval]) -> float:
    """Original clock to trimmed clock. A moment inside a cut lands where the cut was."""
    removed = 0.0
    for start, end in intervals:
        if end <= seconds:
            removed += end - start
        elif start < seconds:
            removed += seconds - start
        else:
            break
    return seconds - removed


def to_original(seconds: float, intervals: list[Interval]) -> float:
    """Trimmed clock to original clock."""
    removed = 0.0
    for start, end in intervals:
        if seconds <= start - removed:
            break
        removed += end - start
    return seconds + removed


def served_version(episode: Episode) -> str:
    """Which copy a client is handed today. Mirrors streaming.preferred_copy, which
    cannot be imported here without a cycle."""
    return PROCESSED if episode.processed_path else ORIGINAL


def _ratio(episode: Episode) -> float | None:
    source = episode.source_duration_seconds
    processed = episode.processed_duration_seconds
    if not source or not processed or source <= 0 or processed >= source:
        return None
    return processed / source


def outbound(episode: Episode, seconds: int | float | None) -> int | None:
    """A stored (original-clock) second, as the served copy's clock reads it."""
    if seconds is None:
        return None
    if served_version(episode) != PROCESSED:
        return int(seconds)
    intervals = removed_intervals(episode)
    if intervals is not None:
        return int(to_processed(float(seconds), intervals))
    ratio = _ratio(episode)
    return int(seconds * ratio) if ratio else int(seconds)


def inbound(episode: Episode, seconds: int, version: str | None) -> int:
    """A second a client reports, on the clock of the copy it named, stored on the
    original's clock. No version means the copy served today."""
    heard_on = version or served_version(episode)
    if heard_on != PROCESSED or not episode.processed_path:
        return int(seconds)
    intervals = removed_intervals(episode)
    if intervals is not None:
        return int(round(to_original(float(seconds), intervals)))
    ratio = _ratio(episode)
    return int(round(seconds / ratio)) if ratio else int(seconds)
