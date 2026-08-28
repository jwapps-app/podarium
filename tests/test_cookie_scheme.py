"""The session cookie's Secure flag follows the request, not the configuration.

One server is reachable two ways: through a hostname that terminates TLS, and directly on
the LAN over plain HTTP. A single configured answer is wrong for one of them, and wrong in
an unhelpful direction -- a browser silently discards a Secure cookie over http, so signing
in appears to succeed and every request afterwards is anonymous, with nothing in any log.
"""

import httpx
import pytest

from podarium.auth import hash_password
from podarium.config import get_settings
from podarium.main import app
from podarium.models import User

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def account(session):
    user = User(username="tester", password_hash=hash_password(PASSWORD))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
def public_https(monkeypatch):
    """The deployment this bug appeared in: PUBLIC_URL is the TLS hostname."""
    monkeypatch.setattr(get_settings(), "public_url", "https://podarium.example", raising=False)


async def login(headers: dict | None = None, base: str = "http://test") -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=base) as client:
        return await client.post(
            "/api/auth/login",
            json={"username": "tester", "password": PASSWORD},
            headers=headers or {},
        )


async def test_plain_http_gets_a_cookie_the_browser_will_keep(account, public_https):
    """The reported failure: signing in on the LAN address left every request anonymous."""
    response = await login()

    assert response.status_code == 200
    assert "secure" not in response.headers["set-cookie"].lower()


async def test_a_tls_terminating_proxy_still_gets_a_secure_cookie(account, public_https):
    """Cloudflare reaches the server over plain http and says so in this header. Without
    honouring it, the public hostname would hand out an unflagged cookie."""
    response = await login(headers={"X-Forwarded-Proto": "https"})

    assert "secure" in response.headers["set-cookie"].lower()


async def test_a_chain_of_proxies_is_read_left_to_right(account, public_https):
    """The header accumulates; the first entry is the client-facing scheme."""
    response = await login(headers={"X-Forwarded-Proto": "https, http"})

    assert "secure" in response.headers["set-cookie"].lower()


async def test_direct_https_needs_no_header(account, public_https):
    response = await login(base="https://test")

    assert "secure" in response.headers["set-cookie"].lower()


async def test_the_cookie_is_still_locked_down_either_way(account, public_https):
    """Dropping Secure over http must not quietly drop the other protections with it."""
    cookie = (await login()).headers["set-cookie"].lower()

    assert "httponly" in cookie
    assert "samesite=lax" in cookie
