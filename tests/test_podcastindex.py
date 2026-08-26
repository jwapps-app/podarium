"""Podcast Index request signing and the no-credentials path.

Apple is deliberately absent from discovery; these tests pin the Podcast Index contract so
a refactor cannot quietly swap in a different provider or drop a required header.
"""

import hashlib

import pytest

from podarium.clients.podcastindex import (
    PodcastIndexUnavailable,
    build_auth_headers,
    search_by_term,
)
from podarium.config import get_settings


def test_auth_headers_match_the_documented_scheme():
    headers = build_auth_headers("KEY", "SECRET", "Podarium/0.1.0", unix_seconds=1700000000)

    assert headers["X-Auth-Key"] == "KEY"
    assert headers["X-Auth-Date"] == "1700000000"
    assert headers["Authorization"] == hashlib.sha1(b"KEYSECRET1700000000").hexdigest()
    assert headers["User-Agent"] == "Podarium/0.1.0"


def test_signature_changes_with_the_timestamp():
    first = build_auth_headers("K", "S", "UA", unix_seconds=1700000000)["Authorization"]
    second = build_auth_headers("K", "S", "UA", unix_seconds=1700000001)["Authorization"]
    assert first != second


async def test_missing_credentials_raise_a_typed_error():
    """Surfaces as a 503 rather than a crash, which is the expected state until keys exist."""
    settings = get_settings()
    assert not settings.podcastindex_configured

    with pytest.raises(PodcastIndexUnavailable):
        await search_by_term("anything", user_agent="test")
