"""Per-show notification opt-out.

Without it, cadence decides everything. A show publishing hourly produces a notification
every hour around the clock, which drowns the weekly show you actually wanted telling about
and takes the whole feature down with it -- the only remaining control is switching
notifications off entirely.

On by default, because a show you just subscribed to is one you want to hear about.
"""

import httpx
import pytest
import respx

from podarium.auth import current_user
from podarium.jobs import refresh as refresh_job
from podarium.main import app
from podarium.models import Feed

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Hourly News</title>
  <item><guid>ep-1</guid><title>Bulletin</title>
    <enclosure url="https://publisher.example/1.mp3" type="audio/mpeg" length="100"/>
  </item>
</channel></rss>"""


@pytest.fixture
async def client(user):
    app.dependency_overrides[current_user] = lambda: user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def feed(session):
    row = Feed(feed_url="https://publisher.example/feed.xml", title="Hourly News")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def test_a_new_show_notifies_by_default(client, feed):
    """A show you just subscribed to is one you want to hear about."""
    assert (await client.get(f"/api/feeds/{feed.id}")).json()["notify"] is True


async def test_the_toggle_round_trips(client, feed):
    assert (await client.patch(f"/api/feeds/{feed.id}", json={"notify": False})).json()["notify"] is False
    assert (await client.patch(f"/api/feeds/{feed.id}", json={"notify": True})).json()["notify"] is True


async def test_an_omitted_field_does_not_change_it(client, feed):
    """Saving retention settings must not silently switch notifications back on."""
    await client.patch(f"/api/feeds/{feed.id}", json={"notify": False})

    body = (await client.patch(f"/api/feeds/{feed.id}", json={"active": True})).json()

    assert body["notify"] is False


@respx.mock
async def test_a_muted_show_sends_no_notification(session, user, feed, monkeypatch):
    """The point of the whole exercise: new episodes still arrive, silently."""
    respx.get("https://publisher.example/feed.xml").mock(
        return_value=httpx.Response(200, text=FEED_XML)
    )
    feed.notify = False
    await session.commit()

    sent: list[dict] = []

    async def record(session, user_id, payload, *, user_agent):
        sent.append(payload)
        return 1

    monkeypatch.setattr(refresh_job.push, "send_to_all", record)

    await refresh_job.refresh_due_feeds()

    assert sent == []
    # ...and the episode itself landed regardless. Muting is about the buzz, not the feed.
    assert (
        await session.execute(
            refresh_job.Episode.__table__.select().where(
                refresh_job.Episode.feed_id == feed.id
            )
        )
    ).all()


@respx.mock
async def test_an_unmuted_show_still_notifies(session, user, feed, monkeypatch):
    """The counterpart, so the test above cannot pass by nothing being sent at all."""
    respx.get("https://publisher.example/feed.xml").mock(
        return_value=httpx.Response(200, text=FEED_XML)
    )

    sent: list[dict] = []

    async def record(session, user_id, payload, *, user_agent):
        sent.append(payload)
        return 1

    monkeypatch.setattr(refresh_job.push, "send_to_all", record)

    await refresh_job.refresh_due_feeds()

    assert len(sent) == 1
    assert "1 new episode" in sent[0]["title"]
    assert sent[0]["body"] == "Hourly News"
