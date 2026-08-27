"""Searching your own subscriptions.

/api/search talks to Podcast Index and answers "does this show exist". This answers the
different question of "where is that episode I already have", which nothing could ask
before -- with thousands of episodes across a handful of shows, remembering which show
something was on is the whole difficulty.
"""

import httpx
import pytest

from podarium.auth import current_user
from podarium.main import app
from podarium.models import Episode, Feed


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def library(session):
    show = Feed(feed_url="https://example.com/a.xml", title="The Gardening Hour")
    other = Feed(feed_url="https://example.com/b.xml", title="Rocket Talk")
    session.add_all([show, other])
    await session.commit()
    await session.refresh(show)
    await session.refresh(other)

    session.add_all(
        [
            Episode(feed_id=show.id, guid="g1", title="Pruning roses in winter"),
            Episode(
                feed_id=show.id,
                guid="g2",
                title="#412",
                description_html="<p>Composting for beginners</p>",
            ),
            Episode(feed_id=other.id, guid="g3", title="Staging separation explained"),
            Episode(feed_id=other.id, guid="g4", title="Discount codes: 50% off"),
            # Contains "50" but not "50%", so an unescaped query would match it too.
            Episode(feed_id=other.id, guid="g5", title="Chapter 504 revisited"),
        ]
    )
    await session.commit()
    return {"show": show, "other": other}


async def titles(client, q: str) -> set[str]:
    response = await client.get("/api/episodes", params={"q": q, "limit": 50})
    return {item["title"] for item in response.json()["items"]}


async def test_matches_episode_titles_case_insensitively(client, library):
    assert await titles(client, "ROSES") == {"Pruning roses in winter"}


async def test_matches_the_show_name(client, library):
    """"Everything from Rocket Talk" is a search people actually type."""
    assert await titles(client, "rocket talk") == {
        "Staging separation explained",
        "Discount codes: 50% off",
        "Chapter 504 revisited",
    }


async def test_matches_the_description(client, library):
    """A show that titles its episodes "#412" puts every searchable word in the notes."""
    assert await titles(client, "composting") == {"#412"}


async def test_a_percent_sign_is_literal_not_a_wildcard(client, library):
    """Unescaped, "50%" would become a LIKE wildcard and match the entire library."""
    assert await titles(client, "50%") == {"Discount codes: 50% off"}


async def test_an_underscore_is_literal_too(client, library):
    """_ matches any single character in LIKE, so "s_aging" would find "staging"."""
    assert await titles(client, "s_aging") == set()


async def test_search_combines_with_the_other_filters(client, library, session):
    """Filters narrow the search rather than replacing it."""
    episodes = (await client.get("/api/episodes", params={"q": "rocket talk"})).json()["items"]
    marked = episodes[0]["id"]
    await client.put(f"/api/episodes/{marked}/state", json={"played": True})

    response = await client.get(
        "/api/episodes", params={"q": "rocket talk", "unplayed": "true"}
    )
    returned = {item["id"] for item in response.json()["items"]}

    assert marked not in returned
    assert len(returned) == 2


async def test_blank_query_is_not_a_filter(client, library):
    """Whitespace is what an empty search box sends, and it should not hide everything."""
    response = await client.get("/api/episodes", params={"q": "   ", "limit": 50})

    assert len(response.json()["items"]) == 5
