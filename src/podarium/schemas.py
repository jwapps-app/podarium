"""Request and response bodies.

Note what is *absent* from every response model: ``enclosure_url`` and raw ``image_url``.
A publisher URL in a client response would let a client fetch it directly, which is the
one thing this server exists to prevent. Artwork is exposed as ``/api/images/...`` and
audio as ``/api/stream/...``; both are server-mediated.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from podarium.models import Episode, Feed, RetentionMode


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class LoginRequest(BaseModel):
    username: str
    password: str
    # Required only once a second factor is enabled. A sign-in without it then fails with
    # code "totp_required", which is how the form knows to ask rather than to complain
    # that the password was wrong.
    totp_code: str | None = None


class UserOut(BaseModel):
    id: int
    username: str
    created_at: datetime
    totp_enabled: bool = False


class TotpSetupOut(BaseModel):
    """A pending secret. Not in force until a code from it has been confirmed."""

    secret: str
    provisioning_uri: str


class TotpEnableRequest(BaseModel):
    code: str


class TotpDisableRequest(BaseModel):
    # The password again, so a borrowed session cannot quietly remove the second factor.
    password: str


class TokenCreateRequest(BaseModel):
    name: str = ""


class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None = None


class TokenCreatedOut(TokenOut):
    # Returned exactly once, at creation.
    token: str


class SearchResultOut(BaseModel):
    podcast_index_id: int | None = None
    title: str | None = None
    author: str | None = None
    description: str | None = None
    feed_url: str
    image_url: str | None = None
    episode_count: int | None = None
    already_subscribed: bool = False


class FeedCreateRequest(BaseModel):
    feed_url: str | None = None
    podcast_index_id: int | None = None


class FeedUpdateRequest(BaseModel):
    auto_download_count: int | None = Field(default=None, ge=0)
    retention_mode: RetentionMode | None = None
    retention_days: int | None = Field(default=None, ge=0)
    active: bool | None = None
    # NULL is meaningful on these columns (it means "inherit the global"), so an explicit
    # clear needs its own flag rather than being confused with "field omitted".
    clear_retention_mode: bool = False
    clear_retention_days: bool = False
    clear_auto_download_count: bool = False


class FeedOut(BaseModel):
    id: int
    feed_url: str
    podcast_index_id: int | None
    title: str | None
    author: str | None
    description: str | None
    link: str | None
    language: str | None
    explicit: bool
    image_url: str | None
    # None means the feed inherits the global; effective_auto_download_count is what is
    # actually applied, so a client never has to fetch settings to display it.
    auto_download_count: int | None
    effective_auto_download_count: int
    retention_mode: RetentionMode | None
    retention_days: int | None
    active: bool
    last_fetched_at: datetime | None
    fetch_error: str | None
    fetch_error_count: int
    created_at: datetime
    updated_at: datetime
    episode_count: int | None = None
    unplayed_count: int | None = None
    # Episodes that arrived since this show was last looked at. This is what the badge
    # shows -- unplayed counts a backlog nobody intends to finish.
    new_episode_count: int | None = None


class EpisodeOut(BaseModel):
    id: int
    feed_id: int
    guid: str
    title: str | None
    description_html: str | None
    image_url: str | None
    episode_number: int | None
    season: int | None
    explicit: bool
    published_at: datetime | None
    first_seen_at: datetime
    duration_seconds: int | None
    enclosure_type: str | None
    enclosure_bytes: int | None
    downloaded: bool
    local_bytes: int | None
    downloaded_at: datetime | None
    purged_at: datetime | None
    stream_url: str
    played: bool = False
    position_seconds: int = 0
    completed_at: datetime | None = None
    # Exposed so a client can build its own resume list without re-deriving the order.
    last_played_at: datetime | None = None
    starred: bool = False
    updated_at: datetime


class EpisodeListOut(BaseModel):
    items: list[EpisodeOut]
    next_cursor: str | None = None


class EpisodeStateUpdate(BaseModel):
    played: bool | None = None
    position_seconds: int | None = Field(default=None, ge=0)
    starred: bool | None = None
    # When the change was actually made, as opposed to when it arrived.
    #
    # A client that queues writes while offline flushes them later, so arrival order stops
    # meaning anything: an hours-old position would otherwise land after, and overwrite,
    # something newer done elsewhere. Sending this makes the write lose that argument
    # instead of winning it. Omit it and the write is treated as happening now.
    changed_at: datetime | None = None


class QueueItemOut(BaseModel):
    episode_id: int
    position: int
    added_at: datetime
    episode: EpisodeOut


class QueueAddRequest(BaseModel):
    episode_id: int
    position: int | None = Field(default=None, ge=0)


class QueueOrderRequest(BaseModel):
    episode_ids: list[int]


class SettingsOut(BaseModel):
    global_retention_mode: RetentionMode
    global_retention_days: int
    download_dir_max_bytes: int | None
    refresh_interval_minutes: int
    user_agent: str
    default_playback_rate: float
    global_auto_download_count: int


class SettingsUpdate(BaseModel):
    global_retention_mode: RetentionMode | None = None
    global_retention_days: int | None = Field(default=None, ge=0)
    download_dir_max_bytes: int | None = Field(default=None, ge=0)
    clear_download_dir_max_bytes: bool = False
    refresh_interval_minutes: int | None = Field(default=None, ge=1)
    global_auto_download_count: int | None = Field(default=None, ge=0)
    user_agent: str | None = None
    # Bounded to what browsers and AVPlayer actually honour; outside this range playback
    # is either unintelligible or silently clamped by the platform.
    default_playback_rate: float | None = Field(default=None, ge=0.5, le=4.0)


class SyncOut(BaseModel):
    # Use this as the ``since`` on the next call -- but only once next_cursor is null.
    # Server-supplied so client clock skew cannot open a gap in the delta.
    now: datetime
    feeds: list[FeedOut]
    episodes: list[EpisodeOut]
    queue: list[QueueItemOut]
    deleted_feed_ids: list[int] = []
    # Non-null means this response was truncated. Call again with the same `since` and this
    # value as `cursor`; advancing `since` before the pages run out loses the remainder
    # permanently, since those episodes will never appear in a later delta.
    next_cursor: str | None = None


class OpmlImportResult(BaseModel):
    imported: int
    skipped: int
    failed: int
    errors: list[str] = []


def feed_image_url(feed: Feed) -> str | None:
    return f"/api/images/feed/{feed.id}" if feed.image_url else None


def episode_image_url(episode: Episode) -> str | None:
    if episode.image_url:
        return f"/api/images/episode/{episode.id}"
    return f"/api/images/feed/{episode.feed_id}"


def feed_out(
    feed: Feed,
    *,
    episode_count: int | None = None,
    unplayed_count: int | None = None,
    new_episode_count: int | None = None,
    global_auto_download_count: int = 0,
) -> FeedOut:
    return FeedOut(
        id=feed.id,
        feed_url=feed.feed_url,
        podcast_index_id=feed.podcast_index_id,
        title=feed.title,
        author=feed.author,
        description=feed.description,
        link=feed.link,
        language=feed.language,
        explicit=feed.explicit,
        image_url=feed_image_url(feed),
        auto_download_count=feed.auto_download_count,
        effective_auto_download_count=(
            feed.auto_download_count
            if feed.auto_download_count is not None
            else global_auto_download_count
        ),
        retention_mode=feed.retention_mode,
        retention_days=feed.retention_days,
        active=feed.active,
        last_fetched_at=feed.last_fetched_at,
        fetch_error=feed.fetch_error,
        fetch_error_count=feed.fetch_error_count,
        created_at=feed.created_at,
        updated_at=feed.updated_at,
        episode_count=episode_count,
        unplayed_count=unplayed_count,
        new_episode_count=new_episode_count,
    )


def episode_out(episode: Episode, state=None) -> EpisodeOut:
    return EpisodeOut(
        id=episode.id,
        feed_id=episode.feed_id,
        guid=episode.guid,
        title=episode.title,
        description_html=episode.description_html,
        image_url=episode_image_url(episode),
        episode_number=episode.episode_number,
        season=episode.season,
        explicit=episode.explicit,
        published_at=episode.published_at,
        first_seen_at=episode.first_seen_at,
        duration_seconds=episode.duration_seconds,
        enclosure_type=episode.enclosure_type,
        enclosure_bytes=episode.enclosure_bytes,
        downloaded=episode.local_path is not None,
        local_bytes=episode.local_bytes,
        downloaded_at=episode.downloaded_at,
        purged_at=episode.purged_at,
        stream_url=f"/api/stream/{episode.id}",
        played=bool(state.played) if state else False,
        position_seconds=int(state.position_seconds) if state else 0,
        completed_at=state.completed_at if state else None,
        last_played_at=state.last_played_at if state else None,
        starred=bool(state.starred) if state else False,
        updated_at=episode.updated_at,
    )
