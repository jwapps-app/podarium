import ipaddress

import httpx

from podarium.config import get_settings

# Hostnames that mean "this machine" without needing DNS to say so.
_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


async def _refuse_private_targets(request: httpx.Request) -> None:
    """Outbound guard: no publisher-derived fetch may target a private address.

    Runs per request, so a redirect into the LAN is caught as well as a direct URL. Only
    literal IPs and localhost names are checked -- resolving hostnames here would break
    nothing but add a DNS round trip per request, and a hostname pointed somewhere private
    (DNS rebinding) defeats resolve-then-connect checks anyway unless the resolved address
    is pinned for the actual connection. Defence, not a boundary.
    """
    host = request.url.host
    if host.lower().strip("[]") in _LOCAL_NAMES:
        raise httpx.RequestError(f"refusing to fetch from {host}", request=request)
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return  # A hostname; let DNS and the connection proceed.
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise httpx.RequestError(f"refusing to fetch from {host}", request=request)


def build_client(user_agent: str, *, follow_redirects: bool = True) -> httpx.AsyncClient:
    """The only place outbound HTTP clients are constructed.

    Everything that leaves this process for a publisher host goes through here, which is
    what keeps the "one IP address, the server's" guarantee auditable -- and it is where
    the private-address guard attaches, for the same reason.
    """
    settings = get_settings()
    hooks = {} if settings.allow_private_fetch else {"request": [_refuse_private_targets]}
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        timeout=httpx.Timeout(settings.http_timeout_seconds, read=settings.http_timeout_seconds),
        follow_redirects=follow_redirects,
        event_hooks=hooks,
    )
