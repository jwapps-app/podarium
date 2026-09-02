import asyncio
import ipaddress
import socket

import httpx

from podarium.config import get_settings

# Hostnames that mean "this machine" without needing DNS to say so.
_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_disallowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Anything that is not a public, routable address.

    ``is_global`` covers the obvious ranges -- loopback, RFC 1918, link-local, multicast,
    unspecified -- and the less obvious ones a hand-written list forgets: carrier-grade NAT
    (100.64/10), the benchmarking block, and the IPv4-mapped IPv6 forms of all of them.
    """
    return not address.is_global


async def _resolve_via_dns(host: str, port: int) -> list[str]:
    """Every address the OS would connect to for this host, as strings."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # Unresolvable. Let the connection fail on its own and say so in its own words;
        # refusing here would report every typo as a security decision.
        return []
    return [info[4][0] for info in infos]


# Indirection so the test suite can stub resolution: most tests fetch from hosts that do
# not exist, and a real lookup per request is a DNS timeout per test on an offline machine.
_resolve = _resolve_via_dns


async def _refuse_private_targets(request: httpx.Request) -> None:
    """Outbound guard: no publisher-derived fetch may target a private address.

    Runs per request, so a redirect into the LAN is caught as well as a direct URL.

    The host is *resolved*, not pattern-matched. A string check on literal IPs looks
    sufficient and is not: "2130706433", "0x7f000001", "127.1" and "0" are not addresses
    to the ``ipaddress`` module, so they read as hostnames -- and the resolver turns every
    one of them into 127.0.0.1 and connects. Asking the OS what it would connect to
    catches those spellings and the plain case of a hostname that points inside the
    network, at the price of one lookup the resolver caches anyway.

    What this still does not close is DNS rebinding proper: an answer that changes between
    this lookup and the connection a moment later. Pinning the resolved address into the
    transport is the cure, and more machinery than a single-user server warrants.
    """
    host = request.url.host.strip("[]")
    if host.lower() in _LOCAL_NAMES:
        raise httpx.RequestError(f"refusing to fetch from {host}", request=request)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if _is_disallowed(literal):
            raise httpx.RequestError(f"refusing to fetch from {host}", request=request)
        return

    port = request.url.port or _DEFAULT_PORTS.get(request.url.scheme, 80)
    for resolved in await _resolve(host, port):
        try:
            address = ipaddress.ip_address(resolved)
        except ValueError:
            continue
        if _is_disallowed(address):
            raise httpx.RequestError(
                f"refusing to fetch from {host} (resolves to {resolved})", request=request
            )


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
