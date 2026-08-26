"""One show, many URLs.

A podcast is commonly reachable at both its hosting platform's address and the publisher's
own -- Darknet Diaries answers at feeds.megaphone.fm/darknetdiaries and at
podcast.darknetdiaries.com, and Podcast Index reports the latter while a user may well have
subscribed with the former. Matching on the raw string subscribes the same show twice, and
because the rows have different ids the (feed_id, guid) constraint cannot see the duplicate
episodes: two copies of every download, and played state split across both.
"""

import httpx
import pytest
import respx
from sqlalchemy import select

from podarium.models import Feed
from podarium.subscribe import find_existing_feed, subscribe_feed
from podarium.urls import normalize_feed_url
from tests.feeds import build_feed

PLATFORM_URL = "https://feeds.megaphone.fm/darknetdiaries"
CANONICAL_URL = "https://podcast.darknetdiaries.com/"
FEED_XML = build_feed(
    title="Darknet Diaries",
    items=[{"guid": "ep-1", "title": "Episode 1", "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT"}],
)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://example.com/feed.xml", "https://example.com/feed.xml/"),
        ("https://example.com/feed.xml", "HTTPS://Example.COM/feed.xml"),
        ("http://example.com/feed.xml", "https://example.com/feed.xml"),
        ("https://example.com:443/feed.xml", "https://example.com/feed.xml"),
        ("https://example.com/feed.xml#top", "https://example.com/feed.xml"),
    ],
)
def test_equivalent_urls_normalise_together(left, right):
    assert normalize_feed_url(left) == normalize_feed_url(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Paths are case-sensitive on plenty of hosts.
        ("https://example.com/Feed.xml", "https://example.com/feed.xml"),
        # Private feeds carry their token in the query string.
        ("https://example.com/f?token=a", "https://example.com/f?token=b"),
        ("https://example.com/a.xml", "https://other.com/a.xml"),
    ],
)
def test_genuinely_different_urls_stay_different(left, right):
    assert normalize_feed_url(left) != normalize_feed_url(right)


async def test_feed_is_found_by_its_resolved_url(session):
    """Subscribed via the platform URL, then looked up by the canonical one."""
    session.add(Feed(feed_url=PLATFORM_URL, resolved_url=CANONICAL_URL))
    await session.commit()

    found = await find_existing_feed(session, feed_url="https://podcast.darknetdiaries.com")
    assert found is not None
    assert found.feed_url == PLATFORM_URL


async def test_feed_is_found_by_podcast_index_id(session):
    """The spec's stated reason for storing the id: a feed URL that has moved."""
    session.add(Feed(feed_url="https://old-host.example/feed.xml", podcast_index_id=577105))
    await session.commit()

    found = await find_existing_feed(
        session, feed_url="https://brand-new-host.example/feed.xml", podcast_index_id=577105
    )
    assert found is not None
    assert found.podcast_index_id == 577105


@respx.mock
async def test_subscribing_by_canonical_url_does_not_duplicate(session):
    respx.get(PLATFORM_URL).mock(
        return_value=httpx.Response(200, content=FEED_XML)
    )
    first, created = await subscribe_feed(session, PLATFORM_URL, user_agent="test")
    assert created is True

    # The refresh records where the feed actually served from.
    first.resolved_url = CANONICAL_URL
    await session.commit()

    respx.get(CANONICAL_URL).mock(return_value=httpx.Response(200, content=FEED_XML))
    second, created_again = await subscribe_feed(
        session, "https://podcast.darknetdiaries.com", user_agent="test", podcast_index_id=577105
    )

    assert created_again is False, "the same show must not be subscribed twice"
    assert second.id == first.id
    assert len((await session.execute(select(Feed))).scalars().all()) == 1


@respx.mock
async def test_subscribing_by_platform_url_follows_redirects_to_find_the_existing_feed(session):
    """The reverse direction: we hold the canonical URL, the user pastes the platform one.

    Nothing stored matches the address given, so it has to be followed before a new row is
    created.
    """
    session.add(Feed(feed_url=CANONICAL_URL, resolved_url=CANONICAL_URL))
    await session.commit()

    # The platform URL redirects to the canonical one, exactly as the real feed does.
    respx.get(PLATFORM_URL).mock(
        return_value=httpx.Response(301, headers={"Location": CANONICAL_URL})
    )
    respx.get(CANONICAL_URL).mock(return_value=httpx.Response(200, content=FEED_XML))

    feed, created = await subscribe_feed(session, PLATFORM_URL, user_agent="test")

    assert created is False
    assert len((await session.execute(select(Feed))).scalars().all()) == 1


@respx.mock
async def test_an_unrelated_feed_still_subscribes(session):
    """The guard must not collapse genuinely different shows into one."""
    session.add(Feed(feed_url=CANONICAL_URL, resolved_url=CANONICAL_URL))
    await session.commit()

    other = "https://example.com/other.xml"
    respx.get(other).mock(return_value=httpx.Response(200, content=FEED_XML))

    _, created = await subscribe_feed(session, other, user_agent="test")

    assert created is True
    assert len((await session.execute(select(Feed))).scalars().all()) == 2
