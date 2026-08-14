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
  printf 'delete\n' >"$STATE_DIR/marker-policy"
  printf 'marker-rv\n' >"$STATE_DIR/marker-rv"
  printf '[]\n' >"$STATE_DIR/marker-descendants"
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
    [ "${MOCK_MUTATION_FAILURE:-}" != helm-uninstall ] || exit 9
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
  if [ "${MOCK_FLUX_LONG_DEFAULT:-false}" = true ]; then
    printf '{"items":[{"metadata":{"name":"extremely-long-release-source-name","namespace":"flux-system"},"spec":{"targetNamespace":"very-long-target-namespace"}}]}\n'
  elif [ "${MOCK_FLUX_CROSS_DEFAULT:-false}" = true ]; then
    flux_name=${RELEASE_NAME#"${NAMESPACE}-"}
    printf '{"items":[{"metadata":{"name":"%s","namespace":"flux-system"},"spec":{"targetNamespace":"%s"}}]}\n' "$flux_name" "$NAMESPACE"
  elif [ "${MOCK_FLUX_MANAGED:-false}" = true ]; then
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
    descendants) [[ "$joined" != *"deployment,statefulset,daemonset,replicaset,controllerrevision,pod,poddisruptionbudget,service,endpoints,endpointslice,job,configmap,secret,serviceaccount,role,rolebinding,networkpolicy,scaledjob,scaledobject,keycloakrealmimports.k8s.keycloak.org,pvc"* ]] || exit 1 ;;
    absence) [ -f "$STATE_DIR/helm" ] || { [[ "$joined" != *" app.kubernetes.io/instance="* ]] || exit 1; } ;;
  esac
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" configmap "*"${RELEASE_NAME}-harness-lifecycle"* ]]; then
  [ -f "$STATE_DIR/marker" ] || exit 0
  if [ "$output" = json ]; then
    jq -n --arg name "${RELEASE_NAME}-harness-lifecycle" --arg namespace "$NAMESPACE" \
      --arg rv "$(sed -n '1p' "$STATE_DIR/marker-rv")" --arg release "$RELEASE_NAME" \
      --arg lease "${MOCK_MARKER_LEASE_ID:-11111111-1111-4111-8111-111111111111}" \
      --arg secret "${MOCK_MARKER_APP_SECRET_NAME:-$APP_SECRET_NAME}" \
      --arg expires "${MOCK_MARKER_EXPIRES_AT:-2099-01-01T00:00:00Z}" \
      --arg policy "${MOCK_MARKER_POLICY:-$(sed -n '1p' "$STATE_DIR/marker-policy")}" \
      --arg descendants "$(sed -n '1p' "$STATE_DIR/marker-descendants")" \
      '{metadata:{name:$name,namespace:$namespace,uid:"marker-uid",resourceVersion:$rv,
        labels:{"tertius.io/harness-managed":"true","app.kubernetes.io/instance":$release},
        annotations:{"tertius.io/lease-id":$lease,"tertius.io/release-name":$release,
          "tertius.io/app-secret-name":$secret,"tertius.io/expires-at":$expires,
          "tertius.io/cleanup-policy":$policy,"tertius.io/operator-descendants":$descendants}}}'
  else
    printf '%s-harness-lifecycle\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = patch ] && [[ "$joined" == *" configmap "*"${RELEASE_NAME}-harness-lifecycle"* ]]; then
  [ "${MOCK_MUTATION_FAILURE:-}" != marker-claim ] || exit 8
  patch_json=""
  while [ "$#" -gt 0 ]; do
    [ "$1" != -p ] || { patch_json=$2; break; }
    shift
  done
  if [ "${MOCK_MUTATION_FAILURE:-}" = renewal-patch ] &&
     printf '%s' "$patch_json" | jq -e 'any(.[]; .op == "replace" and .path == "/metadata/annotations/tertius.io~1expires-at")' >/dev/null; then
    exit 15
  fi
  if [ "${MOCK_MUTATION_FAILURE:-}" = retention-patch ] &&
     printf '%s' "$patch_json" | jq -e 'any(.[]; .value == "retain")' >/dev/null; then
    exit 14
  fi
  printf '%s' "$patch_json" | jq -e 'any(.[]; .op == "test" and .path == "/metadata/uid") and
    any(.[]; .op == "test" and .path == "/metadata/resourceVersion") and
    any(.[]; .op == "test" and .path == "/metadata/annotations/tertius.io~1lease-id") and
    any(.[]; .op == "test" and .path == "/metadata/annotations/tertius.io~1expires-at")' >/dev/null || exit 10
  printf '%s' "$patch_json" | jq -r '.[] | select(.op == "replace" and .path == "/metadata/annotations/tertius.io~1cleanup-policy") | .value' >"$STATE_DIR/marker-policy"
  descendant_value=$(printf '%s' "$patch_json" | jq -r '.[] | select(.path == "/metadata/annotations/tertius.io~1operator-descendants") | .value' | tail -1)
  [ -z "$descendant_value" ] || printf '%s\n' "$descendant_value" >"$STATE_DIR/marker-descendants"
  printf 'marker-rv-claimed\n' >"$STATE_DIR/marker-rv"
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" secret "*"${APP_SECRET_NAME}"* ]]; then
  [ -f "$STATE_DIR/secret" ] || exit 0
  if [ "$output" = json ]; then
    printf '{"metadata":{"name":"%s","namespace":"%s","uid":"secret-uid","resourceVersion":"secret-rv","annotations":{"tertius.io/lease-id":"%s"}}}\n' "$APP_SECRET_NAME" "$NAMESPACE" "${MOCK_SECRET_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
  else
    printf '%s\n' "$APP_SECRET_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *"clusters.postgresql.cnpg.io"* ]]; then
  if [ "$output" = json ]; then
    if [[ "$joined" == *" ${RELEASE_NAME}-postgres "* ]]; then
      [ -f "$STATE_DIR/cluster" ] || exit 0
      printf '{"apiVersion":"postgresql.cnpg.io/v1","kind":"Cluster","metadata":{"name":"%s-postgres","uid":"cluster-uid","resourceVersion":"cluster-rv"}}\n' "$RELEASE_NAME"
      exit 0
    fi
    if [ -f "$STATE_DIR/cluster" ]; then
      printf '{"items":[{"apiVersion":"postgresql.cnpg.io/v1","kind":"Cluster","metadata":{"name":"%s-postgres","uid":"cluster-uid","resourceVersion":"cluster-rv","labels":{"app.kubernetes.io/instance":"%s"},"annotations":{"tertius.io/lease-id":"%s"}}}]}\n' "$RELEASE_NAME" "$RELEASE_NAME" "${MOCK_DATA_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
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
    if [[ "$joined" == *" ${RELEASE_NAME}-data "* || "$joined" == *" ${RELEASE_NAME}-pi-agent-auth "* ]]; then
      pvc_name=${3:-}
      case "$pvc_name" in
        "${RELEASE_NAME}-data") state_file=data-pvc; uid=data-uid; rv=data-rv ;;
        "${RELEASE_NAME}-pi-agent-auth") state_file=auth-pvc; uid=auth-uid; rv=auth-rv ;;
      esac
      [ -f "$STATE_DIR/$state_file" ] || exit 0
      printf '{"apiVersion":"v1","kind":"PersistentVolumeClaim","metadata":{"name":"%s","uid":"%s","resourceVersion":"%s"}}\n' "$pvc_name" "$uid" "$rv"
      exit 0
    fi
    printf '{"items":['
    separator=""
    if [ -f "$STATE_DIR/data-pvc" ]; then
      printf '%s{"apiVersion":"v1","kind":"PersistentVolumeClaim","metadata":{"name":"%s-data","uid":"data-uid","resourceVersion":"data-rv","labels":{"app.kubernetes.io/instance":"%s"},"annotations":{"tertius.io/lease-id":"%s"}}}' "$separator" "$RELEASE_NAME" "$RELEASE_NAME" "${MOCK_DATA_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
      separator=,
    fi
    if [ -f "$STATE_DIR/auth-pvc" ]; then
      printf '%s{"apiVersion":"v1","kind":"PersistentVolumeClaim","metadata":{"name":"%s-pi-agent-auth","uid":"auth-uid","resourceVersion":"auth-rv","labels":{"app.kubernetes.io/instance":"%s","app.kubernetes.io/component":"pi-agent-auth"},"annotations":{"tertius.io/lease-id":"%s"}}}' "$separator" "$RELEASE_NAME" "$RELEASE_NAME" "${MOCK_DATA_LEASE_ID:-11111111-1111-4111-8111-111111111111}"
    fi
    printf ']}\n'
  else
    [ ! -f "$STATE_DIR/data-pvc" ] || printf 'persistentvolumeclaim/%s-data\n' "$RELEASE_NAME"
    [ ! -f "$STATE_DIR/auth-pvc" ] || printf 'persistentvolumeclaim/%s-pi-agent-auth\n' "$RELEASE_NAME"
  fi
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" deployment,statefulset,daemonset,replicaset,controllerrevision,pod,poddisruptionbudget,service,endpoints,endpointslice,job,configmap,secret,serviceaccount,role,rolebinding,networkpolicy,scaledjob,scaledobject,keycloakrealmimports.k8s.keycloak.org,pvc "* ]] && [ "$output" = json ]; then
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
  if [ -f "$STATE_DIR/operator-pdb" ]; then
    printf '%s{"apiVersion":"policy/v1","kind":"PodDisruptionBudget","metadata":{"name":"%s-operator-pdb","uid":"operator-pdb-uid","ownerReferences":[{"uid":"cluster-uid"}]}}' "$separator" "$RELEASE_NAME"
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

if [ "${1:-}" = get ] && [[ "$joined" == *" poddisruptionbudget "*"${RELEASE_NAME}-operator-pdb"* ]]; then
  [ -f "$STATE_DIR/operator-pdb" ] || exit 0
  [ "$output" != json ] || printf '{"apiVersion":"policy/v1","kind":"PodDisruptionBudget","metadata":{"name":"%s-operator-pdb","uid":"operator-pdb-uid"}}\n' "$RELEASE_NAME"
  exit 0
fi

if [ "${1:-}" = get ] && [[ "$joined" == *" pods "* ]]; then
  if [[ "$joined" == *"tertius.io/harness-probe=true"* ]]; then
    [ "${MOCK_PROBE_INVENTORY_FAILURE:-false}" != true ] || exit 13
    printf '{"items":[]}\n'
  elif [ "${MOCK_COLLIDING_PROBE:-false}" = true ]; then
    printf 'pod/%s-pg-check-collision\n' "$RELEASE_NAME"
  fi
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

if [ "${1:-}" = delete ] && [ "${2:-}" = --raw ]; then
  if [ "${MOCK_MUTATION_FAILURE:-}" = raw-delete-absent ]; then
    case "${3:-}" in
      */secrets/*) rm -f "$STATE_DIR/secret" ;;
      */clusters/*) rm -f "$STATE_DIR/cluster" ;;
      */persistentvolumeclaims/*data) rm -f "$STATE_DIR/data-pvc" ;;
      */persistentvolumeclaims/*pi-agent-auth) rm -f "$STATE_DIR/auth-pvc" ;;
      */configmaps/*) rm -f "$STATE_DIR/marker" ;;
    esac
    exit 11
  fi
  [ "${MOCK_MUTATION_FAILURE:-}" != raw-delete ] || exit 11
  delete_options=$(cat)
  printf '%s\n' "$delete_options" >>"${COMMAND_LOG}.stdin"
  printf '%s' "$delete_options" | jq -e '.preconditions.uid != null and .preconditions.resourceVersion != null' >/dev/null || exit 12
  case "${3:-}" in
    */secrets/*) rm -f "$STATE_DIR/secret" ;;
    */clusters/*) rm -f "$STATE_DIR/cluster" ;;
    */persistentvolumeclaims/*data) rm -f "$STATE_DIR/data-pvc" ;;
    */persistentvolumeclaims/*pi-agent-auth) rm -f "$STATE_DIR/auth-pvc" ;;
    */configmaps/*) rm -f "$STATE_DIR/marker" ;;
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

if [ "${1:-}" = apply ] || [ "${1:-}" = create ]; then
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
  MOCK_FLUX_CROSS_DEFAULT="${MOCK_FLUX_CROSS_DEFAULT:-false}" \
  MOCK_FLUX_LONG_DEFAULT="${MOCK_FLUX_LONG_DEFAULT:-false}" \
  MOCK_MUTATION_FAILURE="${MOCK_MUTATION_FAILURE:-}" \
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
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/secrets/test-release-app -f -' \
  "default cleanup must delete the exact external app Secret with API preconditions"
grep -q '"uid":"secret-uid".*"resourceVersion":"secret-rv"\|"resourceVersion":"secret-rv".*"uid":"secret-uid"' "${COMMAND_LOG}.stdin" || \
  fail "external Secret deletion must carry UID and resourceVersion preconditions"
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/configmaps/test-release-harness-lifecycle -f -' \
  "default cleanup must delete the lifecycle marker with API preconditions"
assert_log 'kubectl delete --raw /apis/postgresql\.cnpg\.io/v1/namespaces/test-ns/clusters/test-release-postgres' \
  "default cleanup must delete release data"
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/persistentvolumeclaims/test-release-data' \
  "default cleanup must delete the data PVC"
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/persistentvolumeclaims/test-release-pi-agent-auth' \
  "default cleanup must delete the auth PVC"
assert_log 'helm (status|list)' \
  "default cleanup must verify Helm release absence"
assert_log 'kubectl get .*app\.kubernetes\.io/instance=test-release' \
  "default cleanup must verify scoped Kubernetes resource absence"
for required_kind in \
  deployment statefulset daemonset replicaset controllerrevision pod poddisruptionbudget service endpoints endpointslice job configmap secret \
  serviceaccount role rolebinding networkpolicy scaledjob scaledobject \
  clusters.postgresql.cnpg.io keycloaks.k8s.keycloak.org keycloakrealmimports.k8s.keycloak.org pvc; do
  assert_log "kubectl get ([^[:space:]]*,)*${required_kind}(,|[[:space:]])" \
    "absence verification must inspect ${required_kind} resources"
done

reset_state
: >"$COMMAND_LOG"
printf 'cleaning\n' >"$STATE_DIR/marker-policy"
if PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  TEST_K3S_DEPLOYMENT_LIB_ONLY=true NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  bash -c 'script=$1; shift; . "$script"; trap - ERR EXIT INT TERM; create_lifecycle_marker' \
  bash "$ROOT_DIR/scripts/test-k3s-deployment.sh"; then
  fail "up must refuse a lifecycle marker already claimed for cleaning"
fi
assert_not_log 'kubectl apply' "up refusal for cleaning marker must not overwrite the cleanup claim"

reset_state
: >"$COMMAND_LOG"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  TEST_K3S_DEPLOYMENT_LIB_ONLY=true NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  bash -c 'script=$1; shift; . "$script"; trap - ERR EXIT INT TERM; create_lifecycle_marker' \
  bash "$ROOT_DIR/scripts/test-k3s-deployment.sh"
assert_log 'kubectl patch configmap test-release-harness-lifecycle .*metadata/resourceVersion.*cleanup-policy' \
  "existing marker renewal must use UID/resourceVersion/policy CAS"
assert_not_log 'kubectl (apply|create) -f -' "existing marker renewal must not overwrite by manifest"

reset_state
: >"$COMMAND_LOG"
if PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  MOCK_MUTATION_FAILURE=renewal-patch TEST_K3S_DEPLOYMENT_LIB_ONLY=true \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  bash -c 'script=$1; shift; . "$script"; trap - ERR EXIT INT TERM; create_lifecycle_marker' \
  bash "$ROOT_DIR/scripts/test-k3s-deployment.sh"; then
  fail "renewal CAS interleave must be refused"
fi
assert_not_log 'kubectl (apply|create) -f -' "failed renewal CAS must not fall back to overwrite"

: >"$COMMAND_LOG"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  TEST_K3S_DEPLOYMENT_LIB_ONLY=true NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  bash -c '
    script=$1; shift; . "$script"; trap - ERR EXIT INT TERM
    detect_container_tool() { :; }; detect_k3s_container() { :; }; apply_image_defaults() { :; }
    check_preflight() { printf "preflight\n" >>"$COMMAND_LOG"; }
    ensure_namespace() { printf "namespace\n" >>"$COMMAND_LOG"; }
    create_lifecycle_marker() { printf "marker\n" >>"$COMMAND_LOG"; }
    build_images() { :; }; load_images() { :; }; render_and_install() { :; }
    wait_for_rollout() { :; }; run_smoke_tests() { :; }
    main
  ' bash "$ROOT_DIR/scripts/test-k3s-deployment.sh"
namespace_line=$(grep -n '^namespace$' "$COMMAND_LOG" | cut -d: -f1)
marker_line=$(grep -n '^marker$' "$COMMAND_LOG" | cut -d: -f1)
[ -n "$namespace_line" ] && [ "$namespace_line" -lt "$marker_line" ] || \
  fail "brand-new namespace must be created before the lifecycle marker"

: >"$COMMAND_LOG"
if PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  MOCK_PROBE_INVENTORY_FAILURE=true TEST_K3S_DEPLOYMENT_LIB_ONLY=true \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  bash -c 'script=$1; shift; . "$script"; trap - ERR EXIT INT TERM; LIFECYCLE_LEASE_ID=11111111-1111-4111-8111-111111111111; inventory_test_pods' \
  bash "$ROOT_DIR/scripts/test-k3s-deployment.sh"; then
  fail "probe cleanup must fail closed when exact ownership inventory fails"
fi
assert_not_log 'kubectl delete.*pg-check-collision' "probe inventory failure must not delete guessed names"

: >"$COMMAND_LOG"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  MOCK_COLLIDING_PROBE=true TEST_K3S_DEPLOYMENT_LIB_ONLY=true \
  NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  bash -c 'script=$1; shift; . "$script"; trap - ERR EXIT INT TERM; LIFECYCLE_LEASE_ID=11111111-1111-4111-8111-111111111111; inventory_test_pods; delete_test_pods' \
  bash "$ROOT_DIR/scripts/test-k3s-deployment.sh"
assert_not_log 'kubectl delete.*pg-check-collision' "colliding unrelated Pod names must never be deleted"

[ "$(rg -c -- '--labels=.*tertius.io/harness-probe=true' "$ROOT_DIR/scripts/test-k3s-deployment.sh")" -ge 4 ] || \
  fail "every harness probe Pod creation must carry exact release, lease, and probe labels"

: >"$COMMAND_LOG"
PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
  TEST_K3S_DEPLOYMENT_LIB_ONLY=true NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
  bash -c 'script=$1; shift; . "$script"; trap - ERR EXIT INT TERM; failure_context' \
  bash "$ROOT_DIR/scripts/test-k3s-deployment.sh" >/dev/null 2>&1
assert_log 'helm status test-release -n test-ns' "failure context must capture bounded Helm status"
assert_log 'helm history test-release -n test-ns' "failure context must capture bounded Helm history"

reset_state
run_deploy_cleanup --retain-data
assert_not_log 'kubectl delete --raw .*(clusters|persistentvolumeclaims)/' \
  "--retain-data must not delete clusters or PVCs"
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/secrets/test-release-app -f -' \
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
assert_not_log 'kubectl annotate configmap test-release-harness-lifecycle' \
  "retention finalization must not mutate a marker by name without CAS"

reset_state
run_deploy_cleanup --retain-auth
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/persistentvolumeclaims/test-release-data' \
  "--retain-auth must delete non-auth data PVCs"
assert_not_log 'kubectl delete --raw /api/v1/namespaces/test-ns/persistentvolumeclaims/test-release-pi-agent-auth' \
  "--retain-auth must preserve only the auth PVC"
assert_log 'kubectl delete --raw /apis/postgresql\.cnpg\.io/v1/namespaces/test-ns/clusters/test-release-postgres' \
  "--retain-auth must delete CNPG data"
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/secrets/test-release-app -f -' \
  "--retain-auth must delete the app Secret"
assert_not_log 'kubectl delete configmap test-release-harness-lifecycle' \
  "--retain-auth must preserve a lifecycle tombstone"
assert_log 'kubectl (annotate|patch|apply).*test-release-harness-lifecycle.*cleanup-policy.*retain' \
  "--retain-auth must mark the lifecycle record as a retention tombstone"
assert_log 'kubectl (annotate|patch|apply).*test-release-harness-lifecycle.*retained-objects.*auth-uid' \
  "--retain-auth tombstone must record the retained auth PVC UID"
assert_not_log 'kubectl annotate configmap test-release-harness-lifecycle' \
  "auth retention finalization must use marker CAS"

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

reset_legacy_state
: >"$COMMAND_LOG"
if NAMESPACE=target-ns RELEASE_NAME=target-ns-cross-release MOCK_FLUX_CROSS_DEFAULT=true run_deploy_cleanup; then
  fail "cross-namespace Flux default release cleanup must be refused"
fi
assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
  "cross-namespace omitted releaseName must be recognized before mutation"

reset_legacy_state
: >"$COMMAND_LOG"
if NAMESPACE=very-long-target-namespace \
  RELEASE_NAME=very-long-target-namespace-extremely-lon-a7669c62942e \
  APP_SECRET_NAME=very-long-target-namespace-extremely-lon-a7669c62942e-app \
  MOCK_FLUX_LONG_DEFAULT=true run_deploy_cleanup; then
  fail "long hashed Flux default release cleanup must be refused"
fi
assert_not_log 'helm uninstall|kubectl (delete|annotate|patch|apply)' \
  "long omitted releaseName must use Flux 40-plus-hash effective naming"

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

assert_log 'kubectl patch configmap test-release-harness-lifecycle .* --type=json .*tertius.io.*cleanup-policy.*cleaning' \
  "cleanup must atomically claim the lifecycle marker before teardown"
claim_line=$(grep -n 'kubectl patch configmap test-release-harness-lifecycle' "$COMMAND_LOG" | head -1 | cut -d: -f1)
helm_line=$(grep -n 'helm uninstall test-release' "$COMMAND_LOG" | head -1 | cut -d: -f1)
[ "$claim_line" -lt "$helm_line" ] || fail "marker CAS claim must be the first cleanup mutation"

reset_state
: >"$COMMAND_LOG"
if MOCK_MUTATION_FAILURE=marker-claim run_deploy_cleanup; then
  fail "cleanup must stop when the atomic marker claim fails"
fi
assert_not_log 'helm uninstall|kubectl delete --raw|kubectl annotate' \
  "failed marker claim must prevent every subsequent destructive mutation"

reset_state
: >"$COMMAND_LOG"
if MOCK_MUTATION_FAILURE=helm-uninstall run_deploy_cleanup; then
  fail "cleanup must propagate Helm mutation failure"
fi
assert_not_log 'kubectl delete --raw|kubectl delete configmap' \
  "failed Helm mutation must prevent subsequent destructive mutations"

reset_state
: >"$COMMAND_LOG"
if MOCK_MUTATION_FAILURE=raw-delete run_deploy_cleanup; then
  fail "UID/resourceVersion precondition refusal must fail cleanup"
fi
[ "$(grep -Ec 'kubectl delete --raw' "$COMMAND_LOG" || true)" -eq 1 ] || \
  fail "a replacement-race delete refusal must prevent subsequent deletes"
assert_not_log '/persistentvolumeclaims/|/secrets/|kubectl delete configmap' \
  "replacement-race refusal must preserve every later cleanup target"

reset_state
: >"$COMMAND_LOG"
MOCK_MUTATION_FAILURE=raw-delete-absent run_deploy_cleanup

reset_state
: >"$COMMAND_LOG"
if MOCK_MUTATION_FAILURE=retention-patch run_deploy_cleanup --retain-data; then
  fail "retention finalization CAS refusal must fail cleanup"
fi
assert_not_log 'kubectl annotate configmap test-release-harness-lifecycle' \
  "retention CAS refusal must not fall back to name-only annotation"

reset_state
: >"$COMMAND_LOG"
MOCK_MARKER_APP_SECRET_NAME=custom-app-secret run_deploy_cleanup
assert_log 'kubectl delete --raw /api/v1/namespaces/test-ns/secrets/custom-app-secret -f -' \
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

for failed_adoption_inventory in marker secret cluster pvc; do
  reset_legacy_state
  : >"$COMMAND_LOG"
  if printf '%s\n' 'test-ns/test-release' | PATH="${MOCK_BIN}:$PATH" COMMAND_LOG="$COMMAND_LOG" STATE_DIR="$STATE_DIR" \
    NAMESPACE=test-ns RELEASE_NAME=test-release APP_SECRET_NAME=test-release-app \
    MOCK_INVENTORY_READ_FAILURE="$failed_adoption_inventory" \
    "$ROOT_DIR/scripts/harness-k3s.sh" adopt test-ns/test-release; then
    fail "legacy adoption must fail closed on ${failed_adoption_inventory} inventory errors"
  fi
  assert_not_log 'kubectl (delete|annotate|patch|apply)' \
    "${failed_adoption_inventory} inventory failure must not partially adopt Kubernetes resources"
done

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
grep -q 'operator-child-uid.*operator-grandchild-uid.*operator-pod-uid' "$STATE_DIR/marker-descendants" || \
  fail "atomic cleanup claim must persist recursive descendant identities"

# A retry after operator roots disappear must merge persisted identities and still
# block marker deletion while an originally captured UID remains.
rm -f "$STATE_DIR/cluster"
: >"$COMMAND_LOG"
if run_deploy_cleanup; then
  fail "cleanup retry must verify descendants persisted before root deletion"
fi
assert_log 'kubectl get deployment test-release-operator-child -n test-ns .* -o json' \
  "retry must verify persisted descendant identities even without the root"
assert_not_log 'kubectl delete configmap test-release-harness-lifecycle' \
  "retry must preserve marker while a persisted descendant UID remains"

reset_state
touch "$STATE_DIR/keycloak-root" "$STATE_DIR/realm-import"
: >"$COMMAND_LOG"
if run_deploy_cleanup; then
  fail "cleanup must fail when a captured KeycloakRealmImport remains"
fi
assert_log 'kubectl get keycloakrealmimport test-release-realm -n test-ns .* -o json' \
  "cleanup must verify captured KeycloakRealmImport UIDs"

reset_state
touch "$STATE_DIR/operator-pdb"
: >"$COMMAND_LOG"
if run_deploy_cleanup; then
  fail "cleanup must fail when a captured PodDisruptionBudget remains"
fi
assert_log 'kubectl get poddisruptionbudget test-release-operator-pdb -n test-ns .* -o json' \
  "cleanup must verify captured PodDisruptionBudget UIDs"

reset_state
touch "$STATE_DIR/operator-child" "$STATE_DIR/operator-grandchild"
: >"$COMMAND_LOG"
run_deploy_cleanup --retain-data
assert_log 'kubectl patch configmap test-release-harness-lifecycle .*retained-objects.*test-release-operator-child.*operator-child-uid' \
  "retained data tombstone must record the retained operator child"
assert_log 'kubectl patch configmap test-release-harness-lifecycle .*retained-objects.*test-release-operator-grandchild.*operator-grandchild-uid' \
  "retained data tombstone must record recursive retained descendants"
assert_log 'kubectl patch configmap test-release-harness-lifecycle .*retained-objects.*test-release-operator-pod.*operator-pod-uid' \
  "retained data tombstone must record recursive retained pods"

echo "k3s harness lifecycle contract tests passed"
