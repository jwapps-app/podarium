# Podarium

A self-hosted podcast server and player. Replaces PinePods.

The full design lives in [podarium-spec.md](podarium-spec.md). Three requirements drive
everything here:

1. **The server does all fetching.** Publishers see one IP address — the server's. No
   client ever requests anything from a publisher host.
2. **Apple is not in the loop.** Discovery goes through Podcast Index, never iTunes.
3. **It is a media server, not a sync service.** It stores audio, serves it with range
   requests, and manages retention.

Phases 1 and 2 are done: the server with its HTTP API, and the web UI that runs entirely
against that public API. iOS is phase 3 and will use the same contract.

## Local development

Requires `uv` and Docker.

```bash
docker compose -f docker-compose.dev.yml up -d   # Postgres 18 on :5455
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env                             # then edit the password
uv run alembic upgrade head
uv run uvicorn --app-dir src podarium.main:app --port 8044 --reload
```

For the UI, either build it once and let the API serve it at `/`:

```bash
cd web && npm install && npm run build
```

or run the Vite dev server for hot reload, which proxies `/api` to port 8044:

```bash
cd web && npm run dev
```

The proxy matters for more than convenience: the session cookie is httpOnly, so
`<audio src="/api/stream/...">` only carries it on a same-origin request.

The user account is created on first boot from `PODARIUM_USERNAME` / `PODARIUM_PASSWORD`,
and only while the `users` table is empty — restarting will not reset the password.

Interactive API docs: <http://localhost:8044/docs>

## Tests

```bash
uv run pytest && (cd web && npm test)
```

The Python tests run against the dev Postgres container, creating a `podarium_test`
database beside it. Coverage is deliberately narrow on both sides: the invariants that are
expensive to get wrong (refresh idempotency, `first_seen_at` stability, retention keeping
rows, byte-range correctness, Podcast Index signing, show-note sanitising) rather than
every endpoint and component.

## End-to-end check

`scripts/verify.sh` walks the whole phase-1 surface with curl — subscribe, refresh, queue,
download, range request, purge, sync — against a running server.

## Deployment

`deploy/portainer-stack.yml` is pasted into the Portainer web editor on `docker-audio`; no
compose file lives on the host. Read the comments at the top of it before deploying — the
port, the NFS mount options, and the `PGDATA` path all have specific gotchas carried over
from PinePods.

The image builds the web UI in its own stage and serves it from `/app/web`, so there is
one container and one port for both the API and the UI.

## Notifications

Off unless the server has a VAPID keypair. Generate one, paste the three lines it prints
into the environment, and restart:

```bash
docker exec podarium-api python -m podarium.vapid
```

The private key prints base64-encoded rather than as a PEM. Both are read, along with a PEM
whose newlines are written as `\n` — but this value has to cross a Portainer environment
panel, and a multi-line PEM full of newlines and slashes is exactly the shape that arrives
truncated. A mangled key says so on sight rather than surfacing later as a push that never
lands.

Browsers pin the public key at subscribe time, so rotating the private key silently breaks
every subscription already issued — every device then has to enable notifications again.
Generate a separate pair for local development rather than reusing the server's.

On iOS, web push only works once Podarium has been added to the Home Screen, and only over
HTTPS. On the LAN over plain HTTP it will not be offered at all.

## Offline

Podarium streams from the server by design, so with no network there is nothing to play.
Episodes explicitly kept on a device are the exception: the service worker stores them whole
and serves byte ranges out of that copy, so they play with the server unreachable.

This needs a secure context, which means the HTTPS hostname rather than the LAN address.
Browser storage is also granted rather than guaranteed — iOS in particular reclaims it
without warning — so it is a convenience for a journey, not a copy to rely on. Downloading
to the device properly is what the native client is for.

## Backups

`vzdump` already sweeps `pgdata`, but a filesystem snapshot of a running Postgres is
crash-consistent rather than consistent: what you would have if the power went out mid-write.
Postgres normally replays its WAL and comes up clean from that, and "normally" is doing real
work in that sentence.

The stack therefore runs a nightly `pg_dump` into `/home/jworthington/docker/podarium/backups`,
inside the tree vzdump already picks up, so the backup holds both the raw directory and a
logically consistent dump. The service carries its own script inline — there is nothing to
place on the host, and `docker restart podarium-backup` forces a dump immediately.

Restore into a scratch database and compare before touching the live one; loading a dump
into a populated database conflicts on every table it recreates.

```bash
gunzip -c podarium-YYYYMMDD-HHMMSS.sql.gz | docker exec -i podarium-db psql -U podarium -d restore_test
```

What is in that database: subscriptions, every playback position, stars, the queue, and API
tokens. The audio can be downloaded again; none of that can.

## Locked out

Two-factor codes are checked against a secret stored encrypted with a key derived from
`SECRET_KEY`. Change that variable and the secret can no longer be read — sign-in then says
so explicitly rather than pretending the code was wrong.

Either way, the way back in is one statement on the host. It clears the second factor and
leaves the password alone:

```bash
docker exec podarium-db psql -U podarium -d podarium \
  -c "update users set totp_secret = null, totp_last_step = null"
```

The same applies if repeated failures have locked sign-in and you would rather not wait out
the window:

```bash
docker exec podarium-db psql -U podarium -d podarium -c "delete from login_attempts"
```

Both require access to the host, which is the point: they are recovery for the person who
owns the machine, not a bypass reachable from the login form.

## Invariants

Changes to this codebase should preserve these. Each has a test.

- No client response carries a publisher URL. Artwork is `/api/images/...`, audio is
  `/api/stream/...`, and that holds for search results too — a show you have not subscribed
  to still has its cover proxied, keyed by a server-minted hash so the endpoint cannot be
  pointed anywhere else. Show notes are sanitised in the browser as well: an `<img>` left in
  a publisher's description would fetch straight from their CDN and leak the viewer's IP.
- A show is one subscription however you reach it. Feeds are matched by URL, by resolved
  URL after redirects, and by Podcast Index id — matching the raw string would let the same
  podcast be subscribed twice, and two feed rows means two copies of every episode.
- Retention deletes files, never episode rows.
- `first_seen_at`, not `published_at`, decides whether an episode is new.
- Feed refresh is idempotent.
- Every user-scoped table carries `user_id`, even with exactly one user.
- A one-time code is accepted once. Its 30-second window makes a code seen over a shoulder
  or in a log otherwise replayable.
- Nothing large or churny is written to the VM disk.
