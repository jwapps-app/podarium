from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Environment only -- secrets never live in the database (spec 7)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://podarium:podarium@localhost:5455/podarium"

    # The externally reachable base URL. The server builds absolute links from it (spec 3).
    public_url: str = "http://localhost:8044"

    secret_key: str = "dev-insecure-change-me"
    session_cookie_name: str = "podarium_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 30

    download_dir: Path = Path("./data/downloads")
    artwork_dir: Path = Path("./data/artwork")

    # Built web UI (phase 2). Absent in an API-only deployment, in which case the server
    # simply serves no HTML and every /api route behaves exactly as before.
    web_dir: Path = Path("./web/dist")

    podcastindex_key: str | None = None
    podcastindex_secret: str | None = None

    # First-boot bootstrap. Applied only when the users table is empty.
    podarium_username: str | None = None
    podarium_password: str | None = None

    download_concurrency: int = 2
    retention_sweep_minutes: int = 60
    http_timeout_seconds: float = 30.0

    # Set false in tests so the app does not spawn background jobs.
    run_background_jobs: bool = True

    @property
    def podcastindex_configured(self) -> bool:
        return bool(self.podcastindex_key and self.podcastindex_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
