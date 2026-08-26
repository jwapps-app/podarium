"""Inbox ordering: chronological by publication, capped at discovery.

Two failure modes pull in opposite directions.

Sorting on published_at alone reads naturally until a publisher migrates hosts and
re-stamps pubDate across its archive -- the case first_seen_at exists for -- at which point
hundreds of old episodes leap to the top of the inbox.

Sorting on first_seen_at alone is immune to that but clumps by subscription: a show added
today puts its whole back catalogue above episodes from shows added last week, whatever
their real dates.

Taking the earlier of the two gives publication order in the normal case and caps a
re-stamped episode at the moment it was first seen, so it cannot climb.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, Feed

NOW = datetime.now(UTC)


@pytest.fixture
async def client(session, user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _feed(session, url: str, title: str) -> Feed:
    feed = Feed(feed_url=url, title=title)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


async def _episode(session, feed: Feed, guid: str, *, published, first_seen) -> Episode:
    episode = Episode(
        feed_id=feed.id, guid=guid, title=guid, published_at=published, first_seen_at=first_seen
    )
    session.add(episode)
    await session.commit()
    return episode


async def _titles(client, **params) -> list[str]:
    body = (await client.get("/api/episodes", params={"limit": 50, **params})).json()
    return [item["title"] for item in body["items"]]


async def test_shows_interleave_by_publication_not_subscription(session, client):
    """The case that prompted this: an old show subscribed today must not bury a newer one
    subscribed last week."""
    old_show = await _feed(session, "https://a.example/f.xml", "Subscribed today")
    new_show = await _feed(session, "https://b.example/f.xml", "Subscribed last week")

    # A back catalogue, all discovered a moment ago.
    await _episode(session, old_show, "archive-2019", published=NOW - timedelta(days=2000), first_seen=NOW)
    await _episode(session, old_show, "archive-2024", published=NOW - timedelta(days=400), first_seen=NOW)

    # An episode from a show subscribed earlier, published between them.
    await _episode(
        session, new_show, "recent", published=NOW - timedelta(days=1), first_seen=NOW - timedelta(days=7)
    )

    assert await _titles(client) == ["recent", "archive-2024", "archive-2019"]


async def test_a_restamped_archive_does_not_leap_to_the_top(session, client):
    """The PBD Podcast case. A host migration re-stamps pubDate on the whole back
    catalogue; those episodes must stay where they were."""
    feed = await _feed(session, "https://c.example/f.xml", "Migrating show")

    await _episode(
        session, feed, "genuinely-new", published=NOW - timedelta(hours=1), first_seen=NOW - timedelta(hours=1)
    )
    # Seen a year ago, but the publisher just stamped today's date on it.
    await _episode(session, feed, "restamped-old", published=NOW, first_seen=NOW - timedelta(days=365))

    assert await _titles(client) == ["genuinely-new", "restamped-old"]


async def test_a_future_pubdate_cannot_pin_an_episode_to_the_top(session, client):
    """Feeds do publish dates in the future by mistake; discovery caps them."""
    feed = await _feed(session, "https://d.example/f.xml", "Show")

    await _episode(session, feed, "normal", published=NOW - timedelta(minutes=5), first_seen=NOW)
    await _episode(session, feed, "dated-next-year", published=NOW + timedelta(days=365), first_seen=NOW - timedelta(days=30))

    assert await _titles(client) == ["normal", "dated-next-year"]


async def test_an_episode_without_a_publication_date_falls_back_to_discovery(session, client):
    feed = await _feed(session, "https://e.example/f.xml", "Show")

    await _episode(session, feed, "undated", published=None, first_seen=NOW - timedelta(days=1))
    await _episode(session, feed, "older", published=NOW - timedelta(days=10), first_seen=NOW - timedelta(days=10))

    assert await _titles(client) == ["undated", "older"]


async def test_paging_matches_the_sort_order(session, client):
    """The cursor is keyed to the same expression, so pages must not skip or repeat."""
    feed = await _feed(session, "https://f.example/f.xml", "Show")
    for index in range(12):
        await _episode(
            session,
            feed,
            f"ep-{index:02d}",
            published=NOW - timedelta(days=index),
            first_seen=NOW,
        )

    collected: list[str] = []
    cursor = None
    for _ in range(10):
        params = {"limit": 5, **({"cursor": cursor} if cursor else {})}
        body = (await client.get("/api/episodes", params=params)).json()
        collected += [item["title"] for item in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert collected == [f"ep-{i:02d}" for i in range(12)]
    assert len(collected) == len(set(collected))


# --- unsubscribed shows -------------------------------------------------------
#
# `active` is a soft unsubscribe: the show keeps its episodes and their played state, so
# re-subscribing loses nothing. But it should stop filling the inbox, or unsubscribing
# hides the show from the library while its episodes carry on arriving.


async def test_the_inbox_hides_unsubscribed_shows(session, client):
    kept = await _feed(session, "https://kept.example/f.xml", "Still subscribed")
    dropped = await _feed(session, "https://gone.example/f.xml", "Unsubscribed")
    dropped.active = False
    await session.commit()

    await _episode(session, kept, "kept-ep", published=NOW - timedelta(days=1), first_seen=NOW)
    await _episode(session, dropped, "dropped-ep", published=NOW, first_seen=NOW)

    assert await _titles(client) == ["kept-ep"]


async def test_the_show_page_still_lists_them(session, client):
    """Asking for one show means you want that show -- otherwise the library's
    Unsubscribed section would lead to an empty page."""
    dropped = await _feed(session, "https://gone.example/f.xml", "Unsubscribed")
    dropped.active = False
    await session.commit()
    await _episode(session, dropped, "dropped-ep", published=NOW, first_seen=NOW)

    assert await _titles(client, feed_id=dropped.id) == ["dropped-ep"]


async def test_resubscribing_brings_them_back(session, client):
    """Nothing was deleted, only hidden."""
    feed = await _feed(session, "https://back.example/f.xml", "Returning")
    feed.active = False
    await session.commit()
    await _episode(session, feed, "ep", published=NOW, first_seen=NOW)
    assert await _titles(client) == []

    feed.active = True
    await session.commit()

    assert await _titles(client) == ["ep"]
