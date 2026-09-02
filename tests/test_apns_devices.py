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


class TestTheTestButtonReachesPhones:
    """"Send a test" exists to answer one question: does a device buzz.

    It sent to browsers only, so on an account with a phone registered it would light up
    the desktop and prove nothing about the device in your hand -- the exact failure the
    button is there to rule out.
    """

    async def test_a_registered_phone_is_included(self, client, session, user, monkeypatch):
        sent: list[dict] = []

        async def capture(**kwargs):
            sent.append(kwargs)
            return True

        monkeypatch.setattr(pushrelay, "configured", lambda: True)
        monkeypatch.setattr(pushrelay, "send", capture)

        await client.post(
            "/api/push/device",
            json={"device_token": "phone-token", "bundle_id": "com.jworthington.podarium"},
        )
        await client.post("/api/push/test")

        assert [item["device_token"] for item in sent] == ["phone-token"]

    async def test_nothing_is_sent_when_no_relay_is_configured(
        self, client, session, user, monkeypatch
    ):
        sent: list[dict] = []

        async def capture(**kwargs):
            sent.append(kwargs)
            return True

        monkeypatch.setattr(pushrelay, "configured", lambda: False)
        monkeypatch.setattr(pushrelay, "send", capture)

        await client.post(
            "/api/push/device",
            json={"device_token": "phone-token", "bundle_id": "com.jworthington.podarium"},
        )
        await client.post("/api/push/test")

        assert sent == []


class TestTheRelayKeyName:
    """PUSH_RELAY_API_KEY is what every other app on this relay already calls it.

    Podarium reading only PUSH_RELAY_KEY meant the value resolved to empty, the relay
    looked unconfigured, and notifications went to browsers while the phone stayed silent
    with nothing logged. Both names are accepted so the muscle memory is right.
    """

    def _settings(self, monkeypatch, **env):
        from podarium.config import Settings

        for name in ("PUSH_RELAY_KEY", "PUSH_RELAY_API_KEY", "PUSH_RELAY_URL"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return Settings(_env_file=None)

    def test_the_house_name_is_accepted(self, monkeypatch):
        settings = self._settings(
            monkeypatch, PUSH_RELAY_API_KEY="abc123", PUSH_RELAY_URL="http://relay"
        )
        assert settings.push_relay_key == "abc123"

    def test_the_plain_name_still_works(self, monkeypatch):
        # Whatever is already deployed must not break for the sake of tidiness.
        settings = self._settings(
            monkeypatch, PUSH_RELAY_KEY="abc123", PUSH_RELAY_URL="http://relay"
        )
        assert settings.push_relay_key == "abc123"

    def test_neither_leaves_it_unset(self, monkeypatch):
        assert self._settings(monkeypatch).push_relay_key is None


class TestARefusalIsNotAForgetting:
    """The relay answers 502 for a dead token and for an APNs outage alike.

    Forgetting the phone on the first refusal meant one bad hour at Apple's end lost every
    notification until the app was next opened. A device is forgotten only once the relay
    has refused it continuously for days.
    """

    async def _register(self, client):
        await client.post(
            "/api/push/device",
            json={"device_token": "phone-token", "bundle_id": "com.jworthington.podarium"},
        )

    async def test_one_refusal_keeps_the_device_and_starts_the_clock(
        self, client, session, user, monkeypatch
    ):
        async def refused(**kwargs):
            return False

        monkeypatch.setattr(pushrelay, "configured", lambda: True)
        monkeypatch.setattr(pushrelay, "send", refused)
        await self._register(client)

        await client.post("/api/push/test")

        rows = await devices(session, user)
        assert len(rows) == 1
        assert rows[0].failing_since is not None

    async def test_a_success_clears_the_clock(self, client, session, user, monkeypatch):
        from datetime import UTC, datetime, timedelta

        async def accepted(**kwargs):
            return True

        monkeypatch.setattr(pushrelay, "configured", lambda: True)
        monkeypatch.setattr(pushrelay, "send", accepted)
        await self._register(client)
        (device,) = await devices(session, user)
        device.failing_since = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        await client.post("/api/push/test")

        await session.refresh(device)
        assert device.failing_since is None
        assert device.last_used_at is not None

    async def test_days_of_refusals_forget_the_device(self, client, session, user, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from podarium.jobs.refresh import FORGET_DEVICE_AFTER

        async def refused(**kwargs):
            return False

        monkeypatch.setattr(pushrelay, "configured", lambda: True)
        monkeypatch.setattr(pushrelay, "send", refused)
        await self._register(client)
        (device,) = await devices(session, user)
        device.failing_since = datetime.now(UTC) - FORGET_DEVICE_AFTER - timedelta(hours=1)
        await session.commit()

        await client.post("/api/push/test")

        assert await devices(session, user) == []

    async def test_an_unreachable_relay_is_a_failed_send_not_a_crash(self, monkeypatch):
        """"Send a test" must report that the phone was not reached, not 500."""
        from podarium import config

        monkeypatch.setattr(config.get_settings(), "push_relay_url", "http://127.0.0.1:1")
        monkeypatch.setattr(config.get_settings(), "push_relay_key", "k")

        accepted = await pushrelay.send(
            device_token="t", bundle_id="b", title="x", body="y", user_agent="test"
        )
        assert accepted is False
