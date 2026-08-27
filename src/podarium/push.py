"""Web Push, so a new episode can reach a closed app.

Two halves, both handled here. VAPID signs a claim that this server is the sender, using a
keypair the browser is shown at subscribe time and checks on every delivery. And the
payload is encrypted to keys the browser generated, so the push service -- Apple's,
Google's, Mozilla's -- routes a message it cannot read.

pywebpush does the encoding; the request itself goes out through the shared HTTP client
rather than pywebpush's own, so every outbound request this process makes still leaves from
one place. That is the property the whole design rests on and it is worth a little glue.

Keys come from the environment. Absent, push is simply off: subscribing reports that the
server has no keys, nothing is sent, and no other feature notices.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import httpx
from py_vapid import Vapid02
from pywebpush import WebPusher
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.clients.http import build_client
from podarium.config import get_settings
from podarium.models import PushSubscription

log = logging.getLogger("podarium")

# How long a signed VAPID claim stays valid. The spec caps this at 24 hours; a shorter life
# just means re-signing more often, and signing is cheap.
TTL_SECONDS = 12 * 60 * 60

# How long the push service should hold a message for a device that is offline. A podcast
# notification a week late is noise, so it expires with the day.
MESSAGE_TTL = 24 * 60 * 60


class PushUnavailable(RuntimeError):
    """No VAPID keys configured, so pushes cannot be signed."""


def public_key() -> str | None:
    """The application server key the browser needs in order to subscribe."""
    return get_settings().vapid_public_key or None


def _vapid() -> Vapid02:
    settings = get_settings()
    if not settings.vapid_private_key:
        raise PushUnavailable("VAPID_PRIVATE_KEY is not set")
    return Vapid02.from_pem(settings.vapid_private_key.encode())


def _claim_for(endpoint: str) -> dict[str, str]:
    """The audience is the push service's origin, not ours -- it verifies its own hostname."""
    parsed = urlparse(endpoint)
    settings = get_settings()
    return {
        "aud": f"{parsed.scheme}://{parsed.netloc}",
        "sub": settings.vapid_contact,
    }


async def send_to_all(
    session: AsyncSession, user_id: int, payload: dict, *, user_agent: str
) -> int:
    """Deliver a payload to every device this user has registered. Returns the count sent.

    A push service answering 404 or 410 is telling us the subscription is dead -- the
    browser was reinstalled, the user cleared site data, permission was revoked. Those rows
    are deleted rather than retried, because nothing about them will ever work again.
    """
    if not public_key():
        return 0

    subscriptions = (
        await session.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )
    ).scalars().all()
    if not subscriptions:
        return 0

    body = json.dumps(payload).encode()
    dead: list[int] = []
    sent = 0

    async with build_client(user_agent) as client:
        for subscription in subscriptions:
            try:
                request = _encode(subscription, body)
            except Exception as exc:  # noqa: BLE001 - encoding must not break a refresh
                log.warning("could not encode push for %s: %s", subscription.endpoint, exc)
                continue

            try:
                response = await client.post(
                    subscription.endpoint, content=request["body"], headers=request["headers"]
                )
            except httpx.HTTPError as exc:
                log.info("push delivery failed: %s", exc)
                continue

            if response.status_code in (404, 410):
                dead.append(subscription.id)
            elif response.status_code >= 400:
                log.info(
                    "push rejected with %s: %s", response.status_code, response.text[:200]
                )
            else:
                sent += 1

    if dead:
        await session.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
    await session.commit()
    return sent


def _encode(subscription: PushSubscription, body: bytes) -> dict:
    """Encrypt the payload and build the VAPID-signed headers for one endpoint."""
    pusher = WebPusher(
        {
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        }
    )
    encoded = pusher.encode(body)

    headers = {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(MESSAGE_TTL),
    }
    headers.update(_vapid().sign(_claim_for(subscription.endpoint)))
    return {"body": encoded["body"], "headers": headers}
