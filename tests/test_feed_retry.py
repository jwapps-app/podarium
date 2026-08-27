"""Retrying a feed whose body arrived broken.

Megaphone occasionally truncates the Joe Rogan feed -- a 5 MB gzip document that starts
decoding cleanly and ends early, surfacing as "invalid distance code". Twenty consecutive
fetches succeeded while reproducing it, so it is a CDN hiccup rather than anything about
the feed or the client.

Without a retry the cost is out of proportion to the cause: one blip doubles that feed's
backoff and leaves a red "last refresh failed" banner on the show page until the next
success, potentially hours later.
"""

import httpx
import pytest
import respx

from podarium.clients.feedfetch import fetch_feed

URL = "https://feeds.example/show.xml"

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>A Show</title>
  <item><guid>ep-1</guid><title>Episode</title></item>
</channel></rss>"""


@respx.mock
async def test_a_broken_body_is_refetched_once(monkeypatch):
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    route = respx.get(URL).mock(
        side_effect=[
            httpx.DecodingError("Error -3 while decompressing data: invalid distance code"),
            httpx.Response(200, text=FEED_XML),
        ]
    )

    result = await fetch_feed(URL, user_agent="test")

    assert route.call_count == 2
    assert result.parsed is not None
    assert result.parsed.title == "A Show"


@respx.mock
async def test_a_decoding_failure_is_retried_uncompressed(monkeypatch):
    """A gzip stream that will not decompress can be a corrupted compressed object cached
    at the CDN edge. Asking the same way again reaches the same cache and fails the same
    way, so the retry asks for a different representation with no decompressor in it."""
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    seen: list[str | None] = []

    def respond(request):
        seen.append(request.headers.get("accept-encoding"))
        if len(seen) == 1:
            raise httpx.DecodingError("invalid distance code", request=request)
        return httpx.Response(200, text=FEED_XML)

    respx.get(URL).mock(side_effect=respond)

    result = await fetch_feed(URL, user_agent="test")

    assert result.parsed is not None
    assert seen[0] != "identity", "the first attempt should accept compression normally"
    assert seen[1] == "identity", "the retry should ask for the uncompressed feed"


@respx.mock
async def test_other_broken_bodies_retry_the_same_way(monkeypatch):
    """Only a decoding failure implicates the compressed representation. A dropped
    connection says nothing about it, so that retry is left alone."""
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    seen: list[str | None] = []

    def respond(request):
        seen.append(request.headers.get("accept-encoding"))
        if len(seen) == 1:
            raise httpx.ReadError("connection dropped")
        return httpx.Response(200, text=FEED_XML)

    respx.get(URL).mock(side_effect=respond)

    await fetch_feed(URL, user_agent="test")

    assert seen[1] != "identity"


@respx.mock
async def test_a_short_uncompressed_body_is_caught(monkeypatch):
    """Without a decompressor nothing else notices a truncated document -- it would parse
    into a feed quietly missing its oldest episodes."""
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, content=b"<rss><channel><title>cut off", headers={"content-length": "99999"}
        )
    )

    with pytest.raises(httpx.RemoteProtocolError, match="truncated feed"):
        await fetch_feed(URL, user_agent="test")


@respx.mock
async def test_it_gives_up_after_one_retry(monkeypatch):
    """Two failures in a row is not a blip, and the exponential backoff should take over
    rather than this loop retrying forever."""
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    route = respx.get(URL).mock(side_effect=httpx.DecodingError("still broken"))

    with pytest.raises(httpx.DecodingError):
        await fetch_feed(URL, user_agent="test")

    assert route.call_count == 2


@respx.mock
async def test_a_timeout_is_not_retried(monkeypatch):
    """A slow or down host is what backoff exists for. Retrying here would double what a
    sick publisher costs every refresh pass."""
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    route = respx.get(URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    with pytest.raises(httpx.ReadTimeout):
        await fetch_feed(URL, user_agent="test")

    assert route.call_count == 1


@respx.mock
async def test_an_http_error_is_not_retried(monkeypatch):
    """404 is an answer, not a failure to get one."""
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    route = respx.get(URL).mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_feed(URL, user_agent="test")

    assert route.call_count == 1


@respx.mock
async def test_a_good_response_is_fetched_once(monkeypatch):
    monkeypatch.setattr("podarium.clients.feedfetch.RETRY_PAUSE_SECONDS", 0)
    route = respx.get(URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    await fetch_feed(URL, user_agent="test")

    assert route.call_count == 1


class TestBackoffShape:
    """How long a failing feed waits before it is tried again.

    Backoff protects a host that is down. A single blip is not that, and treating it as
    such leaves a "last refresh failed" banner standing for hours over a fault that lasted
    milliseconds.
    """

    @staticmethod
    def _due_after(error_count: int, interval_seconds: int = 3600) -> int:
        from podarium.jobs.refresh import MAX_BACKOFF_DOUBLINGS

        return interval_seconds * 2 ** min(max(0, error_count - 1), MAX_BACKOFF_DOUBLINGS)

    def test_a_healthy_feed_refreshes_on_the_normal_interval(self):
        assert self._due_after(0) == 3600

    def test_one_failure_does_not_delay_the_next_attempt(self):
        assert self._due_after(1) == 3600

    def test_backoff_starts_at_the_second_consecutive_failure(self):
        assert self._due_after(2) == 7200
        assert self._due_after(3) == 14400

    def test_it_is_capped_so_a_dead_feed_is_still_checked_eventually(self):
        from podarium.jobs.refresh import MAX_BACKOFF_DOUBLINGS

        capped = self._due_after(50)
        assert capped == 3600 * 2**MAX_BACKOFF_DOUBLINGS
        assert capped == self._due_after(MAX_BACKOFF_DOUBLINGS + 1)
