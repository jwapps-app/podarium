#!/bin/sh
# Nightly logical backup of the Podarium database.
#
# vzdump already captures the pgdata directory, but a filesystem snapshot of a running
# Postgres is crash-consistent, not consistent: it is what you would have if the power went
# out mid-write. Postgres normally replays its WAL and comes up clean from that, but "normally"
# is doing real work in that sentence -- a snapshot that is not atomic across the whole
# directory can catch data files and WAL from slightly different moments.
#
# pg_dump has no such problem. It reads inside a transaction and produces a logically
# consistent file whatever else is happening, restorable into any Postgres and verifiable by
# reading it. Writing it into the same tree vzdump sweeps means the backup ends up holding
# both the raw directory and a known-good dump.
#
# What is in this database: subscriptions, every playback position, stars, the queue, API
# tokens. The audio can be downloaded again. None of this can.
set -eu

DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DIR/podarium-$STAMP.sql.gz"

mkdir -p "$DIR"

# Written to a temporary name and moved into place only on success, so a dump interrupted
# halfway cannot be mistaken for a complete one by whatever comes to restore it.
pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip -c > "$OUT.partial"
mv "$OUT.partial" "$OUT"

# Keep the newest N and drop the rest.
ls -1t "$DIR"/podarium-*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -f "$old"
done

echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
