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


def build_client(
    user_agent: str, *, follow_redirects: bool = True, guard_private: bool = True
) -> httpx.AsyncClient:
    """The only place outbound HTTP clients are constructed.

    Everything that leaves this process for a publisher host goes through here, which is
    what keeps the "one IP address, the server's" guarantee auditable -- and it is where
    the private-address guard attaches, for the same reason.

    ``guard_private=False`` is for the one kind of destination the guard is not about: an
    address the operator configured, rather than one a publisher supplied. The guard exists
    so a hostile feed cannot make this server probe the LAN it sits on; a push relay whose
    address came from an environment variable is not that, and self-hosted infrastructure
    lives on private addresses as a matter of course. Turning the guard off globally to
    reach one of them would drop it for every feed too, which is the trade this avoids.
    """
    settings = get_settings()
    guarded = guard_private and not settings.allow_private_fetch
    hooks = {"request": [_refuse_private_targets]} if guarded else {}
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        timeout=httpx.Timeout(settings.http_timeout_seconds, read=settings.http_timeout_seconds),
        follow_redirects=follow_redirects,
        event_hooks=hooks,
    )
