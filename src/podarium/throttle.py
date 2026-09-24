"""Login throttling.

The login form is the only thing between the internet and this server, and it guards a
single password -- with a second factor only where one has been turned on. Without a
limit, an attacker gets unlimited guesses at whatever rate the network allows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.config import get_settings
from podarium.models import LoginAttempt

# Five wrong answers inside a quarter of an hour buys a quarter of an hour of silence.
# Generous enough that fat-fingering a password twice costs nothing, tight enough that
# guessing is hopeless: a hundred attempts a day against a password worth having is not a
# meaningful attack.
MAX_FAILURES = 5
WINDOW = timedelta(minutes=15)

# Per client address, across every username it tries. Larger than the per-username
# limit, since a household behind one address may hold more than one fumbled password,
# and small enough that inventing usernames buys a few dozen hashes and then silence.
SOURCE_MAX_FAILURES = 20

# Attempts are kept for a while after they stop counting, so a look at the table answers
# "has anyone been trying?" rather than only "am I locked out right now?".
RETENTION = timedelta(days=7)


async def seconds_until_unlocked(
    session: AsyncSession, username: str, source: str | None = None
) -> int:
    """How long this username -- or this address -- must wait, or 0 if it may try now."""
    return max(
        await _username_wait(session, username),
        await _source_wait(session, source) if source else 0,
    )


async def _source_wait(session: AsyncSession, source: str) -> int:
    """Failures from one address count whatever name they tried. A success does not
    reset them: the point is the hashing a stranger can cause, and a stranger cannot
    succeed."""
    since = datetime.now(UTC) - WINDOW
    failures = list(
        (
            await session.execute(
                select(LoginAttempt.attempted_at)
                .where(LoginAttempt.source == source)
                .where(LoginAttempt.succeeded.is_(False))
                .where(LoginAttempt.attempted_at > since)
                .order_by(LoginAttempt.attempted_at)
            )
        ).scalars()
    )
    if len(failures) < SOURCE_MAX_FAILURES:
        return 0
    return _wait_from(failures[SOURCE_MAX_FAILURES - 1])


def _wait_from(crossed_at: datetime) -> int:
    """Seconds left of a window that began at the failure that crossed the line, so
    hammering the endpoint cannot extend the lockout indefinitely."""
    unlocks_at = crossed_at + WINDOW
    if unlocks_at.tzinfo is None:
        unlocks_at = unlocks_at.replace(tzinfo=UTC)
    return max(0, int((unlocks_at - datetime.now(UTC)).total_seconds()) + 1)


async def _username_wait(session: AsyncSession, username: str) -> int:
    since = datetime.now(UTC) - WINDOW

    # Only failures after the last success count. Getting in resets the reckoning, so a
    # bad patch earlier in the day does not shorten the allowance later.
    last_success = (
        await session.execute(
            select(func.max(LoginAttempt.attempted_at))
            .where(LoginAttempt.username == username)
            .where(LoginAttempt.succeeded.is_(True))
        )
    ).scalar_one_or_none()

    statement = (
        select(LoginAttempt.attempted_at)
        .where(LoginAttempt.username == username)
        .where(LoginAttempt.succeeded.is_(False))
        .where(LoginAttempt.attempted_at > since)
        .order_by(LoginAttempt.attempted_at)
    )
    if last_success is not None:
        statement = statement.where(LoginAttempt.attempted_at > last_success)

    failures = list((await session.execute(statement)).scalars())
    if len(failures) < MAX_FAILURES:
        return 0
    return _wait_from(failures[MAX_FAILURES - 1])


def client_source(request: Request) -> str | None:
    """The address a request came from, as far as this server can tell."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64] or None
    return request.client.host[:64] if request.client else None


async def record_attempt(
    session: AsyncSession, username: str, *, succeeded: bool, source: str | None = None
) -> None:
    session.add(LoginAttempt(username=username, succeeded=succeeded, source=source))
    await session.execute(
        delete(LoginAttempt).where(LoginAttempt.attempted_at < datetime.now(UTC) - RETENTION)
    )
    await session.commit()
