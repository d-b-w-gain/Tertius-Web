#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
MOCK_BIN="${TMP_DIR}/bin"
STATE_DIR="${TMP_DIR}/state"
COMMAND_LOG="${TMP_DIR}/commands.log"
mkdir -p "$MOCK_BIN" "$STATE_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_log() {
  pattern=$1
  description=$2
  grep -Eq -- "$pattern" "$COMMAND_LOG" || fail "$description"
}

assert_not_log() {
  pattern=$1
  description=$2
  if grep -Eq -- "$pattern" "$COMMAND_LOG"; then
    fail "$description"
  fi
}

cat >"${MOCK_BIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'kubectl' >>"$COMMAND_LOG"
printf ' %q' "$@" >>"$COMMAND_LOG"
printf '\n' >>"$COMMAND_LOG"
joined=" $* "

if [ "${1:-}" = config ] && [ "${2:-}" = current-context ]; then
  printf 'test-context\n'
  exit 0
fi

if [[ "$joined" == *" get namespace "* ]]; then
  [ "${MOCK_NAMESPACE_GET_ERROR:-false}" != true ] || exit 7
  if [ ! -f "$STATE_DIR/namespace" ]; then
    [[ "$joined" == *" --ignore-not-found=true "* ]] && exit 0
    exit 1
  fi
  if [[ "$joined" == *" -o name "* ]] || [[ "$joined" == *" -o name" ]]; then
    printf 'namespace/%s\n' "${DIAGNOSTIC_NAMESPACE:-diagnostic-test}"
  else
    sed -n '1p' "$STATE_DIR/namespace-owner"
  fi
  exit 0
fi

if [ "${1:-}" = apply ]; then
  manifest=$(cat)
  printf '%s\n' "$manifest" >>"${COMMAND_LOG}.stdin"
  if printf '%s\n' "$manifest" | grep -q '^kind: Namespace$'; then
    touch "$STATE_DIR/namespace"
    printf '%s\n' "$manifest" | awk '/tertius.io\/diagnostic-owner-id:/ {print $2; exit}' | tr -d '"' >"$STATE_DIR/namespace-owner"
  fi
  exit 0
fi

if [[ "$joined" == *" get job/"*"Failed"* ]]; then
  printf 'True\n'
  exit 0
fi

if [[ "$joined" == *" get job/"*"Complete"* ]]; then
  exit 0
fi

if [ "${1:-}" = delete ] && [[ "$joined" == *" namespace "* ]]; then
  rm -f "$STATE_DIR/namespace" "$STATE_DIR/namespace-owner"
  exit 0
fi

if [ "${1:-}" = port-forward ]; then
  printf '%s\n' "$$" >"$STATE_DIR/last-child-pid"
  awk '{print $22}' "/proc/$$/stat" >"$STATE_DIR/last-child-start-token"
  if [ "${MOCK_PORT_FORWARD_MODE:-ready}" = ready ]; then
    if [ "${HARNESS_K3S_LIB_ONLY:-false}" = true ]; then
      for _ in $(seq 1 100); do
        if find "$HARNESS_STATE_DIR" -type f -name '*.env' -exec grep -q "^$$[[:space:]]" {} \; 2>/dev/null; then
          touch "$STATE_DIR/state-recorded-before-ready"
          echo 'Forwarding from 127.0.0.1:43210 -> 8080'
          break
        fi
        sleep 0.01
      done
    else
      echo 'Forwarding from 127.0.0.1:43210 -> 8080'
    fi
  fi
  while :; do
    /bin/sleep 1
  done
fi

exit 0
EOF

cat >"${MOCK_BIN}/id" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = -u ] && { echo 0; exit 0; }
exec /usr/bin/id "$@"
EOF

chmod +x "${MOCK_BIN}/kubectl" "${MOCK_BIN}/id"

run_namespace_failure_case() {
  script=$1
  library_var=$2
  keep=${3:-false}
  mismatch=${4:-false}
  rm -f "$STATE_DIR/namespace" "$STATE_DIR/namespace-owner"
  : >"$COMMAND_LOG"
  set +e
  env PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
    DIAGNOSTIC_NAMESPACE=diagnostic-test DIAGNOSTIC_KEEP_NAMESPACE="$keep" \
    "$library_var=true" bash -c '
      . "$1"
      claim_diagnostic_namespace
      if [ "$2" = true ]; then
        printf "%s\n" different-owner >"$STATE_DIR/namespace-owner"
      fi
      false
    ' bash "$script" "$mismatch"
  status=$?
  set -e
  [ "$status" -eq 1 ] || fail "${script} must preserve failure exit status"
}

for diagnostic_case in \
  "scripts/diagnose-k3s-networkpolicy.sh TEST_DIAGNOSE_K3S_LIB_ONLY" \
  "scripts/install-gvisor-k3s.sh TEST_GVISOR_K3S_LIB_ONLY"; do
  set -- $diagnostic_case
  script="${ROOT_DIR}/$1"
  library_var=$2

  run_namespace_failure_case "$script" "$library_var"
  [ "$(grep -Ec 'kubectl delete namespace diagnostic-test' "$COMMAND_LOG" || true)" -eq 1 ] || \
    fail "$1 must delete its UUID-owned namespace exactly once on EXIT failure"

  run_namespace_failure_case "$script" "$library_var" false true
  assert_not_log 'kubectl delete namespace diagnostic-test' \
    "$1 must not delete a namespace whose ownership UUID changed"

  run_namespace_failure_case "$script" "$library_var" true false
  assert_not_log 'kubectl delete namespace diagnostic-test' \
    "$1 must honor explicit diagnostic namespace retention"

  rm -f "$STATE_DIR/namespace-owner"
  touch "$STATE_DIR/namespace"
  printf 'external-owner\n' >"$STATE_DIR/namespace-owner"
  : >"$COMMAND_LOG"
  if env PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
    DIAGNOSTIC_NAMESPACE=diagnostic-test DIAGNOSTIC_KEEP_NAMESPACE=false \
    "$library_var=true" bash -c '. "$1"; claim_diagnostic_namespace' bash "$script"; then
    fail "$1 must refuse a pre-existing diagnostic namespace"
  fi
  assert_not_log 'kubectl (apply|delete)' \
    "$1 must not mutate a pre-existing diagnostic namespace"

  rm -f "$STATE_DIR/namespace" "$STATE_DIR/namespace-owner"
  : >"$COMMAND_LOG"
  if env PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
    MOCK_NAMESPACE_GET_ERROR=true DIAGNOSTIC_NAMESPACE=diagnostic-test \
    DIAGNOSTIC_KEEP_NAMESPACE=false "$library_var=true" \
    bash -c '. "$1"; claim_diagnostic_namespace' bash "$script"; then
    fail "$1 must fail closed when namespace inventory cannot be read"
  fi
  assert_not_log 'kubectl (apply|delete)' \
    "$1 must not mutate namespace state after an inventory API error"

  rm -f "$STATE_DIR/namespace" "$STATE_DIR/namespace-owner"
  : >"$COMMAND_LOG"
  set +e
  env PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
    DIAGNOSTIC_NAMESPACE=diagnostic-test DIAGNOSTIC_KEEP_NAMESPACE=false \
    "$library_var=true" bash -c '. "$1"; claim_diagnostic_namespace; kill -TERM $$' bash "$script"
  status=$?
  set -e
  [ "$status" -eq 143 ] || fail "$1 must preserve conventional SIGTERM status 143"
  [ "$(grep -Ec 'kubectl delete namespace diagnostic-test' "$COMMAND_LOG" || true)" -eq 1 ] || \
    fail "$1 must delete its UUID-owned namespace exactly once on SIGTERM"
done

rm -rf "$STATE_DIR/harness"
mkdir -p "$STATE_DIR/harness"
: >"$COMMAND_LOG"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  HARNESS_STATE_DIR="$STATE_DIR/harness" HARNESS_K3S_LIB_ONLY=true \
  NAMESPACE=target-ns RELEASE_NAME=target-release \
  bash -c '
    . "$1"
    begin_port_forward_session
    start_one_port_forward selected UI target-ui 0 8080
    [ "$selected" = 43210 ]
    [ -s "$PID_FILE" ]
    awk -F "\t" "NF != 3 { exit 1 }" "$PID_FILE"
    printf "%s\n" "$PID_FILE" >"$STATE_DIR/pid-path"
    stop_port_forwards
  ' bash "$ROOT_DIR/scripts/harness-k3s.sh"
[ -f "$STATE_DIR/state-recorded-before-ready" ] || \
  fail "port-forward identity must be atomically recorded before readiness"
assert_log 'kubectl config current-context' \
  "port-forward state must be scoped by the current Kubernetes context"
grep -q 'target-ns.*target-release\|target-release.*target-ns' "$STATE_DIR/pid-path" || \
  fail "port-forward state must be isolated per namespace and release"

# A stale/recycled identity must not be signalled.
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  HARNESS_STATE_DIR="$STATE_DIR/harness" HARNESS_K3S_LIB_ONLY=true \
  NAMESPACE=target-ns RELEASE_NAME=target-release \
  bash -c '
    . "$1"
    begin_port_forward_session
    start_one_port_forward selected UI target-ui 0 8080
    child=$(sed -n "1s/\t.*//p" "$PID_FILE")
    awk -F "\t" -v OFS="\t" "{ \$2 = \$2 + 1; print }" "$PID_FILE" >"${PID_FILE}.tmp"
    mv "${PID_FILE}.tmp" "$PID_FILE"
    stop_port_forwards
    kill -0 "$child"
    kill "$child"
    wait "$child" 2>/dev/null || true
  ' bash "$ROOT_DIR/scripts/harness-k3s.sh"

# A timed-out child is terminated and reaped.
rm -f "$STATE_DIR/last-child-pid"
set +e
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  HARNESS_STATE_DIR="$STATE_DIR/harness" HARNESS_K3S_LIB_ONLY=true \
  PORT_FORWARD_ATTEMPTS=1 MOCK_PORT_FORWARD_MODE=never \
  NAMESPACE=target-ns RELEASE_NAME=target-release \
  bash -c '. "$1"; begin_port_forward_session; start_one_port_forward selected UI target-ui 0 8080' \
  bash "$ROOT_DIR/scripts/harness-k3s.sh"
status=$?
set -e
[ "$status" -ne 0 ] || fail "timed-out port-forward startup must fail"
timed_out_pid=$(sed -n '1p' "$STATE_DIR/last-child-pid")
timed_out_start=$(sed -n '1p' "$STATE_DIR/last-child-start-token")
if kill -0 "$timed_out_pid" 2>/dev/null &&
   [ "$(awk '{print $22}' "/proc/${timed_out_pid}/stat" 2>/dev/null || true)" = "$timed_out_start" ]; then
  fail "timed-out port-forward child must be terminated"
fi

# A later startup failure must clean an earlier successful child.
rm -f "$STATE_DIR/last-child-pid" "$STATE_DIR/first-child-pid"
set +e
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  HARNESS_STATE_DIR="$STATE_DIR/harness" HARNESS_K3S_LIB_ONLY=true \
  PORT_FORWARD_ATTEMPTS=1 NAMESPACE=target-ns RELEASE_NAME=target-release \
  bash -c '
    . "$1"
    begin_port_forward_session
    MOCK_PORT_FORWARD_MODE=ready start_one_port_forward first FIRST target-ui 0 8080
    sed -n "1s/\t.*//p" "$PID_FILE" >"$STATE_DIR/first-child-pid"
    sed -n "1s/^[^\t]*\t\([^\t]*\).*/\1/p" "$PID_FILE" >"$STATE_DIR/first-child-start-token"
    MOCK_PORT_FORWARD_MODE=never start_one_port_forward second SECOND target-api 0 8000
  ' bash "$ROOT_DIR/scripts/harness-k3s.sh"
status=$?
set -e
[ "$status" -ne 0 ] || fail "partial port-forward startup must fail"
first_child_pid=$(sed -n '1p' "$STATE_DIR/first-child-pid")
first_child_start=$(sed -n '1p' "$STATE_DIR/first-child-start-token")
if kill -0 "$first_child_pid" 2>/dev/null &&
   [ "$(awk '{print $22}' "/proc/${first_child_pid}/stat" 2>/dev/null || true)" = "$first_child_start" ]; then
  fail "partial startup failure must clean earlier port-forward children"
fi

# Deployment-script callers must keep child ownership in the parent shell.
rm -f "$STATE_DIR/last-child-pid"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  HARNESS_STATE_DIR="$STATE_DIR/harness" TEST_K3S_DEPLOYMENT_LIB_ONLY=true \
  NAMESPACE=target-ns RELEASE_NAME=target-release \
  bash -c '
    script=$1
    shift
    . "$script"
    trap - ERR EXIT INT TERM
    start_port_forward selected target-api 0 8000
    [ "$selected" = 43210 ]
    child=$(sed -n "1p" "$STATE_DIR/last-child-pid")
    case " $PORT_FORWARD_PIDS " in *" $child "*) ;; *) exit 9 ;; esac
    cleanup_local
  ' bash "$ROOT_DIR/scripts/test-k3s-deployment.sh"

echo "k3s harness process cleanup tests passed"
