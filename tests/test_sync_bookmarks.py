"""Bookmarks travel in the delta.

The iOS app mirrors from /api/sync. Anything missing from that response is something the
phone silently never learns about -- it works, and the feature just does not exist there.
Bookmarks were added after sync was written and had never joined it.
"""

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Bookmark, Episode, Feed


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def episode(session):
    feed = Feed(feed_url="https://example.com/s.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    row = Episode(feed_id=feed.id, guid="e1", title="The one about bookmarks")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


class TestSyncCarriesBookmarks:
    async def test_a_bookmark_appears_in_the_delta(self, client, session, user, episode):
        session.add(
            Bookmark(
                user_id=user.id,
                episode_id=episode.id,
                position_seconds=2480,
                note="the bit about trimming",
            )
        )
        await session.commit()

        payload = (await client.get("/api/sync")).json()

        assert len(payload["bookmarks"]) == 1
        got = payload["bookmarks"][0]
        assert got["position_seconds"] == 2480
        assert got["note"] == "the bit about trimming"
        # Denormalised, so a client can render the list without a request per episode.
        assert got["episode_title"] == "The one about bookmarks"
        assert got["feed_id"] == episode.feed_id

    async def test_a_deleted_bookmark_disappears(self, client, session, user, episode):
        # The reason these are sent whole rather than as a delta: a removed row carries no
        # updated_at, so a delta keyed on one would never mention it and a mirror would
        # hold the bookmark for ever.
        bookmark = Bookmark(
            user_id=user.id, episode_id=episode.id, position_seconds=10
        )
        session.add(bookmark)
        await session.commit()
        await session.refresh(bookmark)

        assert len((await client.get("/api/sync")).json()["bookmarks"]) == 1

        await client.delete(f"/api/bookmarks/{bookmark.id}")
        assert (await client.get("/api/sync")).json()["bookmarks"] == []

    async def test_another_persons_bookmarks_are_not_included(
        self, client, session, user, episode
    ):
        from podarium.models import User

        other = User(username="someone-else", password_hash="x")
        session.add(other)
        await session.commit()
        await session.refresh(other)
        session.add(
            Bookmark(user_id=other.id, episode_id=episode.id, position_seconds=5)
        )
        await session.commit()

        assert (await client.get("/api/sync")).json()["bookmarks"] == []

    async def test_intermediate_pages_leave_them_out(self, client, session, user, episode):
        # Paging a large backfill repeats nothing that is sent whole; the client gets them
        # on the final page. Matches how the queue and feeds are already handled.
        session.add(
            Bookmark(user_id=user.id, episode_id=episode.id, position_seconds=1)
        )
        await session.commit()

        from podarium.cursor import encode_cursor

        cursor = encode_cursor(episode.updated_at, 0)
        payload = (await client.get(f"/api/sync?cursor={cursor}")).json()
        assert payload["bookmarks"] == []
