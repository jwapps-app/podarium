"""Notifications for the iOS app, which do not go to Apple from here.

They go to the shared relay that holds the one Apple key for every app on the account, so
what this server keeps is a device token and the bundle it belongs to.
"""

import httpx
import pytest
from sqlalchemy import select

from podarium.auth import current_user
from podarium.clients import pushrelay
from podarium.main import app
from podarium.models import ApnsDevice


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def devices(session, user) -> list[ApnsDevice]:
    return list(
        (
            await session.execute(
                select(ApnsDevice).where(ApnsDevice.user_id == user.id)
            )
        ).scalars()
    )


class TestRegistration:
    async def test_a_phone_registers(self, client, session, user):
        response = await client.post(
            "/api/push/device",
            json={"device_token": "abc123", "bundle_id": "com.jworthington.podarium"},
        )
        assert response.status_code == 204

        rows = await devices(session, user)
        assert len(rows) == 1
        assert rows[0].device_token == "abc123"
        assert rows[0].sandbox is False

    async def test_registering_the_same_token_twice_makes_one_row(
        self, client, session, user
    ):
        # iOS hands back the same token on every launch; each one should not add a device.
        body = {"device_token": "abc123", "bundle_id": "com.jworthington.podarium"}
        await client.post("/api/push/device", json=body)
        await client.post("/api/push/device", json=body)

        assert len(await devices(session, user)) == 1

    async def test_a_debug_build_is_marked_sandbox(self, client, session, user):
        # APNs rejects a sandbox token on the production host and the other way round,
        # and only the device knows which it was issued.
        await client.post(
            "/api/push/device",
            json={
                "device_token": "sandbox-token",
                "bundle_id": "com.jworthington.podarium",
                "sandbox": True,
            },
        )
        rows = await devices(session, user)
        assert rows[0].sandbox is True

    async def test_signing_out_forgets_the_device(self, client, session, user):
        await client.post(
            "/api/push/device",
            json={"device_token": "abc123", "bundle_id": "com.jworthington.podarium"},
        )
        response = await client.request(
            "DELETE", "/api/push/device", json={"device_token": "abc123"}
        )
        assert response.status_code == 204
        assert await devices(session, user) == []


class TestRelayConfiguration:
    def test_absent_configuration_turns_the_ios_half_off(self, monkeypatch):
        # Same shape as absent VAPID keys turning web push off: the feature is simply not
        # on rather than failing on every refresh.
        from podarium import config

        settings = config.get_settings()
        monkeypatch.setattr(settings, "push_relay_url", None)
        monkeypatch.setattr(settings, "push_relay_key", None)
        assert pushrelay.configured() is False

    def test_both_halves_are_needed(self, monkeypatch):
        from podarium import config

        settings = config.get_settings()
        monkeypatch.setattr(settings, "push_relay_url", "https://push.example.com")
        monkeypatch.setattr(settings, "push_relay_key", None)
        assert pushrelay.configured() is False


class TestTheRelayIsReachableOnALocalNetwork:
    """A self-hosted relay lives on a private address, and must still be reachable.

    The outbound guard exists so a hostile feed cannot make this server probe the network
    it sits on. A relay whose address came from this deployment's own configuration is the
    opposite case, and turning the guard off globally to reach it would drop it for every
    publisher feed as well.

    Both tests force the guard on regardless of the environment: development sets
    ALLOW_PRIVATE_FETCH so it can talk to a database on localhost, and without pinning it
    here these would pass by doing nothing.
    """

    @pytest.fixture(autouse=True)
    def guard_on(self, monkeypatch):
        from podarium import config

        monkeypatch.setattr(config.get_settings(), "allow_private_fetch", False)

    async def test_a_publisher_on_the_lan_is_still_refused(self):
        import httpx

        from podarium.clients.http import build_client

        async with build_client("Podarium/test") as client:
            with pytest.raises(httpx.RequestError, match="refusing to fetch"):
                await client.get("http://192.168.1.11:8088/notify")

    async def test_the_relay_client_is_allowed_through(self):
        # Port 1 on loopback: nothing listens, so this refuses at once. The point is only
        # that the refusal comes from the network rather than from the guard.
        import httpx

        from podarium.clients.http import build_client

        async with build_client("Podarium/test", guard_private=False) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("http://127.0.0.1:1/nothing-listens-here")

    async def test_loopback_is_refused_for_a_publisher_too(self):
        import httpx

        from podarium.clients.http import build_client

        async with build_client("Podarium/test") as client:
            with pytest.raises(httpx.RequestError, match="refusing to fetch"):
                await client.get("http://127.0.0.1:1/")
