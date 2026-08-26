"""Time-based one-time passwords.

The login form is on the public internet guarding a single password. Throttling makes
guessing hopeless; this makes a *known* password insufficient on its own.

The secret is stored encrypted rather than in plain text. pgdata is the one directory here
worth backing up, so it is the one most likely to end up somewhere else -- and a backup
containing a usable second factor is not a second factor. The key is derived from
SECRET_KEY, which lives in the environment and not in the database, so a leaked dump alone
yields nothing.

The consequence of that choice is worth stating plainly: change SECRET_KEY and the stored
secret can no longer be read, which locks the account out of its second factor. Recovery is
one statement on the host, documented in README.
"""

from __future__ import annotations

import base64
import hashlib
import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken

ISSUER = "Podarium"

# How many 30-second steps either side of now are accepted, to tolerate a phone whose clock
# is slightly off. One step is the usual choice: enough for real drift, not so much that a
# code stays usable long after it left the screen.
VALID_WINDOW = 1

STEP_SECONDS = 30


def _key(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def generate_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str, secret_key: str) -> str:
    return _key(secret_key).encrypt(secret.encode()).decode()


def decrypt_secret(stored: str, secret_key: str) -> str | None:
    """Return the secret, or None if it cannot be read.

    Unreadable means SECRET_KEY has changed since it was stored. Callers treat that as
    "no second factor available" and say so, rather than failing in a way that looks like
    a wrong code.
    """
    try:
        return _key(secret_key).decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def provisioning_uri(secret: str, username: str) -> str:
    """The otpauth:// URI an authenticator app scans."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def current_step(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // STEP_SECONDS)


def verify(secret: str, code: str, *, last_step: int | None = None) -> int | None:
    """Check a code and return the step it belongs to, or None if it is not valid.

    Returning the step is what lets the caller refuse a replay. A code stays valid for its
    whole 30-second window, so without recording the step a code observed once -- over a
    shoulder, in a screenshot, from a proxy log -- can be used again inside that window.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return None

    totp = pyotp.TOTP(secret)
    now = current_step()
    for offset in range(-VALID_WINDOW, VALID_WINDOW + 1):
        step = now + offset
        if totp.verify(code, for_time=step * STEP_SECONDS, valid_window=0):
            if last_step is not None and step <= last_step:
                return None  # already used
            return step
    return None
