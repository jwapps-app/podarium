"""SQLAlchemy models. Mirrors spec section 4.

Two invariants are load-bearing here and are called out again at their columns:

* ``Episode.local_path`` is nullable. Retention nulls it and unlinks the file, but the
  row -- its GUID and its played state -- survives forever. Deleting the row would make
  the next feed refresh re-add the episode as new and re-download it.
* ``Episode.first_seen_at`` records when *this server* first saw the GUID. ``published_at``
  is for display only; ``first_seen_at`` answers "is this new?".
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RetentionMode(str, enum.Enum):
    after_played = "after_played"
    after_download = "after_download"
    never = "never"


class JobState(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class JobSource(str, enum.Enum):
    queue = "queue"
    auto = "auto"
    manual = "manual"


# native_enum=False keeps these as portable VARCHAR + CHECK rather than Postgres ENUM types,
# which makes adding a variant a plain migration instead of an ALTER TYPE dance.
def _enum(py_enum: type[enum.Enum], name: str) -> Enum:
    return Enum(py_enum, name=name, native_enum=False, values_callable=lambda e: [m.value for m in e])


def _now_col(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), **kw)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now_col(nullable=False)

    # Encrypted with a key derived from SECRET_KEY, which lives in the environment rather
    # than here -- so this column on its own, in a backup or a dump, is not a second factor.
    totp_secret: Mapped[str | None] = mapped_column(Text)

    # The last 30-second step accepted, so a code cannot be used twice inside its window.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)


class ApiToken(Base):
    """Long-lived bearer tokens for non-browser clients (the iOS app).

    Stored hashed; the plaintext is shown once at creation. A row per device is what
    makes ``DELETE /api/auth/token/{id}`` able to revoke just that device.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = _now_col(nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    # Where the feed actually serves from, after redirects. A show is commonly reachable
    # at both its hosting platform's URL and the publisher's own, and the two are the same
    # subscription -- so identity needs the destination, not just the address we were given.
    resolved_url: Mapped[str | None] = mapped_column(Text)

    podcast_index_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(32))
    image_url: Mapped[str | None] = mapped_column(Text)
    explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Conditional GET. A 304 costs nothing, so we always send these when we have them.
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)

    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetch_error: Mapped[str | None] = mapped_column(Text)
    fetch_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # NULL inherits the global; a value here overrides it. 0 means queue-only: audio is
    # fetched when an episode is queued or explicitly requested. Same shape as retention
    # below, so "inherit" is expressible rather than being confused with "zero".
    auto_download_count: Mapped[int | None] = mapped_column(Integer)

    # NULL on either column inherits the corresponding global setting.
    retention_mode: Mapped[RetentionMode | None] = mapped_column(_enum(RetentionMode, "retention_mode"))
    retention_days: Mapped[int | None] = mapped_column(Integer)

    # Same inherit-or-override shape again. Speed is the setting that varies most by show:
    # a dense interview holds up at 1.5x where a scripted narrative does not.
    playback_rate: Mapped[float | None] = mapped_column(Float)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now_col(nullable=False)
    updated_at: Mapped[datetime] = _now_col(nullable=False, onupdate=func.now())

    episodes: Mapped[list[Episode]] = relationship(back_populates="feed", cascade="all, delete-orphan")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("feed_id", "guid", name="uq_episodes_feed_guid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False, index=True)

    # The dedup key. Everything about refresh idempotency rests on this pair.
    guid: Mapped[str] = mapped_column(Text, nullable=False)

    title: Mapped[str | None] = mapped_column(Text)
    description_html: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    episode_number: Mapped[int | None] = mapped_column(Integer)
    season: Mapped[int | None] = mapped_column(Integer)
    explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # published_at is for display. It lies: a publisher mid-migration re-stamps pubDate on
    # every episode it touches. first_seen_at is the honest "is this new?" signal and is
    # written exactly once, when this server first sees the GUID.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = _now_col(nullable=False, index=True)

    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    enclosure_url: Mapped[str | None] = mapped_column(Text)
    enclosure_type: Mapped[str | None] = mapped_column(String(128))
    enclosure_bytes: Mapped[int | None] = mapped_column(BigInteger)

    # podcast:chapters, as published. The publisher's URL, never handed to a client -- the
    # server fetches it and serves the result from /api/episodes/{id}/chapters, the same way
    # audio and artwork are mediated.
    chapters_url: Mapped[str | None] = mapped_column(Text)
    chapters_json: Mapped[str | None] = mapped_column(Text)
    chapters_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # NULL means not on disk. Retention nulls this; it never deletes the row.
    local_path: Mapped[str | None] = mapped_column(Text)
    local_bytes: Mapped[int | None] = mapped_column(BigInteger)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    updated_at: Mapped[datetime] = _now_col(nullable=False, onupdate=func.now(), index=True)

    feed: Mapped[Feed] = relationship(back_populates="episodes")


class EpisodeState(Base):
    __tablename__ = "episode_state"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True)
    played: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When playback last advanced, which is what "continue listening" orders by.
    #
    # Distinct from updated_at, which moves for any write at all: starring a half-finished
    # episode from six months ago would otherwise jump it above the one paused ten minutes
    # ago. NULL means never played, only ever starred or marked.
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # Drives delta sync.
    updated_at: Mapped[datetime] = _now_col(nullable=False, onupdate=func.now(), index=True)


class FeedState(Base):
    """Per-user, per-feed "I have looked at this show" marker.

    Separate from played state because they answer different questions. "Unplayed" counts a
    backlog you never intended to work through -- subscribing to a show with 2,700 episodes
    does not mean you owe it 2,700 listens -- so a badge built on it reads 99+ forever and
    tells you nothing. "Unseen" is the actionable one: what has arrived since you last
    looked.
    """

    __tablename__ = "feed_state"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), primary_key=True)
    # Episodes first seen after this are new. Set when the show is subscribed, so its
    # existing back catalogue never counts as new, and again whenever it is viewed.
    last_seen_at: Mapped[datetime] = _now_col(nullable=False)
    updated_at: Mapped[datetime] = _now_col(nullable=False, onupdate=func.now(), index=True)


class QueueItem(Base):
    __tablename__ = "queue"
    __table_args__ = (UniqueConstraint("user_id", "episode_id", name="uq_queue_user_episode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = _now_col(nullable=False)


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    state: Mapped[JobState] = mapped_column(_enum(JobState, "job_state"), nullable=False, default=JobState.queued, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[JobSource] = mapped_column(_enum(JobSource, "job_source"), nullable=False)
    # When the worker may next pick this up; set into the future for exponential backoff.
    next_attempt_at: Mapped[datetime] = _now_col(nullable=False, index=True)
    created_at: Mapped[datetime] = _now_col(nullable=False)
    updated_at: Mapped[datetime] = _now_col(nullable=False, onupdate=func.now())


class AppSettings(Base):
    """Singleton row (id == 1)."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    global_retention_mode: Mapped[RetentionMode] = mapped_column(
        _enum(RetentionMode, "retention_mode"), nullable=False, default=RetentionMode.after_played
    )
    global_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    global_auto_download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    download_dir_max_bytes: Mapped[int | None] = mapped_column(BigInteger)
    refresh_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, default="Podarium/0.1.0")

    # Starting playback speed for every episode. Stored server-side rather than in the
    # browser so the iOS client starts at the same speed as the web player.
    default_playback_rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    updated_at: Mapped[datetime] = _now_col(nullable=False, onupdate=func.now())


class LoginAttempt(Base):
    """Every login try, so repeated failures can be throttled.

    Recorded per username rather than per client address. Behind a proxy the address is
    whatever a header claims, and the only way to trust it is to trust the proxy and
    validate the chain -- worth doing for a multi-user service, but here there is exactly
    one account, so throttling it is throttling everything an attacker can try.

    The trade is that someone who can reach the login form can lock the real user out for
    the window. For a single-user server that is the better failure: a quarter of an hour
    of annoyance against an unlimited guessing rate.
    """

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempted_at: Mapped[datetime] = _now_col(nullable=False, index=True)


class PushSubscription(Base):
    """A browser's Web Push endpoint, so new episodes can reach a closed app.

    The endpoint URL is issued by the browser vendor's push service and is the address the
    server posts to; the two keys are the client's half of the encryption, without which a
    push cannot be constructed. All three are opaque to us.

    Keyed by endpoint rather than by device, because that is the only identifier the
    browser gives us and it is regenerated whenever a subscription is replaced. A stale one
    is not an error to chase: the push service answers 404 or 410 for an endpoint that has
    been revoked, and the row is dropped when it does.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    # What the user calls the device, for the settings list.
    label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col(nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeletedFeed(Base):
    """A tombstone, so a client can learn that a feed is gone.

    Unsubscribing with purge removes the row outright, which leaves a synced client with no
    way to know: the feed simply stops appearing in a delta, which is indistinguishable
    from it not having changed. Without this, /api/sync's deleted_feed_ids could only ever
    be an empty list -- a field a client author would reasonably trust.
    """

    __tablename__ = "deleted_feeds"

    # Not a foreign key: the whole point is that the feed no longer exists.
    feed_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_url: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime] = _now_col(nullable=False, index=True)


class ArtworkCache(Base):
    """Server-side artwork cache. Clients never hit a publisher CDN (spec 5)."""

    __tablename__ = "artwork_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    # sha256 of the source URL -- stable across publisher title edits.
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(128))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetch_error: Mapped[str | None] = mapped_column(Text)
