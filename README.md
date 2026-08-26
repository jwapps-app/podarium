# Podarium

A self-hosted podcast server and player. Replaces PinePods.

The full design lives in [podarium-spec.md](podarium-spec.md). Three requirements drive
everything here:

1. **The server does all fetching.** Publishers see one IP address — the server's. No
   client ever requests anything from a publisher host.
2. **Apple is not in the loop.** Discovery goes through Podcast Index, never iTunes.
3. **It is a media server, not a sync service.** It stores audio, serves it with range
   requests, and manages retention.

Phase 1 (this repository) is the server and its HTTP API. The web UI is phase 2, iOS is
phase 3; both talk to the same API.

## Local development

Requires `uv` and Docker.

```bash
docker compose -f docker-compose.dev.yml up -d   # Postgres 18 on :5455
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env                             # then edit the password
uv run alembic upgrade head
uv run uvicorn --app-dir src podarium.main:app --port 8044 --reload
```

The user account is created on first boot from `PODARIUM_USERNAME` / `PODARIUM_PASSWORD`,
and only while the `users` table is empty — restarting will not reset the password.

Interactive API docs: <http://localhost:8044/docs>

## Tests

```bash
uv run pytest
```

They run against the dev Postgres container, creating a `podarium_test` database beside it.
Coverage is deliberately narrow: the invariants that are expensive to get wrong (refresh
idempotency, `first_seen_at` stability, retention keeping rows, byte-range correctness,
Podcast Index signing) rather than every endpoint.

## End-to-end check

`scripts/verify.sh` walks the whole phase-1 surface with curl — subscribe, refresh, queue,
download, range request, purge, sync — against a running server.

## Deployment

`deploy/portainer-stack.yml` is pasted into the Portainer web editor on `docker-audio`; no
compose file lives on the host. Read the comments at the top of it before deploying — the
port, the NFS mount options, and the `PGDATA` path all have specific gotchas carried over
from PinePods.

## Invariants

Changes to this codebase should preserve these. Each has a test.

- No client response carries a publisher URL. Artwork is `/api/images/...`, audio is
  `/api/stream/...`.
- Retention deletes files, never episode rows.
- `first_seen_at`, not `published_at`, decides whether an episode is new.
- Feed refresh is idempotent.
- Every user-scoped table carries `user_id`, even with exactly one user.
- Nothing large or churny is written to the VM disk.
