"""Notifications for the iOS app, which do not go to Apple from here.

They go to the shared relay that holds the one Apple key for every app on the account, so
what this server keeps is a device token and the bundle it belongs to.
"""

import httpx
import pytest

from podarium.auth import current_user
from podarium.clients import pushrelay
from podarium.main import app
from podarium.models import ApnsDevice
from sqlalchemy import select


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
