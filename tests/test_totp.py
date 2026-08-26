"""The second factor.

Throttling makes guessing the password hopeless. This makes a *known* password
insufficient -- the case that matters once the login form is on the public internet.
"""

import httpx
import pyotp
import pytest
from sqlalchemy import select

from podarium.auth import hash_password
from podarium.config import get_settings
from podarium.main import app
from podarium.models import User
from podarium.totp import decrypt_secret, encrypt_secret, generate_secret

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
async def client(session):
    session.add(User(username="jw", password_hash=hash_password(PASSWORD)))
    await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def sign_in(client, **extra):
    return await client.post(
        "/api/auth/login", json={"username": "jw", "password": PASSWORD, **extra}
    )


async def enable(client, session) -> str:
    """Walk the real setup flow and return the secret."""
    await sign_in(client)
    setup = (await client.post("/api/auth/totp/setup")).json()
    secret = setup["secret"]
    response = await client.post(
        "/api/auth/totp/enable",
        params={"secret": secret},
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["totp_enabled"] is True
    return secret


async def test_setup_does_not_enable_anything(client, session):
    """Storing a secret that was never successfully scanned would lock the account out."""
    await sign_in(client)
    await client.post("/api/auth/totp/setup")

    user = (await session.execute(select(User))).scalar_one()
    await session.refresh(user)
    assert user.totp_secret is None
    assert (await client.get("/api/auth/me")).json()["totp_enabled"] is False


async def test_the_provisioning_uri_is_scannable(client):
    await sign_in(client)
    setup = (await client.post("/api/auth/totp/setup")).json()

    assert setup["provisioning_uri"].startswith("otpauth://totp/Podarium:jw?")
    assert setup["secret"] in setup["provisioning_uri"]


async def test_enabling_requires_a_working_code(client):
    await sign_in(client)
    secret = (await client.post("/api/auth/totp/setup")).json()["secret"]

    response = await client.post(
        "/api/auth/totp/enable", params={"secret": secret}, json={"code": "000000"}
    )

    assert response.status_code == 400
    assert (await client.get("/api/auth/me")).json()["totp_enabled"] is False


async def test_a_password_alone_no_longer_signs_in(client, session):
    secret = await enable(client, session)
    await client.post("/api/auth/logout")

    response = await sign_in(client)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "totp_required", (
        "the form has to tell 'now ask for a code' apart from 'wrong password'"
    )


async def test_the_password_and_a_code_together_work(client, session):
    secret = await enable(client, session)
    await client.post("/api/auth/logout")

    response = await sign_in(client, totp_code=pyotp.TOTP(secret).now())

    assert response.status_code == 200


async def test_a_wrong_code_is_refused(client, session):
    secret = await enable(client, session)
    await client.post("/api/auth/logout")

    assert (await sign_in(client, totp_code="000000")).status_code == 401


async def test_the_setup_code_still_works_for_the_next_sign_in(client, session):
    """Recording the setup step would refuse the code still on screen, and call it
    "invalid credentials" while doing it."""
    secret = await enable(client, session)
    await client.post("/api/auth/logout")

    assert (await sign_in(client, totp_code=pyotp.TOTP(secret).now())).status_code == 200


async def test_a_code_cannot_be_used_twice(client, session):
    """A code stays valid for its whole window, so one seen over a shoulder or in a log
    would otherwise be reusable."""
    secret = await enable(client, session)
    await client.post("/api/auth/logout")
    code = pyotp.TOTP(secret).now()

    assert (await sign_in(client, totp_code=code)).status_code == 200
    await client.post("/api/auth/logout")

    assert (await sign_in(client, totp_code=code)).status_code == 401


async def test_the_secret_is_not_stored_in_the_clear(client, session):
    """pgdata is the one directory here worth backing up, so it is the one most likely to
    end up somewhere else."""
    secret = await enable(client, session)

    user = (await session.execute(select(User))).scalar_one()
    await session.refresh(user)
    assert user.totp_secret is not None
    assert secret not in user.totp_secret
    assert decrypt_secret(user.totp_secret, get_settings().secret_key) == secret


async def test_an_unreadable_secret_says_so(client, session):
    """Changing SECRET_KEY must not look like a wrong code."""
    await enable(client, session)
    user = (await session.execute(select(User))).scalar_one()
    user.totp_secret = encrypt_secret(generate_secret(), "a-different-secret-key")
    await session.commit()
    await client.post("/api/auth/logout")

    response = await sign_in(client, totp_code="123456")

    assert response.status_code == 401
    assert "SECRET_KEY" in response.json()["error"]["message"]


async def test_disabling_requires_the_password(client, session):
    """A borrowed session must not be able to quietly remove the second factor."""
    await enable(client, session)

    refused = await client.post("/api/auth/totp/disable", json={"password": "wrong"})
    assert refused.status_code == 401
    assert (await client.get("/api/auth/me")).json()["totp_enabled"] is True

    accepted = await client.post("/api/auth/totp/disable", json={"password": PASSWORD})
    assert accepted.status_code == 200
    assert accepted.json()["totp_enabled"] is False


async def test_failed_codes_count_towards_the_lockout(client, session):
    """Otherwise the second factor is an unlimited guessing surface of its own."""
    from podarium.throttle import MAX_FAILURES

    await enable(client, session)
    await client.post("/api/auth/logout")

    for _ in range(MAX_FAILURES):
        assert (await sign_in(client, totp_code="000000")).status_code == 401

    assert (await sign_in(client, totp_code="000000")).status_code == 429
