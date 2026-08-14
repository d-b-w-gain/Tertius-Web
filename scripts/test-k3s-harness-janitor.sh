#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
MOCK_BIN="${TMP_DIR}/bin"
COMMAND_LOG="${TMP_DIR}/commands.log"
mkdir -p "$MOCK_BIN"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_log() { grep -Eq -- "$1" "$COMMAND_LOG" || fail "$2"; }
assert_not_log() { ! grep -Eq -- "$1" "$COMMAND_LOG" || fail "$2"; }

cat >"${MOCK_BIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'kubectl %s\n' "$*" >>"$COMMAND_LOG"
if [[ " $* " == *" get configmaps "* ]]; then
  if [ "${MOCK_MALFORMED_MARKERS:-false}" = true ]; then
    printf '{broken\n'
    exit 0
  fi
  cat <<JSON
{"items":[
 {"metadata":{"name":"future-harness-lifecycle","namespace":"dev","uid":"uid-future","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"future"},"annotations":{"tertius.io/lease-id":"11111111-1111-4111-8111-111111111111","tertius.io/release-name":"future","tertius.io/expires-at":"2030-01-01T00:00:01Z","tertius.io/cleanup-policy":"delete"}}},
 {"metadata":{"name":"expired-harness-lifecycle","namespace":"dev","uid":"uid-expired","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"expired"},"annotations":{"tertius.io/lease-id":"22222222-2222-4222-8222-222222222222","tertius.io/release-name":"expired","tertius.io/expires-at":"2030-01-01T00:00:00Z","tertius.io/cleanup-policy":"delete"}}},
 {"metadata":{"name":"retained-harness-lifecycle","namespace":"dev","uid":"uid-retained","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"retained"},"annotations":{"tertius.io/lease-id":"33333333-3333-4333-8333-333333333333","tertius.io/release-name":"retained","tertius.io/expires-at":"2020-01-01T00:00:00Z","tertius.io/cleanup-policy":"retain"}}},
 {"metadata":{"name":"wrong-name","namespace":"dev","uid":"uid-malformed","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"malformed"},"annotations":{"tertius.io/lease-id":"not-a-uuid","tertius.io/release-name":"malformed","tertius.io/expires-at":"broken","tertius.io/cleanup-policy":"delete"}}},
 {"metadata":{"name":"tertius-harness-lifecycle","namespace":"tertius","uid":"uid-tertius","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"tertius"},"annotations":{"tertius.io/lease-id":"44444444-4444-4444-8444-444444444444","tertius.io/release-name":"tertius","tertius.io/expires-at":"2020-01-01T00:00:00Z","tertius.io/cleanup-policy":"delete"}}},
 {"metadata":{"name":"flux-harness-lifecycle","namespace":"dev","uid":"uid-flux","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"flux"},"annotations":{"tertius.io/lease-id":"55555555-5555-4555-8555-555555555555","tertius.io/release-name":"flux","tertius.io/expires-at":"2020-01-01T00:00:00Z","tertius.io/cleanup-policy":"delete"}}},
 {"metadata":{"name":"dev-cross-harness-lifecycle","namespace":"dev","uid":"uid-flux-cross","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"dev-cross"},"annotations":{"tertius.io/lease-id":"88888888-8888-4888-8888-888888888888","tertius.io/release-name":"dev-cross","tertius.io/expires-at":"2020-01-01T00:00:00Z","tertius.io/cleanup-policy":"delete"}}},
 {"metadata":{"name":"retry-cleaning-harness-lifecycle","namespace":"dev","uid":"uid-cleaning","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"retry-cleaning"},"annotations":{"tertius.io/lease-id":"99999999-9999-4999-8999-999999999999","tertius.io/release-name":"retry-cleaning","tertius.io/expires-at":"2020-01-01T00:00:00Z","tertius.io/cleanup-policy":"cleaning"}}},
 {"metadata":{"name":"cleanup-fails-harness-lifecycle","namespace":"dev","uid":"uid-cleanup-fails","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"cleanup-fails"},"annotations":{"tertius.io/lease-id":"66666666-6666-4666-8666-666666666666","tertius.io/release-name":"cleanup-fails","tertius.io/expires-at":"2020-01-01T00:00:00Z","tertius.io/cleanup-policy":"delete"}}},
 {"metadata":{"name":"later-harness-lifecycle","namespace":"dev","uid":"uid-later","resourceVersion":"1","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"later"},"annotations":{"tertius.io/lease-id":"77777777-7777-4777-8777-777777777777","tertius.io/release-name":"later","tertius.io/expires-at":"2020-01-01T00:00:00Z","tertius.io/cleanup-policy":"delete"}}}
]}
JSON
elif [[ " $* " == *" get helmreleases.helm.toolkit.fluxcd.io "* ]]; then
  if [ "${MOCK_MALFORMED_FLUX:-false}" = true ]; then
    printf '{broken\n'
    exit 0
  fi
  printf '{"items":[{"metadata":{"name":"different","namespace":"flux-system"},"spec":{"targetNamespace":"dev","releaseName":"flux"}},{"metadata":{"name":"cross","namespace":"flux-system"},"spec":{"targetNamespace":"dev"}}]}\n'
fi
EOF

cat >"${MOCK_BIN}/harness-cleanup" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'cleanup %s namespace=%s release=%s uid=%s rv=%s lease=%s expires=%s now=%s\n' \
  "$*" "${NAMESPACE:-}" "${RELEASE_NAME:-}" "${EXPECTED_HARNESS_MARKER_UID:-}" \
  "${EXPECTED_HARNESS_MARKER_RESOURCE_VERSION:-}" "${EXPECTED_HARNESS_LEASE_ID:-}" \
  "${EXPECTED_HARNESS_EXPIRES_AT:-}" "${EXPECTED_HARNESS_NOW_EPOCH:-}" >>"$COMMAND_LOG"
[ "${RELEASE_NAME:-}" != cleanup-fails ]
EOF

cat >"${MOCK_BIN}/systemctl" <<'EOF'
#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >>"$COMMAND_LOG"
if [ "${MOCK_SYSTEMCTL_DISABLE_FAILURE:-false}" = true ] && [ "${2:-}" = disable ]; then
  exit 1
fi
EOF
chmod +x "${MOCK_BIN}/"*
: >"$COMMAND_LOG"

if PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" \
  NOW_EPOCH=1893456000 HARNESS_CLEANUP_COMMAND="${MOCK_BIN}/harness-cleanup" \
  "$ROOT_DIR/scripts/cleanup-expired-k3s-harness.sh"; then
  fail "mixed malformed and cleanup-failure inventory must return nonzero"
fi
assert_log 'cleanup down namespace=dev release=expired uid=uid-expired rv=1 lease=22222222-2222-4222-8222-222222222222 expires=2030-01-01T00:00:00Z now=1893456000' \
  "exactly expired marker must be cleaned only under its observed marker identity"
assert_log 'cleanup down namespace=dev release=cleanup-fails' "cleanup failures must be attempted"
assert_log 'cleanup down namespace=dev release=later' "janitor must continue after a cleanup failure"
assert_log 'cleanup down namespace=dev release=retry-cleaning uid=uid-cleaning' \
  "janitor must retry expired markers already atomically claimed for cleaning"
assert_not_log 'release=future|release=retained|release=tertius|release=flux|release=dev-cross|release=malformed' \
  "future, retained, protected, Flux, and malformed markers must not be cleaned"

for malformed_case in MOCK_MALFORMED_MARKERS MOCK_MALFORMED_FLUX; do
  : >"$COMMAND_LOG"
  if env "$malformed_case=true" PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" \
    NOW_EPOCH=1893456000 HARNESS_CLEANUP_COMMAND="${MOCK_BIN}/harness-cleanup" \
    "$ROOT_DIR/scripts/cleanup-expired-k3s-harness.sh" 2>/dev/null; then
    fail "${malformed_case} must fail closed"
  fi
  assert_not_log '^cleanup ' "${malformed_case} must not invoke cleanup"
done

: >"$COMMAND_LOG"
TEST_HOME="${TMP_DIR}/home"
SPECIAL_KUBECONFIG="${TMP_DIR}/kube & 100% config"
mkdir -p "$TEST_HOME"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" HOME="$TEST_HOME" \
  REPOSITORY_ROOT="$ROOT_DIR" KUBECONFIG="$SPECIAL_KUBECONFIG" \
  "$ROOT_DIR/scripts/install-k3s-harness-cleanup-timer.sh" install
SERVICE="${TEST_HOME}/.config/systemd/user/tertius-k3s-harness-cleanup.service"
TIMER="${TEST_HOME}/.config/systemd/user/tertius-k3s-harness-cleanup.timer"
[ -f "$SERVICE" ] || fail "installer must write the user service"
[ -f "$TIMER" ] || fail "installer must write the user timer"
grep -Fx 'OnBootSec=5m' "$TIMER" >/dev/null || fail "timer must delay five minutes after boot"
grep -Fx 'OnUnitActiveSec=15m' "$TIMER" >/dev/null || fail "timer must run every fifteen minutes"
grep -Fx 'Persistent=true' "$TIMER" >/dev/null || fail "timer must catch up after downtime"
grep -F "ExecStart=\"${ROOT_DIR}/scripts/cleanup-expired-k3s-harness.sh\"" "$SERVICE" >/dev/null || \
  fail "service must pin the repository janitor path"
grep -F "Environment=\"KUBECONFIG=${TMP_DIR}/kube & 100%% config\"" "$SERVICE" >/dev/null || \
  fail "service must quote and escape the pinned kubeconfig path"
assert_log 'systemctl --user daemon-reload' "installer must reload user units"
assert_log 'systemctl --user enable --now tertius-k3s-harness-cleanup.timer' "installer must enable timer"

PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" HOME="$TEST_HOME" \
  "$ROOT_DIR/scripts/install-k3s-harness-cleanup-timer.sh" uninstall
[ ! -e "$SERVICE" ] || fail "uninstall must remove service"
[ ! -e "$TIMER" ] || fail "uninstall must remove timer"
assert_log 'systemctl --user disable --now tertius-k3s-harness-cleanup.timer' "uninstall must disable timer"

# Repeated uninstall is idempotent and does not ask systemd to disable a missing unit.
: >"$COMMAND_LOG"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" HOME="$TEST_HOME" \
  "$ROOT_DIR/scripts/install-k3s-harness-cleanup-timer.sh" uninstall
assert_not_log ' disable ' "repeated uninstall must not disable a missing timer"

# A disable failure must not prevent removal of inspectable unit files.
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" HOME="$TEST_HOME" \
  REPOSITORY_ROOT="$ROOT_DIR" KUBECONFIG="$SPECIAL_KUBECONFIG" \
  "$ROOT_DIR/scripts/install-k3s-harness-cleanup-timer.sh" install
if PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" HOME="$TEST_HOME" \
  MOCK_SYSTEMCTL_DISABLE_FAILURE=true \
  "$ROOT_DIR/scripts/install-k3s-harness-cleanup-timer.sh" uninstall; then
  fail "disable failure must be reported"
fi
[ ! -e "$SERVICE" ] && [ ! -e "$TIMER" ] || fail "disable failure must still remove both unit files"

echo "k3s harness janitor contract tests passed"
