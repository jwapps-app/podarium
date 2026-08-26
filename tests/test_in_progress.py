"""The resume list: episodes you started and did not finish.

Two things make this list useful rather than noise. It is ordered by when you last
listened, not by when the episode was published -- the whole point is picking up where you
left off, and catalogue order buries the episode you paused ten minutes ago under a
fortnight-old one. And it is bounded at both ends, because a tap-and-stop is not a
commitment and the closing credits are not unfinished business.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, EpisodeState, Feed, User

HOUR = 3600


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def make(session, user):
    """Build an episode with a playback state, and return it."""
    feed = Feed(feed_url="https://example.com/feed.xml", title="Show")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)

    counter = {"n": 0}

    async def build(
        *,
        position: int,
        duration: int | None = HOUR,
        played: bool = False,
        last_played_at: datetime | None = None,
        published_at: datetime | None = None,
        state: bool = True,
    ) -> Episode:
        counter["n"] += 1
        episode = Episode(
            feed_id=feed.id,
            guid=f"ep-{counter['n']}",
            title=f"Episode {counter['n']}",
            duration_seconds=duration,
            published_at=published_at or datetime.now(UTC),
        )
        session.add(episode)
        await session.commit()
        await session.refresh(episode)
        if state:
            session.add(
                EpisodeState(
                    user_id=user.id,
                    episode_id=episode.id,
                    position_seconds=position,
                    played=played,
                    last_played_at=last_played_at,
                )
            )
            await session.commit()
        return episode

    return build


async def titles(client, **params) -> list[str]:
    response = await client.get("/api/episodes", params={"in_progress": "true", **params})
    return [item["title"] for item in response.json()["items"]]


async def test_lists_only_what_was_started_and_not_finished(client, make):
    started = await make(position=10 * 60)
    await make(position=0)  # never touched
    await make(position=10 * 60, played=True)  # finished
    await make(position=0, state=False)  # no state row at all

    assert await titles(client) == [started.title]


async def test_a_tap_and_stop_is_not_in_progress(client, make):
    """Ten seconds in is not a commitment, and nothing else would ever clear it."""
    await make(position=10)
    kept = await make(position=10 * 60)

    assert await titles(client) == [kept.title]


async def test_the_closing_credits_are_not_unfinished_business(client, make):
    """Under a minute left is done, whatever the played flag says."""
    await make(position=HOUR - 30, duration=HOUR)
    kept = await make(position=HOUR - 600, duration=HOUR)

    assert await titles(client) == [kept.title]


async def test_an_episode_with_no_duration_is_kept(client, make):
    """Plenty of feeds omit it. Better a stale row than dropping one you did start."""
    kept = await make(position=10 * 60, duration=None)

    assert await titles(client) == [kept.title]


async def test_ordered_by_when_you_last_listened_not_by_publication(client, make):
    """The episode paused ten minutes ago comes first, even if it is the older episode."""
    now = datetime.now(UTC)
    old_episode_heard_recently = await make(
        position=10 * 60,
        published_at=now - timedelta(days=14),
        last_played_at=now - timedelta(minutes=10),
    )
    new_episode_heard_last_week = await make(
        position=10 * 60,
        published_at=now,
        last_played_at=now - timedelta(days=7),
    )

    assert await titles(client) == [
        old_episode_heard_recently.title,
        new_episode_heard_last_week.title,
    ]


async def test_paging_holds_the_resume_order(client, make):
    """The cursor keys off the same expression the ordering does, so nothing repeats."""
    now = datetime.now(UTC)
    for index in range(5):
        await make(position=10 * 60, last_played_at=now - timedelta(hours=index))

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params = {"in_progress": "true", "limit": 2, **({"cursor": cursor} if cursor else {})}
        body = (await client.get("/api/episodes", params=params)).json()
        seen.extend(item["title"] for item in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert seen == sorted(set(seen), key=seen.index)
    assert len(seen) == 5


async def test_position_writes_stamp_last_played_at_and_other_writes_do_not(
    client, session, make, user
):
    """Starring an old half-finished episode must not pass for listening to it."""
    episode = await make(position=10 * 60, last_played_at=datetime.now(UTC) - timedelta(days=30))
    state = await session.get(EpisodeState, {"user_id": user.id, "episode_id": episode.id})
    original = state.last_played_at

    await client.put(f"/api/episodes/{episode.id}/state", json={"starred": True})
    await session.refresh(state)
    assert state.last_played_at == original

    await client.put(f"/api/episodes/{episode.id}/state", json={"position_seconds": 700})
    await session.refresh(state)
    assert state.last_played_at > original


async def test_another_users_progress_is_not_mine(client, session, make):
    """State is per-user, so someone else's half-finished episode is not on my list."""
    stranger = User(username="stranger", password_hash="x")
    session.add(stranger)
    await session.commit()
    await session.refresh(stranger)

    episode = await make(position=0, state=False)
    session.add(
        EpisodeState(
            user_id=stranger.id,
            episode_id=episode.id,
            position_seconds=10 * 60,
            last_played_at=datetime.now(UTC),
        )
    )
    await session.commit()

    assert await titles(client) == []
