"""Web Push registration and delivery.

The parts worth testing are the ones that fail quietly in production: a server with no keys
must not pretend push works, a device that re-enables must not end up notified twice, and a
subscription the push service has retired must be dropped rather than retried forever.
"""

import httpx
import pytest
import respx

from podarium import push
from podarium.auth import current_user
from podarium.config import get_settings
from podarium.main import app
from podarium.models import PushSubscription

ENDPOINT = "https://push.example/send/abc123"

# A real subscription's keys: p256dh is an uncompressed P-256 point, auth is 16 bytes.
# Encoding is exercised for real here rather than mocked, because a malformed key is
# precisely the failure that only shows up on a device.
P256DH = (
    "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U"
)
AUTH = "tBHItJI5svbpez7KI4CCXg"


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def keys(monkeypatch):
    """A real generated keypair, so signing is exercised rather than stubbed."""
    from podarium.vapid import Vapid02, public_key_b64

    vapid = Vapid02()
    vapid.generate_keys()

    settings = get_settings()
    monkeypatch.setattr(settings, "vapid_public_key", public_key_b64(vapid), raising=False)
    monkeypatch.setattr(
        settings, "vapid_private_key", vapid.private_pem().decode(), raising=False
    )
    monkeypatch.setattr(settings, "vapid_contact", "mailto:test@example.com", raising=False)
    return settings


@pytest.fixture
def no_keys(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "vapid_public_key", None, raising=False)
    monkeypatch.setattr(settings, "vapid_private_key", None, raising=False)
    return settings


async def subscribe(client) -> httpx.Response:
    return await client.post(
        "/api/push", json={"endpoint": ENDPOINT, "p256dh": P256DH, "auth": AUTH}
    )


async def test_without_keys_the_server_says_push_is_off(client, no_keys):
    """The client uses this to decide whether to offer the button at all."""
    body = (await client.get("/api/push/config")).json()

    assert body["public_key"] is None
    assert body["subscribed"] is False


async def test_subscribing_without_keys_is_refused_rather_than_stored(client, no_keys):
    """A stored subscription that can never be signed for is worse than none."""
    assert (await subscribe(client)).status_code == 503


async def test_subscribing_twice_from_one_device_stores_one_row(client, keys, session):
    """Browsers hand back the same endpoint, and two rows would mean two notifications."""
    await subscribe(client)
    await subscribe(client)

    rows = (
        await session.execute(PushSubscription.__table__.select())
    ).all()
    assert len(rows) == 1
    assert (await client.get("/api/push/config")).json()["subscribed"] is True


async def test_unsubscribing_removes_the_device(client, keys):
    await subscribe(client)

    await client.delete("/api/push", params={"endpoint": ENDPOINT})

    assert (await client.get("/api/push/config")).json()["subscribed"] is False


@respx.mock
async def test_a_payload_is_signed_and_encrypted_before_it_leaves(client, keys, session, user):
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(201))
    await subscribe(client)

    sent = await push.send_to_all(
        session, user.id, {"title": "Hello"}, user_agent="Podarium/test"
    )

    assert sent == 1
    request = route.calls[0].request
    # VAPID identifies the sender; aes128gcm is the payload encryption.
    assert request.headers["authorization"].startswith("vapid")
    assert request.headers["content-encoding"] == "aes128gcm"
    # The body must not be the plaintext -- the push service is not supposed to read it.
    assert b"Hello" not in request.content


@respx.mock
async def test_a_retired_endpoint_is_dropped_not_retried(client, keys, session, user):
    """410 Gone means the browser was reinstalled or permission revoked. Nothing about that
    subscription will ever work again, so keeping it just means failing forever."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(410))
    await subscribe(client)

    sent = await push.send_to_all(session, user.id, {"title": "x"}, user_agent="Podarium/test")

    assert sent == 0
    assert (await client.get("/api/push/config")).json()["subscribed"] is False


@respx.mock
async def test_a_transient_failure_keeps_the_subscription(client, keys, session, user):
    """A push service returning 500, or being unreachable, is not the device's fault."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(500))
    await subscribe(client)

    sent = await push.send_to_all(session, user.id, {"title": "x"}, user_agent="Podarium/test")

    assert sent == 0
    assert (await client.get("/api/push/config")).json()["subscribed"] is True


async def test_sending_with_no_keys_is_a_no_op(session, user, no_keys):
    assert await push.send_to_all(session, user.id, {"title": "x"}, user_agent="t") == 0
