# Podarium — v1 Specification

A self-hosted podcast server and player. Replaces PinePods.

---

## 1. Why this exists

Three requirements drive every design decision below. If a choice conflicts with one of these, the requirement wins.

1. **The server does all fetching.** Feed polling, episode downloads, artwork, and search all originate from the server. Publishers see one IP address — the server's — never a phone on a cell network. No client ever makes a request to a publisher's host.
2. **Apple is not in the loop.** Podcast search uses the Podcast Index API, not the iTunes Search API. No Apple service is contacted for discovery, metadata, or artwork.
3. **It is a media server, not a sync service.** It stores audio, serves it with range requests, and manages retention. Subscription-sync-only designs (gpodder/opodsync) do not satisfy requirement 1.

### Non-goals for v1

Multi-user sharing, transcripts, chapter editing, video podcasts, recommendation engines, social features, YouTube ingestion, cross-device playback handoff.

---

## 2. Decisions

| Decision | Choice |
|---|---|
| v1 scope | Server + HTTP API + web UI. iOS app follows as client #2 against the same API. |
| Download strategy | Queue-driven by default. Per-feed opt-in "keep latest N downloaded". |
| Retention | Per-show setting with a global default; a per-show value overrides the global. |
| Users | Single user. `user_id` foreign keys present on all user-scoped tables from day one. |
| Search | Podcast Index, built in from the start. |

**Scope note.** The web UI is client #1, not a throwaway. Everything it does goes through the public HTTP API, so the iOS app inherits a contract that has already been exercised rather than one invented for it later.

**Download note.** Default behavior is metadata-only refresh; audio is fetched when an episode is queued or explicitly requested. Feeds may opt in to `auto_download_count = N`, which pre-downloads the N newest episodes during the refresh job — not merely marking them, since the point of the flag is that the audio is already on disk before the app is opened.

**Retention note.** Retention deletes the *file*, never the *row*. See §4.

**User note.** One account, one login, no signup flow, no roles, no invites. The foreign keys cost nothing now and turn "add my wife" into an afternoon rather than a migration.

---

## 3. Stack and deployment

- **Server:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic migrations, `httpx` for outbound HTTP, APScheduler (or an asyncio task group) for background jobs.
- **Database:** PostgreSQL 18.
- **Web UI:** React + TypeScript + Vite, served as static files by the API container.
- **iOS:** Swift/SwiftUI, phase 3.
- **Packaging:** Docker Compose, deployed through the Portainer web editor. No compose file on disk.

### Host

A Debian VM on the home network, running alongside the other media containers and
(during migration) PinePods. Referred to below as the Docker host.

### Storage split

Follows the established rule: small-and-valuable on NVMe, large-and-worthless on the NAS, never on a VM disk.

| Data | Location |
|---|---|
| Postgres | `~/docker/podarium/pgdata` (local NVMe) |
| Episode audio | `/mnt/nas/podarium/downloads` (NFS from 192.168.1.42) |
| Artwork cache | `/mnt/nas/podarium/artwork` |
| DB dumps | `/mnt/nas/podarium/backups` |

Audio is high-churn, incompressible, and has zero recovery value — it must not enter `vzdump` or reach PBS.

### Compose gotchas carried over from PinePods

- Postgres 18 mounts at `/var/lib/pgdata` with `PGDATA=/var/lib/pgdata/pgdata`. The old `/var/lib/postgresql/data` path fails on overlay2 hosts.
- `PUID=1026 / PGID=100` to match NFS share ownership.
- `PUBLIC_URL` must be the externally reachable URL; the server builds absolute links from it.
- NFS mount via fstab with `noauto,x-systemd.automount,soft,timeo=100`. Do **not** add `RequiresMountsFor=` to the docker.service drop-in — with `noauto` it blocks Docker forever.

### Port

Pick an unused port on the Docker host (8040 is PinePods here). `8044` is the suggested default; verify with `ss -tlnp` before deploying.

---

## 4. Data model

### `users`
`id`, `username`, `password_hash` (argon2), `created_at`

### `feeds`
| Column | Notes |
|---|---|
| `id` | PK |
| `feed_url` | unique |
| `podcast_index_id` | nullable, from search |
| `title`, `author`, `description`, `link`, `language`, `image_url`, `explicit` | from feed |
| `etag`, `last_modified` | for conditional GET |
| `last_fetched_at`, `fetch_error`, `fetch_error_count` | |
| `auto_download_count` | INTEGER NOT NULL DEFAULT 0 — 0 means queue-only |
| `retention_mode` | ENUM(`after_played`, `after_download`, `never`) NULL — NULL inherits global |
| `retention_days` | INTEGER NULL — NULL inherits global |
| `active` | soft-unsubscribe |
| `created_at` | |

### `episodes`
| Column | Notes |
|---|---|
| `id` | PK |
| `feed_id` | FK |
| `guid` | UNIQUE (`feed_id`, `guid`) — the dedup key |
| `title`, `description_html`, `image_url`, `episode_number`, `season`, `explicit` | |
| `published_at` | from `pubDate` |
| `first_seen_at` | when *this server* first saw the GUID |
| `duration_seconds` | |
| `enclosure_url`, `enclosure_type`, `enclosure_bytes` | |
| `local_path` | TEXT NULL — NULL means not on disk |
| `local_bytes`, `downloaded_at`, `purged_at` | |

**The `local_path` nullability is the most important thing in this schema.** Retention nulls `local_path`, sets `purged_at`, and unlinks the file. The row, its GUID, and its played state survive forever. Delete the row and the next feed refresh re-adds the episode as new — the deleted episode reappears, and re-downloads.

**`first_seen_at` exists because `pubDate` lies.** A publisher mid-migration (Anchor → Megaphone, as PBD Podcast did on Aug 16) re-stamps `pubDate` on every episode it touches, and old episodes resurface as new. Rule: **`published_at` is for display; `first_seen_at` is for "is this new?"** New-episode detection, notifications, and the default inbox sort all key off `first_seen_at`. Never mark an episode unplayed on re-fetch, and never reset `first_seen_at`.

### `episode_state`
`user_id`, `episode_id` (composite PK), `played` BOOL, `position_seconds` INT, `completed_at`, `starred`, `updated_at`

`updated_at` drives delta sync — see §6.

### `queue`
`user_id`, `episode_id`, `position` INT, `added_at`. Reordering rewrites `position`.

### `download_jobs`
`id`, `episode_id`, `state` ENUM(`queued`,`running`,`done`,`failed`), `attempts`, `last_error`, `source` ENUM(`queue`,`auto`,`manual`), `created_at`, `updated_at`

Idempotent: a job for an episode with a non-null `local_path` completes immediately. Attempts cap at 5 with exponential backoff. On startup, any job left `running` is requeued.

### `settings`
Singleton row: `global_retention_mode` (default `after_played`), `global_retention_days` (default 7), `download_dir_max_bytes` (nullable global ceiling), `refresh_interval_minutes` (default 60), `user_agent`.

---

## 5. Background jobs

**Feed refresh** — every `refresh_interval_minutes`, jittered per feed so requests spread out. Conditional GET using stored `etag`/`last_modified`; a 304 costs nothing. On 200, parse and upsert episodes by `(feed_id, guid)`. New episodes on a feed with `auto_download_count > 0` are enqueued as `source='auto'`. Consecutive failures increment `fetch_error_count` and back the feed off; surface it in the UI rather than failing silently.

**Download worker** — small fixed concurrency (2–3). Streams to a `.part` file, verifies size against `enclosure_bytes` where present, then atomically renames into place. Files land at `/mnt/nas/podarium/downloads/{feed_id}/{episode_id}.{ext}`. IDs in the path, not titles — no filesystem-unsafe characters, no rename churn when a publisher edits a title.

**Retention sweeper** — hourly. For each downloaded episode, resolve effective policy (`feed.retention_mode ?? settings.global_retention_mode`, same for days), then:
- `after_played` — purge if `played` and `completed_at` older than N days
- `after_download` — purge if `downloaded_at` older than N days regardless of played state
- `never` — skip

Then, if `download_dir_max_bytes` is set and exceeded, purge oldest-played-first until under the ceiling. Purging = unlink file, null `local_path`, set `purged_at`. Episodes currently in the queue are never purged.

**Artwork cache** — feed and episode images fetched server-side once, stored under `/mnt/nas/podarium/artwork`, served from `/api/images/...`. Clients never hit a publisher CDN.

---

## 6. HTTP API

All endpoints under `/api`. JSON in, JSON out. Errors as `{"error": {"code", "message"}}`.

### Auth
```
POST   /api/auth/login          {username, password} → session cookie
POST   /api/auth/logout
GET    /api/auth/me
POST   /api/auth/token          → long-lived bearer token for the iOS client
DELETE /api/auth/token/{id}     revoke a device
```
Web UI uses the session cookie; iOS uses a bearer token. Both resolve to the same user.

### Search and subscribe
```
GET  /api/search?q=                    Podcast Index search
GET  /api/search/byfeedurl?url=        resolve an arbitrary feed URL
POST /api/feeds                        {feed_url} or {podcast_index_id}
```

### Feeds
```
GET    /api/feeds
GET    /api/feeds/{id}
PATCH  /api/feeds/{id}                 auto_download_count, retention_*, active
DELETE /api/feeds/{id}                 ?purge=true also deletes downloaded audio
POST   /api/feeds/{id}/refresh         force refresh now
```

### Episodes
```
GET    /api/episodes?feed_id=&unplayed=&downloaded=&since=&limit=&cursor=
GET    /api/episodes/{id}
POST   /api/episodes/{id}/download     manual download
DELETE /api/episodes/{id}/download     purge file, keep row
PUT    /api/episodes/{id}/state        {played, position_seconds}
```

### Queue
```
GET    /api/queue
POST   /api/queue                      {episode_id, position?}
DELETE /api/queue/{episode_id}
PUT    /api/queue/order                {episode_ids: [...]}
```

### Media
```
GET /api/stream/{episode_id}           range-capable
GET /api/images/{kind}/{id}            cached artwork
```
`/api/stream` serves the local file when `local_path` is set. When it is not, the server proxies the origin, streaming through — the client still never contacts the publisher. Full `Range` / `206` / `Accept-Ranges` support is required or iOS `AVPlayer` seeking breaks.

### Sync and admin
```
GET /api/sync?since=<iso8601>          delta: changed feeds, episodes, states, queue
GET /api/settings   PUT /api/settings
GET /api/opml/export   POST /api/opml/import
GET /healthz
GET /metrics                           Prometheus — the fleet already scrapes
```

`/api/sync` is what makes the iOS app cheap: one call returns everything changed since a timestamp, with a server-supplied `now` to use as the next cursor. Build it in phase 1 even though nothing consumes it until phase 3.

---

## 7. Podcast Index integration

Free API key from podcastindex.org. Every request carries:

```
X-Auth-Key:    <api key>
X-Auth-Date:   <unix seconds>
Authorization: sha1(api_key + api_secret + unix_seconds)
User-Agent:    Podarium/<version>
```

Endpoints used: `/search/byterm`, `/podcasts/byfeedurl`, `/podcasts/byfeedid`, `/episodes/byfeedid`.

Podcast Index is used for **discovery only**. Once subscribed, the RSS feed is the source of truth — Podarium polls the feed directly. Store `podcast_index_id` anyway; it is how you detect a feed URL that has permanently moved.

Keys go in env vars, never the database: `PODCASTINDEX_KEY`, `PODCASTINDEX_SECRET`.

---

## 8. Migration from PinePods

1. Export OPML from PinePods, import via `POST /api/opml/import`.
2. Optionally seed played state: read PinePods' Postgres directly, match its episodes to Podarium's by `(feed_url, guid)`, and insert `episode_state` rows. Match on GUID, never on title.
3. Run both in parallel for a week. PinePods keeps its own downloads at `/mnt/nas/pinepods` — separate directory, no conflict.
4. Stop the PinePods stack, keep its volumes for a month, then remove.

---

## 9. Build order

**Phase 1 — server.** Migrations, feed parsing and refresh, Podcast Index search, download worker, retention sweeper, full API including `/api/sync`, streaming with range support, OPML import/export, Prometheus metrics. Verifiable entirely with `curl`.

**Phase 2 — web UI.** Subscribe/search, feed list, episode list with filters, per-feed settings, queue with drag reorder, an audio player that reports position back to `/api/episodes/{id}/state`, settings page. This is the point PinePods can be retired.

**Phase 3 — iOS.** SwiftUI, `AVPlayer` against `/api/stream`, background downloads via `URLSession` background config, delta sync against `/api/sync`, offline playback and queued state writes that flush on reconnect. Push notifications for new episodes route through the existing shared push-relay container rather than a new APNs integration.

---

## 10. Invariants for the implementer

- No client ever issues an HTTP request to a publisher host. Audio, artwork, and feeds are all server-mediated. If a code path would put a publisher URL in a client response, it is wrong.
- Episode rows are never deleted by retention. Only files are.
- `first_seen_at`, not `published_at`, determines whether an episode is new.
- Feed refresh is idempotent. Running it twice changes nothing.
- Every user-scoped table carries `user_id`, even though there is exactly one user.
- Nothing large or churny is written to the VM disk.
