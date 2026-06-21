#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_PARITY_UI_PORT="${COMPOSE_PARITY_UI_PORT:-18080}"
COMPOSE_PARITY_API_PORT="${COMPOSE_PARITY_API_PORT:-18000}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <dev-up|parity-up|smoke|live-flow|status|down>
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

case "${1:-}" in
  dev-up)
    compose up -d postgres keycloak nats otel-collector victoriametrics
    compose up backend compile-job-runner frontend
    ;;
  parity-up)
    preflight_parity_ports
    compose_parity up -d --build postgres keycloak nats otel-collector victoriametrics backend compile-job-runner frontend
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
    fi
    "${ROOT_DIR}/scripts/smoke-live-flow.sh" "${live_flow_args[@]}" "http://localhost:${COMPOSE_PARITY_UI_PORT}"
    ;;
  status)
    if compose_available; then
      compose ps
    else
      echo "Docker Compose plugin is unavailable; service status cannot be queried."
    fi
    echo "Compose dev UI: http://localhost:5173"
    echo "Compose dev API: http://localhost:8000"
    echo "Compose parity UI: http://localhost:${COMPOSE_PARITY_UI_PORT}"
    echo "Compose parity API: http://localhost:${COMPOSE_PARITY_API_PORT}"
    echo "k3s uses the same default parity ports; override with COMPOSE_PARITY_UI_PORT and COMPOSE_PARITY_API_PORT."
    ;;
  down)
    if [ "${DELETE_DATA:-false}" = "true" ]; then
      compose_parity down -v
      compose down -v
    else
      compose_parity down
      compose down
    fi
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
