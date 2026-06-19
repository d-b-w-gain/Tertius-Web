#!/bin/sh
set -eu

export PYTHONPATH="/app:/app/server${PYTHONPATH:+:$PYTHONPATH}"

cd /app/server

migration_attempts="${API_MIGRATION_ATTEMPTS:-60}"
migration_sleep_seconds="${API_MIGRATION_RETRY_SECONDS:-2}"
attempt=1
while ! alembic upgrade head; do
    if [ "$attempt" -ge "$migration_attempts" ]; then
        echo "Database migrations failed after $attempt attempts." >&2
        exit 1
    fi
    echo "Database is not ready for migrations yet; retrying in ${migration_sleep_seconds}s (${attempt}/${migration_attempts})." >&2
    attempt=$((attempt + 1))
    sleep "$migration_sleep_seconds"
done

cd /app
exec uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8000}"
