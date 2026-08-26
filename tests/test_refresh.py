"""Refresh must be idempotent, and must never let a publisher's pubDate rewrite our history."""

import httpx
import pytest
import respx
from sqlalchemy import select

from podarium.jobs.refresh import refresh_feed
from podarium.models import Episode, EpisodeState, Feed
from tests.feeds import build_feed

FEED_URL = "https://example.com/feed.xml"

ORIGINAL = build_feed(
    items=[
        {"guid": "ep-1", "title": "Episode 1", "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT"},
        {"guid": "ep-2", "title": "Episode 2", "pub_date": "Mon, 08 Jan 2024 10:00:00 GMT"},
    ]
)


async def _make_feed(session) -> Feed:
    feed = Feed(feed_url=FEED_URL)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


@respx.mock
async def test_refresh_is_idempotent(session):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=ORIGINAL))
    feed = await _make_feed(session)

    first = await refresh_feed(session, feed, user_agent="test")
    assert first.new_episodes == 2

    before = {
        (e.id, e.guid, e.first_seen_at, e.title)
        for e in (await session.execute(select(Episode))).scalars()
    }

    second = await refresh_feed(session, feed, user_agent="test")
    assert second.new_episodes == 0
    assert second.updated_episodes == 0

    after = {
        (e.id, e.guid, e.first_seen_at, e.title)
        for e in (await session.execute(select(Episode))).scalars()
    }
    assert before == after


@respx.mock
async def test_not_modified_is_a_no_op(session):
    route = respx.get(FEED_URL)
    route.side_effect = [
        httpx.Response(200, content=ORIGINAL, headers={"ETag": '"abc"'}),
        httpx.Response(304),
    ]
    feed = await _make_feed(session)

    await refresh_feed(session, feed, user_agent="test")
    assert feed.etag == '"abc"'

    outcome = await refresh_feed(session, feed, user_agent="test")
    assert outcome.not_modified is True
    assert outcome.new_episodes == 0
    # The conditional request must actually have carried the stored validator.
    assert route.calls[1].request.headers["If-None-Match"] == '"abc"'


@respx.mock
async def test_restamped_pubdate_does_not_resurrect_episodes(session, user):
    """The PBD Podcast case: a host migration re-stamps pubDate on the back catalogue.

    published_at may move, because it is display data. first_seen_at and played state must
    not, because they are what "is this new?" and "have I heard this?" are built on.
    """
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=ORIGINAL))
    feed = await _make_feed(session)
    await refresh_feed(session, feed, user_agent="test")

    episode = (
        await session.execute(select(Episode).where(Episode.guid == "ep-1"))
    ).scalar_one()
    original_first_seen = episode.first_seen_at
    original_id = episode.id

    session.add(EpisodeState(user_id=user.id, episode_id=episode.id, played=True, position_seconds=42))
    await session.commit()

    # Same GUIDs, brand new pubDates -- exactly what a migrating publisher emits.
    restamped = build_feed(
        items=[
            {"guid": "ep-1", "title": "Episode 1", "pub_date": "Fri, 16 Aug 2024 12:00:00 GMT"},
            {"guid": "ep-2", "title": "Episode 2", "pub_date": "Fri, 16 Aug 2024 12:00:00 GMT"},
        ]
    )
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=restamped))
    outcome = await refresh_feed(session, feed, user_agent="test")

    assert outcome.new_episodes == 0, "re-stamped episodes must not be treated as new"

    await session.refresh(episode)
    assert episode.id == original_id
    assert episode.first_seen_at == original_first_seen
    assert episode.published_at.strftime("%Y-%m-%d") == "2024-08-16"

    state = await session.get(EpisodeState, {"user_id": user.id, "episode_id": episode.id})
    assert state.played is True
    assert state.position_seconds == 42


@respx.mock
async def test_duplicate_guids_in_one_document_insert_once(session):
    duplicated = build_feed(
        items=[
            {"guid": "ep-1", "title": "Episode 1", "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT"},
            {"guid": "ep-1", "title": "Episode 1 (repost)", "pub_date": "Tue, 02 Jan 2024 10:00:00 GMT"},
        ]
    )
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=duplicated))
    feed = await _make_feed(session)

    outcome = await refresh_feed(session, feed, user_agent="test")
    assert outcome.new_episodes == 1
    assert (await session.execute(select(Episode))).scalars().all().__len__() == 1


@respx.mock
async def test_fetch_failure_backs_the_feed_off(session):
    respx.get(FEED_URL).mock(side_effect=httpx.ConnectError("boom"))
    feed = await _make_feed(session)

    outcome = await refresh_feed(session, feed, user_agent="test")
    assert outcome.error is not None
    assert feed.fetch_error_count == 1
    assert "ConnectError" in feed.fetch_error

    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=ORIGINAL))
    await refresh_feed(session, feed, user_agent="test")
    assert feed.fetch_error_count == 0
    assert feed.fetch_error is None
