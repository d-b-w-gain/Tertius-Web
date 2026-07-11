#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_PARITY_UI_PORT="${COMPOSE_PARITY_UI_PORT:-18080}"
COMPOSE_PARITY_API_PORT="${COMPOSE_PARITY_API_PORT:-18000}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <dev-up|parity-up|smoke|live-flow|auth-preflight|status|down|delete-data>
EOF
}

compose() {
  docker compose "$@"
}

compose_parity() {
  COMPOSE_PARITY_UI_PORT="$COMPOSE_PARITY_UI_PORT" COMPOSE_PARITY_API_PORT="$COMPOSE_PARITY_API_PORT" \
    docker compose -f "${ROOT_DIR}/docker-compose.yml" -f "${ROOT_DIR}/docker-compose.parity.yml" "$@"
}

compose_available() {
  docker compose version >/dev/null 2>&1
}

port_free() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)
PY
}

preflight_parity_ports() {
  for port in "$COMPOSE_PARITY_UI_PORT" "$COMPOSE_PARITY_API_PORT"; do
    if ! port_free "$port"; then
      echo "Port ${port} is already in use. Compose parity shares defaults with k3s." >&2
      echo "Set COMPOSE_PARITY_UI_PORT/COMPOSE_PARITY_API_PORT or stop the conflicting process." >&2
      exit 1
    fi
  done
}

require_pi_agent_auth() {
  canary_timeout="${PI_AGENT_AUTH_CANARY_TIMEOUT_SECONDS:-60}"
  canary="$(timeout "$canary_timeout" docker compose \
    -f "${ROOT_DIR}/docker-compose.yml" -f "${ROOT_DIR}/docker-compose.parity.yml" \
    run --rm --no-deps --entrypoint pi pi-agent-worker \
    --no-session --no-tools --no-extensions --no-skills --no-prompt-templates \
    --no-themes --no-context-files --no-approve \
    --provider openai-codex --model gpt-5.5 -p \
    'Reply with exactly PI_AUTH_OK and no other text.' 2>/dev/null)" || {
    echo "Compose Pi OpenAI Codex authentication canary failed or timed out after ${canary_timeout}s." >&2
    echo "Run: docker compose run --rm --entrypoint pi pi-agent-worker" >&2
    echo "Complete /login, then exit Pi before running the full live-flow." >&2
    exit 1
  }
  [ "$canary" = "PI_AUTH_OK" ] || {
    echo "Compose Pi OpenAI Codex authentication canary returned an unexpected response." >&2
    echo "Run: docker compose run --rm --entrypoint pi pi-agent-worker" >&2
    exit 1
  }
}

case "${1:-}" in
  dev-up)
    compose up -d postgres keycloak nats otel-collector victoriametrics victoriatraces
    compose up backend compile-job-runner pi-agent-worker frontend
    ;;
  parity-up)
    preflight_parity_ports
    compose_parity up -d --build postgres keycloak nats otel-collector victoriametrics victoriatraces backend compile-job-runner pi-agent-worker frontend
    ;;
  smoke)
    "${ROOT_DIR}/scripts/smoke-http.sh" \
      "http://localhost:${COMPOSE_PARITY_UI_PORT}" \
      "http://localhost:${COMPOSE_PARITY_API_PORT}"
    ;;
  live-flow)
    live_flow_args=()
    if [ "${LIVE_FLOW_COMPILE_ONLY:-false}" = "true" ]; then
      live_flow_args+=(--compile-only)
    else
      require_pi_agent_auth
    fi
    "${ROOT_DIR}/scripts/smoke-live-flow.sh" "${live_flow_args[@]}" "http://localhost:${COMPOSE_PARITY_UI_PORT}"
    ;;
  auth-preflight)
    require_pi_agent_auth
    ;;
  status)
    if compose_available; then
      compose ps
    else
      echo "Docker Compose plugin is unavailable; service status cannot be queried."
    fi
    echo "Compose dev UI: http://localhost:5173"
    echo "Compose dev API: http://localhost:8000"
    echo "Compose dev Metrics: http://localhost:8428"
    echo "Compose dev Traces: http://localhost:10428"
    echo "Compose parity UI: http://localhost:${COMPOSE_PARITY_UI_PORT}"
    echo "Compose parity API: http://localhost:${COMPOSE_PARITY_API_PORT}"
    echo "k3s uses the same default parity ports; override with COMPOSE_PARITY_UI_PORT and COMPOSE_PARITY_API_PORT."
    ;;
  down)
    compose_parity down
    compose down
    ;;
  delete-data)
    if [ "${HARNESS_ASSUME_YES:-false}" != "true" ]; then
      printf 'Delete all Compose harness data, including pi-agent-auth? Type yes to continue: '
      read -r answer
      [ "$answer" = "yes" ] || exit 1
    fi
    compose_parity down -v
    compose down -v
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
