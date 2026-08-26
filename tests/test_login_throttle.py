"""Throttling the login form.

It is the only thing between the internet and this server, and it guards a single password
with no second factor. Unthrottled, an attacker gets unlimited guesses at whatever rate the
network allows.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from podarium.auth import hash_password
from podarium.main import app
from podarium.models import LoginAttempt, User
from podarium.throttle import MAX_FAILURES, WINDOW

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
async def client(session):
    session.add(User(username="jw", password_hash=hash_password(PASSWORD)))
    await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def attempt(client, password: str, username: str = "jw"):
    return await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


async def test_a_correct_password_works(client):
    assert (await attempt(client, PASSWORD)).status_code == 200


async def test_a_few_wrong_answers_do_not_lock_anything(client):
    """Fat-fingering a password twice must cost nothing."""
    for _ in range(MAX_FAILURES - 1):
        assert (await attempt(client, "wrong")).status_code == 401

    assert (await attempt(client, PASSWORD)).status_code == 200


async def test_repeated_failures_lock_the_account(client):
    for _ in range(MAX_FAILURES):
        assert (await attempt(client, "wrong")).status_code == 401

    response = await attempt(client, "wrong")
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert int(response.headers["Retry-After"]) > 0


async def test_the_lock_holds_even_against_the_right_password(client):
    """Otherwise an attacker who guesses correctly on attempt six still gets in."""
    for _ in range(MAX_FAILURES):
        await attempt(client, "wrong")

    assert (await attempt(client, PASSWORD)).status_code == 429


async def test_hammering_does_not_extend_the_lock(session, client):
    """The clock runs from the failure that crossed the line, not the most recent one."""
    for _ in range(MAX_FAILURES):
        await attempt(client, "wrong")
    first = int((await attempt(client, "wrong")).headers["Retry-After"])

    for _ in range(5):
        await attempt(client, "wrong")
    later = int((await attempt(client, "wrong")).headers["Retry-After"])

    assert later <= first, "continued attempts must not push the unlock further out"


async def test_the_lock_expires(session, client):
    for _ in range(MAX_FAILURES):
        await attempt(client, "wrong")
    assert (await attempt(client, PASSWORD)).status_code == 429

    # Age every recorded failure past the window.
    for row in (await session.execute(select(LoginAttempt))).scalars():
        row.attempted_at = datetime.now(UTC) - WINDOW - timedelta(minutes=1)
    await session.commit()

    assert (await attempt(client, PASSWORD)).status_code == 200


async def test_a_success_resets_the_reckoning(session, client):
    """A bad patch earlier must not shorten the allowance later."""
    for _ in range(MAX_FAILURES - 1):
        await attempt(client, "wrong")
    assert (await attempt(client, PASSWORD)).status_code == 200

    for _ in range(MAX_FAILURES - 1):
        assert (await attempt(client, "wrong")).status_code == 401
    assert (await attempt(client, PASSWORD)).status_code == 200


async def test_an_unknown_username_is_indistinguishable(client):
    """The reply must not reveal whether the account exists."""
    known = await attempt(client, "wrong", username="jw")
    unknown = await attempt(client, "wrong", username="someone-else")

    assert known.status_code == unknown.status_code == 401
    assert known.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_attempts_are_recorded_for_inspection(session, client):
    """So the table answers "has anyone been trying?", not only "am I locked out?"."""
    await attempt(client, "wrong")
    await attempt(client, PASSWORD)

    rows = (await session.execute(select(LoginAttempt).order_by(LoginAttempt.id))).scalars().all()
    assert [r.succeeded for r in rows] == [False, True]
