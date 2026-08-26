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
trap 'rm -rf "$JAR" "$TMP"' EXIT

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
TOKEN="$(api -X POST "$BASE/api/auth/token" -H 'content-type: application/json' \
  -d '{"name":"verify"}' | json 'd["token"]')"
[ "$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$BASE/api/auth/me")" = 200 ] \
  || fail "bearer token"
pass "bearer token works"

step "subscribe"
FEED_ID="$(api -X POST "$BASE/api/feeds" -H 'content-type: application/json' \
  -d "{\"feed_url\":\"$FEED\"}" | json 'd["id"]')"
pass "subscribed to feed $FEED_ID"

COUNT_1="$(api "$BASE/api/episodes?feed_id=$FEED_ID&limit=200" | json 'len(d["items"])')"
[ "$COUNT_1" -gt 0 ] || fail "no episodes parsed"
pass "$COUNT_1 episodes parsed"

step "refresh is idempotent"
api -X POST "$BASE/api/feeds/$FEED_ID/refresh" >/dev/null
api -X POST "$BASE/api/feeds/$FEED_ID/refresh" >/dev/null
COUNT_2="$(api "$BASE/api/episodes?feed_id=$FEED_ID&limit=200" | json 'len(d["items"])')"
[ "$COUNT_1" = "$COUNT_2" ] || fail "episode count changed after refresh ($COUNT_1 -> $COUNT_2)"
pass "two refreshes added no rows"

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
COUNT_3="$(api "$BASE/api/episodes?feed_id=$FEED_ID&limit=200" | json 'len(d["items"])')"
[ "$COUNT_3" = "$COUNT_1" ] || fail "refresh resurrected the purged episode"
pass "refresh after purge did not re-add the episode"

step "delta sync"
CURSOR="$(api "$BASE/api/sync" | json 'd["now"]')"
EMPTY="$(api --get --data-urlencode "since=$CURSOR" "$BASE/api/sync" | json 'len(d["episodes"])')"
[ "$EMPTY" = 0 ] || fail "sync returned changes with a current cursor"
api -X PUT "$BASE/api/episodes/$EP_ID/state" -H 'content-type: application/json' \
  -d '{"played":true}' >/dev/null
DELTA="$(api --get --data-urlencode "since=$CURSOR" "$BASE/api/sync" | json 'len(d["episodes"])')"
[ "$DELTA" -ge 1 ] || fail "state change did not appear in the delta"
pass "sync cursor is empty when current and picks up changes"

step "opml"
api "$BASE/api/opml/export" -o "$TMP/export.opml" >/dev/null
grep -q "$FEED" "$TMP/export.opml" || fail "feed missing from OPML export"
api -X POST "$BASE/api/opml/import" -H 'content-type: text/xml' \
  --data-binary @"$TMP/export.opml" | grep -q '"imported":0' || fail "re-import created duplicates"
pass "OPML round-trips without duplicating subscriptions"

step "metrics"
curl -sS "$BASE/metrics" | grep -q '^podarium_' || fail "no podarium metrics exposed"
pass "Prometheus metrics exposed"

printf '\nAll checks passed.\n'
