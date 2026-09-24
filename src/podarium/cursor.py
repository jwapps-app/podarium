"""Opaque keyset cursors.

Every paginated listing here sorts by a timestamp with the row id as a tiebreak, so one
cursor shape serves them all. Keyset rather than offset: rows keep arriving while a client
pages through, and OFFSET would silently skip or repeat them.

The value is base64 only to discourage clients from parsing it. It is not a secret, and
nothing downstream trusts its contents beyond the bounds check.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime


class InvalidCursor(ValueError):
    """Raised for a cursor that is not decodable. Callers map this to a 400."""


def encode_cursor(stamp: datetime, row_id: int, checkpoint: datetime | None = None) -> str:
    """``checkpoint`` is the moment a paging run began, carried so every page of the run
    can report the same ``now``. Only sync uses it."""
    raw = f"{stamp.isoformat()}|{row_id}"
    if checkpoint is not None:
        raw += f"|{checkpoint.isoformat()}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _fields(cursor: str) -> list[str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        fields = base64.urlsafe_b64decode(padded).decode().split("|")
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor(str(exc)) from exc
    if len(fields) < 2:
        raise InvalidCursor("missing separator")
    return fields


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    fields = _fields(cursor)
    try:
        return datetime.fromisoformat(fields[0]), int(fields[1])
    except ValueError as exc:
        raise InvalidCursor(str(exc)) from exc


def decode_checkpoint(cursor: str) -> datetime | None:
    fields = _fields(cursor)
    if len(fields) < 3:
        return None
    try:
        return datetime.fromisoformat(fields[2])
    except ValueError as exc:
        raise InvalidCursor(str(exc)) from exc
