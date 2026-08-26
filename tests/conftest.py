"""Test fixtures.

Tests run against a real Postgres (the dev container), not SQLite: the queries under test
use ``FOR UPDATE SKIP LOCKED`` and ``count(...) FILTER (WHERE ...)``, so a different engine
would not be exercising the code that actually ships.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="podarium-tests-"))

DEV_DB = os.environ.get(
    "PODARIUM_DEV_DATABASE_URL", "postgresql+asyncpg://podarium:podarium@localhost:5455/podarium"
)
TEST_DB = os.environ.get("PODARIUM_TEST_DATABASE_URL", DEV_DB.rsplit("/", 1)[0] + "/podarium_test")

# Set before anything imports podarium.config, whose settings are cached on first read.
os.environ["DATABASE_URL"] = TEST_DB
os.environ["DOWNLOAD_DIR"] = str(_TMP / "downloads")
os.environ["ARTWORK_DIR"] = str(_TMP / "artwork")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["RUN_BACKGROUND_JOBS"] = "false"
# Blanked, not popped: Settings also reads .env, so removing the variables would let a
# developer's real credentials leak into the run and make the suite pass or fail depending
# on whose machine it is on. Environment takes precedence over the file, so "" wins.
os.environ["PODCASTINDEX_KEY"] = ""
os.environ["PODCASTINDEX_SECRET"] = ""

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from podarium import db as db_module  # noqa: E402
from podarium.config import get_settings  # noqa: E402
from podarium.models import Base, User  # noqa: E402

get_settings.cache_clear()


async def _ensure_test_database() -> None:
    """Create the test database if it does not exist. CREATE DATABASE cannot run in a
    transaction, so this uses an AUTOCOMMIT connection to the maintenance database."""
    admin_url = DEV_DB.rsplit("/", 1)[0] + "/postgres"
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    from sqlalchemy import text

    name = TEST_DB.rsplit("/", 1)[1]
    async with engine.connect() as connection:
        exists = (
            await connection.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
        ).scalar()
        if not exists:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def _database():
    await _ensure_test_database()
    engine = db_module.get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_state():
    """Reset the database *and* the media directories between tests.

    Both halves matter: RESTART IDENTITY hands the next test the same episode ids, so a
    file left behind by the previous one would sit at exactly the path the next test
    computes.
    """
    import shutil

    from sqlalchemy import text

    yield

    async with db_module.get_sessionmaker()() as session:
        await session.execute(
            # Every table, derived from the metadata rather than listed by hand -- a
            # hand-written list silently stops covering each new table that is added.
            text(
                "TRUNCATE "
                + ", ".join(Base.metadata.tables)
                + " RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()

    settings = get_settings()
    for directory in (settings.download_dir, settings.artwork_dir):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)


@pytest.fixture
async def session():
    async with db_module.get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def user(session) -> User:
    u = User(username="tester", password_hash="x")
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


@pytest.fixture
def tmp_media_root() -> Path:
    return _TMP
