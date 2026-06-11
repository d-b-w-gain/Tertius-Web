#!/bin/sh
set -eu

export PYTHONPATH="/app:/app/server${PYTHONPATH:+:$PYTHONPATH}"

cd /app/server
alembic upgrade head

cd /app
exec uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8000}"
