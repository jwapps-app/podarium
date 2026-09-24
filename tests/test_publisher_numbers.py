"""A publisher's number that does not fit its column must not fail the whole refresh.

The flush that stores episodes used to sit outside refresh_feed's error handling, so
an out-of-range duration failed the refresh *after* the fetch had succeeded: nothing was
recorded, nothing backed off, and the scheduler tried again on its next pass, for ever.
"""

import httpx
import respx
from sqlalchemy import select

from podarium.clients.feedfetch import INT32_MAX, parse_duration
from podarium.jobs import refresh as refresh_module
from podarium.jobs.refresh import refresh_feed
from podarium.models import Episode, Feed
from tests.feeds import build_feed

FEED_URL = "https://example.com/feed.xml"


async def _make_feed(session) -> Feed:
    feed = Feed(feed_url=FEED_URL)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


def test_durations_past_the_column_are_dropped_not_stored():
    assert parse_duration(str(INT32_MAX)) == INT32_MAX
    assert parse_duration(str(INT32_MAX + 1)) is None
    assert parse_duration("99999999:00:00") is None
    assert parse_duration("01:02:03") == 3723


@respx.mock
async def test_an_absurd_duration_does_not_fail_the_feed(session):
    xml = build_feed(items=[
        {"guid": "ep-1", "title": "Episode 1", "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT",
         "duration": str(2**31), "length": str(2**63)},
    ])
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=xml))
    feed = await _make_feed(session)

    outcome = await refresh_feed(session, feed, user_agent="test")

    assert outcome.error is None
    assert outcome.new_episodes == 1
    episode = (await session.execute(select(Episode))).scalar_one()
    assert episode.duration_seconds is None
    assert episode.enclosure_bytes is None


@respx.mock
async def test_a_failure_while_storing_backs_the_feed_off(session, monkeypatch):
    xml = build_feed(items=[
        {"guid": "ep-1", "title": "Episode 1", "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT"},
    ])
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=xml))
    feed = await _make_feed(session)

    async def explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(refresh_module, "apply_auto_download_window", explode)
    outcome = await refresh_feed(session, feed, user_agent="test")

    assert outcome.error is not None and "disk on fire" in outcome.error
    await session.refresh(feed)
    assert feed.fetch_error_count == 1, "recorded, so the next pass backs off"
    assert feed.last_fetched_at is not None
    assert (await session.execute(select(Episode))).scalars().all() == [], "rolled back"
