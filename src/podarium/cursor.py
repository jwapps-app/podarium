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


def encode_cursor(stamp: datetime, row_id: int) -> str:
    raw = f"{stamp.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, separator, row_id = base64.urlsafe_b64decode(padded).decode().partition("|")
        if not separator:
            raise ValueError("missing separator")
        return datetime.fromisoformat(stamp), int(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor(str(exc)) from exc
