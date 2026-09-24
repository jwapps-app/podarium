"""Feed URL identity.

The same show reaches us under more than one URL. Podcast Index hands back the publisher's
canonical address (``https://podcast.darknetdiaries.com``) while a user may well have
subscribed via the hosting platform (``https://feeds.megaphone.fm/darknetdiaries``), and
OPML exports carry whatever URL that app happened to store.

Matching on the raw string means the same show can be subscribed twice, and because the
two rows have different ids, the ``(feed_id, guid)`` constraint does not stop the episodes
being duplicated along with them -- two copies of every download and a played state split
across both.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_feed_url(url: str) -> str:
    """A comparison key for feed URLs. Not for fetching -- always fetch the stored URL.

    Scheme and host are lower-cased and the default port dropped, both of which are
    case- and redundancy-insensitive per RFC 3986. http and https are folded together,
    because a publisher moving to TLS is not a different feed.

    The path keeps its case and the query is preserved: plenty of private feeds carry a
    token in the query string, and some hosts genuinely serve different feeds from paths
    that differ only in case.
    """
    if not url:
        return ""

    parts = urlsplit(url.strip())

    # A bare "example.com/feed.xml" parses as all-path; treat it as a host.
    if not parts.scheme and not parts.netloc:
        parts = urlsplit(f"https://{url.strip()}")

    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        # "example.com:abc" -- not a port. Keep it as written rather than raise from a
        # comparison key; the fetch will report the address in its own words.
        port = None
        host = parts.netloc.rsplit("@", 1)[-1].lower()
    if port is not None and _DEFAULT_PORTS.get(parts.scheme.lower()) != str(port):
        host = f"{host}:{port}"
    # Credentials in the URL are part of which feed this is: two private feeds on one
    # host differ in nothing else.
    if parts.username:
        host = f"{parts.username}@{host}"

    path = parts.path.rstrip("/")

    # Fragments never identify a feed.
    return urlunsplit(("https", host, path, parts.query, ""))


_CREDENTIAL_PARAMS = ("token", "key", "auth", "secret", "pass", "sig", "access", "subscri")


def looks_private(url: str) -> bool:
    """Whether a feed URL appears to carry a subscriber's credentials.

    A guess from the URL's shape, since nothing else is recorded: user:pass@ in the
    authority, or a query parameter named like a token. Used to keep such URLs away
    from discovery services and share links, where erring towards private costs a
    description and erring the other way leaks a paid feed.
    """
    parts = urlsplit(url.strip())
    if parts.username or parts.password:
        return True
    names = [pair.split("=", 1)[0].lower() for pair in parts.query.split("&") if pair]
    return any(hint in name for name in names for hint in _CREDENTIAL_PARAMS)
