"""Authentication. One user, two credential shapes.

The web UI carries a signed session cookie; non-browser clients carry a bearer token from
``api_tokens``. Both resolve to the same ``User``, so every route below depends on
``current_user`` and never cares which one was used.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.config import Settings, get_settings
from podarium.db import get_session
from podarium.models import ApiToken, User

log = logging.getLogger(__name__)

_hasher = PasswordHasher()

# How stale a token's last_used_at may be before it is worth a write to refresh.
LAST_USED_GRANULARITY = timedelta(minutes=15)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="podarium-session")


def issue_session_cookie(response: Response, user: User, settings: Settings) -> None:
    token = _serializer(settings).dumps({"uid": user.id})
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        # PUBLIC_URL tells us whether the deployment is actually behind TLS.
        secure=settings.public_url.startswith("https://"),
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


def _user_id_from_cookie(request: Request, settings: Settings) -> int | None:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        data = _serializer(settings).loads(raw, max_age=settings.session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, int) else None


def generate_api_token() -> tuple[str, str]:
    """Return (plaintext, sha256). Only the hash is stored; the plaintext is shown once."""
    plaintext = secrets.token_urlsafe(32)
    return plaintext, hash_api_token(plaintext)


def hash_api_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _bearer_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    uid = _user_id_from_cookie(request, settings)
    if uid is not None:
        user = await session.get(User, uid)
        if user is not None:
            return user

    plaintext = _bearer_from_request(request)
    if plaintext:
        token = (
            await session.execute(
                select(ApiToken).where(ApiToken.token_hash == hash_api_token(plaintext))
            )
        ).scalar_one_or_none()
        if token is not None:
            user = await session.get(User, token.user_id)
            if user is not None:
                # last_used_at answers "is this device still in use", which is a
                # day-granularity question. Stamping it on every call would put a write
                # and a commit inside every authenticated request a device makes -- for a
                # phone syncing on a timer, thousands of writes a day recording nothing.
                last = token.last_used_at
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if last is None or datetime.now(UTC) - last > LAST_USED_GRANULARITY:
                    token.last_used_at = func.now()
                    await session.commit()
                return user

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def bootstrap_user(session: AsyncSession, settings: Settings) -> None:
    """Create the single user from env on first boot, and only then.

    Once a user exists these env vars are ignored, so leaving them in the deployed stack
    does not silently reset the password on every restart.
    """
    existing = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    if existing:
        return
    if not settings.podarium_username or not settings.podarium_password:
        # Worth a line in the log. Without one this is a silent dead end: the server starts
        # cleanly, serves a login page, and no credentials work, with nothing saying why.
        log.warning(
            "No user exists and PODARIUM_USERNAME/PODARIUM_PASSWORD are not both set, "
            "so no account was created. Set them and restart."
        )
        return
    session.add(
        User(
            username=settings.podarium_username,
            password_hash=hash_password(settings.podarium_password),
        )
    )
    await session.commit()
