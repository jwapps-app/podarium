"""Download jobs: idempotent, atomic, and honest about truncation."""

from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select

from podarium.config import get_settings
from podarium.jobs.downloader import run_job, requeue_stuck_jobs, target_path
from podarium.models import DownloadJob, Episode, Feed, JobSource, JobState
from podarium.services import enqueue_download

AUDIO_URL = "https://cdn.example.com/ep-1.mp3"
PAYLOAD = b"ID3" + b"\x00" * 4000


async def _episode(session, **kwargs) -> Episode:
    feed = Feed(feed_url=f"https://example.com/{id(kwargs)}.xml")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    episode = Episode(
        feed_id=feed.id,
        guid="ep-1",
        enclosure_url=AUDIO_URL,
        enclosure_type="audio/mpeg",
        **kwargs,
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode)
    return episode


async def _job(session, episode, source=JobSource.manual) -> DownloadJob:
    job = DownloadJob(
        episode_id=episode.id, source=source, state=JobState.running, next_attempt_at=datetime.now(UTC)
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@respx.mock
async def test_download_writes_the_file_and_records_it(session):
    respx.get(AUDIO_URL).mock(
        return_value=httpx.Response(200, content=PAYLOAD, headers={"Content-Length": str(len(PAYLOAD))})
    )
    episode = await _episode(session, enclosure_bytes=len(PAYLOAD))
    job = await _job(session, episode)

    await run_job(session, job, user_agent="test")

    assert job.state is JobState.done
    await session.refresh(episode)
    assert episode.local_path is not None
    path = target_path(episode)
    assert path.read_bytes() == PAYLOAD
    assert episode.local_bytes == len(PAYLOAD)
    # No .part file left behind.
    assert not path.with_suffix(path.suffix + ".part").exists()


@respx.mock
async def test_job_for_an_already_downloaded_episode_completes_without_fetching(session):
    route = respx.get(AUDIO_URL).mock(return_value=httpx.Response(200, content=PAYLOAD))

    existing = get_settings().download_dir / "already.mp3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(PAYLOAD)

    episode = await _episode(session, local_path=str(existing), local_bytes=len(PAYLOAD))
    job = await _job(session, episode)

    await run_job(session, job, user_agent="test")

    assert job.state is JobState.done
    assert route.call_count == 0, "an episode already on disk must not be re-fetched"


@respx.mock
@pytest.mark.parametrize(
    "declared",
    [
        pytest.param(len(PAYLOAD) - 500, id="ad-insertion-makes-the-file-larger"),
        pytest.param(len(PAYLOAD) * 8, id="stale-feed-metadata-overstates-a-short-episode"),
    ],
)
async def test_declared_enclosure_length_never_rejects_a_complete_download(session, declared):
    """<enclosure length> is wrong in both directions in the wild.

    Dynamic ad insertion makes the served file larger than declared; stale or copy-pasted
    metadata can overstate a short episode several times over. Neither is a failed
    download, and rejecting either would strand real episodes in a retry loop.
    """
    respx.get(AUDIO_URL).mock(
        return_value=httpx.Response(200, content=PAYLOAD, headers={"Content-Length": str(len(PAYLOAD))})
    )
    episode = await _episode(session, enclosure_bytes=declared)
    job = await _job(session, episode)

    await run_job(session, job, user_agent="test")

    assert job.state is JobState.done
    await session.refresh(episode)
    assert episode.local_bytes == len(PAYLOAD)


@respx.mock
async def test_truncated_transfer_fails_and_retries(session):
    """A short read against the transfer's own Content-Length is a real truncation."""
    respx.get(AUDIO_URL).mock(
        return_value=httpx.Response(200, content=PAYLOAD, headers={"Content-Length": "999999"})
    )
    episode = await _episode(session, enclosure_bytes=len(PAYLOAD))
    job = await _job(session, episode)

    await run_job(session, job, user_agent="test")

    assert job.state is JobState.queued, "should retry, not give up"
    assert job.attempts == 1
    assert "truncated" in job.last_error
    await session.refresh(episode)
    assert episode.local_path is None
    assert not target_path(episode).exists(), "a truncated file must not be left in place"


@respx.mock
async def test_attempts_cap_at_five(session):
    respx.get(AUDIO_URL).mock(side_effect=httpx.ConnectError("boom"))
    episode = await _episode(session)
    job = await _job(session, episode)

    for _ in range(5):
        job.state = JobState.running
        await run_job(session, job, user_agent="test")

    assert job.attempts == 5
    assert job.state is JobState.failed


async def test_enqueue_is_idempotent(session):
    episode = await _episode(session)

    first = await enqueue_download(session, episode, JobSource.queue)
    await session.commit()
    second = await enqueue_download(session, episode, JobSource.manual)
    await session.commit()

    assert first is not None
    assert second is first, "a second request must reuse the pending job"
    jobs = (await session.execute(select(DownloadJob))).scalars().all()
    assert len(jobs) == 1


async def test_enqueue_skips_episodes_already_on_disk(session):
    episode = await _episode(session, local_path="/somewhere/1.mp3")
    assert await enqueue_download(session, episode, JobSource.queue) is None


async def test_running_jobs_are_requeued_on_startup(session):
    episode = await _episode(session)
    job = await _job(session, episode)
    assert job.state is JobState.running

    assert await requeue_stuck_jobs() == 1

    await session.refresh(job)
    assert job.state is JobState.queued
