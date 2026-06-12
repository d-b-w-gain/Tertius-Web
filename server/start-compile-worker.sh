#!/bin/sh
set -eu

export PYTHONPATH="/app:/app/server${PYTHONPATH:+:$PYTHONPATH}"
cd /app
exec python -m workflows.intus.compile_worker
