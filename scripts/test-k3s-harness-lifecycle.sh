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

reset_state() {
  rm -f "$STATE_DIR"/*
  : >"$COMMAND_LOG"
  touch "$STATE_DIR/helm" "$STATE_DIR/marker" "$STATE_DIR/secret" \
    "$STATE_DIR/cluster" "$STATE_DIR/data-pvc" "$STATE_DIR/auth-pvc"
}

reset_legacy_state() {
  reset_state
  rm -f "$STATE_DIR/marker"
}

cat >"${MOCK_BIN}/helm" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'helm' >>"$COMMAND_LOG"
printf ' %q' "$@" >>"$COMMAND_LOG"
printf '\n' >>"$COMMAND_LOG"

case "${1:-}" in
  uninstall)
    rm -f "$STATE_DIR/helm"
    ;;
  status)
    [ -f "$STATE_DIR/helm" ] || exit 1
    ;;
  list)
    if [ -f "$STATE_DIR/helm" ]; then
      printf '[{"name":"%s","namespace":"%s"}]\n' "$RELEASE_NAME" "$NAMESPACE"
    else
      printf '[]\n'
    fi
    ;;
  get)
    printf '{}\n'
    ;;
esac
EOF

cat >"${MOCK_BIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'kubectl' >>"$COMMAND_LOG"
printf ' %q' "$@" >>"$COMMAND_LOG"
printf '\n' >>"$COMMAND_LOG"

joined=" $* "
output=""
if [[ "$joined" == *" -o jsonpath="* ]]; then
  output=jsonpath
elif [[ "$joined" == *" -o json "* ]] || [[ "$joined" == *" -o json" ]]; then
  output=json
elif [[ "$joined" == *" -o name "* ]] || [[ "$joined" == *" -o name" ]]; then
  output=name
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" helmrelease"* || "$joined" == *" helmreleases"* ]]; then
  if [ "${MOCK_FLUX_MANAGED:-false}" = true ]; then
    printf '{"items":[{"metadata":{"name":"different-object-name","namespace":"flux-system"},"spec":{"targetNamespace":"%s","releaseName":"%s"}}]}\n' "$NAMESPACE" "$RELEASE_NAME"
  else
    printf '{"items":[]}\n'
  fi
  exit 0
fi

if [ "${1:-}" = get ]; then
  case "${MOCK_INVENTORY_READ_FAILURE:-}" in
    marker) [[ "$joined" != *" configmap "*"${RELEASE_NAME}-harness-lifecycle"* ]] || exit 1 ;;
    secret) [[ "$joined" != *" secret "*"${APP_SECRET_NAME}"* ]] || exit 1 ;;
    cluster) [[ "$joined" != *"clusters.postgresql.cnpg.io"* ]] || exit 1 ;;
    pvc) [[ "$joined" != *" pvc "* && "$joined" != *" persistentvolumeclaims "* ]] || exit 1 ;;
    keycloak) [[ "$joined" != *"keycloaks.k8s.keycloak.org"* ]] || exit 1 ;;
    descendants) [[ "$joined" != *"deployment,statefulset,daemonset,replicaset,controllerrevision,pod,service,endpoints,endpointslice,job,configmap,secret,serviceaccount,role,rolebinding,networkpolicy,scaledjob,scaledobject,keycloakrealmimports.k8s.keycloak.org,pvc"* ]] || exit 1 ;;
    absence) [ -f "$STATE_DIR/helm" ] || { [[ "$joined" != *" app.kubernetes.io/instance="* ]] || exit 1; } ;;
  esac
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" configmap "*"${RELEASE_NAME}-harness-lifecycle"* ]]; then
  [ -f "$STATE_DIR/marker" ] || exit 0
  if [ "$output" = json ]; then
    printf '{"metadata":{"name":"%s-harness-lifecycle","namespace":"%s","uid":"marker-uid","resourceVersion":"marker-rv","labels":{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":"%s"},"annotations":{"tertius.io/lease-id":"%s","tertius.io/release-name":"%s","tertius.io/app-secret-name":"%s","tertius.io/expires-at":"%s","tertius.io/cleanup-policy":"%s"}}}\n' "$RELEASE_NAME" "$NAMESPACE" "$RELEASE_NAME" "${MOCK_MARKER_LEASE_ID:-11111111-1111-4111-8111-111111111111}" "$RELEASE_NAME" "$APP_SECRET_NAME" "${MOCK_MARKER_EXPIRES_AT:-2099-01-01T00:00:00Z}" "${MOCK_MARKER_POLICY:-delete}"
  else
    printf '%s-harness-lifecycle\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" secret "*"${APP_SECRET_NAME}"* ]]; then
  [ -f "$STATE_DIR/secret" ] || exit 0
  if [ "$output" = json ]; then
    printf '{"metadata":{"name":"%s","namespace":"%s","annotations":{"tertius.io/lease-id":"%s"}}}\n' "$APP_SECRET_NAME" "$NAMESPACE" "${MOCK_SECRET_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
  else
    printf '%s\n' "$APP_SECRET_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *"clusters.postgresql.cnpg.io"* ]]; then
  if [ "$output" = json ]; then
    if [ -f "$STATE_DIR/cluster" ]; then
      printf '{"items":[{"apiVersion":"postgresql.cnpg.io/v1","kind":"Cluster","metadata":{"name":"%s-postgres","uid":"cluster-uid","labels":{"app.kubernetes.io/instance":"%s"},"annotations":{"tertius.io/lease-id":"%s"}}}]}\n' "$RELEASE_NAME" "$RELEASE_NAME" "${MOCK_DATA_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
    else
      printf '{"items":[]}\n'
    fi
  elif [ -f "$STATE_DIR/cluster" ]; then
    printf 'cluster.postgresql.cnpg.io/%s-postgres\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *"keycloaks.k8s.keycloak.org"* ]]; then
  if [ "$output" = json ]; then
    if [ -f "$STATE_DIR/keycloak-root" ]; then
      printf '{"items":[{"apiVersion":"k8s.keycloak.org/v2alpha1","kind":"Keycloak","metadata":{"name":"%s-keycloak","uid":"keycloak-uid","labels":{"app.kubernetes.io/instance":"%s"}}}]}\n' "$RELEASE_NAME" "$RELEASE_NAME"
    else
      printf '{"items":[]}\n'
    fi
  elif [ -f "$STATE_DIR/keycloak-root" ]; then
    printf 'keycloak.k8s.keycloak.org/%s-keycloak\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" pvc "* || "$joined" == *" persistentvolumeclaims "* ]]; then
  if [ "$output" = json ]; then
    printf '{"items":['
    separator=""
    if [ -f "$STATE_DIR/data-pvc" ]; then
      printf '%s{"apiVersion":"v1","kind":"PersistentVolumeClaim","metadata":{"name":"%s-data","uid":"data-uid","labels":{"app.kubernetes.io/instance":"%s"},"annotations":{"tertius.io/lease-id":"%s"}}}' "$separator" "$RELEASE_NAME" "$RELEASE_NAME" "${MOCK_DATA_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
      separator=,
    fi
    if [ -f "$STATE_DIR/auth-pvc" ]; then
      printf '%s{"apiVersion":"v1","kind":"PersistentVolumeClaim","metadata":{"name":"%s-pi-agent-auth","uid":"auth-uid","labels":{"app.kubernetes.io/instance":"%s","app.kubernetes.io/component":"pi-agent-auth"},"annotations":{"tertius.io/lease-id":"%s"}}}' "$separator" "$RELEASE_NAME" "$RELEASE_NAME" "${MOCK_DATA_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
    fi
    printf ']}\n'
  else
    [ ! -f "$STATE_DIR/data-pvc" ] || printf 'persistentvolumeclaim/%s-data\n' "$RELEASE_NAME"
    [ ! -f "$STATE_DIR/auth-pvc" ] || printf 'persistentvolumeclaim/%s-pi-agent-auth\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" deployment,statefulset,daemonset,replicaset,controllerrevision,pod,service,endpoints,endpointslice,job,configmap,secret,serviceaccount,role,rolebinding,networkpolicy,scaledjob,scaledobject,keycloakrealmimports.k8s.keycloak.org,pvc "* ]] && [ "$output" = json ]; then
  printf '{"items":['
  separator=""
  if [ -f "$STATE_DIR/operator-child" ]; then
    printf '%s{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"%s-operator-child","uid":"operator-child-uid","ownerReferences":[{"uid":"cluster-uid"}]}}' "$separator" "$RELEASE_NAME"
    separator=,
  fi
  if [ -f "$STATE_DIR/operator-grandchild" ]; then
    printf '%s{"apiVersion":"apps/v1","kind":"ReplicaSet","metadata":{"name":"%s-operator-grandchild","uid":"operator-grandchild-uid","ownerReferences":[{"uid":"operator-child-uid"}]}}' "$separator" "$RELEASE_NAME"
    separator=,
    printf '%s{"apiVersion":"v1","kind":"Pod","metadata":{"name":"%s-operator-pod","uid":"operator-pod-uid","ownerReferences":[{"uid":"operator-grandchild-uid"}]}}' "$separator" "$RELEASE_NAME"
  fi
  if [ -f "$STATE_DIR/realm-import" ]; then
    printf '%s{"apiVersion":"k8s.keycloak.org/v2alpha1","kind":"KeycloakRealmImport","metadata":{"name":"%s-realm","uid":"realm-import-uid","ownerReferences":[{"uid":"keycloak-uid"}]}}' "$separator" "$RELEASE_NAME"
    separator=,
  fi
  printf ']}\n'
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" deployment "*"${RELEASE_NAME}-operator-child"* ]]; then
  [ -f "$STATE_DIR/operator-child" ] || exit 0
  if [ "$output" = json ]; then
    printf '{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"%s-operator-child","uid":"operator-child-uid"}}\n' "$RELEASE_NAME"
  else
    printf 'deployment/%s-operator-child\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" replicaset "*"${RELEASE_NAME}-operator-grandchild"* ]]; then
  [ -f "$STATE_DIR/operator-grandchild" ] || exit 0
  if [ "$output" = json ]; then
    printf '{"apiVersion":"apps/v1","kind":"ReplicaSet","metadata":{"name":"%s-operator-grandchild","uid":"operator-grandchild-uid"}}\n' "$RELEASE_NAME"
  else
    printf 'replicaset/%s-operator-grandchild\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" pod "*"${RELEASE_NAME}-operator-pod"* ]]; then
  [ -f "$STATE_DIR/operator-grandchild" ] || exit 0
  [ "$output" != json ] || printf '{"apiVersion":"v1","kind":"Pod","metadata":{"name":"%s-operator-pod","uid":"operator-pod-uid"}}\n' "$RELEASE_NAME"
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" keycloakrealmimport "*"${RELEASE_NAME}-realm"* ]]; then
  [ -f "$STATE_DIR/realm-import" ] || exit 0
  [ "$output" != json ] || printf '{"apiVersion":"k8s.keycloak.org/v2alpha1","kind":"KeycloakRealmImport","metadata":{"name":"%s-realm","uid":"realm-import-uid"}}\n' "$RELEASE_NAME"
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" -l app.kubernetes.io/instance="* ]]; then
  resource_arg=${2:-}
  case ",${resource_arg}," in
    *,service,*|*,services,*|*,svc,*)
      [ "${MOCK_REMAINING_RESOURCE:-false}" != true ] || printf 'service/%s-ui\n' "$RELEASE_NAME"
      ;;
  esac
  exit 0
fi

if [ "${1:-}" = delete ]; then
  case "$joined" in
    *" secret "*"${APP_SECRET_NAME}"*) rm -f "$STATE_DIR/secret" ;;
    *" configmap "*"${RELEASE_NAME}-harness-lifecycle"*) rm -f "$STATE_DIR/marker" ;;
    *"clusters.postgresql.cnpg.io"*|*"cluster.postgresql.cnpg.io/"*) rm -f "$STATE_DIR/cluster" ;;
  esac
  [[ "$joined" != *"persistentvolumeclaim/${RELEASE_NAME}-data"* ]] || rm -f "$STATE_DIR/data-pvc"
  [[ "$joined" != *"persistentvolumeclaim/${RELEASE_NAME}-pi-agent-auth"* ]] || rm -f "$STATE_DIR/auth-pvc"
  exit 0
fi

if [ "${1:-}" = apply ]; then
  input=$(cat)
  printf '%s\n' "$input" >>"${COMMAND_LOG}.stdin"
  touch "$STATE_DIR/marker"
  exit 0
fi

exit 0
EOF

chmod +x "${MOCK_BIN}/helm" "${MOCK_BIN}/kubectl"

run_deploy_cleanup() {
  PATH="${MOCK_BIN}:$PATH" \
  COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  NAMESPACE="${NAMESPACE:-test-ns}" RELEASE_NAME="${RELEASE_NAME:-test-release}" \
  APP_SECRET_NAME="${APP_SECRET_NAME:-test-release-app}" \
  MOCK_FLUX_MANAGED="${MOCK_FLUX_MANAGED:-false}" \
  MOCK_MARKER_LEASE_ID="${MOCK_MARKER_LEASE_ID:-11111111-1111-4111-8111-111111111111}" \
  MOCK_SECRET_LEASE_ID="${MOCK_SECRET_LEASE_ID:-11111111-1111-4111-8111-111111111111}" \
  MOCK_DATA_LEASE_ID="${MOCK_DATA_LEASE_ID:-11111111-1111-4111-8111-111111111111}" \
  MOCK_REMAINING_RESOURCE="${MOCK_REMAINING_RESOURCE:-false}" \
  HARNESS_CLEANUP_POLL_ATTEMPTS=1 \
  "$ROOT_DIR/scripts/test-k3s-deployment.sh" --cleanup "$@"
}

reset_state
run_deploy_cleanup
assert_log 'helm uninstall test-release -n test-ns --ignore-not-found' \
  "default cleanup must uninstall the exact Helm release"
assert_log 'kubectl delete secret test-release-app -n test-ns --ignore-not-found=true' \
  "default cleanup must delete the exact external app Secret"
assert_log 'kubectl delete configmap test-release-harness-lifecycle -n test-ns --ignore-not-found=true' \
  "default cleanup must delete the lifecycle marker"
assert_log 'kubectl delete .*cluster\.postgresql\.cnpg\.io/test-release-postgres|kubectl delete .*clusters\.postgresql\.cnpg\.io' \
  "default cleanup must delete release data"
assert_log 'kubectl delete .*persistentvolumeclaim/test-release-data' \
  "default cleanup must delete the data PVC"
assert_log 'kubectl delete .*persistentvolumeclaim/test-release-pi-agent-auth' \
  "default cleanup must delete the auth PVC"
assert_log 'helm (status|list)' \
  "default cleanup must verify Helm release absence"
assert_log 'kubectl get .*app\.kubernetes\.io/instance=test-release' \
  "default cleanup must verify scoped Kubernetes resource absence"
for required_kind in \
  deployment statefulset daemonset replicaset controllerrevision pod service endpoints endpointslice job configmap secret \
  serviceaccount role rolebinding networkpolicy scaledjob scaledobject \
  clusters.postgresql.cnpg.io keycloaks.k8s.keycloak.org keycloakrealmimports.k8s.keycloak.org pvc; do
  assert_log "kubectl get ([^[:space:]]*,)*${required_kind}(,|[[:space:]])" \
    "absence verification must inspect ${required_kind} resources"
done

reset_state
run_deploy_cleanup --retain-data
assert_not_log 'kubectl delete .*cluster\.postgresql\.cnpg\.io|kubectl delete .*persistentvolumeclaim/' \
  "--retain-data must not delete clusters or PVCs"
assert_log 'kubectl delete secret test-release-app -n test-ns --ignore-not-found=true' \
  "--retain-data must still delete the app Secret"
assert_not_log 'kubectl delete .*configmap test-release-harness-lifecycle' \
  "--retain-data must preserve a lifecycle tombstone"
assert_log 'kubectl (annotate|patch|apply).*test-release-harness-lifecycle.*cleanup-policy.*retain' \
  "--retain-data must mark the lifecycle record as a retention tombstone"
assert_log 'kubectl (annotate|patch|apply).*test-release-harness-lifecycle.*retained-objects.*(data-uid|cluster-uid)' \
  "--retain-data tombstone must record retained names and UIDs"
for retained_identity in \
  test-release-postgres cluster-uid \
  test-release-data data-uid \
  test-release-pi-agent-auth auth-uid; do
  assert_log "kubectl (annotate|patch|apply).*test-release-harness-lifecycle.*retained-objects.*${retained_identity}" \
    "--retain-data tombstone must record ${retained_identity}"
done

reset_state
run_deploy_cleanup --retain-auth
assert_log 'kubectl delete .*persistentvolumeclaim/test-release-data' \
  "--retain-auth must delete non-auth data PVCs"
assert_not_log 'kubectl delete .*persistentvolumeclaim/test-release-pi-agent-auth' \
  "--retain-auth must preserve only the auth PVC"
assert_log 'kubectl delete .*cluster\.postgresql\.cnpg\.io/test-release-postgres|kubectl delete .*clusters\.postgresql\.cnpg\.io' \
  "--retain-auth must delete CNPG data"
assert_log 'kubectl delete secret test-release-app -n test-ns --ignore-not-found=true' \
  "--retain-auth must delete the app Secret"
assert_not_log 'kubectl delete configmap test-release-harness-lifecycle' \
  "--retain-auth must preserve a lifecycle tombstone"
assert_log 'kubectl (annotate|patch|apply).*test-release-harness-lifecycle.*cleanup-policy.*retain' \
  "--retain-auth must mark the lifecycle record as a retention tombstone"
assert_log 'kubectl (annotate|patch|apply).*test-release-harness-lifecycle.*retained-objects.*auth-uid' \
  "--retain-auth tombstone must record the retained auth PVC UID"

reset_legacy_state
: >"$COMMAND_LOG"
if RELEASE_NAME=tertius APP_SECRET_NAME=tertius-app run_deploy_cleanup; then
  fail "production release cleanup must be refused"
fi
assert_not_log 'helm uninstall' "production refusal must not mutate Helm"
assert_not_log 'kubectl (delete|annotate|patch|apply)' \
  "production refusal must not mutate Kubernetes"

reset_legacy_state
: >"$COMMAND_LOG"
if MOCK_FLUX_MANAGED=true run_deploy_cleanup; then
  fail "Flux-managed release cleanup must be refused"
fi
assert_not_log 'helm uninstall' "Flux refusal must not mutate Helm"
assert_not_log 'kubectl (delete|annotate|patch|apply)' \
  "Flux refusal must not mutate Kubernetes"

reset_state
: >"$COMMAND_LOG"
if MOCK_SECRET_LEASE_ID=99999999-9999-4999-8999-999999999999 run_deploy_cleanup; then
  fail "lease identity mismatch must be refused"
fi
assert_not_log 'helm uninstall' "lease mismatch must not mutate Helm"
assert_not_log 'kubectl (delete|annotate|patch|apply)' \
  "lease mismatch must not mutate Kubernetes"

reset_state
: >"$COMMAND_LOG"
if MOCK_DATA_LEASE_ID=99999999-9999-4999-8999-999999999999 run_deploy_cleanup; then
  fail "data lease identity mismatch must be refused"
fi
assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
  "data lease mismatch must not mutate the target"

for failed_inventory in marker secret cluster pvc keycloak descendants; do
  reset_state
  : >"$COMMAND_LOG"
  if MOCK_INVENTORY_READ_FAILURE="$failed_inventory" run_deploy_cleanup; then
    fail "cleanup must fail closed when ${failed_inventory} ownership inventory cannot be read"
  fi
  assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
    "${failed_inventory} inventory failure must occur before any mutation"
done


reset_state
: >"$COMMAND_LOG"
if MOCK_INVENTORY_READ_FAILURE=absence run_deploy_cleanup; then
  fail "cleanup must fail closed when an absence-gate API read fails"
fi
assert_log 'helm uninstall test-release' \
  "absence-gate read failure must be detected after teardown starts"
assert_not_log 'kubectl delete configmap test-release-harness-lifecycle' \
  "absence-gate API failure must preserve the lifecycle marker for retry"

reset_state
: >"$COMMAND_LOG"
if EXPECTED_HARNESS_LEASE_ID=99999999-9999-4999-8999-999999999999 run_deploy_cleanup; then
  fail "cleanup must refuse when the marker lease changed after janitor inventory"
fi
assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
  "janitor expected-lease mismatch must occur before any mutation"

for invalid_marker_case in uuid expiry policy; do
  reset_state
  : >"$COMMAND_LOG"
  case "$invalid_marker_case" in
    uuid) MOCK_MARKER_LEASE_ID=not-a-uuid run_deploy_cleanup 2>/dev/null && marker_accepted=true || marker_accepted=false ;;
    expiry) MOCK_MARKER_EXPIRES_AT=not-a-time run_deploy_cleanup 2>/dev/null && marker_accepted=true || marker_accepted=false ;;
    policy) MOCK_MARKER_POLICY=unknown run_deploy_cleanup 2>/dev/null && marker_accepted=true || marker_accepted=false ;;
  esac
  if [ "$marker_accepted" = true ]; then
    fail "cleanup must refuse a marker with invalid ${invalid_marker_case}"
  fi
  assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
    "invalid marker ${invalid_marker_case} must be refused before mutation"
done

reset_state
: >"$COMMAND_LOG"
MOCK_MARKER_EXPIRES_AT=2020-01-01T00:00:00Z \
EXPECTED_HARNESS_MARKER_UID=marker-uid \
EXPECTED_HARNESS_MARKER_RESOURCE_VERSION=marker-rv \
EXPECTED_HARNESS_LEASE_ID=11111111-1111-4111-8111-111111111111 \
EXPECTED_HARNESS_EXPIRES_AT=2020-01-01T00:00:00Z \
EXPECTED_HARNESS_NOW_EPOCH=1893456000 \
  run_deploy_cleanup

reset_state
: >"$COMMAND_LOG"
APP_SECRET_NAME=custom-app-secret run_deploy_cleanup
assert_log 'kubectl delete secret custom-app-secret -n test-ns --ignore-not-found=true' \
  "cleanup must delete the exact app Secret recorded by the lifecycle marker"

reset_legacy_state
if printf '%s\n' 'wrong/target' | PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  "$ROOT_DIR/scripts/harness-k3s.sh" adopt test-ns/test-release; then
  fail "legacy adoption must reject incorrect exact confirmation"
fi
assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
  "wrong adoption confirmation must not mutate the cluster"

reset_legacy_state
: >"$COMMAND_LOG"
if printf '%s\n' 'test-ns/tertius' | PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  NAMESPACE=test-ns RELEASE_NAME=tertius APP_SECRET_NAME=tertius-app \
  "$ROOT_DIR/scripts/harness-k3s.sh" adopt test-ns/tertius; then
  fail "legacy adoption must refuse the production release"
fi
assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
  "production adoption refusal must not mutate the cluster"

reset_legacy_state
: >"$COMMAND_LOG"
if printf '%s\n' 'test-ns/test-release' | PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app MOCK_FLUX_MANAGED=true \
  "$ROOT_DIR/scripts/harness-k3s.sh" adopt test-ns/test-release; then
  fail "legacy adoption must refuse a Flux-managed release"
fi
assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
  "Flux adoption refusal must not mutate the cluster"

reset_legacy_state
rm -f "$STATE_DIR/helm"
: >"$COMMAND_LOG"
if printf '%s\n' 'test-ns/test-release' | PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  "$ROOT_DIR/scripts/harness-k3s.sh" adopt test-ns/test-release; then
  fail "legacy adoption must refuse when the exact Helm release is absent"
fi
assert_log 'helm status test-release -n test-ns' \
  "legacy adoption must prove the exact Helm release exists"
assert_not_log 'kubectl (delete|annotate|patch|apply)' \
  "absent-release adoption refusal must not mutate Kubernetes"

reset_state
: >"$COMMAND_LOG"
if printf '%s\n' 'test-ns/test-release' | PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  "$ROOT_DIR/scripts/harness-k3s.sh" adopt test-ns/test-release; then
  fail "legacy adoption must refuse when a lifecycle marker already exists"
fi
assert_log 'kubectl get configmap test-release-harness-lifecycle -n test-ns' \
  "legacy adoption must check for an existing lifecycle marker"
assert_not_log 'kubectl (delete|annotate|patch|apply)' \
  "existing-marker adoption refusal must not mutate Kubernetes"

reset_legacy_state
: >"${COMMAND_LOG}.stdin"
printf '%s\n' 'test-ns/test-release' | PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  "$ROOT_DIR/scripts/harness-k3s.sh" adopt test-ns/test-release
assert_log 'kubectl (annotate|patch).*test-release-app.*tertius\.io/lease-id=' \
  "legacy adoption must apply lease ownership to the external Secret"
assert_log 'kubectl (apply|create).*harness-lifecycle|kubectl apply' \
  "legacy adoption must create the lifecycle marker"
grep -Eq 'tertius\.io/lease-id:[[:space:]]*[^[:space:]]+' "${COMMAND_LOG}.stdin" || \
  fail "legacy adoption marker must contain its lease UUID"
assert_log 'kubectl (annotate|patch).*test-release-postgres.*tertius\.io/lease-id=' \
  "legacy adoption must lease the existing CNPG Cluster"
assert_log 'kubectl (annotate|patch).*test-release-data.*tertius\.io/lease-id=' \
  "legacy adoption must lease the existing data PVC"
assert_log 'kubectl (annotate|patch).*test-release-pi-agent-auth.*tertius\.io/lease-id=' \
  "legacy adoption must lease the existing auth PVC"
lease_values=$(
  {
    grep -Eo 'tertius\.io/lease-id=[^[:space:]]+' "$COMMAND_LOG" | sed 's/.*=//; s/\\//g'
    grep -E 'tertius\.io/lease-id:' "${COMMAND_LOG}.stdin" | sed "s/.*tertius\\.io\\/lease-id:[[:space:]]*//; s/[\"']//g"
  } | sed '/^$/d' | sort -u
)
[ "$(printf '%s\n' "$lease_values" | grep -c .)" -eq 1 ] || \
  fail "adoption must assign one identical lease UUID to marker, Secret, CNPG Cluster, and PVCs"

reset_state
run_deploy_cleanup
run_deploy_cleanup

reset_state
: >"$COMMAND_LOG"
if MOCK_REMAINING_RESOURCE=true run_deploy_cleanup; then
  fail "cleanup must fail when an exact release resource remains"
fi
assert_log 'helm uninstall test-release -n test-ns --ignore-not-found' \
  "remaining-resource failure must occur after Helm uninstall"
assert_log 'kubectl get .*app\.kubernetes\.io/instance=test-release' \
  "remaining-resource failure must be produced by the absence gate"
assert_not_log 'kubectl delete configmap test-release-harness-lifecycle' \
  "cleanup failure must preserve the lifecycle marker for a safe retry"

reset_state
touch "$STATE_DIR/operator-child" "$STATE_DIR/operator-grandchild"
: >"$COMMAND_LOG"
if run_deploy_cleanup; then
  fail "cleanup must fail when an unlabeled operator descendant with the captured UID remains"
fi
assert_log 'kubectl get deployment test-release-operator-child -n test-ns .* -o json' \
  "cleanup must verify the captured operator child by exact name and UID"
assert_log 'kubectl get replicaset test-release-operator-grandchild -n test-ns .* -o json' \
  "cleanup must recursively verify captured operator grandchildren"
assert_log 'kubectl get pod test-release-operator-pod -n test-ns .* -o json' \
  "cleanup must recursively verify captured operator pods"

reset_state
touch "$STATE_DIR/keycloak-root" "$STATE_DIR/realm-import"
: >"$COMMAND_LOG"
if run_deploy_cleanup; then
  fail "cleanup must fail when a captured KeycloakRealmImport remains"
fi
assert_log 'kubectl get keycloakrealmimport test-release-realm -n test-ns .* -o json' \
  "cleanup must verify captured KeycloakRealmImport UIDs"

reset_state
touch "$STATE_DIR/operator-child" "$STATE_DIR/operator-grandchild"
: >"$COMMAND_LOG"
run_deploy_cleanup --retain-data
assert_log 'retained-objects=.*test-release-operator-child.*operator-child-uid' \
  "retained data tombstone must record the retained operator child"
assert_log 'retained-objects=.*test-release-operator-grandchild.*operator-grandchild-uid' \
  "retained data tombstone must record recursive retained descendants"
assert_log 'retained-objects=.*test-release-operator-pod.*operator-pod-uid' \
  "retained data tombstone must record recursive retained pods"

echo "k3s harness lifecycle contract tests passed"
