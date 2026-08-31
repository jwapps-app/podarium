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

    # Web Push. Both halves of one keypair; generate with `python -m podarium.vapid`.
    #
    # The public key is handed to the browser at subscribe time and pinned to the
    # subscription it creates, so rotating the private key silently breaks every existing
    # subscription -- the push service will reject a claim signed by a key that does not
    # match. Absent, push is simply off.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    # Required by the spec so a push service has someone to contact about a misbehaving
    # sender. mailto: or https:.
    vapid_contact: str = "mailto:admin@localhost"

    # APNs for the iOS app, which does not go to Apple from here. It goes to the shared
    # relay that holds the one Apple key for every app on this account, so all this server
    # needs is where the relay is and a key scoped to Podarium's bundle. Absent means the
    # iOS half is simply off, exactly as absent VAPID keys turn web push off.
    push_relay_url: str | None = None
    push_relay_key: str | None = None

    # First-boot bootstrap. Applied only when the users table is empty.
    podarium_username: str | None = None
    podarium_password: str | None = None

    download_concurrency: int = 2
    retention_sweep_minutes: int = 60
    http_timeout_seconds: float = 30.0

    # The most a single audio download may occupy. A malicious or broken publisher can
    # otherwise stream forever and fill the disk. Three hours of 320kbps audio is about
    # 450 MB; two gigabytes is comfortably above any real episode.
    download_max_bytes: int = 2_000_000_000

    # Refuse outbound fetches to loopback and private-range addresses. Feed, artwork,
    # enclosure and chapter URLs are all publisher-controlled once a feed is subscribed,
    # and without this a malicious feed can point them at hosts inside the LAN -- the
    # router, Portainer, the NAS -- and have this server fetch them. Checked against
    # literal IPs and localhost names; a hostname that *resolves* somewhere private is not
    # caught (DNS rebinding), so this is a guard, not a boundary. Set true for a
    # deployment that genuinely hosts feeds on its own network.
    allow_private_fetch: bool = False

    # When set, GET /metrics requires "Authorization: Bearer <this>". Unset, the endpoint
    # stays open -- fine on a LAN, but through a public hostname it hands operational
    # detail to anyone who asks.
    metrics_token: str | None = None

    # Set false in tests so the app does not spawn background jobs.
    run_background_jobs: bool = True

    # The commit this image was built from, stamped by CI. "dev" when running from source.
    podarium_build: str = "dev"

    @property
    def podcastindex_configured(self) -> bool:
        return bool(self.podcastindex_key and self.podcastindex_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
