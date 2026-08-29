"""The number on the home-screen icon.

Counts episodes that arrived since the inbox was last looked at and have not been played.
The marker is the user's own, not each show's, so glancing at the inbox clears the badge
without clearing the new marker on shows that were never opened.
"""

from datetime import UTC, datetime, timedelta

from podarium.models import Episode, EpisodeState, Feed, FeedState
from podarium.services import mark_inbox_seen, unseen_episode_count


async def a_feed(session, **kwargs) -> Feed:
    feed = Feed(feed_url=f"https://example.com/{kwargs.pop('slug', 'a')}.xml", **kwargs)
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


async def an_episode(session, feed, guid, *, seen: datetime) -> Episode:
    episode = Episode(feed_id=feed.id, guid=guid, title=guid, first_seen_at=seen)
    session.add(episode)
    await session.commit()
    await session.refresh(episode)
    return episode


class TestUnseenCount:
    async def test_counts_what_arrived_since_the_inbox_was_looked_at(self, session, user):
        feed = await a_feed(session)
        now = datetime.now(UTC)
        await an_episode(session, feed, "old", seen=now - timedelta(days=2))
        await mark_inbox_seen(session, user.id)
        await session.commit()

        await an_episode(session, feed, "new-1", seen=datetime.now(UTC))
        await an_episode(session, feed, "new-2", seen=datetime.now(UTC))

        assert await unseen_episode_count(session, user.id) == 2

    async def test_looking_at_the_inbox_clears_it(self, session, user):
        feed = await a_feed(session)
        await an_episode(session, feed, "e1", seen=datetime.now(UTC))
        assert await unseen_episode_count(session, user.id) >= 1

        await mark_inbox_seen(session, user.id)
        await session.commit()
        assert await unseen_episode_count(session, user.id) == 0

    async def test_a_played_episode_does_not_count(self, session, user):
        # Marking something played from a notification should take it off the icon.
        feed = await a_feed(session)
        episode = await an_episode(session, feed, "e1", seen=datetime.now(UTC))
        session.add(
            EpisodeState(user_id=user.id, episode_id=episode.id, played=True)
        )
        await session.commit()

        assert await unseen_episode_count(session, user.id) == 0

    async def test_an_inactive_show_does_not_count(self, session, user):
        # Deactivating a feed is how you stop hearing from it; the icon must agree.
        feed = await a_feed(session, slug="b", active=False)
        await an_episode(session, feed, "e1", seen=datetime.now(UTC))

        assert await unseen_episode_count(session, user.id) == 0

    async def test_clearing_the_badge_leaves_a_shows_own_new_marker_alone(
        self, session, user
    ):
        # The whole reason for a separate marker: the inbox must not silently mark every
        # show as looked at.
        feed = await a_feed(session)
        await an_episode(session, feed, "e1", seen=datetime.now(UTC))
        session.add(
            FeedState(
                user_id=user.id,
                feed_id=feed.id,
                last_seen_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await session.commit()

        await mark_inbox_seen(session, user.id)
        await session.commit()

        state = await session.get(FeedState, {"user_id": user.id, "feed_id": feed.id})
        await session.refresh(state)
        assert state.last_seen_at < datetime.now(UTC) - timedelta(hours=12)
