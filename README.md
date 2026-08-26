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
- Nothing large or churny is written to the VM disk.
