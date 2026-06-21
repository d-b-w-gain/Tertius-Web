#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-tertius}"
RELEASE_NAME="${RELEASE_NAME:-tertius}"
UI_LOCAL_PORT="${UI_LOCAL_PORT:-18080}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18000}"
METRICS_LOCAL_PORT="${METRICS_LOCAL_PORT:-8428}"
STATUS_FILE="${ROOT_DIR}/.tmp/harness/k3s.env"
PID_FILE="${ROOT_DIR}/.tmp/harness/k3s-port-forwards.env"

usage() {
  cat <<EOF
Usage: $(basename "$0") <up|ports|smoke|live-flow|status|stop-ports|down|delete-data>
EOF
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

preflight_ports() {
  for port in "$UI_LOCAL_PORT" "$API_LOCAL_PORT" "$METRICS_LOCAL_PORT"; do
    if ! port_free "$port"; then
      echo "Port ${port} is already in use. k3s and Compose parity share default ports." >&2
      echo "Set UI_LOCAL_PORT/API_LOCAL_PORT/METRICS_LOCAL_PORT or stop the conflicting runtime." >&2
      exit 1
    fi
  done
}

require_not_flux_managed() {
  if [ "${ALLOW_FLUX_MANAGED_RELEASE:-false}" = "true" ]; then
    return
  fi
  if command -v kubectl >/dev/null 2>&1 && kubectl get helmrelease "$RELEASE_NAME" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "Refusing to operate on Flux-managed HelmRelease ${NAMESPACE}/${RELEASE_NAME}." >&2
    echo "Set ALLOW_FLUX_MANAGED_RELEASE=true only when intentional." >&2
    exit 1
  fi
}

status() {
  echo "Namespace: ${NAMESPACE}"
  echo "Release: ${RELEASE_NAME}"
  echo "UI URL: http://localhost:${UI_LOCAL_PORT}"
  echo "API URL: http://localhost:${API_LOCAL_PORT}"
  echo "Metrics URL: http://localhost:${METRICS_LOCAL_PORT}"
  [ -f "$STATUS_FILE" ] && {
    echo
    echo "Status file: ${STATUS_FILE}"
    sed -n '1,120p' "$STATUS_FILE"
  }
  if command -v kubectl >/dev/null 2>&1; then
    kubectl get all,pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o wide 2>/dev/null || true
    kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o wide 2>/dev/null || true
  fi
}

first_service_by_component() {
  component=$1
  kubectl get svc -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=${component}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

service_port() {
  svc=$1
  preferred=$2
  port=$(kubectl get svc "$svc" -n "$NAMESPACE" -o "jsonpath={.spec.ports[?(@.name==\"${preferred}\")].port}" 2>/dev/null || true)
  [ -n "$port" ] || port=$(kubectl get svc "$svc" -n "$NAMESPACE" -o jsonpath='{.spec.ports[0].port}')
  printf '%s\n' "$port"
}

start_one_port_forward() {
  name=$1
  svc=$2
  local_port=$3
  remote_port=$4
  log_file="${ROOT_DIR}/.tmp/harness/${name}.log"

  nohup kubectl port-forward -n "$NAMESPACE" "svc/${svc}" "${local_port}:${remote_port}" >"$log_file" 2>&1 &
  pid=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if grep -q 'Forwarding from' "$log_file"; then
      printf '%s_PID=%s\n' "$name" "$pid" >>"$PID_FILE"
      return
    fi
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      cat "$log_file" >&2
      exit 1
    fi
    sleep 1
  done
  cat "$log_file" >&2
  echo "Timed out waiting for ${name} port-forward." >&2
  exit 1
}

write_status_file() {
  mkdir -p "$(dirname "$STATUS_FILE")"
  {
    printf 'NAMESPACE=%q\n' "$NAMESPACE"
    printf 'RELEASE_NAME=%q\n' "$RELEASE_NAME"
    printf 'UI_BASE_URL=%q\n' "http://127.0.0.1:${UI_LOCAL_PORT}"
    printf 'API_BASE_URL=%q\n' "http://127.0.0.1:${API_LOCAL_PORT}"
    printf 'METRICS_BASE_URL=%q\n' "http://127.0.0.1:${METRICS_LOCAL_PORT}"
  } >"$STATUS_FILE"
}

start_port_forwards() {
  mkdir -p "${ROOT_DIR}/.tmp/harness"
  : >"$PID_FILE"
  ui_svc=$(first_service_by_component ui)
  api_svc=$(first_service_by_component api)
  metrics_svc=$(first_service_by_component metrics-backend)
  [ -n "$ui_svc" ] || ui_svc="${RELEASE_NAME}-ui"
  [ -n "$api_svc" ] || api_svc="${RELEASE_NAME}-api"
  [ -n "$metrics_svc" ] || metrics_svc="${RELEASE_NAME}-victoriametrics"

  start_one_port_forward UI "$ui_svc" "$UI_LOCAL_PORT" "$(service_port "$ui_svc" http)"
  start_one_port_forward API "$api_svc" "$API_LOCAL_PORT" "$(service_port "$api_svc" http)"
  if kubectl get svc "$metrics_svc" -n "$NAMESPACE" >/dev/null 2>&1; then
    start_one_port_forward METRICS "$metrics_svc" "$METRICS_LOCAL_PORT" "$(service_port "$metrics_svc" http)"
  fi
  write_status_file
  echo "UI URL: http://127.0.0.1:${UI_LOCAL_PORT}"
  echo "API URL: http://127.0.0.1:${API_LOCAL_PORT}"
  echo "Metrics URL: http://127.0.0.1:${METRICS_LOCAL_PORT}"
  echo "Wrote harness status: ${STATUS_FILE}"
}

stop_port_forwards() {
  [ -f "$PID_FILE" ] || return 0
  while IFS='=' read -r _ pid; do
    [ -n "${pid:-}" ] || continue
    kill "$pid" >/dev/null 2>&1 || true
  done <"$PID_FILE"
  rm -f "$PID_FILE"
}

ui_reachable() {
  url="${1%/}/"
  curl --silent --show-error --max-time 5 "$url" >/dev/null 2>&1
}

require_compile_worker() {
  if kubectl get scaledjob -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=compile-job" \
    -o name 2>/dev/null | grep -q .; then
    return
  fi
  if kubectl get pods -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=compile-job" \
    -o name 2>/dev/null | grep -q .; then
    return
  fi
  echo "No k3s compile worker was found for ${NAMESPACE}/${RELEASE_NAME}." >&2
  echo "Deploy the validation release with KEDA_ENABLED=true before running live-flow." >&2
  exit 1
}

case "${1:-}" in
  up)
    preflight_ports
    require_not_flux_managed
    UI_LOCAL_PORT="$UI_LOCAL_PORT" API_LOCAL_PORT="$API_LOCAL_PORT" NAMESPACE="$NAMESPACE" RELEASE_NAME="$RELEASE_NAME" \
      "${ROOT_DIR}/scripts/test-k3s-deployment.sh"
    start_port_forwards
    ;;
  ports)
    preflight_ports
    start_port_forwards
    ;;
  smoke)
    if [ -f "$STATUS_FILE" ]; then
      # shellcheck disable=SC1090
      . "$STATUS_FILE"
    fi
    "${ROOT_DIR}/scripts/smoke-http.sh" "${UI_BASE_URL:-http://localhost:${UI_LOCAL_PORT}}" "${API_BASE_URL:-http://localhost:${API_LOCAL_PORT}}"
    ;;
  live-flow)
    require_compile_worker
    if [ -f "$STATUS_FILE" ]; then
      # shellcheck disable=SC1090
      . "$STATUS_FILE"
    fi
    live_flow_started_ports=false
    if ! ui_reachable "${UI_BASE_URL:-http://localhost:${UI_LOCAL_PORT}}"; then
      stop_port_forwards
      preflight_ports
      start_port_forwards
      live_flow_started_ports=true
      # shellcheck disable=SC1090
      . "$STATUS_FILE"
    fi
    live_flow_args=()
    if [ "${LIVE_FLOW_COMPILE_ONLY:-false}" = "true" ]; then
      live_flow_args+=(--compile-only)
    fi
    if [ "$live_flow_started_ports" = true ]; then
      trap stop_port_forwards EXIT
    fi
    "${ROOT_DIR}/scripts/smoke-live-flow.sh" "${live_flow_args[@]}" "${UI_BASE_URL:-http://localhost:${UI_LOCAL_PORT}}"
    ;;
  status)
    status
    ;;
  stop-ports)
    stop_port_forwards
    ;;
  down)
    require_not_flux_managed
    stop_port_forwards
    NAMESPACE="$NAMESPACE" RELEASE_NAME="$RELEASE_NAME" "${ROOT_DIR}/scripts/test-k3s-deployment.sh" --cleanup
    ;;
  delete-data)
    require_not_flux_managed
    if [ "${HARNESS_ASSUME_YES:-false}" != "true" ]; then
      printf 'Delete release data for %s/%s? Type yes to continue: ' "$NAMESPACE" "$RELEASE_NAME"
      read -r answer
      [ "$answer" = "yes" ] || exit 1
    fi
    stop_port_forwards
    NAMESPACE="$NAMESPACE" RELEASE_NAME="$RELEASE_NAME" "${ROOT_DIR}/scripts/test-k3s-deployment.sh" --cleanup --delete-data
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
