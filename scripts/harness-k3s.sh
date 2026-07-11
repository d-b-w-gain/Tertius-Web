#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-tertius}"
RELEASE_NAME="${RELEASE_NAME:-tertius}"
UI_LOCAL_PORT="${UI_LOCAL_PORT:-18080}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18000}"
METRICS_LOCAL_PORT="${METRICS_LOCAL_PORT:-8428}"
TRACES_LOCAL_PORT="${TRACES_LOCAL_PORT:-10428}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-tertius}"
KEYCLOAK_LOCAL_PORT="${KEYCLOAK_LOCAL_PORT:-0}"
PORT_FORWARD_ADDRESS="${PORT_FORWARD_ADDRESS:-127.0.0.1}"
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
for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
    try:
        s = socket.socket(family)
    except OSError:
        continue
    with s:
        s.settimeout(0.25)
        try:
            s.connect((host, port))
        except OSError:
            continue
        raise SystemExit(1)
PY
}

port_bindable() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
  try:
    s = socket.socket(family)
  except OSError:
    continue
  with s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError:
        raise SystemExit(1)
PY
}

preflight_ports() {
  for port in "$UI_LOCAL_PORT" "$API_LOCAL_PORT" "$METRICS_LOCAL_PORT" "$TRACES_LOCAL_PORT" "$KEYCLOAK_LOCAL_PORT"; do
    [ "$port" = "0" ] && continue
    if ! port_free "$port"; then
      echo "Port ${port} is already in use. k3s and Compose parity share default ports." >&2
      echo "Set UI_LOCAL_PORT/API_LOCAL_PORT/METRICS_LOCAL_PORT/TRACES_LOCAL_PORT or stop the conflicting runtime." >&2
      exit 1
    fi
  done
}

wait_for_ports_free() {
  for _ in $(seq 1 10); do
    all_free=true
    for port in "$UI_LOCAL_PORT" "$API_LOCAL_PORT" "$METRICS_LOCAL_PORT" "$TRACES_LOCAL_PORT" "$KEYCLOAK_LOCAL_PORT"; do
      [ "$port" = "0" ] && continue
      if ! port_free "$port"; then
        all_free=false
        break
      fi
    done
    [ "$all_free" = true ] && return
    sleep 1
  done
  return 1
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
  echo "Traces URL: http://localhost:${TRACES_LOCAL_PORT}"
  if [ "${KEYCLOAK_LOCAL_PORT:-0}" != "0" ]; then
    echo "Keycloak URL: http://localhost:${KEYCLOAK_LOCAL_PORT}"
  fi
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

keycloak_service() {
  svc=$(first_service_by_component keycloak)
  [ -n "$svc" ] || svc=$(kubectl get svc "${RELEASE_NAME}-keycloak-service" -n "$NAMESPACE" -o jsonpath='{.metadata.name}' 2>/dev/null || true)
  [ -n "$svc" ] || svc=$(kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep -i 'keycloak.*service' | head -1 || true)
  [ -n "$svc" ] || svc=$(kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep -i keycloak | head -1 || true)
  printf '%s\n' "$svc"
}

start_one_port_forward() {
  name=$1
  svc=$2
  local_port=$3
  remote_port=$4
  result_var=${5:-}
  log_file="${ROOT_DIR}/.tmp/harness/${name}.log"

  if [ "$local_port" = "0" ]; then
    port_spec=":${remote_port}"
  else
    port_spec="${local_port}:${remote_port}"
  fi

  nohup kubectl port-forward --address "$PORT_FORWARD_ADDRESS" -n "$NAMESPACE" "svc/${svc}" "$port_spec" >"$log_file" 2>&1 < /dev/null &
  pid=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if grep -q 'Forwarding from' "$log_file"; then
      printf '%s_PID=%s\n' "$name" "$pid" >>"$PID_FILE"
      if [ "$local_port" = "0" ]; then
        selected_port=$(awk '
          /^Forwarding from [^:]+:[0-9][0-9]* -> / {
            sub(/^Forwarding from [^:]+:/, "")
            sub(/ -> .*$/, "")
            print
            exit
          }
        ' "$log_file")
      else
        selected_port="$local_port"
      fi
      [ -n "$result_var" ] && printf -v "$result_var" '%s' "$selected_port"
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
    printf 'TRACES_BASE_URL=%q\n' "http://127.0.0.1:${TRACES_LOCAL_PORT}"
    if [ "${KEYCLOAK_LOCAL_PORT:-0}" != "0" ]; then
      printf 'KEYCLOAK_TOKEN_URL=%q\n' "http://127.0.0.1:${KEYCLOAK_LOCAL_PORT}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token"
    fi
  } >"$STATUS_FILE"
}

start_port_forwards() {
  mkdir -p "${ROOT_DIR}/.tmp/harness"
  : >"$PID_FILE"
  ui_svc=$(first_service_by_component ui)
  api_svc=$(first_service_by_component api)
  metrics_svc=$(first_service_by_component metrics-backend)
  traces_svc=$(first_service_by_component traces-backend)
  [ -n "$ui_svc" ] || ui_svc="${RELEASE_NAME}-ui"
  [ -n "$api_svc" ] || api_svc="${RELEASE_NAME}-api"
  [ -n "$metrics_svc" ] || metrics_svc="${RELEASE_NAME}-victoriametrics"
  [ -n "$traces_svc" ] || traces_svc="${RELEASE_NAME}-victoriatraces"
  keycloak_svc=$(keycloak_service)

  start_one_port_forward UI "$ui_svc" "$UI_LOCAL_PORT" "$(service_port "$ui_svc" http)" UI_LOCAL_PORT
  start_one_port_forward API "$api_svc" "$API_LOCAL_PORT" "$(service_port "$api_svc" http)" API_LOCAL_PORT
  if kubectl get svc "$metrics_svc" -n "$NAMESPACE" >/dev/null 2>&1; then
    start_one_port_forward METRICS "$metrics_svc" "$METRICS_LOCAL_PORT" "$(service_port "$metrics_svc" http)" METRICS_LOCAL_PORT
  fi
  if kubectl get svc "$traces_svc" -n "$NAMESPACE" >/dev/null 2>&1; then
    start_one_port_forward TRACES "$traces_svc" "$TRACES_LOCAL_PORT" "$(service_port "$traces_svc" http)" TRACES_LOCAL_PORT
  fi
  if [ -n "$keycloak_svc" ] && kubectl get svc "$keycloak_svc" -n "$NAMESPACE" >/dev/null 2>&1; then
    start_one_port_forward KEYCLOAK "$keycloak_svc" "$KEYCLOAK_LOCAL_PORT" "$(service_port "$keycloak_svc" http)" KEYCLOAK_LOCAL_PORT
  fi
  write_status_file
  echo "UI URL: http://127.0.0.1:${UI_LOCAL_PORT}"
  echo "API URL: http://127.0.0.1:${API_LOCAL_PORT}"
  echo "Metrics URL: http://127.0.0.1:${METRICS_LOCAL_PORT}"
  echo "Traces URL: http://127.0.0.1:${TRACES_LOCAL_PORT}"
  if [ "${KEYCLOAK_LOCAL_PORT:-0}" != "0" ]; then
    echo "Keycloak URL: http://127.0.0.1:${KEYCLOAK_LOCAL_PORT}"
  fi
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

require_pi_agent_auth() {
  claim=$(kubectl get pvc -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=pi-agent-auth" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  [ -n "$claim" ] || {
    echo "Pi agent auth PVC is missing for ${NAMESPACE}/${RELEASE_NAME}." >&2
    echo "Deploy with piAgent.auth.storage.enabled=true, then run scripts/pi-agent-auth.sh login and verify." >&2
    exit 1
  }
  phase=$(kubectl get pvc "$claim" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || true)
  verified=$(kubectl get pvc "$claim" -n "$NAMESPACE" -o jsonpath='{.metadata.annotations.tertius\.io/pi-agent-auth-verified}' 2>/dev/null || true)
  [ "$phase" = "Bound" ] && [ "$verified" = "true" ] || {
    echo "Pi agent auth PVC ${NAMESPACE}/${claim} is not ready (phase=${phase:-missing}, verified=${verified:-false})." >&2
    echo "Run scripts/pi-agent-auth.sh login --namespace ${NAMESPACE} --release ${RELEASE_NAME}, then verify." >&2
    exit 1
  }
}

require_pi_agent_worker() {
  if kubectl get scaledjob -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=pi-agent-worker" \
    -o name 2>/dev/null | grep -q .; then
    return
  fi
  echo "No serial Pi agent worker was found for ${NAMESPACE}/${RELEASE_NAME}." >&2
  echo "Redeploy the verified release with KEDA_ENABLED=true PI_AGENT_ENABLED=true before running full live-flow." >&2
  exit 1
}

case "${1:-}" in
  up)
    stop_port_forwards
    preflight_ports
    require_not_flux_managed
    UI_LOCAL_PORT="$UI_LOCAL_PORT" API_LOCAL_PORT="$API_LOCAL_PORT" NAMESPACE="$NAMESPACE" RELEASE_NAME="$RELEASE_NAME" \
      "${ROOT_DIR}/scripts/test-k3s-deployment.sh"
    stop_port_forwards
    if wait_for_ports_free; then
      start_port_forwards
    else
      echo "Deployment smoke passed, but local smoke port-forwards are still draining." >&2
      echo "Run scripts/harness-k3s.sh ports or scripts/harness-k3s.sh live-flow to start fresh port-forwards." >&2
    fi
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
    if [ -f "$STATUS_FILE" ]; then
      # shellcheck disable=SC1090
      . "$STATUS_FILE"
    fi
    require_compile_worker
    if [ "${LIVE_FLOW_COMPILE_ONLY:-false}" != "true" ]; then
      require_pi_agent_auth
      require_pi_agent_worker
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
    export KEYCLOAK_TOKEN_URL="${KEYCLOAK_TOKEN_URL:-}"
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
