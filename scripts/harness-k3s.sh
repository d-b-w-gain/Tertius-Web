#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE_EXPLICIT=false
RELEASE_NAME_EXPLICIT=false
[ "${NAMESPACE+x}" = x ] && NAMESPACE_EXPLICIT=true
[ "${RELEASE_NAME+x}" = x ] && RELEASE_NAME_EXPLICIT=true
NAMESPACE="${NAMESPACE:-tertius}"
RELEASE_NAME="${RELEASE_NAME:-tertius}"
UI_LOCAL_PORT="${UI_LOCAL_PORT:-18080}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18000}"
METRICS_LOCAL_PORT="${METRICS_LOCAL_PORT:-8428}"
TRACES_LOCAL_PORT="${TRACES_LOCAL_PORT:-10428}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-tertius}"
KEYCLOAK_LOCAL_PORT="${KEYCLOAK_LOCAL_PORT:-0}"
PORT_FORWARD_ADDRESS="${PORT_FORWARD_ADDRESS:-127.0.0.1}"
HARNESS_STATE_DIR="${HARNESS_STATE_DIR:-${ROOT_DIR}/.tmp/harness}"
STATUS_FILE="${HARNESS_STATE_DIR}/k3s.env"
PID_FILE=""
PORT_FORWARD_ATTEMPTS="${PORT_FORWARD_ATTEMPTS:-10}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <up|ports|smoke|live-flow|status|stop-ports|down|delete-data|adopt> [options]

Cleanup options: --retain-data, --retain-auth. delete-data is a compatibility alias for full cleanup.
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

matching_flux_release() {
  flux_json=$(kubectl get helmreleases.helm.toolkit.fluxcd.io --all-namespaces -o json 2>/dev/null) || return 2
  if printf '%s' "$flux_json" | jq -e --arg namespace "$NAMESPACE" --arg release "$RELEASE_NAME" '
    any(.items[]?;
      ((.spec.targetNamespace // .metadata.namespace) == $namespace) and
      ((.spec.releaseName // .metadata.name) == $release)
    )
  ' >/dev/null; then
    return 0
  else
    jq_status=$?
  fi
  [ "$jq_status" -eq 1 ] && return 1
  return 2
}

require_not_flux_managed() {
  allow_override=${1:-false}
  if [ "$allow_override" = true ] && [ "${ALLOW_FLUX_MANAGED_RELEASE:-false}" = "true" ]; then
    return
  fi
  if command -v kubectl >/dev/null 2>&1; then
    if matching_flux_release; then
      echo "Refusing to operate on Flux-managed HelmRelease ${NAMESPACE}/${RELEASE_NAME}." >&2
      exit 1
    else
      flux_status=$?
      if [ "$allow_override" = false ] && [ "$flux_status" -eq 2 ]; then
        echo "Unable to inspect Flux HelmRelease ownership; refusing ${NAMESPACE}/${RELEASE_NAME}." >&2
        exit 1
      fi
    fi
  fi
}

resolve_saved_cleanup_target() {
  if [ "$NAMESPACE_EXPLICIT" = false ] && [ "$RELEASE_NAME_EXPLICIT" = false ] && [ -f "$STATUS_FILE" ]; then
    # shellcheck disable=SC1090
    . "$STATUS_FILE"
  fi
}

new_lease_id() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  else
    sed -n '1p' /proc/sys/kernel/random/uuid
  fi
}

adopt_release() {
  target=${1:-}
  command -v jq >/dev/null 2>&1 || { echo "Missing required command: jq" >&2; exit 1; }
  case "$target" in
    */*) ;;
    *) echo "Usage: $(basename "$0") adopt <namespace>/<release>" >&2; exit 2 ;;
  esac
  NAMESPACE=${target%%/*}
  RELEASE_NAME=${target#*/}
  if [ -z "$NAMESPACE" ] || [ -z "$RELEASE_NAME" ] || [ "$target" != "${NAMESPACE}/${RELEASE_NAME}" ]; then
    echo "Adoption target must be exactly <namespace>/<release>." >&2
    exit 2
  fi
  if [ "$RELEASE_NAME" = tertius ]; then
    echo "Refusing to adopt protected release ${NAMESPACE}/tertius." >&2
    exit 1
  fi
  require_not_flux_managed false
  if ! helm status "$RELEASE_NAME" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "Refusing adoption: Helm release ${NAMESPACE}/${RELEASE_NAME} does not exist." >&2
    exit 1
  fi
  existing_marker=$(kubectl get configmap "${RELEASE_NAME}-harness-lifecycle" -n "$NAMESPACE" -o name 2>/dev/null || true)
  if [ -n "$existing_marker" ]; then
    echo "Refusing adoption: lifecycle marker ${NAMESPACE}/${RELEASE_NAME}-harness-lifecycle already exists." >&2
    exit 1
  fi
  printf 'Type %s to adopt this existing release: ' "$target" >&2
  read -r confirmation
  if [ "$confirmation" != "$target" ]; then
    echo "Adoption confirmation did not match ${target}." >&2
    exit 1
  fi
  lease_id=$(new_lease_id)
  ttl_seconds=${HARNESS_TTL_SECONDS:-21600}
  case "$ttl_seconds" in
    ""|*[!0-9]*) echo "HARNESS_TTL_SECONDS must be an integer from 900 to 86400." >&2; exit 1 ;;
  esac
  if [ "$ttl_seconds" -lt 900 ] || [ "$ttl_seconds" -gt 86400 ]; then
    echo "HARNESS_TTL_SECONDS must be an integer from 900 to 86400." >&2
    exit 1
  fi
  expires_at=$(date -u -d "+${ttl_seconds} seconds" '+%Y-%m-%dT%H:%M:%SZ')
  app_secret_name=${APP_SECRET_NAME:-${RELEASE_NAME}-app}
  kubectl annotate secret "$app_secret_name" -n "$NAMESPACE" "tertius.io/lease-id=${lease_id}" --overwrite
  clusters=$(kubectl get clusters.postgresql.cnpg.io -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
  pvcs=$(kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
  [ -z "$clusters" ] || kubectl annotate -n "$NAMESPACE" $clusters "tertius.io/lease-id=${lease_id}" --overwrite
  [ -z "$pvcs" ] || kubectl annotate -n "$NAMESPACE" $pvcs "tertius.io/lease-id=${lease_id}" --overwrite
  kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${RELEASE_NAME}-harness-lifecycle
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: tertius-harness
    app.kubernetes.io/instance: ${RELEASE_NAME}
    tertius.io/harness-managed: "true"
  annotations:
    tertius.io/lease-id: ${lease_id}
    tertius.io/release-name: ${RELEASE_NAME}
    tertius.io/app-secret-name: ${app_secret_name}
    tertius.io/expires-at: ${expires_at}
    tertius.io/cleanup-policy: delete
EOF
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

configure_pid_file() {
  context=$(kubectl config current-context 2>/dev/null) || {
    echo "Unable to resolve current Kubernetes context for port-forward ownership." >&2
    return 1
  }
  safe_context=$(printf '%s' "$context" | tr -c '[:alnum:]._-' '_')
  safe_namespace=$(printf '%s' "$NAMESPACE" | tr -c '[:alnum:]._-' '_')
  safe_release=$(printf '%s' "$RELEASE_NAME" | tr -c '[:alnum:]._-' '_')
  PID_FILE="${HARNESS_STATE_DIR}/port-forwards/${safe_context}__${safe_namespace}__${safe_release}.env"
}

process_start_token() {
  awk '{print $22}' "/proc/$1/stat" 2>/dev/null
}

process_exact_command() {
  tr '\0' ' ' 2>/dev/null <"/proc/$1/cmdline" | sed 's/[[:space:]]*$//'
}

record_port_forward_identity() {
  pid=$1
  start_token=$2
  exact_command=$3
  mkdir -p "$(dirname "$PID_FILE")"
  state_tmp=$(mktemp "${PID_FILE}.XXXXXX")
  [ ! -f "$PID_FILE" ] || cp "$PID_FILE" "$state_tmp"
  printf '%s\t%s\t%s\n' "$pid" "$start_token" "$exact_command" >>"$state_tmp"
  mv "$state_tmp" "$PID_FILE"
}

terminate_if_owned() {
  pid=$1
  expected_start=$2
  expected_command=$3
  live_start=$(process_start_token "$pid" || true)
  live_command=$(process_exact_command "$pid" || true)
  if [ -n "$live_start" ] && [ "$live_start" = "$expected_start" ] && [ "$live_command" = "$expected_command" ]; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
  fi
}

port_forward_exit() {
  status=$1
  trap - EXIT INT TERM
  stop_port_forwards
  exit "$status"
}

begin_port_forward_session() {
  configure_pid_file
  stop_port_forwards
  mkdir -p "$(dirname "$PID_FILE")"
  state_tmp=$(mktemp "${PID_FILE}.XXXXXX")
  : >"$state_tmp"
  mv "$state_tmp" "$PID_FILE"
  trap 'port_forward_exit $?' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

start_one_port_forward() {
  result_var=$1
  name=$2
  svc=$3
  local_port=$4
  remote_port=$5
  log_file="${HARNESS_STATE_DIR}/${name}.log"

  if [ "$local_port" = "0" ]; then
    port_spec=":${remote_port}"
  else
    port_spec="${local_port}:${remote_port}"
  fi

  nohup kubectl port-forward --address "$PORT_FORWARD_ADDRESS" -n "$NAMESPACE" "svc/${svc}" "$port_spec" >"$log_file" 2>&1 < /dev/null &
  pid=$!
  start_token=""
  exact_command=""
  for _ in $(seq 1 100); do
    start_token=$(process_start_token "$pid" || true)
    exact_command=$(process_exact_command "$pid" || true)
    case " $exact_command " in
      *" port-forward "*" svc/${svc} "*)
        [ -z "$start_token" ] || break
        ;;
    esac
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      break
    fi
    sleep 0.01
  done
  case " $exact_command " in
    *" port-forward "*" svc/${svc} "*) identity_ready=true ;;
    *) identity_ready=false ;;
  esac
  if [ -z "$start_token" ] || [ "$identity_ready" != "true" ]; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
    echo "Unable to record ${name} port-forward process identity." >&2
    exit 1
  fi
  record_port_forward_identity "$pid" "$start_token" "$exact_command"
  for _ in $(seq 1 "$PORT_FORWARD_ATTEMPTS"); do
    if grep -q 'Forwarding from' "$log_file"; then
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
      printf -v "$result_var" '%s' "$selected_port"
      return
    fi
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      cat "$log_file" >&2
      exit 1
    fi
    sleep 1
  done
  terminate_if_owned "$pid" "$start_token" "$exact_command"
  cat "$log_file" >&2
  echo "Timed out waiting for ${name} port-forward." >&2
  exit 1
}

write_status_file() {
  mkdir -p "$(dirname "$STATUS_FILE")"
  {
    printf 'NAMESPACE=%q\n' "$NAMESPACE"
    printf 'RELEASE_NAME=%q\n' "$RELEASE_NAME"
    printf 'APP_SECRET_NAME=%q\n' "${APP_SECRET_NAME:-${RELEASE_NAME}-app}"
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
  begin_port_forward_session
  ui_svc=$(first_service_by_component ui)
  api_svc=$(first_service_by_component api)
  metrics_svc=$(first_service_by_component metrics-backend)
  traces_svc=$(first_service_by_component traces-backend)
  [ -n "$ui_svc" ] || ui_svc="${RELEASE_NAME}-ui"
  [ -n "$api_svc" ] || api_svc="${RELEASE_NAME}-api"
  [ -n "$metrics_svc" ] || metrics_svc="${RELEASE_NAME}-victoriametrics"
  [ -n "$traces_svc" ] || traces_svc="${RELEASE_NAME}-victoriatraces"
  keycloak_svc=$(keycloak_service)

  start_one_port_forward UI_LOCAL_PORT UI "$ui_svc" "$UI_LOCAL_PORT" "$(service_port "$ui_svc" http)"
  start_one_port_forward API_LOCAL_PORT API "$api_svc" "$API_LOCAL_PORT" "$(service_port "$api_svc" http)"
  if kubectl get svc "$metrics_svc" -n "$NAMESPACE" >/dev/null 2>&1; then
    start_one_port_forward METRICS_LOCAL_PORT METRICS "$metrics_svc" "$METRICS_LOCAL_PORT" "$(service_port "$metrics_svc" http)"
  fi
  if kubectl get svc "$traces_svc" -n "$NAMESPACE" >/dev/null 2>&1; then
    start_one_port_forward TRACES_LOCAL_PORT TRACES "$traces_svc" "$TRACES_LOCAL_PORT" "$(service_port "$traces_svc" http)"
  fi
  if [ -n "$keycloak_svc" ] && kubectl get svc "$keycloak_svc" -n "$NAMESPACE" >/dev/null 2>&1; then
    start_one_port_forward KEYCLOAK_LOCAL_PORT KEYCLOAK "$keycloak_svc" "$KEYCLOAK_LOCAL_PORT" "$(service_port "$keycloak_svc" http)"
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
  [ -n "$PID_FILE" ] || configure_pid_file || return 1
  [ -f "$PID_FILE" ] || return 0
  while IFS=$'\t' read -r pid start_token exact_command; do
    [ -n "${pid:-}" ] || continue
    terminate_if_owned "$pid" "$start_token" "$exact_command"
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

if [ "${HARNESS_K3S_LIB_ONLY:-false}" = "true" ]; then
  return 0 2>/dev/null || exit 0
fi

case "${1:-}" in
  up)
    stop_port_forwards
    preflight_ports
    require_not_flux_managed true
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
    shift
    cleanup_args=(--cleanup)
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --retain-data|--retain-auth) cleanup_args+=("$1") ;;
        --delete-data) ;;
        *) echo "Unknown down option: $1" >&2; exit 2 ;;
      esac
      shift
    done
    resolve_saved_cleanup_target
    require_not_flux_managed false
    stop_port_forwards
    NAMESPACE="$NAMESPACE" RELEASE_NAME="$RELEASE_NAME" APP_SECRET_NAME="${APP_SECRET_NAME:-${RELEASE_NAME}-app}" \
      "${ROOT_DIR}/scripts/test-k3s-deployment.sh" "${cleanup_args[@]}"
    ;;
  delete-data)
    resolve_saved_cleanup_target
    require_not_flux_managed false
    stop_port_forwards
    NAMESPACE="$NAMESPACE" RELEASE_NAME="$RELEASE_NAME" APP_SECRET_NAME="${APP_SECRET_NAME:-${RELEASE_NAME}-app}" \
      "${ROOT_DIR}/scripts/test-k3s-deployment.sh" --cleanup
    ;;
  adopt)
    shift
    adopt_release "${1:-}"
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
