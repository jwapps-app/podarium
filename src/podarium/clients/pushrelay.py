"""Notifications for the iOS app, sent through the shared relay.

Apple's key lives in the relay, not here. Every app on this account posts to it and it
talks to APNs, which means one key and one place that has to be kept current -- and this
server never needs an Apple credential at all.

Failures are swallowed by the caller for the same reason web push failures are: a relay
that is unreachable is not a reason for a feed refresh to be recorded as failed, and the
episodes are already saved.
"""

from __future__ import annotations

import logging

import httpx

from podarium.clients.http import build_client
from podarium.config import get_settings

log = logging.getLogger(__name__)

def configured() -> bool:
    settings = get_settings()
    return bool(settings.push_relay_url and settings.push_relay_key)


async def send(
    *,
    device_token: str,
    bundle_id: str,
    title: str,
    body: str,
    badge: int | None = None,
    sandbox: bool = False,
    user_agent: str,
) -> bool:
    """One notification to one device. True when the relay accepted it."""
    settings = get_settings()
    if not configured():
        return False

    payload: dict = {
        "bundle_id": bundle_id,
        "device_token": device_token,
        "title": title,
        "body": body,
    }
    # Only when there is a number to show. Omitting it leaves whatever the icon already
    # had, which is right for a notification that is not about the count.
    if badge is not None:
        payload["badge"] = badge
    if sandbox:
        payload["sandbox"] = True

    # Through the shared client, as every outbound request is -- but without the
    # private-address guard. That guard is there so a hostile feed cannot make this server
    # probe its own network; the relay's address comes from this deployment's own
    # configuration, and self-hosted infrastructure sits on a private address as a matter
    # of course. The alternative was ALLOW_PRIVATE_FETCH, which would also have dropped
    # the guard for every publisher feed.
    try:
        async with build_client(user_agent, guard_private=False) as client:
            response = await client.post(
                f"{settings.push_relay_url.rstrip('/')}/notify",
                json=payload,
                headers={"X-API-Key": settings.push_relay_key},
            )
    except httpx.HTTPError as exc:
        # The relay being down is a failure to deliver, not a reason for the caller to
        # blow up: "send a test" should say it did not reach the phone, not 500.
        log.warning("push relay unreachable: %s", exc)
        return False

    if response.status_code >= 400:
        # The relay does not say which. A dead token, an APNs outage and a revoked key
        # all come back as one status, so the caller must not read a refusal as "this
        # device is gone".
        log.warning(
            "push relay refused a notification: %s %s",
            response.status_code,
            response.text[:200],
        )
        return False
    return True
