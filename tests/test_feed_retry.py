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
