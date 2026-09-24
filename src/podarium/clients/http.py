import asyncio
import ipaddress
import socket

import httpcore
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
    except socket.gaierror as exc:
        # Unresolvable. The connection would fail a moment later anyway, so refusing here
        # costs nothing; waving it through meant a guard that had checked nothing.
        raise httpx.ConnectError(f"could not resolve {host}: {exc}") from exc
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

    This is the early, cheap refusal. The binding one is _PinnedBackend below: the
    address checked here is not necessarily the address connected to a moment later,
    because the connection does its own lookup, and a publisher who controls their DNS
    can answer differently the second time. The backend resolves once and connects to
    exactly what it checked.
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


class _PinnedBackend(httpcore.AnyIOBackend):
    """Connect only to an address that has just been checked.

    The hook above resolves a name and inspects the answers; the connection then resolved
    the name again for itself, and nothing tied the two together. A DNS server the
    publisher runs can hand the check a public address and the connection a private one
    -- rebinding -- and the check has then guarded nothing. Here the lookup happens at
    connect time, every answer is checked, and the socket is opened to the first one, so
    the address inspected is the address used. TLS still verifies against the hostname:
    httpcore passes it as the SNI name separately from where the socket goes.
    """

    async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
        bare = host.strip("[]")
        try:
            literal = ipaddress.ip_address(bare)
        except ValueError:
            literal = None

        if literal is not None:
            if _is_disallowed(literal):
                raise httpcore.ConnectError(f"refusing to connect to {host}")
            target = bare
        else:
            addresses = await _resolve(bare, port)
            for resolved in addresses:
                try:
                    address = ipaddress.ip_address(resolved)
                except ValueError:
                    continue
                if _is_disallowed(address):
                    raise httpcore.ConnectError(
                        f"refusing to connect to {host} (resolves to {resolved})"
                    )
            # The test resolver answers nothing; a real one raises when it cannot answer.
            target = addresses[0] if addresses else bare

        return await super().connect_tcp(
            target, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx's transport over a pool that connects through _PinnedBackend."""

    def __init__(self, *, limits: httpx.Limits) -> None:
        super().__init__(limits=limits)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            network_backend=_PinnedBackend(),
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
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=5)
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        timeout=httpx.Timeout(settings.http_timeout_seconds, read=settings.http_timeout_seconds),
        follow_redirects=follow_redirects,
        event_hooks=hooks,
        transport=_PinnedTransport(limits=limits) if guarded else None,
    )
