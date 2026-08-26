"""Login throttling.

The login form is the only thing between the internet and this server, and it guards a
single password with no second factor. Without a limit, an attacker gets unlimited guesses
at whatever rate the network allows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.models import LoginAttempt

# Five wrong answers inside a quarter of an hour buys a quarter of an hour of silence.
# Generous enough that fat-fingering a password twice costs nothing, tight enough that
# guessing is hopeless: a hundred attempts a day against a password worth having is not a
# meaningful attack.
MAX_FAILURES = 5
WINDOW = timedelta(minutes=15)

# Attempts are kept for a while after they stop counting, so a look at the table answers
# "has anyone been trying?" rather than only "am I locked out right now?".
RETENTION = timedelta(days=7)


async def seconds_until_unlocked(session: AsyncSession, username: str) -> int:
    """How long this username must wait, or 0 if it may try now."""
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

    # The clock runs from the failure that crossed the line, not from the latest one, so
    # hammering the endpoint cannot extend the lockout indefinitely.
    unlocks_at = failures[MAX_FAILURES - 1] + WINDOW
    if unlocks_at.tzinfo is None:
        unlocks_at = unlocks_at.replace(tzinfo=UTC)
    return max(0, int((unlocks_at - datetime.now(UTC)).total_seconds()) + 1)


async def record_attempt(session: AsyncSession, username: str, *, succeeded: bool) -> None:
    session.add(LoginAttempt(username=username, succeeded=succeeded))
    await session.execute(
        delete(LoginAttempt).where(LoginAttempt.attempted_at < datetime.now(UTC) - RETENTION)
    )
    await session.commit()
