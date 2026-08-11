#!/usr/bin/env sh
set -eu

export PYTHONPATH="${PYTHONPATH:-/app:/app/server}"
exec python -m workflows.intus.import_3mf_job
