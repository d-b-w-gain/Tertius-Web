#!/usr/bin/env sh
set -eu

export PYTHONPATH="${PYTHONPATH:-/app:/app/server}"
exec python -m workflows.intus.pi_agent_job
