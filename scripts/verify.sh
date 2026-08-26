#!/usr/bin/env bash
# End-to-end check of the phase-1 API against a running server.
#
#   ./scripts/verify.sh [base_url] [username] [password]
#
# Walks the whole surface: login, subscribe, refresh idempotency, queue, download,
# byte-range serving, retention, delta sync, OPML, metrics. Exits non-zero on the first
# failure.

set -euo pipefail

BASE="${1:-http://127.0.0.1:8044}"
USERNAME="${2:-${PODARIUM_USERNAME:-jworthington}}"
PASSWORD="${3:-${PODARIUM_PASSWORD:-devpassword}}"
FEED="${VERIFY_FEED_URL:-https://feeds.npr.org/500005/podcast.xml}"

JAR="$(mktemp)"
TMP="$(mktemp -d)"
TOKEN_ID=""

cleanup() {
  # Revoke the device token this run created. Each one is a long-lived credential with
  # full API access, so a script that mints one per run and walks away leaves a pile of
  # live keys behind -- revoke before the cookie jar goes, since revoking needs it.
  if [ -n "$TOKEN_ID" ]; then
    curl -sS -b "$JAR" -X DELETE "$BASE/api/auth/token/$TOKEN_ID" -o /dev/null || true
  fi
  rm -rf "$JAR" "$TMP"
}
trap cleanup EXIT

pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }

api() { curl -sS -b "$JAR" -c "$JAR" "$@"; }
code() { curl -sS -b "$JAR" -c "$JAR" -o /dev/null -w '%{http_code}' "$@"; }
json() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }

step "health"
[ "$(code "$BASE/healthz")" = 200 ] || fail "healthz"
pass "healthz responds"

step "auth"
[ "$(code -X POST "$BASE/api/feeds")" = 401 ] || fail "unauthenticated request was allowed"
pass "unauthenticated requests are rejected"
api -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" >/dev/null || fail "login"
pass "logged in as $USERNAME"
api -X POST "$BASE/api/auth/token" -H 'content-type: application/json' \
  -d '{"name":"verify"}' > "$TMP/token.json"
TOKEN="$(python3 -c "import json;print(json.load(open('$TMP/token.json'))['token'])")"
TOKEN_ID="$(python3 -c "import json;print(json.load(open('$TMP/token.json'))['id'])")"
[ "$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$BASE/api/auth/me")" = 200 ] \
  || fail "bearer token"
pass "bearer token works"

step "subscribe"
FEED_ID="$(api -X POST "$BASE/api/feeds" -H 'content-type: application/json' \
  -d "{\"feed_url\":\"$FEED\"}" | json 'd["id"]')"
pass "subscribed to feed $FEED_ID"

api "$BASE/api/episodes?feed_id=$FEED_ID&limit=200" > "$TMP/before.json"
COUNT_1="$(python3 -c "import json;print(len(json.load(open('$TMP/before.json'))['items']))")"
[ "$COUNT_1" -gt 0 ] || fail "no episodes parsed"
pass "$COUNT_1 episodes parsed"

step "refresh is idempotent"
api -X POST "$BASE/api/feeds/$FEED_ID/refresh" >/dev/null
api -X POST "$BASE/api/feeds/$FEED_ID/refresh" >/dev/null
api "$BASE/api/episodes?feed_id=$FEED_ID&limit=200" > "$TMP/after.json"

# Compared as a set, not a count. The default feed is an hourly news bulletin, so a genuinely
# new episode can land mid-run -- that is the feed working, not a broken refresh. What
# idempotency actually claims is narrower: refreshing re-adds nothing and loses nothing.
python3 - "$TMP/before.json" "$TMP/after.json" <<'PY' || exit 1
import json, sys
before = json.load(open(sys.argv[1]))["items"]
after = json.load(open(sys.argv[2]))["items"]

guids = [e["guid"] for e in after]
if len(guids) != len(set(guids)):
    sys.exit("  FAIL refresh created duplicate GUIDs")

lost = {e["id"] for e in before} - {e["id"] for e in after}
if lost:
    sys.exit(f"  FAIL refresh dropped {len(lost)} episodes")

# first_seen_at is what "is this new?" is built on, so it must survive a refresh untouched.
seen = {e["id"]: e["first_seen_at"] for e in after}
moved = [e["id"] for e in before if seen.get(e["id"]) != e["first_seen_at"]]
if moved:
    sys.exit(f"  FAIL refresh moved first_seen_at on {len(moved)} episodes")

arrived = len(after) - len(before)
if arrived:
    print(f"  ..   {arrived} genuinely new episode(s) arrived mid-run; not counted against idempotency")
PY
pass "two refreshes re-added nothing and lost nothing"

step "no publisher URLs in responses"
api "$BASE/api/episodes?feed_id=$FEED_ID&limit=200" > "$TMP/eps.json"
python3 - "$TMP/eps.json" <<'PY' || exit 1
import json, re, sys
items = json.load(open(sys.argv[1]))["items"]
leaks = [
    (i["id"], k) for i in items for k, v in i.items()
    if k != "description_html" and isinstance(v, str) and re.search(r"https?://", v)
]
if leaks:
    print(f"  FAIL publisher URLs exposed: {leaks}", file=sys.stderr)
    sys.exit(1)
PY
pass "no publisher URLs outside description bodies"

EP_ID="$(python3 -c "
import json;d=json.load(open('$TMP/eps.json'))
print(next(e['id'] for e in d['items'] if e['enclosure_bytes']))")"

step "artwork is served locally"
[ "$(code "$BASE/api/images/feed/$FEED_ID")" = 200 ] || fail "artwork"
pass "artwork cached and served from /api/images"

step "queue and download"
api -X POST "$BASE/api/queue" -H 'content-type: application/json' \
  -d "{\"episode_id\":$EP_ID}" >/dev/null
printf '  waiting for the download worker'
for _ in $(seq 1 120); do
  DOWNLOADED="$(api "$BASE/api/episodes/$EP_ID" | json 'd["downloaded"]')"
  [ "$DOWNLOADED" = "True" ] && break
  printf '.'; sleep 2
done
printf '\n'
[ "$DOWNLOADED" = "True" ] || fail "episode never downloaded"
SIZE="$(api "$BASE/api/episodes/$EP_ID" | json 'd["local_bytes"]')"
pass "episode $EP_ID downloaded ($SIZE bytes)"

step "range requests"
HEADERS="$TMP/h.txt"
api -o "$TMP/full.bin" -D "$HEADERS" "$BASE/api/stream/$EP_ID" >/dev/null
grep -qi '^accept-ranges: bytes' "$HEADERS" || fail "Accept-Ranges missing"
pass "full response advertises Accept-Ranges"

api -r 0-1023 -o "$TMP/head.bin" -D "$HEADERS" "$BASE/api/stream/$EP_ID" >/dev/null
grep -qi '206' "$HEADERS" || fail "range request did not return 206"
grep -qi "^content-range: bytes 0-1023/$SIZE" "$HEADERS" || fail "wrong Content-Range"
[ "$(wc -c < "$TMP/head.bin")" -eq 1024 ] || fail "wrong byte count"
pass "bytes=0-1023 returns 206 with the right Content-Range"

api -r 1000- -o "$TMP/tail.bin" "$BASE/api/stream/$EP_ID" >/dev/null
python3 - "$TMP/full.bin" "$TMP/head.bin" "$TMP/tail.bin" <<'PY' || exit 1
import sys
full, head, tail = (open(p, "rb").read() for p in sys.argv[1:4])
assert head == full[:1024], "leading range bytes do not match the file"
assert tail == full[1000:], "open-ended range bytes do not match the file"
PY
pass "range bytes match the file exactly"

[ "$(code -r 99999999- "$BASE/api/stream/$EP_ID")" = 416 ] || fail "out-of-range should be 416"
pass "unsatisfiable range returns 416"

step "retention keeps the row"
GUID="$(api "$BASE/api/episodes/$EP_ID" | json 'd["guid"]')"
api -X DELETE "$BASE/api/queue/$EP_ID" >/dev/null
[ "$(code -X DELETE "$BASE/api/episodes/$EP_ID/download")" = 204 ] || fail "purge"
AFTER="$(api "$BASE/api/episodes/$EP_ID")"
[ "$(echo "$AFTER" | json 'd["downloaded"]')" = "False" ] || fail "file still present"
[ "$(echo "$AFTER" | json 'd["guid"]')" = "$GUID" ] || fail "row or GUID changed"
[ "$(echo "$AFTER" | json 'd["purged_at"] is not None')" = "True" ] || fail "purged_at not set"
pass "file purged, row and GUID intact"

api -X POST "$BASE/api/feeds/$FEED_ID/refresh" >/dev/null
api "$BASE/api/episodes?feed_id=$FEED_ID&limit=200" > "$TMP/after-purge.json"

# Assert what the invariant actually claims about *this* episode, rather than that the
# feed's total is unchanged. The default feed is an hourly news bulletin, so a new episode
# can land mid-run -- that is the feed working, and it failed this check for it.
python3 - "$TMP/after-purge.json" "$EP_ID" "$GUID" <<'PY' || exit 1
import json, sys
items = json.load(open(sys.argv[1]))["items"]
episode_id, guid = int(sys.argv[2]), sys.argv[3]

matching = [e for e in items if e["guid"] == guid]
if len(matching) != 1:
    sys.exit(f"  FAIL the purged GUID appears {len(matching)} times; it was re-added")
if matching[0]["id"] != episode_id:
    sys.exit("  FAIL the purged episode came back under a new id")
if matching[0]["downloaded"]:
    sys.exit("  FAIL the purged episode was downloaded again")
if not matching[0]["purged_at"]:
    sys.exit("  FAIL purged_at was cleared by the refresh")
PY
pass "refresh after purge did not re-add the episode"

step "delta sync"
# Establish a known baseline *before* taking the cursor. A previous run may have left this
# episode played, and writing the value it already holds is correctly a no-op -- an
# unchanged row must not be pushed into every client's delta -- so the script has to make
# a real change to observe one.
api -X PUT "$BASE/api/episodes/$EP_ID/state" -H 'content-type: application/json' \
  -d '{"played":false,"position_seconds":0}' >/dev/null

CURSOR="$(api "$BASE/api/sync" | json 'd["now"]')"
EMPTY="$(api --get --data-urlencode "since=$CURSOR" "$BASE/api/sync" | json 'len(d["episodes"])')"
[ "$EMPTY" = 0 ] || fail "sync returned changes with a current cursor"

api -X PUT "$BASE/api/episodes/$EP_ID/state" -H 'content-type: application/json' \
  -d '{"played":true,"position_seconds":42}' >/dev/null
DELTA="$(api --get --data-urlencode "since=$CURSOR" "$BASE/api/sync" | json 'len(d["episodes"])')"
[ "$DELTA" -ge 1 ] || fail "state change did not appear in the delta"
pass "sync cursor is empty when current and picks up changes"

step "sync paging"
# A truncated sync that did not say so would be silent data loss: the client adopts `now`
# as its next cursor and the episodes that did not fit are never mentioned again.
python3 - "$JAR" "$BASE" <<'PY' || exit 1
import json, subprocess, sys
jar, base = sys.argv[1], sys.argv[2]

def get(path, params):
    args = ["curl", "-sS", "-b", jar, "--get", f"{base}{path}"]
    for key, value in params.items():
        args += ["--data-urlencode", f"{key}={value}"]
    return json.loads(subprocess.run(args, capture_output=True, text=True).stdout)

def drain(path, key, page_size):
    """Page to exhaustion, returning ids in order plus the page count."""
    ids, pages, cursor = [], 0, None
    while True:
        params = {"limit": page_size}
        if cursor:
            params["cursor"] = cursor
        payload = get(path, params)
        ids += [item["id"] for item in payload[key]]
        pages += 1
        cursor = payload.get("next_cursor")
        if not cursor:
            return ids, pages
        if pages > 500:
            sys.exit(f"  FAIL {path} cursor never terminated after {pages} pages")

# Deliberately a small page size: the point is to force several pages, whatever the size
# of the library this runs against.
synced, pages = drain("/api/sync", "episodes", 250)
listed, _ = drain("/api/episodes", "items", 200)

if len(synced) != len(set(synced)):
    sys.exit("  FAIL sync repeated episodes across pages")
if set(synced) != set(listed):
    missing = len(set(listed) - set(synced))
    sys.exit(f"  FAIL sync missed {missing} episodes that /api/episodes returns")
print(f"  ..   {len(synced)} episodes over {pages} sync pages")
PY
pass "a paged sync returns every episode exactly once"

[ "$(code "$BASE/api/sync?cursor=nonsense")" = 400 ] || fail "malformed cursor should be rejected"
pass "malformed cursor rejected"

step "opml"
api "$BASE/api/opml/export" -o "$TMP/export.opml" >/dev/null
grep -q "$FEED" "$TMP/export.opml" || fail "feed missing from OPML export"
api -X POST "$BASE/api/opml/import" -H 'content-type: text/xml' \
  --data-binary @"$TMP/export.opml" | grep -q '"imported":0' || fail "re-import created duplicates"
pass "OPML round-trips without duplicating subscriptions"

step "storage report"
# Asserted as identities, not amounts: this run's own downloads and whatever else is on
# this disk both move the totals, but the parts must always add up to the whole.
api "$BASE/api/storage" > "$TMP/storage.json"
python3 - "$TMP/storage.json" <<'STORAGE' || fail "storage report does not add up"
import json, sys

d = json.load(open(sys.argv[1]))
assert d["protected_bytes"] + d["reclaimable_bytes"] == d["total_bytes"], d
assert d["protected_episodes"] <= d["episodes"], d
assert sum(f["bytes"] for f in d["feeds"]) == d["total_bytes"], d
assert sum(f["episodes"] for f in d["feeds"]) == d["episodes"], d
# Sorted largest first: the panel's whole purpose is finding what to trim.
sizes = [f["bytes"] for f in d["feeds"]]
assert sizes == sorted(sizes, reverse=True), d
STORAGE
pass "storage totals reconcile with the per-feed breakdown"

step "metrics"
curl -sS "$BASE/metrics" | grep -q '^podarium_' || fail "no podarium metrics exposed"
pass "Prometheus metrics exposed"

step "web ui"
if curl -sS -o /dev/null -w '%{http_code}' "$BASE/" | grep -q 200; then
  curl -sS "$BASE/" | grep -qi '<div id="root"' || fail "index.html is not the built app"
  pass "index.html served at /"

  # A client-side route has no file behind it and must fall back to index.html.
  curl -sS "$BASE/feeds/$FEED_ID" | grep -qi '<div id="root"' || fail "SPA deep link did not fall back"
  pass "deep link falls back to index.html"

  # ...but an unknown API path must still be JSON, not HTML.
  curl -sS "$BASE/api/does-not-exist" | grep -q '"error"' || fail "unknown API path returned HTML"
  pass "unknown /api path still returns a JSON error"

  ASSET="$(curl -sS "$BASE/" | grep -o '/assets/[^"]*\.js' | head -1)"
  [ -n "$ASSET" ] && [ "$(code "$BASE$ASSET")" = 200 ] || fail "bundle not served"
  pass "hashed bundle served from /assets"

  # Artwork is validated rather than cached blind: a publisher can change their cover art
  # behind a URL that never changes.
  ART_HEADERS="$TMP/art.txt"
  curl -sS -b "$JAR" -D "$ART_HEADERS" -o /dev/null "$BASE/api/images/feed/$FEED_ID"
  grep -qi '^etag:' "$ART_HEADERS" || fail "artwork served without an ETag"
  grep -qi '^cache-control:.*must-revalidate' "$ART_HEADERS" || fail "artwork not revalidated"
  ART_ETAG="$(grep -i '^etag:' "$ART_HEADERS" | tr -d '\r' | cut -d' ' -f2)"
  [ "$(curl -sS -b "$JAR" -o /dev/null -w '%{http_code}' -H "If-None-Match: $ART_ETAG" "$BASE/api/images/feed/$FEED_ID")" = 304 ] \
    || fail "artwork did not answer a conditional request with 304"
  pass "artwork revalidates with an ETag"
else
  printf '  --   no web UI built; skipping (run: cd web && npm run build)\n'
fi

printf '\nAll checks passed.\n'
