#!/bin/sh
set -e

# Migrations run on every boot. Alembic is idempotent, so a container that restarts
# against an already-current database is a no-op.
echo "podarium: running migrations"
alembic upgrade head

echo "podarium: starting api on :8044"
exec uvicorn podarium.main:app --host 0.0.0.0 --port 8044 --app-dir /app/src "$@"
