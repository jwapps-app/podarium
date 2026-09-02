"""The guards this server points at itself.

Once a feed is subscribed, its enclosure, artwork and chapter URLs are all controlled by
the publisher -- and this server's whole design is that it fetches them. These tests pin
the limits on what "fetches them" can be made to mean.
"""

import httpx
import pytest
import respx

from podarium.auth import current_user
from podarium.clients.http import build_client
from podarium.config import get_settings
from podarium.main import app
from podarium.models import User


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def guard_on(monkeypatch):
    """The dev .env disables the guard so local verify can preview its own server;
    production leaves it on. These tests exercise the production shape."""
    monkeypatch.setattr(get_settings(), "allow_private_fetch", False, raising=False)


class TestOutboundGuard:
    @pytest.mark.parametrize(
        "target",
        [
            "http://127.0.0.1:9001/admin",
            "http://192.168.1.1/",
            "http://10.0.0.5/api",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]:8080/",
            "http://localhost:9443/portainer",
            "http://0.0.0.0/",
        ],
    )
    async def test_private_targets_are_refused(self, guard_on, target):
        async with build_client("test") as outbound:
            with pytest.raises(httpx.RequestError, match="refusing"):
                await outbound.get(target)

    @respx.mock
    async def test_public_hostnames_pass_through(self, guard_on):
        respx.get("https://publisher.example/feed.xml").mock(
            return_value=httpx.Response(200, text="ok")
        )
        async with build_client("test") as outbound:
            response = await outbound.get("https://publisher.example/feed.xml")
        assert response.status_code == 200

    @respx.mock
    async def test_a_redirect_into_the_lan_is_caught(self, guard_on):
        """The guard runs per request, so a public feed 302ing to the router is stopped
        at the redirect, not waved through because the first hop looked fine."""
        respx.get("https://publisher.example/feed.xml").mock(
            return_value=httpx.Response(302, headers={"Location": "http://192.168.1.1/"})
        )
        async with build_client("test") as outbound:
            with pytest.raises(httpx.RequestError, match="refusing"):
                await outbound.get("https://publisher.example/feed.xml")


class TestMetricsToken:
    async def test_without_a_token_configured_metrics_stay_open(self, client):
        assert (await client.get("/metrics")).status_code == 200

    async def test_with_a_token_configured_the_scrape_must_carry_it(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "metrics_token", "s3cret", raising=False)

        assert (await client.get("/metrics")).status_code == 401
        assert (
            await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        assert (
            await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
        ).status_code == 200


class TestPushEndpointScheme:
    async def test_a_plain_http_push_endpoint_is_refused(self, client, monkeypatch):
        """The endpoint is a URL this server POSTs to on a schedule; every real push
        service is https, so anything else is a standing delivery instruction."""
        monkeypatch.setattr(get_settings(), "vapid_public_key", "x", raising=False)

        response = await client.post(
            "/api/push",
            json={"endpoint": "http://192.168.1.30:9000/hook", "p256dh": "k", "auth": "a"},
        )

        assert response.status_code == 422


class TestSecurityHeaders:
    async def test_every_response_carries_the_policy(self, client):
        response = await client.get("/healthz")

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

    async def test_scripts_are_locked_to_this_origin(self, client):
        """The part of the CSP that matters: markup the sanitiser missed still cannot
        execute script or fetch from a publisher host in a browser that honours CSP."""
        csp = (await client.get("/healthz")).headers["content-security-policy"]

        assert "script-src 'self'" in csp
        assert "default-src 'self'" in csp
        assert "unsafe-inline" not in csp.split("style-src")[0]  # never in script-src


class TestTheGuardResolvesWhatItIsGiven:
    """A string check on the host is not a check on where the connection goes.

    "2130706433", "0x7f000001", "127.1" and "0" are not addresses to the ipaddress module,
    so a guard that only parses literals waves them through as hostnames -- and the
    resolver turns every one of them into 127.0.0.1. The guard now asks the OS what it
    would connect to. These tests use the real resolver for exactly that reason; the rest
    of the suite stubs it.
    """

    @pytest.fixture(autouse=True)
    def real_resolver(self, monkeypatch):
        from podarium.clients import http as outbound

        monkeypatch.setattr(outbound, "_resolve", outbound._resolve_via_dns)

    @pytest.mark.parametrize(
        "target",
        [
            "http://2130706433/",
            "http://0x7f000001/",
            "http://127.1/",
            "http://0/",
        ],
    )
    async def test_loopback_spelled_differently_is_still_loopback(self, guard_on, target):
        async with build_client("test") as outbound:
            with pytest.raises(httpx.RequestError, match="refusing"):
                await outbound.get(target)

    async def test_a_hostname_that_resolves_inside_the_network_is_refused(
        self, guard_on, monkeypatch
    ):
        """The case the old docstring admitted it did not cover: a publisher's own DNS
        pointing a name at the router."""
        from podarium.clients import http as outbound

        async def points_at_the_router(host: str, port: int) -> list[str]:
            return ["192.168.1.1"]

        monkeypatch.setattr(outbound, "_resolve", points_at_the_router)

        async with build_client("test") as client:
            with pytest.raises(httpx.RequestError, match="resolves to 192.168.1.1"):
                await client.get("http://cdn.publisher.example/art.jpg")

    async def test_carrier_grade_nat_counts_as_private(self, guard_on):
        # 100.64/10 is not in the RFC 1918 list and is exactly the kind of range a
        # hand-written check forgets. is_global knows about it.
        async with build_client("test") as outbound:
            with pytest.raises(httpx.RequestError, match="refusing"):
                await outbound.get("http://100.64.0.1/")


class TestTheDevelopmentSecretIsRefused:
    def test_a_real_deployment_will_not_start_on_it(self):
        from types import SimpleNamespace

        from podarium.config import INSECURE_SECRET_KEY
        from podarium.main import check_secret_key

        settings = SimpleNamespace(secret_key=INSECURE_SECRET_KEY, run_background_jobs=True)
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            check_secret_key(settings)

    def test_tests_are_exempt(self):
        from types import SimpleNamespace

        from podarium.config import INSECURE_SECRET_KEY
        from podarium.main import check_secret_key

        check_secret_key(SimpleNamespace(secret_key=INSECURE_SECRET_KEY, run_background_jobs=False))

    def test_a_private_key_passes(self):
        from types import SimpleNamespace

        from podarium.main import check_secret_key

        check_secret_key(SimpleNamespace(secret_key="x" * 64, run_background_jobs=True))


class TestValidationErrorsDoNotEchoInput:
    async def test_a_rejected_login_does_not_contain_the_password(self, client):
        response = await client.post(
            "/api/auth/login", json={"username": "jw", "password": 12345}
        )
        assert response.status_code == 422
        message = response.json()["error"]["message"]
        assert "12345" not in message
        assert "password" in message
