#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/infra/charts/tertius"
VALUES_FILE="${CHART_DIR}/values-local.yaml"

NAMESPACE="${NAMESPACE:-tertius}"
RELEASE_NAME="${RELEASE_NAME:-tertius}"
API_IMAGE="${API_IMAGE:-}"
UI_IMAGE="${UI_IMAGE:-}"
PI_AGENT_IMAGE="${PI_AGENT_IMAGE:-}"
GIS_CACHE_IMAGE="${GIS_CACHE_IMAGE:-}"
ENABLE_TUNNEL="${ENABLE_TUNNEL:-false}"
TUNNEL_TOKEN_SECRET_NAME="${TUNNEL_TOKEN_SECRET_NAME:-}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-tertius}"
KEYCLOAK_SMOKE_USERNAME="${KEYCLOAK_SMOKE_USERNAME:-demo}"
KEYCLOAK_SMOKE_PASSWORD="${KEYCLOAK_SMOKE_PASSWORD:-demo}"
KEYCLOAK_CHECK_IMAGE="${KEYCLOAK_CHECK_IMAGE:-busybox:1.37.0}"
VALKEY_CHECK_IMAGE="${VALKEY_CHECK_IMAGE:-}"
NATS_CHECK_IMAGE="${NATS_CHECK_IMAGE:-natsio/nats-box:0.19.7}"
KEDA_ENABLED="${KEDA_ENABLED:-false}"
PI_AGENT_ENABLED="${PI_AGENT_ENABLED:-false}"
ALLOW_FLUX_MANAGED_RELEASE="${ALLOW_FLUX_MANAGED_RELEASE:-false}"
ALLOW_KEYCLOAK_OPERATOR_SCOPE_MISMATCH="${ALLOW_KEYCLOAK_OPERATOR_SCOPE_MISMATCH:-false}"
BUILDX_GHA_CACHE="${BUILDX_GHA_CACHE:-false}"
CLEAN_LOCAL_IMAGES_AFTER_LOAD="${CLEAN_LOCAL_IMAGES_AFTER_LOAD:-false}"
APP_SECRET_NAME="${APP_SECRET_NAME:-${RELEASE_NAME}-app}"
APP_AUTH_SESSION_SECRET="${APP_AUTH_SESSION_SECRET:-local-auth-session-secret-change-me}"
APP_OIDC_CLIENT_SECRET="${APP_OIDC_CLIENT_SECRET:-}"
APP_DATABASE_URL="${APP_DATABASE_URL:-}"
APP_VALKEY_URL="${APP_VALKEY_URL:-redis://${RELEASE_NAME}-valkey:6379/0}"
UI_LOCAL_PORT="${UI_LOCAL_PORT:-18080}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18000}"
KEYCLOAK_LOCAL_PORT="${KEYCLOAK_LOCAL_PORT:-0}"
PORT_FORWARD_ADDRESS="${PORT_FORWARD_ADDRESS:-127.0.0.1}"
METRICS_LOCAL_PORT="${METRICS_LOCAL_PORT:-8428}"
TIMEOUT="${TIMEOUT:-10m}"
DOCKER="${DOCKER:-}"
K3S_CONTAINER="${K3S_CONTAINER:-}"
BUILD_TAG="${BUILD_TAG:-$(date +%Y%m%d%H%M%S)}"
HARNESS_TTL_SECONDS="${HARNESS_TTL_SECONDS:-21600}"
HARNESS_RETAIN_ON_FAILURE="${HARNESS_RETAIN_ON_FAILURE:-false}"

CLEANUP=false
RETAIN_DATA=false
RETAIN_AUTH=false
PORT_FORWARD_PIDS=""
TEMP_FILES=""
PI_AUTH_CLAIM=""
PI_AUTH_RENDERED_STORAGE_CLASS=""
LIFECYCLE_MARKER="${RELEASE_NAME}-harness-lifecycle"
LIFECYCLE_LEASE_ID=""
LIFECYCLE_CREATED=false
AUTOMATIC_CLEANUP_STARTED=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [--cleanup] [--retain-data] [--retain-auth] [--delete-data] [--help]

Runs the Tertius Helm chart end-to-end against the current k3s context.

Environment:
  KUBECONFIG                    Optional; kubectl uses the current context by default.
  NAMESPACE                     Default: tertius
  RELEASE_NAME                  Default: tertius
  API_IMAGE                     Default: tertius-api:local (auto-suffixed with :local-<timestamp> for fresh rollout)
  UI_IMAGE                      Default: tertius-ui:local (auto-suffixed with :local-<timestamp> for fresh rollout)
  PI_AGENT_IMAGE                Default: tertius-pi-agent:local (auto-suffixed with :local-<timestamp> for fresh rollout)
  GIS_CACHE_IMAGE               Default: tertius-gis-cache:local (auto-suffixed with :local-<timestamp> for fresh rollout)
  ENABLE_TUNNEL                 Default: false
  TUNNEL_TOKEN_SECRET_NAME      Required when ENABLE_TUNNEL=true
  TUNNEL_HOSTNAME               Optional external hostname to smoke test when tunnel is enabled.
  KEYCLOAK_REALM                Default: tertius
  KEYCLOAK_SMOKE_USERNAME       Default: demo
  KEYCLOAK_SMOKE_PASSWORD       Default: demo
  KEYCLOAK_CHECK_IMAGE          Default: busybox:1.37.0
  VALKEY_CHECK_IMAGE            Default: valkey image from values-local.yaml, then valkey/valkey:9.0.0
  NATS_CHECK_IMAGE              Default: natsio/nats-box:0.19.7
  KEDA_ENABLED                  Default: false. Enables KEDA ScaledJob rendering during the smoke deploy.
  PI_AGENT_ENABLED              Default: false. Enables the serial Pi ScaledJob after OAuth verification.
  ALLOW_FLUX_MANAGED_RELEASE    Default: false. Set true only when intentionally testing a Flux-managed release.
  ALLOW_KEYCLOAK_OPERATOR_SCOPE_MISMATCH
                                Default: false. Set true only when an external Keycloak operator is known to watch NAMESPACE.
  BUILDX_GHA_CACHE              Default: false. Set true in GitHub Actions to use Buildx GHA cache for local image builds.
  CLEAN_LOCAL_IMAGES_AFTER_LOAD Default: false. Set true in CI to reduce peak disk use while importing images into k3s.
  APP_SECRET_NAME               Default: <release>-app. External app Secret consumed by the API.
  APP_AUTH_SESSION_SECRET       Default: local-auth-session-secret-change-me.
  APP_OIDC_CLIENT_SECRET        Default: empty.
  APP_DATABASE_URL              Default: empty; chart DB env fields derive DATABASE_URL when unset.
  APP_VALKEY_URL                Default: redis://<release>-valkey:6379/0.
  UI_LOCAL_PORT                 Default: 18080
  API_LOCAL_PORT                Default: 18000
  KEYCLOAK_LOCAL_PORT           Default: 0, meaning kubectl chooses a free local port.
  PORT_FORWARD_ADDRESS          Default: 127.0.0.1. Set to 0.0.0.0 only for explicit shared previews.
  METRICS_LOCAL_PORT            Default: 8428
  TIMEOUT                       Default: 10m
  DOCKER                        Default: docker when available, otherwise podman.
  K3S_CONTAINER                 Optional k3s Podman/Docker container name for image imports.

Cleanup:
  --cleanup       Fully remove the exact harness release and its leased data.
  --retain-data   With --cleanup, retain leased CNPG clusters and release PVCs.
  --retain-auth   With --cleanup, retain only the leased Pi agent auth PVC.
  --delete-data   Compatibility alias for full --cleanup behavior.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cleanup)
      CLEANUP=true
      ;;
    --retain-data)
      RETAIN_DATA=true
      ;;
    --retain-auth)
      RETAIN_AUTH=true
      ;;
    --delete-data)
      CLEANUP=true
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

quote_cmd() {
  printf '+'
  while [ "$#" -gt 0 ]; do
    printf ' %q' "$1"
    shift
  done
  printf '\n'
}

run() {
  quote_cmd "$@"
  "$@"
}

capture() {
  quote_cmd "$@" >&2
  "$@"
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

values_image_for() {
  section=$1
  fallback=$2
  if [ ! -f "$VALUES_FILE" ]; then
    printf '%s\n' "$fallback"
    return
  fi

  image=$(awk -v section="$section" '
    function leading_spaces(line) {
      match(line, /[^ ]/)
      return RSTART ? RSTART - 1 : 0
    }
    function clean(value) {
      gsub(/"/, "", value)
      gsub(/\047/, "", value)
      return value
    }
    $0 ~ "^[[:space:]]*" section ":" {
      in_section = 1
      section_indent = leading_spaces($0)
      next
    }
    in_section && leading_spaces($0) <= section_indent && $0 !~ "^[[:space:]]*$" {
      in_section = 0
      in_image = 0
    }
    in_section && $0 ~ "^[[:space:]]*image:[[:space:]]*[^[:space:]]" {
      value = $0
      sub(/^[[:space:]]*image:[[:space:]]*/, "", value)
      print clean(value)
      found = 1
      exit
    }
    in_section && $0 ~ "^[[:space:]]*image:[[:space:]]*$" {
      in_image = 1
      image_indent = leading_spaces($0)
      next
    }
    in_image && leading_spaces($0) <= image_indent && $0 !~ "^[[:space:]]*$" {
      in_image = 0
    }
    in_image && $0 ~ "^[[:space:]]*repository:" {
      repo = $0
      sub(/^[[:space:]]*repository:[[:space:]]*/, "", repo)
      repo = clean(repo)
    }
    in_image && $0 ~ "^[[:space:]]*tag:" {
      tag = $0
      sub(/^[[:space:]]*tag:[[:space:]]*/, "", tag)
      tag = clean(tag)
    }
    END {
      if (!found && repo != "") {
        if (tag == "") {
          tag = "latest"
        }
        print repo ":" tag
      }
    }
  ' "$VALUES_FILE")

  [ -n "$image" ] || image=$fallback
  printf '%s\n' "$image"
}

refresh_local_image_tag() {
  local image image_without_digest tag repo

  image=${1:-}
  [ -n "$image" ] || {
    printf '%s\n' "$image"
    return
  }

  image_without_digest=${image%%@*}
  tag=${image_without_digest##*/}
  if [ "${tag#*:}" = "$tag" ]; then
    printf '%s\n' "$image"
    return
  fi

  repo=${image_without_digest%:*}
  tag=${tag##*:}
  if [ "$tag" != "local" ]; then
    printf '%s\n' "$image"
    return
  fi

  printf '%s:%s-%s\n' "$repo" "$tag" "$BUILD_TAG"
}

apply_image_defaults() {
  api_from_default=0
  ui_from_default=0
  pi_agent_from_default=0
  gis_cache_from_default=0

  if [ -z "$API_IMAGE" ]; then
    API_IMAGE=$(values_image_for api tertius-api:local)
    api_from_default=1
  fi
  if [ -z "$UI_IMAGE" ]; then
    UI_IMAGE=$(values_image_for ui tertius-ui:local)
    ui_from_default=1
  fi
  if [ -z "$PI_AGENT_IMAGE" ]; then
    PI_AGENT_IMAGE=$(values_image_for piAgent tertius-pi-agent:local)
    pi_agent_from_default=1
  fi
  if [ -z "$GIS_CACHE_IMAGE" ]; then
    GIS_CACHE_IMAGE=$(values_image_for gisCache tertius-gis-cache:local)
    gis_cache_from_default=1
  fi
  [ -n "$VALKEY_CHECK_IMAGE" ] || VALKEY_CHECK_IMAGE=$(values_image_for valkey valkey/valkey:9.0.0)

  if [ "$api_from_default" -eq 1 ]; then
    API_IMAGE=$(refresh_local_image_tag "$API_IMAGE")
  fi
  if [ "$ui_from_default" -eq 1 ]; then
    UI_IMAGE=$(refresh_local_image_tag "$UI_IMAGE")
  fi
  if [ "$pi_agent_from_default" -eq 1 ]; then
    PI_AGENT_IMAGE=$(refresh_local_image_tag "$PI_AGENT_IMAGE")
  fi
  if [ "$gis_cache_from_default" -eq 1 ]; then
    GIS_CACHE_IMAGE=$(refresh_local_image_tag "$GIS_CACHE_IMAGE")
  fi
}

detect_container_tool() {
  if [ -n "$DOCKER" ]; then
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    DOCKER=docker
    return
  fi
  if command -v podman >/dev/null 2>&1; then
    DOCKER=podman
    return
  fi
  DOCKER=docker
}

detect_k3s_container() {
  if [ -n "$K3S_CONTAINER" ]; then
    return
  fi
  if command -v docker >/dev/null 2>&1 && docker container inspect tertius-k3s >/dev/null 2>&1; then
    K3S_CONTAINER=tertius-k3s
    return
  fi
  if ! command -v podman >/dev/null 2>&1; then
    return
  fi
  if podman container exists tertius-k3s >/dev/null 2>&1; then
    K3S_CONTAINER=tertius-k3s
  fi
}

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

truthy() {
  case "$(lower "$1")" in
    1|true|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

validate_target() {
  case "$NAMESPACE" in
    ""|*[!a-z0-9.-]*|.*|*.) echo "Invalid Kubernetes namespace: ${NAMESPACE}" >&2; return 1 ;;
  esac
  case "$RELEASE_NAME" in
    ""|*[!a-z0-9-]*|-*|*-) echo "Invalid Helm release name: ${RELEASE_NAME}" >&2; return 1 ;;
  esac
}

validate_ttl() {
  case "$HARNESS_TTL_SECONDS" in
    ""|*[!0-9]*) echo "HARNESS_TTL_SECONDS must be an integer from 900 to 86400." >&2; return 1 ;;
  esac
  if [ "$HARNESS_TTL_SECONDS" -lt 900 ] || [ "$HARNESS_TTL_SECONDS" -gt 86400 ]; then
    echo "HARNESS_TTL_SECONDS must be an integer from 900 to 86400." >&2
    return 1
  fi
}

new_lease_id() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  else
    sed -n '1p' /proc/sys/kernel/random/uuid
  fi
}

flux_effective_release_name() {
  local target_namespace source_namespace source_name explicit_name base_name digest
  target_namespace=$1
  source_namespace=$2
  source_name=$3
  explicit_name=$4
  if [ -n "$explicit_name" ]; then printf '%s\n' "$explicit_name"; return; fi
  if [ "$target_namespace" = "$source_namespace" ]; then base_name=$source_name; else base_name="${target_namespace}-${source_name}"; fi
  if [ "${#base_name}" -le 53 ]; then printf '%s\n' "$base_name"; return; fi
  digest=$(printf '%s' "$base_name" | sha256sum) || return 1
  printf '%.40s-%.12s\n' "$base_name" "$digest"
}

matching_flux_release() {
  local flux_json flux_resources flux_records target source_namespace source_name explicit_name effective
  flux_json=$(kubectl get helmreleases.helm.toolkit.fluxcd.io --all-namespaces -o json 2>/dev/null) || {
    flux_resources=$(kubectl api-resources --api-group=helm.toolkit.fluxcd.io -o name 2>/dev/null) || {
      echo "Unable to inspect Flux API discovery; refusing ${NAMESPACE}/${RELEASE_NAME}." >&2
      return 2
    }
    if ! printf '%s\n' "$flux_resources" | grep -Fxq 'helmreleases.helm.toolkit.fluxcd.io'; then
      return 1
    fi
    echo "Unable to inspect Flux HelmRelease ownership; refusing ${NAMESPACE}/${RELEASE_NAME}." >&2
    return 2
  }
  printf '%s' "$flux_json" | jq -e 'type == "object" and (.items | type == "array")' >/dev/null || return 2
  flux_records=$(printf '%s' "$flux_json" | jq -r '.items[]? |
    [(.spec.targetNamespace // .metadata.namespace),.metadata.namespace,.metadata.name,(.spec.releaseName // "")] | @tsv') || return 2
  while IFS=$'\t' read -r target source_namespace source_name explicit_name; do
    [ -n "$target" ] || continue
    effective=$(flux_effective_release_name "$target" "$source_namespace" "$source_name" "$explicit_name") || return 2
    [ "$target" != "$NAMESPACE" ] || [ "$effective" != "$RELEASE_NAME" ] || return 0
  done <<<"$flux_records"
  return 1
}

require_safe_destructive_target() {
  validate_target || return 1
  if [ "$RELEASE_NAME" = "tertius" ]; then
    echo "Refusing destructive cleanup of protected release ${NAMESPACE}/tertius." >&2
    return 1
  fi
  if matching_flux_release; then
    echo "Refusing destructive cleanup of Flux-managed release ${NAMESPACE}/${RELEASE_NAME}." >&2
    return 1
  else
    flux_status=$?
    [ "$flux_status" -ne 2 ] || return 1
  fi
}

marker_json() {
  kubectl get configmap "$LIFECYCLE_MARKER" -n "$NAMESPACE" --ignore-not-found=true -o json 2>/dev/null
}

valid_uuid() {
  [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
}

valid_rfc3339_utc() {
  value=$1
  [[ "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || return 1
  normalized=$(date -u -d "$value" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || return 1
  [ "$normalized" = "$value" ]
}

valid_kubernetes_resource_name() {
  local value=$1
  local old_ifs label
  [ "${#value}" -le 253 ] || return 1
  [[ "$value" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]] || return 1
  case "$value" in *..*) return 1 ;; esac
  old_ifs=$IFS
  IFS=.
  set -- $value
  IFS=$old_ifs
  for label in "$@"; do
    [ "${#label}" -le 63 ] || return 1
  done
}

marker_is_valid() {
  json=$1
  [ -n "$json" ] || return 1
  printf '%s' "$json" | jq -e --arg namespace "$NAMESPACE" --arg release "$RELEASE_NAME" --arg secret "$APP_SECRET_NAME" '
    .metadata.namespace == $namespace and
    (.metadata.uid | type == "string" and length > 0) and
    (.metadata.resourceVersion | type == "string" and length > 0) and
    .metadata.labels["tertius.io/harness-managed"] == "true" and
    .metadata.labels["app.kubernetes.io/instance"] == $release and
    .metadata.annotations["tertius.io/release-name"] == $release and
    .metadata.annotations["tertius.io/app-secret-name"] == $secret and
    ((.metadata.annotations["tertius.io/cleanup-policy"] == "delete") or
      (.metadata.annotations["tertius.io/cleanup-policy"] == "retain") or
      (.metadata.annotations["tertius.io/cleanup-policy"] == "cleaning")) and
    (.metadata.annotations["tertius.io/lease-id"] | type == "string" and length > 0)
  ' >/dev/null || return 1
  lease=$(printf '%s' "$json" | jq -r '.metadata.annotations["tertius.io/lease-id"]')
  expires=$(printf '%s' "$json" | jq -r '.metadata.annotations["tertius.io/expires-at"] // ""')
  valid_uuid "$lease" && valid_rfc3339_utc "$expires"
}

create_lifecycle_marker() {
  validate_ttl
  existing_marker=$(marker_json)
  if [ -n "$existing_marker" ] &&
     [ "$(printf '%s' "$existing_marker" | jq -r '.metadata.annotations["tertius.io/cleanup-policy"] // ""')" = cleaning ]; then
    echo "Refusing lifecycle marker ${NAMESPACE}/${LIFECYCLE_MARKER}: cleanup is already in progress." >&2
    return 1
  fi
  if helm status "$RELEASE_NAME" -n "$NAMESPACE" >/dev/null 2>&1; then
    if ! marker_is_valid "$existing_marker"; then
      echo "Refusing existing Helm release ${NAMESPACE}/${RELEASE_NAME} without a valid harness lifecycle marker; adopt it explicitly." >&2
      return 1
    fi
    LIFECYCLE_LEASE_ID=$(printf '%s' "$existing_marker" | jq -r '.metadata.annotations["tertius.io/lease-id"]')
  else
    if [ -n "$existing_marker" ] && ! marker_is_valid "$existing_marker"; then
      echo "Refusing invalid lifecycle marker ${NAMESPACE}/${LIFECYCLE_MARKER}." >&2
      return 1
    fi
    if [ -n "$existing_marker" ]; then
      LIFECYCLE_LEASE_ID=$(printf '%s' "$existing_marker" | jq -r '.metadata.annotations["tertius.io/lease-id"]')
    else
      LIFECYCLE_LEASE_ID=$(new_lease_id)
    fi
  fi
  expires_at=$(date -u -d "+${HARNESS_TTL_SECONDS} seconds" '+%Y-%m-%dT%H:%M:%SZ')
  if [ -n "$existing_marker" ]; then
    existing_uid=$(printf '%s' "$existing_marker" | jq -er '.metadata.uid') || return 1
    existing_rv=$(printf '%s' "$existing_marker" | jq -er '.metadata.resourceVersion') || return 1
    existing_policy=$(printf '%s' "$existing_marker" | jq -er '.metadata.annotations["tertius.io/cleanup-policy"]') || return 1
    existing_expires=$(printf '%s' "$existing_marker" | jq -er '.metadata.annotations["tertius.io/expires-at"]') || return 1
    renewal_patch=$(jq -cn --arg uid "$existing_uid" --arg rv "$existing_rv" --arg lease "$LIFECYCLE_LEASE_ID" \
      --arg policy "$existing_policy" --arg oldExpires "$existing_expires" --arg expires "$expires_at" '[
        {op:"test",path:"/metadata/uid",value:$uid},
        {op:"test",path:"/metadata/resourceVersion",value:$rv},
        {op:"test",path:"/metadata/annotations/tertius.io~1lease-id",value:$lease},
        {op:"test",path:"/metadata/annotations/tertius.io~1expires-at",value:$oldExpires},
        {op:"test",path:"/metadata/annotations/tertius.io~1cleanup-policy",value:$policy},
        {op:"replace",path:"/metadata/annotations/tertius.io~1expires-at",value:$expires},
        {op:"replace",path:"/metadata/annotations/tertius.io~1cleanup-policy",value:"delete"}
      ]') || return 1
    quote_cmd kubectl patch configmap "$LIFECYCLE_MARKER" -n "$NAMESPACE" --type=json -p "$renewal_patch" >&2
    kubectl patch configmap "$LIFECYCLE_MARKER" -n "$NAMESPACE" --type=json -p "$renewal_patch" >/dev/null || return 1
  else
    quote_cmd kubectl create -f - >&2
    kubectl create -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${LIFECYCLE_MARKER}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: tertius-harness
    app.kubernetes.io/instance: ${RELEASE_NAME}
    tertius.io/harness-managed: "true"
  annotations:
    tertius.io/lease-id: ${LIFECYCLE_LEASE_ID}
    tertius.io/release-name: ${RELEASE_NAME}
    tertius.io/app-secret-name: ${APP_SECRET_NAME}
    tertius.io/expires-at: ${expires_at}
    tertius.io/cleanup-policy: delete
EOF
  fi
  LIFECYCLE_CREATED=true
}

image_repo() {
  image_without_digest=${1%%@*}
  last_part=${image_without_digest##*/}
  case "$last_part" in
    *:*) printf '%s\n' "${image_without_digest%:*}" ;;
    *) printf '%s\n' "$image_without_digest" ;;
  esac
}

image_tag() {
  image_without_digest=${1%%@*}
  last_part=${image_without_digest##*/}
  case "$last_part" in
    *:*) printf '%s\n' "${last_part##*:}" ;;
    *) printf '%s\n' "latest" ;;
  esac
}

is_registry_image() {
  case "$1" in
    */*) ;;
    *) return 1 ;;
  esac
  first_part=${1%%/*}
  case "$first_part" in
    localhost:*|127.0.0.1:*|0.0.0.0:*|*.*|*:*) return 0 ;;
    *) return 1 ;;
  esac
}

cleanup_local() {
  for pid in $PORT_FORWARD_PIDS; do
    if kill "$pid" >/dev/null 2>&1; then
      wait "$pid" 2>/dev/null || true
    fi
  done
  for file in $TEMP_FILES; do
    [ -n "$file" ] && [ -f "$file" ] && rm -f "$file"
  done
}

failure_context() {
  echo
  echo "Failure context for namespace ${NAMESPACE}, release ${RELEASE_NAME}:"
  timeout "${FAILURE_CONTEXT_TIMEOUT_SECONDS:-10}s" helm status "$RELEASE_NAME" -n "$NAMESPACE" 2>/dev/null || true
  timeout "${FAILURE_CONTEXT_TIMEOUT_SECONDS:-10}s" helm history "$RELEASE_NAME" -n "$NAMESPACE" --max 10 2>/dev/null || true
  kubectl get all,pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o wide 2>/dev/null || true
  kubectl get clusters.postgresql.cnpg.io -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o wide 2>/dev/null || true
  kubectl get keycloaks.k8s.keycloak.org -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o wide 2>/dev/null || true
  kubectl get events -n "$NAMESPACE" --sort-by='.lastTimestamp' 2>/dev/null | tail -40 || true
  pods=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
  for pod in $pods; do
    echo
    echo "Describe ${pod}:"
    kubectl describe -n "$NAMESPACE" "$pod" 2>/dev/null || true
    echo
    echo "Logs ${pod}:"
    kubectl logs -n "$NAMESPACE" "$pod" --all-containers --tail=120 2>/dev/null || true
  done
}

on_error() {
  status=$?
  line=$1
  trap - ERR INT TERM
  echo "Command failed at line ${line} with exit status ${status}." >&2
  failure_context >&2
  if truthy "$LIFECYCLE_CREATED" && ! truthy "$HARNESS_RETAIN_ON_FAILURE" && ! truthy "$AUTOMATIC_CLEANUP_STARTED"; then
    AUTOMATIC_CLEANUP_STARTED=true
    cleanup_release >&2 || echo "Automatic harness cleanup failed; original exit status remains ${status}." >&2
  fi
  cleanup_local
  exit "$status"
}

on_signal() {
  status=$1
  trap - ERR INT TERM
  failure_context >&2
  if truthy "$LIFECYCLE_CREATED" && ! truthy "$HARNESS_RETAIN_ON_FAILURE" && ! truthy "$AUTOMATIC_CLEANUP_STARTED"; then
    AUTOMATIC_CLEANUP_STARTED=true
    cleanup_release >&2 || echo "Automatic harness cleanup failed; original signal status remains ${status}." >&2
  fi
  cleanup_local
  exit "$status"
}

trap 'on_error $LINENO' ERR
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap cleanup_local EXIT

require_chart_files() {
  [ -d "$CHART_DIR" ] || {
    echo "Missing Helm chart directory: ${CHART_DIR}" >&2
    exit 1
  }
  [ -f "$VALUES_FILE" ] || {
    echo "Missing local values file: ${VALUES_FILE}" >&2
    exit 1
  }
  [ -f "${ROOT_DIR}/Dockerfile.api" ] || {
    echo "Missing API image Dockerfile: ${ROOT_DIR}/Dockerfile.api" >&2
    exit 1
  }
  [ -f "${ROOT_DIR}/Dockerfile.ui" ] || {
    echo "Missing UI image Dockerfile: ${ROOT_DIR}/Dockerfile.ui" >&2
    exit 1
  }
  [ -f "${ROOT_DIR}/Dockerfile.gis" ] || {
    echo "Missing GIS cache image Dockerfile: ${ROOT_DIR}/Dockerfile.gis" >&2
    exit 1
  }
}

chart_lock_dependencies_present() {
  [ -f "${CHART_DIR}/Chart.lock" ] || return 1
  [ -d "${CHART_DIR}/charts" ] || return 1

  missing=0
  archives=$(awk '
    /^[[:space:]]*-[[:space:]]*name:/ {
      name = $0
      sub(/^[[:space:]]*-[[:space:]]*name:[[:space:]]*/, "", name)
      gsub(/"/, "", name)
      gsub(/\047/, "", name)
      next
    }
    name != "" && /^[[:space:]]*version:/ {
      version = $0
      sub(/^[[:space:]]*version:[[:space:]]*/, "", version)
      gsub(/"/, "", version)
      gsub(/\047/, "", version)
      print name "-" version ".tgz"
      name = ""
    }
  ' "${CHART_DIR}/Chart.lock")

  for archive in $archives; do
    [ -n "$archive" ] || continue
    if [ ! -f "${CHART_DIR}/charts/${archive}" ]; then
      echo "Missing vendored Helm dependency archive: ${CHART_DIR}/charts/${archive}" >&2
      missing=1
    fi
  done

  [ "$missing" -eq 0 ]
}

check_keycloak_operator_scope() {
  if truthy "$ALLOW_KEYCLOAK_OPERATOR_SCOPE_MISMATCH"; then
    return
  fi

  operator_lines=$(kubectl get deployments -A \
    -l app.kubernetes.io/name=keycloak-operator \
    -o jsonpath='{range .items[*]}{.metadata.namespace}{"|"}{.metadata.name}{"|"}{range .spec.template.spec.containers[*].env[*]}{.name}{"="}{.value}{";"}{end}{"\n"}{end}' 2>/dev/null || true)

  if [ -z "$operator_lines" ]; then
    echo "No Keycloak operator deployment was found with label app.kubernetes.io/name=keycloak-operator." >&2
    echo "Install the operator before running full-stack k3s validation, or set ALLOW_KEYCLOAK_OPERATOR_SCOPE_MISMATCH=true only when another operator watches ${NAMESPACE}." >&2
    exit 1
  fi

  target_namespace_operator=false
  current_namespace_only_operator=false
  while IFS='|' read -r operator_namespace _operator_name operator_env; do
    [ -n "$operator_namespace" ] || continue
    if [ "$operator_namespace" = "$NAMESPACE" ]; then
      target_namespace_operator=true
    fi
    case "$operator_env" in
      *QUARKUS_OPERATOR_SDK_CONTROLLERS_KEYCLOAKCONTROLLER_NAMESPACES=JOSDK_WATCH_CURRENT*|*QUARKUS_OPERATOR_SDK_CONTROLLERS_KEYCLOAKREALMIMPORTCONTROLLER_NAMESPACES=JOSDK_WATCH_CURRENT*)
        current_namespace_only_operator=true
        ;;
    esac
  done <<EOF
$operator_lines
EOF

  if [ "$target_namespace_operator" = false ] && [ "$current_namespace_only_operator" = true ]; then
    echo "Keycloak operator appears namespace-scoped and no operator is running in target namespace ${NAMESPACE}." >&2
    echo "Use NAMESPACE matching the operator namespace, install a cluster-wide/target-namespace operator, or set ALLOW_KEYCLOAK_OPERATOR_SCOPE_MISMATCH=true only when another reconciler is known to handle Keycloak CRs." >&2
    exit 1
  fi
}

check_preflight() {
  need kubectl
  need helm
  need jq
  need curl
  need "$DOCKER"
  require_chart_files

  run kubectl cluster-info
  nodes=$(capture kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.nodeInfo.containerRuntimeVersion}{" "}{.status.nodeInfo.osImage}{"\n"}{end}')
  printf '%s\n' "$nodes"
  if ! printf '%s\n' "$nodes" | grep -Eiq 'k3s|containerd'; then
    echo "The selected context does not look k3s-compatible. Expected k3s/containerd nodes." >&2
    exit 1
  fi

  run helm version
  run kubectl get crd clusters.postgresql.cnpg.io
  run kubectl get crd keycloaks.k8s.keycloak.org
  check_keycloak_operator_scope

  if truthy "$ENABLE_TUNNEL"; then
    [ -n "$TUNNEL_TOKEN_SECRET_NAME" ] || {
      echo "TUNNEL_TOKEN_SECRET_NAME is required when ENABLE_TUNNEL=true." >&2
      exit 1
    }
    run kubectl get namespace "$NAMESPACE"
    run kubectl get secret "$TUNNEL_TOKEN_SECRET_NAME" -n "$NAMESPACE"
  fi

  if ! truthy "$ALLOW_FLUX_MANAGED_RELEASE" && kubectl get helmrelease "$RELEASE_NAME" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "Refusing to smoke test Flux-managed HelmRelease ${NAMESPACE}/${RELEASE_NAME}." >&2
    echo "Use an isolated target, for example: NAMESPACE=tertius-smoke RELEASE_NAME=tertius-smoke $0" >&2
    echo "Or set ALLOW_FLUX_MANAGED_RELEASE=true if you intentionally want to race the GitOps controller." >&2
    exit 1
  fi

  if chart_lock_dependencies_present; then
    echo "Using vendored Helm chart dependencies from ${CHART_DIR}/charts."
  else
    run helm dependency update "$CHART_DIR"
  fi
}

buildx_gha_cache_available() {
  truthy "$BUILDX_GHA_CACHE" || return 1
  [ "$DOCKER" = "docker" ] || return 1
  "$DOCKER" buildx version >/dev/null 2>&1
}

build_image() {
  scope=$1
  dockerfile=$2
  image=$3
  shift 3

  if buildx_gha_cache_available; then
    run "$DOCKER" buildx build \
      --load \
      --cache-from "type=gha,scope=${scope}" \
      --cache-to "type=gha,mode=max,scope=${scope},ignore-error=true" \
      -f "$dockerfile" \
      -t "$image" \
      "$@" \
      "$ROOT_DIR"
    return
  fi

  run "$DOCKER" build -f "$dockerfile" -t "$image" "$@" "$ROOT_DIR"
}

build_images() {
  build_image tertius-api "${ROOT_DIR}/Dockerfile.api" "$API_IMAGE"
  build_image tertius-ui "${ROOT_DIR}/Dockerfile.ui" "$UI_IMAGE" --build-arg VITE_API_URL=/api --build-arg VITE_OTEL_ENABLED=true --build-arg VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=/otel/v1/traces
  build_image tertius-pi-agent "${ROOT_DIR}/Dockerfile.api" "$PI_AGENT_IMAGE" --target pi-agent
  build_image tertius-gis-cache "${ROOT_DIR}/Dockerfile.gis" "$GIS_CACHE_IMAGE" --target gis-cache
}

build_and_load_images() {
  build_image tertius-api "${ROOT_DIR}/Dockerfile.api" "$API_IMAGE"
  load_image "$API_IMAGE"

  build_image tertius-ui "${ROOT_DIR}/Dockerfile.ui" "$UI_IMAGE" --build-arg VITE_API_URL=/api --build-arg VITE_OTEL_ENABLED=true --build-arg VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=/otel/v1/traces
  load_image "$UI_IMAGE"

  build_image tertius-pi-agent "${ROOT_DIR}/Dockerfile.api" "$PI_AGENT_IMAGE" --target pi-agent
  load_image "$PI_AGENT_IMAGE"

  build_image tertius-gis-cache "${ROOT_DIR}/Dockerfile.gis" "$GIS_CACHE_IMAGE" --target gis-cache
  load_image "$GIS_CACHE_IMAGE"
}

k3s_ctr() {
  if command -v k3s >/dev/null 2>&1; then
    if k3s ctr "$@"; then
      return
    fi
  fi
  if [ -n "$K3S_CONTAINER" ] && command -v podman >/dev/null 2>&1 && podman container exists "$K3S_CONTAINER" >/dev/null 2>&1; then
    if podman exec "$K3S_CONTAINER" k3s ctr "$@" 2>/dev/null; then
      return
    fi
    podman exec "$K3S_CONTAINER" ctr "$@"
    return
  fi
  if [ -n "$K3S_CONTAINER" ] && command -v docker >/dev/null 2>&1 && docker container inspect "$K3S_CONTAINER" >/dev/null 2>&1; then
    if docker exec "$K3S_CONTAINER" k3s ctr "$@" 2>/dev/null; then
      return
    fi
    docker exec "$K3S_CONTAINER" ctr "$@"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -n k3s ctr "$@"
    return
  fi
  return 127
}

cluster_has_image() {
  image=$1
  if k3s_ctr -n k8s.io images ls -q 2>/dev/null | grep -Fxq "$image"; then
    return 0
  fi
  return 1
}

load_image() {
  image=$1
  tar_file=""
  if cluster_has_image "$image"; then
    echo "Image already present in k3s containerd: ${image}"
    return
  fi

  if is_registry_image "$image"; then
    run "$DOCKER" push "$image"
    return
  fi

  tar_file=$(mktemp "${TMPDIR:-/tmp}/tertius-image.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${tar_file}"
  run "$DOCKER" save -o "$tar_file" "$image"
  if truthy "$CLEAN_LOCAL_IMAGES_AFTER_LOAD"; then
    run "$DOCKER" image rm -f "$image" || true
  fi
  if [ -n "$K3S_CONTAINER" ] && command -v podman >/dev/null 2>&1 && podman container exists "$K3S_CONTAINER" >/dev/null 2>&1; then
    container_tar="/tmp/$(basename "$tar_file")"
    run podman cp "$tar_file" "${K3S_CONTAINER}:${container_tar}"
    quote_cmd podman exec "$K3S_CONTAINER" ctr -n k8s.io images import "$container_tar"
    podman exec "$K3S_CONTAINER" ctr -n k8s.io images import "$container_tar"
    run podman exec "$K3S_CONTAINER" rm -f "$container_tar"
    rm -f "$tar_file"
    TEMP_FILES="${TEMP_FILES//$tar_file/}"
    return
  fi
  if [ -n "$K3S_CONTAINER" ] && command -v docker >/dev/null 2>&1 && docker container inspect "$K3S_CONTAINER" >/dev/null 2>&1; then
    container_tar="/tmp/$(basename "$tar_file")"
    run docker cp "$tar_file" "${K3S_CONTAINER}:${container_tar}"
    quote_cmd docker exec "$K3S_CONTAINER" ctr -n k8s.io images import "$container_tar"
    docker exec "$K3S_CONTAINER" ctr -n k8s.io images import "$container_tar"
    run docker exec "$K3S_CONTAINER" rm -f "$container_tar"
    rm -f "$tar_file"
    TEMP_FILES="${TEMP_FILES//$tar_file/}"
    return
  fi
  quote_cmd k3s ctr -n k8s.io images import "$tar_file"
  if ! k3s_ctr -n k8s.io images import "$tar_file"; then
    echo "Unable to import ${image} into k3s containerd." >&2
    echo "Use a local registry tag such as localhost:5000/tertius-api:local, or run this script where k3s ctr is available." >&2
    exit 1
  fi
  rm -f "$tar_file"
  TEMP_FILES="${TEMP_FILES//$tar_file/}"
}

load_images() {
  load_image "$API_IMAGE"
  load_image "$UI_IMAGE"
  load_image "$PI_AGENT_IMAGE"
  load_image "$GIS_CACHE_IMAGE"
}

helm_set_args() {
  api_repo=$(image_repo "$API_IMAGE")
  api_tag=$(image_tag "$API_IMAGE")
  ui_repo=$(image_repo "$UI_IMAGE")
  ui_tag=$(image_tag "$UI_IMAGE")
  pi_agent_repo=$(image_repo "$PI_AGENT_IMAGE")
  pi_agent_tag=$(image_tag "$PI_AGENT_IMAGE")
  gis_cache_repo=$(image_repo "$GIS_CACHE_IMAGE")
  gis_cache_tag=$(image_tag "$GIS_CACHE_IMAGE")

  HELM_EXTRA_ARGS="
--set-string api.image.repository=${api_repo}
--set-string api.image.tag=${api_tag}
--set-string ui.image.repository=${ui_repo}
--set-string ui.image.tag=${ui_tag}
--set-string piAgent.image.repository=${pi_agent_repo}
--set-string piAgent.image.tag=${pi_agent_tag}
--set-string gisCache.image.repository=${gis_cache_repo}
--set-string gisCache.image.tag=${gis_cache_tag}
--set piAgent.enabled=${PI_AGENT_ENABLED}
--set keda.enabled=${KEDA_ENABLED}
--set app.secret.create=false
--set-string app.secretName=${APP_SECRET_NAME}
--set-string postgres.appUserSecretName=${RELEASE_NAME}-app-db
--set-string keycloak.database.appUserSecretName=${RELEASE_NAME}-keycloak-db
--set-string harnessLifecycle.leaseId=${LIFECYCLE_LEASE_ID}
--set-string valkey.dataStorage.annotations.tertius\\.io/lease-id=${LIFECYCLE_LEASE_ID}
--set-string nats.config.jetstream.fileStore.pvc.merge.metadata.annotations.tertius\\.io/lease-id=${LIFECYCLE_LEASE_ID}
"
  if truthy "$ENABLE_TUNNEL"; then
    HELM_EXTRA_ARGS="${HELM_EXTRA_ARGS}
--set cloudflared.enabled=true
--set cloudflareTunnel.enabled=true
--set-string cloudflared.tunnelTokenSecretName=${TUNNEL_TOKEN_SECRET_NAME}
--set-string cloudflared.existingSecret=${TUNNEL_TOKEN_SECRET_NAME}
--set-string cloudflareTunnel.existingSecret=${TUNNEL_TOKEN_SECRET_NAME}
"
  fi
}

helm_cmd_with_extra() {
  # shellcheck disable=SC2086
  run "$@" $HELM_EXTRA_ARGS
}

ensure_namespace() {
  quote_cmd kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml '|' kubectl apply -f - >&2
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
}

ensure_app_secret() {
  printf '+ kubectl -n %q create secret generic %q --from-literal=DATABASE_URL=<redacted> --from-literal=VALKEY_URL=<redacted> --from-literal=OIDC_CLIENT_SECRET=<redacted> --from-literal=AUTH_SESSION_SECRET=<redacted> --dry-run=client -o json | jq <add lifecycle lease annotation> | kubectl apply -f -\n' "$NAMESPACE" "$APP_SECRET_NAME" >&2
  kubectl -n "$NAMESPACE" create secret generic "$APP_SECRET_NAME" \
    --from-literal=DATABASE_URL="$APP_DATABASE_URL" \
    --from-literal=VALKEY_URL="$APP_VALKEY_URL" \
    --from-literal=OIDC_CLIENT_SECRET="$APP_OIDC_CLIENT_SECRET" \
    --from-literal=AUTH_SESSION_SECRET="$APP_AUTH_SESSION_SECRET" \
    --dry-run=client \
    -o json |
    jq --arg lease "$LIFECYCLE_LEASE_ID" \
      '.metadata.annotations = ((.metadata.annotations // {}) + {"tertius.io/lease-id": $lease})' |
    kubectl apply -f -
}

pi_auth_manifest_fields() {
  manifest_file=$1
  awk '
    function scalar(line, value) {
      value = line
      sub(/^[^:]+:[[:space:]]*/, "", value)
      if (value ~ /^".*"$/) {
        value = substr(value, 2, length(value) - 2)
      }
      return value
    }

    function reset_document() {
      kind = ""
      section = ""
      resource_name = ""
      component = ""
      storage_class = ""
    }

    function finish_document() {
      if (kind == "PersistentVolumeClaim" && component == "pi-agent-auth") {
        matches++
        selected_name = resource_name
        selected_storage_class = storage_class
      }
    }

    BEGIN { reset_document() }
    /^---[[:space:]]*$/ {
      finish_document()
      reset_document()
      next
    }
    /^kind:[[:space:]]*/ { kind = scalar($0); next }
    /^metadata:[[:space:]]*$/ { section = "metadata"; next }
    /^spec:[[:space:]]*$/ { section = "spec"; next }
    section == "metadata" && /^  name:[[:space:]]*/ {
      resource_name = scalar($0)
      next
    }
    /^    app\.kubernetes\.io\/component:[[:space:]]*/ {
      component = scalar($0)
      next
    }
    section == "spec" && /^  storageClassName:[[:space:]]*/ {
      storage_class = scalar($0)
      next
    }
    END {
      finish_document()
      if (matches != 1) {
        print "expected exactly one chart-managed Pi auth PVC" > "/dev/stderr"
        exit 5
      }
      printf "%s\t%s\n", selected_name, selected_storage_class
    }
  ' "$manifest_file"
}

render_and_install() {
  helm_set_args
  helm_cmd_with_extra helm lint "$CHART_DIR" --values "$VALUES_FILE"
  quote_cmd helm template "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" --values "$VALUES_FILE" '>/tmp/tertius-helm-template.yaml'
  # shellcheck disable=SC2086
  helm template "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" --values "$VALUES_FILE" $HELM_EXTRA_ARGS >/tmp/tertius-helm-template.yaml
  rendered_pi_auth_fields=$(pi_auth_manifest_fields /tmp/tertius-helm-template.yaml)
  IFS=$'\t' read -r PI_AUTH_CLAIM PI_AUTH_RENDERED_STORAGE_CLASS <<<"$rendered_pi_auth_fields"
  ensure_app_secret
  if helm_wait_required; then
    helm_cmd_with_extra helm upgrade --install "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" --create-namespace --values "$VALUES_FILE" --wait --timeout "$TIMEOUT"
  else
    echo "Pi auth PVC ${NAMESPACE}/${PI_AUTH_CLAIM} is awaiting its first consumer; installing without Helm --wait and checking runtime readiness explicitly."
    helm_cmd_with_extra helm upgrade --install "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" --create-namespace --values "$VALUES_FILE" --timeout "$TIMEOUT"
  fi
}

helm_wait_required() {
  truthy "$PI_AGENT_ENABLED" && return 0
  [ -n "$PI_AUTH_CLAIM" ] || return 0

  if ! phase=$(kubectl get pvc "$PI_AUTH_CLAIM" -n "$NAMESPACE" --ignore-not-found -o jsonpath='{.status.phase}' 2>/dev/null); then
    return 0
  fi
  case "$phase" in
    "") pi_auth_storage_is_wffc "$PI_AUTH_RENDERED_STORAGE_CLASS" && return 1 ;;
    Pending)
      if ! storage_class=$(kubectl get pvc "$PI_AUTH_CLAIM" -n "$NAMESPACE" -o jsonpath='{.spec.storageClassName}' 2>/dev/null); then
        return 0
      fi
      pi_auth_storage_is_wffc "$storage_class" && return 1
      ;;
    *) return 0 ;;
  esac
  return 0
}

pi_auth_storage_is_wffc() {
  storage_class=${1:-}
  if [ -z "$storage_class" ]; then
    if ! storage_classes=$(kubectl get storageclass -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{"\n"}{end}' 2>/dev/null); then
      return 1
    fi
    storage_class=$(printf '%s\n' "$storage_classes" | head -n 1)
  fi
  [ -n "$storage_class" ] || return 1
  if ! binding_mode=$(kubectl get storageclass "$storage_class" -o jsonpath='{.volumeBindingMode}' 2>/dev/null); then
    return 1
  fi
  [ "$binding_mode" = "WaitForFirstConsumer" ]
}

wait_for_rollout() {
  run kubectl wait --for=condition=Available deployment -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --timeout="$TIMEOUT"
  statefulsets=$(kubectl get statefulset -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
  [ -z "$statefulsets" ] || run kubectl rollout status -n "$NAMESPACE" $statefulsets --timeout="$TIMEOUT"
  valkey_pods=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=valkey" -o name 2>/dev/null || true)
  [ -z "$valkey_pods" ] || run kubectl wait --for=condition=Ready -n "$NAMESPACE" $valkey_pods --timeout="$TIMEOUT"
  nats_pods=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=nats" -o name 2>/dev/null || true)
  [ -z "$nats_pods" ] || run kubectl wait --for=condition=Ready -n "$NAMESPACE" $nats_pods --timeout="$TIMEOUT"
  jobs=$(kubectl get jobs -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
  [ -z "$jobs" ] || run kubectl wait --for=condition=Complete -n "$NAMESPACE" $jobs --timeout="$TIMEOUT"
  run kubectl wait --for=condition=Ready clusters.postgresql.cnpg.io -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --timeout="$TIMEOUT"
  run kubectl wait --for=condition=Ready keycloaks.k8s.keycloak.org -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --timeout="$TIMEOUT"
  if truthy "$ENABLE_TUNNEL"; then
    run kubectl wait --for=condition=Available deployment -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=cloudflared" --timeout="$TIMEOUT"
  fi
  check_release_pvcs_ready
}

first_resource_by_label() {
  kind=$1
  label=$2
  capture kubectl get "$kind" -n "$NAMESPACE" -l "$label" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

resource_named() {
  kind=$1
  name=$2
  if kubectl get "$kind" "$name" -n "$NAMESPACE" >/dev/null 2>&1; then
    printf '%s\n' "$name"
  fi
}

find_service() {
  role=$1
  name=$(first_resource_by_label svc "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=${role}")
  [ -n "$name" ] && {
    printf '%s\n' "$name"
    return
  }
  name=$(first_resource_by_label svc "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=${RELEASE_NAME}-${role}")
  [ -n "$name" ] && {
    printf '%s\n' "$name"
    return
  }
  for candidate in "${RELEASE_NAME}-${role}" "$role"; do
    name=$(resource_named svc "$candidate" || true)
    [ -n "$name" ] && {
      printf '%s\n' "$name"
      return
    }
  done
  echo "Unable to find ${role} service for release ${RELEASE_NAME}." >&2
  exit 1
}

find_pod() {
  role=$1
  name=$(first_resource_by_label pod "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=${role}")
  [ -n "$name" ] && {
    printf '%s\n' "$name"
    return
  }
  name=$(capture kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name | grep "$role" | head -1 | sed 's#pod/##')
  [ -n "$name" ] && {
    printf '%s\n' "$name"
    return
  }
  echo "Unable to find ${role} pod for release ${RELEASE_NAME}." >&2
  exit 1
}

service_port() {
  svc=$1
  port=$(capture kubectl get svc "$svc" -n "$NAMESPACE" -o jsonpath='{.spec.ports[?(@.name=="http")].port}' || true)
  [ -n "$port" ] || port=$(capture kubectl get svc "$svc" -n "$NAMESPACE" -o jsonpath='{.spec.ports[0].port}')
  printf '%s\n' "$port"
}

start_port_forward() {
  result_var=$1
  svc=$2
  local_port=$3
  remote_port=$4
  log_file=$(mktemp "${TMPDIR:-/tmp}/tertius-port-forward.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${log_file}"
  if [ "$local_port" = "0" ]; then
    port_spec=":${remote_port}"
  else
    port_spec="${local_port}:${remote_port}"
  fi
  quote_cmd kubectl port-forward --address "$PORT_FORWARD_ADDRESS" -n "$NAMESPACE" "svc/${svc}" "$port_spec" >&2
  kubectl port-forward --address "$PORT_FORWARD_ADDRESS" -n "$NAMESPACE" "svc/${svc}" "$port_spec" >"$log_file" 2>&1 &
  pid=$!
  PORT_FORWARD_PIDS="${PORT_FORWARD_PIDS} ${pid}"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
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
  cat "$log_file" >&2
  echo "Timed out waiting for port-forward to svc/${svc}." >&2
  exit 1
}

curl_expect() {
  url=$1
  pattern=$2
  body_file=$(mktemp "${TMPDIR:-/tmp}/tertius-curl.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${body_file}"
  run curl --fail --silent --show-error --max-time 20 "$url" -o "$body_file"
  if ! grep -Eiq "$pattern" "$body_file"; then
    echo "Unexpected response from ${url}. Expected pattern: ${pattern}" >&2
    cat "$body_file" >&2
    exit 1
  fi
}

curl_capture() {
  url=$1
  body_file=$(mktemp "${TMPDIR:-/tmp}/tertius-curl.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${body_file}"
  quote_cmd curl --fail --silent --show-error --max-time 20 "$url" -o "$body_file" >&2
  curl --fail --silent --show-error --max-time 20 "$url" -o "$body_file"
  printf '%s\n' "$body_file"
}

curl_expect_same_body() {
  proxied_url=$1
  direct_url=$2
  description=$3
  proxied_body=$(curl_capture "$proxied_url")
  direct_body=$(curl_capture "$direct_url")
  if ! cmp -s "$proxied_body" "$direct_body"; then
    echo "${description} did not return the same response through the frontend service and direct API service." >&2
    echo "Frontend proxied response:" >&2
    cat "$proxied_body" >&2
    echo >&2
    echo "Direct API response:" >&2
    cat "$direct_body" >&2
    exit 1
  fi
}

check_release_pvcs_ready() {
  pvc_names=$(capture kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' || true)
  [ -n "$pvc_names" ] || {
    echo "No PVCs found for release ${RELEASE_NAME}." >&2
    exit 1
  }
  printf '%s\n' "$pvc_names"
  allow_pending_pi_auth=false
  if ! truthy "$PI_AGENT_ENABLED"; then
    if storage_class=$(kubectl get pvc "$PI_AUTH_CLAIM" -n "$NAMESPACE" -o jsonpath='{.spec.storageClassName}' 2>/dev/null) && pi_auth_storage_is_wffc "$storage_class"; then
      allow_pending_pi_auth=true
    fi
  fi
  if ! printf '%s\n' "$pvc_names" | awk -v expected="$PI_AUTH_CLAIM" -v allow_pending="$allow_pending_pi_auth" '
    $2 == "Bound" { next }
    allow_pending == "true" && $1 == expected && $2 == "Pending" { next }
    { print > "/dev/stderr"; failed=1 }
    END { exit failed ? 1 : 0 }
  '; then
    echo "At least one unexpected PVC is not Bound." >&2
    exit 1
  fi
}

check_api_has_no_pvc_mount() {
  api_pod=$(find_pod api)
  api_claims=$(capture kubectl get pod "$api_pod" -n "$NAMESPACE" -o jsonpath='{range .spec.volumes[*]}{.persistentVolumeClaim.claimName}{"\n"}{end}' || true)
  if printf '%s\n' "$api_claims" | grep -q .; then
    echo "API pod ${api_pod} still mounts PVCs:" >&2
    printf '%s\n' "$api_claims" >&2
    exit 1
  fi
}

secret_field() {
  secret=$1
  field=$2
  capture kubectl get secret "$secret" -n "$NAMESPACE" -o "go-template={{ with index .data \"${field}\" }}{{ . | base64decode }}{{ end }}" 2>/dev/null || true
}

postgres_check_for_cluster() {
  cluster=$1
  secret=$(capture kubectl get cluster.postgresql.cnpg.io "$cluster" -n "$NAMESPACE" -o jsonpath='{.spec.bootstrap.initdb.secret.name}' || true)
  database=$(capture kubectl get cluster.postgresql.cnpg.io "$cluster" -n "$NAMESPACE" -o jsonpath='{.spec.bootstrap.initdb.database}' || true)
  owner=$(capture kubectl get cluster.postgresql.cnpg.io "$cluster" -n "$NAMESPACE" -o jsonpath='{.spec.bootstrap.initdb.owner}' || true)
  image_name=$(capture kubectl get cluster.postgresql.cnpg.io "$cluster" -n "$NAMESPACE" -o jsonpath='{.spec.imageName}' || true)

  for candidate in "$secret" "${cluster}-app" "${cluster}-superuser"; do
    [ -n "$candidate" ] || continue
    if kubectl get secret "$candidate" -n "$NAMESPACE" >/dev/null 2>&1; then
      secret=$candidate
      break
    fi
  done
  [ -n "$secret" ] || {
    echo "No connection secret found for CloudNativePG cluster ${cluster}." >&2
    exit 1
  }

  username=$(secret_field "$secret" username)
  password=$(secret_field "$secret" password)
  dbname=$(secret_field "$secret" dbname)
  [ -n "$dbname" ] || dbname=$database
  [ -n "$username" ] || username=$owner
  [ -n "$image_name" ] || image_name=postgres:18-alpine
  [ -n "$dbname" ] || dbname=app
  [ -n "$username" ] || username=postgres
  [ -n "$password" ] || {
    echo "Secret ${secret} does not contain a password." >&2
    exit 1
  }

  sql="select 1"
  if [ "$dbname" = "tertius" ]; then
    sql="select count(*) from projects"
  fi

  pod_name="${RELEASE_NAME}-pg-check-$(date +%s)"
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never --rm -i \
    --labels="app.kubernetes.io/instance=${RELEASE_NAME},tertius.io/harness-probe=true,tertius.io/lease-id=${LIFECYCLE_LEASE_ID}" \
    --image="$image_name" --env="PGPASSWORD=${password}" --command -- psql -h "${cluster}-rw" -U "$username" -d "$dbname" -c "$sql"
}

check_postgres() {
  clusters=$(capture kubectl get clusters.postgresql.cnpg.io -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
  count=$(printf '%s\n' "$clusters" | grep -c . || true)
  if [ "$count" -lt 2 ]; then
    echo "Expected at least two CloudNativePG clusters for app and Keycloak databases; found ${count}." >&2
    exit 1
  fi
  for cluster in $clusters; do
    postgres_check_for_cluster "$cluster"
  done
}

check_valkey() {
  svc=$(first_resource_by_label svc "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=valkey")
  [ -n "$svc" ] || svc=$(capture kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -Ei 'valkey|redis' | head -1 || true)
  [ -n "$svc" ] || {
    echo "Unable to find Valkey service for release ${RELEASE_NAME}." >&2
    exit 1
  }
  pod_name="${RELEASE_NAME}-valkey-check-$(date +%s)"
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never --rm -i \
    --labels="app.kubernetes.io/instance=${RELEASE_NAME},tertius.io/harness-probe=true,tertius.io/lease-id=${LIFECYCLE_LEASE_ID}" \
    --image="$VALKEY_CHECK_IMAGE" --command -- valkey-cli -h "$svc" PING
}

check_nats() {
  svc=$(first_resource_by_label svc "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=nats")
  [ -n "$svc" ] || svc=$(capture kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -Ei 'nats' | head -1 || true)
  [ -n "$svc" ] || {
    echo "Unable to find NATS service for release ${RELEASE_NAME}." >&2
    exit 1
  }
  pod_name="${RELEASE_NAME}-nats-check-$(date +%s)"
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never --rm -i \
    --labels="app.kubernetes.io/instance=${RELEASE_NAME},tertius.io/harness-probe=true,tertius.io/lease-id=${LIFECYCLE_LEASE_ID}" \
    --image="$NATS_CHECK_IMAGE" --command -- nats server check jetstream --server "nats://${svc}:4222"
}

keycloak_probe() {
  url=$1
  pod_name="${RELEASE_NAME}-keycloak-check-$(date +%s)"
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never \
    --labels="app.kubernetes.io/instance=${RELEASE_NAME},tertius.io/harness-probe=true,tertius.io/lease-id=${LIFECYCLE_LEASE_ID}" \
    --image="$KEYCLOAK_CHECK_IMAGE" --command -- wget -qO- "$url"
  quote_cmd kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${pod_name}" -n "$NAMESPACE" --timeout="$TIMEOUT"
  if kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${pod_name}" -n "$NAMESPACE" --timeout="$TIMEOUT"; then
    quote_cmd kubectl logs "$pod_name" -n "$NAMESPACE"
    kubectl logs "$pod_name" -n "$NAMESPACE" || true
    delete_owned_probe_pod "$pod_name" || return 1
    return 0
  fi
  kubectl logs "$pod_name" -n "$NAMESPACE" 2>/dev/null || true
  delete_owned_probe_pod "$pod_name" || return 1
  return 1
}

keycloak_service() {
  keycloak_cr=$(first_resource_by_label keycloaks.k8s.keycloak.org "app.kubernetes.io/instance=${RELEASE_NAME}")
  svc=$(first_resource_by_label svc "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=keycloak")
  [ -n "$svc" ] || svc=$(capture kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -i keycloak | head -1 || true)
  [ -n "$svc" ] || [ -z "$keycloak_cr" ] || svc=$(resource_named svc "${keycloak_cr}-service" || true)
  [ -n "$svc" ] || [ -z "$keycloak_cr" ] || svc=$(resource_named svc "$keycloak_cr" || true)
  printf '%s' "$svc"
}

check_keycloak() {
  keycloak_cr=$(first_resource_by_label keycloaks.k8s.keycloak.org "app.kubernetes.io/instance=${RELEASE_NAME}")
  if [ -n "$keycloak_cr" ] && kubectl get job "${keycloak_cr}-realm" -n "$NAMESPACE" >/dev/null 2>&1; then
    run kubectl wait --for=condition=Complete "job/${keycloak_cr}-realm" -n "$NAMESPACE" --timeout="$TIMEOUT"
  fi
  svc=$(keycloak_service)
  [ -n "$svc" ] || {
    echo "Unable to find Keycloak service for release ${RELEASE_NAME}." >&2
    exit 1
  }
  remote_port=$(service_port "$svc")
  realm_url="http://${svc}.${NAMESPACE}.svc:${remote_port}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration"
  master_url="http://${svc}.${NAMESPACE}.svc:${remote_port}/realms/master/.well-known/openid-configuration"
  if keycloak_probe "$realm_url"; then
    return
  fi
  keycloak_probe "$master_url"
}

keycloak_token() {
  svc=$(keycloak_service)
  [ -n "$svc" ] || {
    echo "Unable to find Keycloak service for token request." >&2
    exit 1
  }
  remote_port=$(service_port "$svc")
  start_port_forward KEYCLOAK_LOCAL_PORT "$svc" "$KEYCLOAK_LOCAL_PORT" "$remote_port"

  token_file=$(mktemp "${TMPDIR:-/tmp}/tertius-token.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${token_file}"
  token_url="http://127.0.0.1:${KEYCLOAK_LOCAL_PORT}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token"
  quote_cmd curl --fail --silent --show-error --max-time 20 \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=password" \
    -d "client_id=tertius-ui" \
    -d "username=${KEYCLOAK_SMOKE_USERNAME}" \
    -d "password=${KEYCLOAK_SMOKE_PASSWORD}" \
    "$token_url" \
    -o "$token_file" >&2
  token_status=$(curl --silent --show-error --max-time 20 \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=password" \
    -d "client_id=tertius-ui" \
    -d "username=${KEYCLOAK_SMOKE_USERNAME}" \
    -d "password=${KEYCLOAK_SMOKE_PASSWORD}" \
    "$token_url" \
    -o "$token_file" \
    --write-out "%{http_code}") || {
    echo "Keycloak token request failed before an HTTP response." >&2
    cat "$token_file" >&2 || true
    exit 1
  }
  if [ "$token_status" -lt 200 ] || [ "$token_status" -ge 300 ]; then
    echo "Keycloak token request returned HTTP ${token_status}." >&2
    cat "$token_file" >&2 || true
    exit 1
  fi
  COMPILE_SMOKE_TOKEN=$(python3 - "$token_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    token = json.load(f).get("access_token", "")
if not token:
    raise SystemExit("Keycloak token response did not include access_token")
print(token)
PY
)
}

smoke_test_compile_job() {
  if ! truthy "$KEDA_ENABLED"; then
    echo "KEDA_ENABLED is false; skipping compile job lifecycle smoke test."
    return
  fi

  keycloak_token
  token=$COMPILE_SMOKE_TOKEN
  request_file=$(mktemp "${TMPDIR:-/tmp}/tertius-compile-request.XXXXXX")
  response_file=$(mktemp "${TMPDIR:-/tmp}/tertius-compile-response.XXXXXX")
  status_file=$(mktemp "${TMPDIR:-/tmp}/tertius-compile-status.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${request_file} ${response_file} ${status_file}"

  python3 - "$request_file" <<'PY'
import json
import sys

payload = {
    "code": "import build123d as bd\nbox = bd.Box(10, 10, 10)\n",
    "export_format": "stl",
    "file": "design.py",
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(payload, f)
PY

  compile_url="http://127.0.0.1:${API_LOCAL_PORT}/api/intus/projects/default_purlin/compile"
  echo "+ curl --fail --silent --show-error --max-time 30 -H 'Authorization: Bearer <redacted>' -H 'Content-Type: application/json' -X POST --data-binary @${request_file} ${compile_url} -o ${response_file}" >&2
  curl --fail --silent --show-error --max-time 30 \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -X POST \
    --data-binary "@${request_file}" \
    "$compile_url" \
    -o "$response_file"

  job_id=$(python3 - "$response_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    body = json.load(f)
if body.get("success") is not True or body.get("status") != "queued" or body.get("format") != "stl":
    raise SystemExit(f"Unexpected compile enqueue response: {body}")
job_id = body.get("job_id")
if not job_id:
    raise SystemExit(f"Compile enqueue response did not include job_id: {body}")
print(job_id)
PY
)

  status_url="http://127.0.0.1:${API_LOCAL_PORT}/api/intus/projects/default_purlin/compile/jobs/${job_id}"
  for _ in $(seq 1 60); do
    echo "+ curl --fail --silent --show-error --max-time 20 -H 'Authorization: Bearer <redacted>' ${status_url} -o ${status_file}" >&2
    curl --fail --silent --show-error --max-time 20 \
      -H "Authorization: Bearer ${token}" \
      "$status_url" \
      -o "$status_file"
    status=$(python3 - "$status_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("status", ""))
PY
)
    if [ "$status" = "succeeded" ]; then
      python3 - "$status_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    body = json.load(f)
if body.get("format") != "stl" or not body.get("artifact_id") or body.get("error") is not None or body.get("error_code") is not None or body.get("retryable") is not False or not body.get("finished_at"):
    raise SystemExit(f"Unexpected successful compile job response: {body}")
print(f"Compile job {body['job_id']} succeeded with artifact {body['artifact_id']}")
PY
      return
    fi
    if [ "$status" = "failed" ]; then
      echo "Compile job ${job_id} failed:" >&2
      cat "$status_file" >&2
      exit 1
    fi
    sleep 3
  done

  echo "Timed out waiting for compile job ${job_id} to succeed. Last status:" >&2
  cat "$status_file" >&2
  exit 1
}

smoke_test_http() {
  ui_svc=$(find_service ui)
  api_svc=$(find_service api)
  ui_remote_port=$(service_port "$ui_svc")
  api_remote_port=$(service_port "$api_svc")

  start_port_forward UI_LOCAL_PORT "$ui_svc" "$UI_LOCAL_PORT" "$ui_remote_port"
  start_port_forward API_LOCAL_PORT "$api_svc" "$API_LOCAL_PORT" "$api_remote_port"

  "${ROOT_DIR}/scripts/smoke-http.sh" "http://127.0.0.1:${UI_LOCAL_PORT}" "http://127.0.0.1:${API_LOCAL_PORT}"
  write_harness_status
  echo "UI URL: http://127.0.0.1:${UI_LOCAL_PORT}"
  echo "API URL: http://127.0.0.1:${API_LOCAL_PORT}"
}

write_harness_status() {
  status_dir="${ROOT_DIR}/.tmp/harness"
  mkdir -p "$status_dir"
  {
    printf 'NAMESPACE=%q\n' "$NAMESPACE"
    printf 'RELEASE_NAME=%q\n' "$RELEASE_NAME"
    printf 'APP_SECRET_NAME=%q\n' "$APP_SECRET_NAME"
    printf 'UI_BASE_URL=%q\n' "http://127.0.0.1:${UI_LOCAL_PORT}"
    printf 'API_BASE_URL=%q\n' "http://127.0.0.1:${API_LOCAL_PORT}"
    if [ "${KEYCLOAK_LOCAL_PORT:-0}" != "0" ]; then
      printf 'KEYCLOAK_BASE_URL=%q\n' "http://127.0.0.1:${KEYCLOAK_LOCAL_PORT}"
    fi
    printf 'METRICS_BASE_URL=%q\n' "http://127.0.0.1:${METRICS_LOCAL_PORT}"
  } >"${status_dir}/k3s.env"
  echo "Wrote harness status: ${status_dir}/k3s.env"
}

check_tunnel() {
  truthy "$ENABLE_TUNNEL" || return 0
  run kubectl get secret "$TUNNEL_TOKEN_SECRET_NAME" -n "$NAMESPACE"
  run kubectl wait --for=condition=Available deployment -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=cloudflared" --timeout="$TIMEOUT"
  if [ -n "$TUNNEL_HOSTNAME" ]; then
    curl_expect "https://${TUNNEL_HOSTNAME}/" '<html|<!doctype html'
    curl_expect "https://${TUNNEL_HOSTNAME}/api/intus/health" 'healthy|ok|status'
  else
    echo "TUNNEL_HOSTNAME not set; skipping external Cloudflare hostname smoke test."
  fi
}

run_smoke_tests() {
  smoke_test_http
  check_release_pvcs_ready
  check_api_has_no_pvc_mount
  check_postgres
  check_valkey
  check_nats
  check_keycloak
  smoke_test_compile_job
  check_tunnel
}

delete_with_preconditions() {
  api_path=$1
  uid=$2
  resource_version=$3
  resource_kind=$4
  resource_name=$5
  delete_options=$(jq -cn --arg uid "$uid" --arg rv "$resource_version" \
    '{apiVersion:"v1",kind:"DeleteOptions",preconditions:{uid:$uid,resourceVersion:$rv}}') || return 1
  quote_cmd kubectl delete --raw "$api_path" -f - >&2
  if printf '%s\n' "$delete_options" | kubectl delete --raw "$api_path" -f -; then
    return 0
  fi
  if ! live_object=$(kubectl get "$resource_kind" "$resource_name" -n "$NAMESPACE" --ignore-not-found=true -o json 2>/dev/null); then
    echo "Unable to verify ${resource_kind}/${resource_name} after raw delete failure." >&2
    return 1
  fi
  if [ -z "$live_object" ]; then
    return 0
  fi
  echo "Refusing to treat failed deletion of ${resource_kind}/${resource_name} as absent." >&2
  return 1
}

inventory_test_pods() {
  probe_lease=${lease_id:-${LIFECYCLE_LEASE_ID:-}}
  [ -n "$probe_lease" ] || { echo "Harness probe lease is unavailable; refusing cleanup." >&2; return 1; }
  probe_selector="app.kubernetes.io/instance=${RELEASE_NAME},tertius.io/harness-probe=true,tertius.io/lease-id=${probe_lease}"
  if ! PROBES_JSON=$(kubectl get pods -n "$NAMESPACE" -l "$probe_selector" -o json 2>/dev/null); then
    echo "Unable to inventory harness probe Pods; refusing cleanup." >&2
    return 1
  fi
  if ! printf '%s' "$PROBES_JSON" | jq -e --arg release "$RELEASE_NAME" --arg lease "$probe_lease" '
    type == "object" and (.items | type == "array") and
    all(.items[]?;
      .metadata.labels["app.kubernetes.io/instance"] == $release and
      .metadata.labels["tertius.io/harness-probe"] == "true" and
      .metadata.labels["tertius.io/lease-id"] == $lease and
      (.metadata.name | type == "string" and length > 0) and
      (.metadata.uid | type == "string" and length > 0) and
      (.metadata.resourceVersion | type == "string" and length > 0))
  ' >/dev/null; then
    echo "Harness probe Pod inventory is malformed; refusing cleanup." >&2
    return 1
  fi
}

delete_test_pods() {
  while IFS=$'\t' read -r name uid rv; do
    [ -n "$name" ] || continue
    delete_with_preconditions "/api/v1/namespaces/${NAMESPACE}/pods/${name}" "$uid" "$rv" pod "$name" || return 1
  done < <(printf '%s' "$PROBES_JSON" | jq -r '.items[]? | [.metadata.name,.metadata.uid,.metadata.resourceVersion] | @tsv')
}

delete_owned_probe_pod() {
  probe_name=$1
  if ! probe_json=$(kubectl get pod "$probe_name" -n "$NAMESPACE" -o json 2>/dev/null); then
    echo "Unable to read harness probe Pod ${probe_name}; refusing deletion." >&2
    return 1
  fi
  if ! printf '%s' "$probe_json" | jq -e --arg name "$probe_name" --arg release "$RELEASE_NAME" \
    --arg lease "$LIFECYCLE_LEASE_ID" '
      .metadata.name == $name and
      .metadata.labels["app.kubernetes.io/instance"] == $release and
      .metadata.labels["tertius.io/harness-probe"] == "true" and
      .metadata.labels["tertius.io/lease-id"] == $lease and
      (.metadata.uid | type == "string" and length > 0) and
      (.metadata.resourceVersion | type == "string" and length > 0)
    ' >/dev/null; then
    echo "Harness probe Pod ${probe_name} ownership is invalid; refusing deletion." >&2
    return 1
  fi
  probe_uid=$(printf '%s' "$probe_json" | jq -er '.metadata.uid') || return 1
  probe_rv=$(printf '%s' "$probe_json" | jq -er '.metadata.resourceVersion') || return 1
  delete_with_preconditions "/api/v1/namespaces/${NAMESPACE}/pods/${probe_name}" "$probe_uid" "$probe_rv" pod "$probe_name"
}

resource_is_retained() {
  wanted=$1
  shift
  for candidate in "$@"; do
    [ "$candidate" != "$wanted" ] || return 0
  done
  return 1
}

operator_descendants_json() {
  roots_json=$1
  objects_json=$2
  { printf '%s\n' "$roots_json"; printf '%s\n' "$objects_json"; } | jq -s '
    .[0] as $roots |
    .[1] as $objects |
    def children($uids):
      [$objects.items[]? |
        select(any(.metadata.ownerReferences[]?; .uid as $owner | ($uids | index($owner))))];
    def expand($uids):
      children($uids) as $children |
      (($uids + [$children[].metadata.uid]) | unique) as $expanded |
      if ($expanded | length) == ($uids | length) then $expanded else expand($expanded) end;
    expand($roots) as $owned_uids |
    [$objects.items[]? |
      select(.metadata.uid as $uid | ($owned_uids | index($uid))) |
      select(.metadata.uid as $uid | ($roots | index($uid) | not)) |
      {
        kind: (.kind | ascii_downcase),
        name: .metadata.name,
        uid: .metadata.uid
      }]
  '
}

validate_expected_marker_snapshot() {
  expected_fields="${EXPECTED_HARNESS_MARKER_UID:-}${EXPECTED_HARNESS_MARKER_RESOURCE_VERSION:-}${EXPECTED_HARNESS_LEASE_ID:-}${EXPECTED_HARNESS_EXPIRES_AT:-}${EXPECTED_HARNESS_NOW_EPOCH:-}"
  [ -n "$expected_fields" ] || return 0
  for value in \
    "${EXPECTED_HARNESS_MARKER_UID:-}" \
    "${EXPECTED_HARNESS_MARKER_RESOURCE_VERSION:-}" \
    "${EXPECTED_HARNESS_LEASE_ID:-}" \
    "${EXPECTED_HARNESS_EXPIRES_AT:-}" \
    "${EXPECTED_HARNESS_NOW_EPOCH:-}"; do
    [ -n "$value" ] || { echo "Incomplete expected lifecycle marker snapshot; refusing cleanup." >&2; return 1; }
  done
  case "$EXPECTED_HARNESS_NOW_EPOCH" in
    *[!0-9]*) echo "Invalid EXPECTED_HARNESS_NOW_EPOCH; refusing cleanup." >&2; return 1 ;;
  esac
  if ! fresh_marker=$(marker_json); then
    echo "Unable to re-read lifecycle marker before mutation; refusing cleanup." >&2
    return 1
  fi
  marker_is_valid "$fresh_marker" || { echo "Lifecycle marker became invalid before mutation; refusing cleanup." >&2; return 1; }
  fresh_uid=$(printf '%s' "$fresh_marker" | jq -r '.metadata.uid')
  fresh_rv=$(printf '%s' "$fresh_marker" | jq -r '.metadata.resourceVersion')
  fresh_lease=$(printf '%s' "$fresh_marker" | jq -r '.metadata.annotations["tertius.io/lease-id"]')
  fresh_expires=$(printf '%s' "$fresh_marker" | jq -r '.metadata.annotations["tertius.io/expires-at"]')
  if [ "$fresh_uid" != "$EXPECTED_HARNESS_MARKER_UID" ] ||
     [ "$fresh_rv" != "$EXPECTED_HARNESS_MARKER_RESOURCE_VERSION" ] ||
     [ "$fresh_lease" != "$EXPECTED_HARNESS_LEASE_ID" ] ||
     [ "$fresh_expires" != "$EXPECTED_HARNESS_EXPIRES_AT" ]; then
    echo "Lifecycle marker changed after janitor inventory; refusing cleanup." >&2
    return 1
  fi
  expires_epoch=$(date -u -d "$fresh_expires" +%s 2>/dev/null) || return 1
  if [ "$expires_epoch" -gt "$EXPECTED_HARNESS_NOW_EPOCH" ]; then
    echo "Lifecycle marker is no longer expired at the janitor decision time; refusing cleanup." >&2
    return 1
  fi
}

claim_cleanup_marker() {
  discovered_descendants=$1
  validate_expected_marker_snapshot || return 1
  if ! fresh_marker=$(marker_json); then
    echo "Unable to re-read lifecycle marker for the cleanup claim; refusing cleanup." >&2
    return 1
  fi
  marker_is_valid "$fresh_marker" || {
    echo "Lifecycle marker became invalid before the cleanup claim; refusing cleanup." >&2
    return 1
  }
  marker_uid=$(printf '%s' "$fresh_marker" | jq -er '.metadata.uid') || return 1
  marker_rv=$(printf '%s' "$fresh_marker" | jq -er '.metadata.resourceVersion') || return 1
  marker_lease=$(printf '%s' "$fresh_marker" | jq -er '.metadata.annotations["tertius.io/lease-id"]') || return 1
  marker_expires=$(printf '%s' "$fresh_marker" | jq -er '.metadata.annotations["tertius.io/expires-at"]') || return 1
  marker_policy=$(printf '%s' "$fresh_marker" | jq -er '.metadata.annotations["tertius.io/cleanup-policy"]') || return 1
  case "$marker_policy" in
    delete|cleaning) ;;
    *) echo "Lifecycle marker is not eligible for cleanup claiming." >&2; return 1 ;;
  esac
  persisted_descendants=$(printf '%s' "$fresh_marker" | jq -r '.metadata.annotations["tertius.io/operator-descendants"] // "[]"') || return 1
  if ! printf '%s' "$persisted_descendants" | jq -e '
    type == "array" and all(.[]?;
      (.kind | type == "string" and length > 0) and
      (.name | type == "string" and length > 0) and
      (.uid | type == "string" and length > 0))
  ' >/dev/null; then
    echo "Lifecycle marker has malformed persisted descendant identities; refusing cleanup." >&2
    return 1
  fi
  if ! CLAIMED_OPERATOR_DESCENDANTS=$(jq -cn --argjson persisted "$persisted_descendants" \
    --argjson discovered "$discovered_descendants" '($persisted + $discovered) | unique_by(.uid)'); then
    return 1
  fi
  descendants_annotation=$(printf '%s' "$CLAIMED_OPERATOR_DESCENDANTS" | jq -c .) || return 1
  claim_patch=$(jq -cn --arg uid "$marker_uid" --arg rv "$marker_rv" --arg lease "$marker_lease" \
    --arg expires "$marker_expires" --arg policy "$marker_policy" --arg descendants "$descendants_annotation" '[
      {op:"test",path:"/metadata/uid",value:$uid},
      {op:"test",path:"/metadata/resourceVersion",value:$rv},
      {op:"test",path:"/metadata/annotations/tertius.io~1lease-id",value:$lease},
      {op:"test",path:"/metadata/annotations/tertius.io~1expires-at",value:$expires},
      {op:"test",path:"/metadata/annotations/tertius.io~1cleanup-policy",value:$policy},
      {op:"add",path:"/metadata/annotations/tertius.io~1operator-descendants",value:$descendants},
      {op:"replace",path:"/metadata/annotations/tertius.io~1cleanup-policy",value:"cleaning"}
    ]') || return 1
  quote_cmd kubectl patch configmap "$LIFECYCLE_MARKER" -n "$NAMESPACE" --type=json -p "$claim_patch" >&2
  if ! kubectl patch configmap "$LIFECYCLE_MARKER" -n "$NAMESPACE" --type=json -p "$claim_patch" >/dev/null; then
    echo "Unable to atomically claim lifecycle marker for cleanup; refusing mutation." >&2
    return 1
  fi
  CLAIMED_MARKER_UID=$marker_uid
  CLAIMED_MARKER_LEASE=$marker_lease
}

read_claimed_marker() {
  if ! CLAIMED_MARKER_JSON=$(marker_json); then
    echo "Unable to read claimed lifecycle marker; refusing finalization." >&2
    return 1
  fi
  marker_is_valid "$CLAIMED_MARKER_JSON" || { echo "Claimed lifecycle marker is invalid." >&2; return 1; }
  final_uid=$(printf '%s' "$CLAIMED_MARKER_JSON" | jq -er '.metadata.uid') || return 1
  final_lease=$(printf '%s' "$CLAIMED_MARKER_JSON" | jq -er '.metadata.annotations["tertius.io/lease-id"]') || return 1
  final_policy=$(printf '%s' "$CLAIMED_MARKER_JSON" | jq -er '.metadata.annotations["tertius.io/cleanup-policy"]') || return 1
  if [ "$final_uid" != "$CLAIMED_MARKER_UID" ] || [ "$final_lease" != "$CLAIMED_MARKER_LEASE" ] || [ "$final_policy" != cleaning ]; then
    echo "Claimed lifecycle marker identity changed; refusing finalization." >&2
    return 1
  fi
}

finalize_retention_marker() {
  retained_value=$1
  read_claimed_marker || return 1
  final_rv=$(printf '%s' "$CLAIMED_MARKER_JSON" | jq -er '.metadata.resourceVersion') || return 1
  final_expires=$(printf '%s' "$CLAIMED_MARKER_JSON" | jq -er '.metadata.annotations["tertius.io/expires-at"]') || return 1
  retention_patch=$(jq -cn --arg uid "$CLAIMED_MARKER_UID" --arg rv "$final_rv" --arg lease "$CLAIMED_MARKER_LEASE" \
    --arg expires "$final_expires" --arg retained "$retained_value" '[
      {op:"test",path:"/metadata/uid",value:$uid},
      {op:"test",path:"/metadata/resourceVersion",value:$rv},
      {op:"test",path:"/metadata/annotations/tertius.io~1lease-id",value:$lease},
      {op:"test",path:"/metadata/annotations/tertius.io~1expires-at",value:$expires},
      {op:"test",path:"/metadata/annotations/tertius.io~1cleanup-policy",value:"cleaning"},
      {op:"add",path:"/metadata/annotations/tertius.io~1retained-objects",value:$retained},
      {op:"replace",path:"/metadata/annotations/tertius.io~1cleanup-policy",value:"retain"}
    ]') || return 1
  quote_cmd kubectl patch configmap "$LIFECYCLE_MARKER" -n "$NAMESPACE" --type=json -p "$retention_patch" >&2
  kubectl patch configmap "$LIFECYCLE_MARKER" -n "$NAMESPACE" --type=json -p "$retention_patch" >/dev/null || return 1
}

cleanup_release() {
  need kubectl
  need helm
  need jq
  require_safe_destructive_target || return 1

  if ! lifecycle_json=$(marker_json); then
    echo "Unable to read lifecycle marker ${NAMESPACE}/${LIFECYCLE_MARKER}; refusing cleanup." >&2
    return 1
  fi
  if [ -n "$lifecycle_json" ]; then
    if ! marker_app_secret=$(printf '%s' "$lifecycle_json" | \
      jq -er '.metadata.annotations["tertius.io/app-secret-name"] | select(type == "string" and length > 0)' 2>/dev/null) ||
       ! valid_kubernetes_resource_name "$marker_app_secret"; then
      echo "Lifecycle marker has an invalid external Secret identity; refusing cleanup." >&2
      return 1
    fi
    APP_SECRET_NAME=$marker_app_secret
  fi
  if ! clusters_json=$(kubectl get clusters.postgresql.cnpg.io -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o json 2>/dev/null); then
    echo "Unable to inventory CNPG clusters for ${NAMESPACE}/${RELEASE_NAME}; refusing cleanup." >&2
    return 1
  fi
  if ! pvcs_json=$(kubectl get pvc -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o json 2>/dev/null); then
    echo "Unable to inventory PVCs for ${NAMESPACE}/${RELEASE_NAME}; refusing cleanup." >&2
    return 1
  fi
  if ! secret_json=$(kubectl get secret "$APP_SECRET_NAME" -n "$NAMESPACE" --ignore-not-found=true -o json 2>/dev/null); then
    echo "Unable to read external Secret metadata ${NAMESPACE}/${APP_SECRET_NAME}; refusing cleanup." >&2
    return 1
  fi
  if ! labelled_secrets_json=$(kubectl get secret -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o json 2>/dev/null); then
    echo "Unable to inventory release-labelled Secrets for ${NAMESPACE}/${RELEASE_NAME}; refusing cleanup." >&2
    return 1
  fi
  [ -n "$labelled_secrets_json" ] || labelled_secrets_json='{"items":[]}'
  if ! secrets_json=$(
    { [ -z "$secret_json" ] || printf '%s\n' "$secret_json"; printf '%s\n' "$labelled_secrets_json"; } |
      jq -s '{items: ([.[] | if has("items") then .items[] else . end] | unique_by(.metadata.uid))}'
  ); then
    echo "Unable to combine external Secret ownership inventory; refusing cleanup." >&2
    return 1
  fi
  if ! printf '%s' "$clusters_json" | jq -e 'type == "object" and (.items | type == "array")' >/dev/null ||
     ! printf '%s' "$pvcs_json" | jq -e 'type == "object" and (.items | type == "array")' >/dev/null ||
     ! printf '%s' "$secrets_json" | jq -e 'type == "object" and (.items | type == "array")' >/dev/null; then
    echo "Malformed ownership inventory for ${NAMESPACE}/${RELEASE_NAME}; refusing cleanup." >&2
    return 1
  fi

  if [ -z "$lifecycle_json" ]; then
    if ! scoped=$(kubectl get deployment,statefulset,daemonset,replicaset,controllerrevision,pod,poddisruptionbudget,service,endpoints,endpointslice,job,configmap,secret,serviceaccount,role,rolebinding,networkpolicy,scaledjob,scaledobject,clusters.postgresql.cnpg.io,keycloaks.k8s.keycloak.org,keycloakrealmimports.k8s.keycloak.org,pvc \
      -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null); then
      echo "Unable to inventory scoped resources for ${NAMESPACE}/${RELEASE_NAME}; refusing cleanup." >&2
      return 1
    fi
    if ! helm status "$RELEASE_NAME" -n "$NAMESPACE" >/dev/null 2>&1 && \
       [ -z "$secret_json" ] && [ "$(printf '%s' "$clusters_json" | jq '.items | length')" -eq 0 ] && \
       [ "$(printf '%s' "$pvcs_json" | jq '.items | length')" -eq 0 ] && [ -z "$scoped" ]; then
      helm list -n "$NAMESPACE" -o json >/dev/null || return 1
      return 0
    fi
    echo "Refusing cleanup of ${NAMESPACE}/${RELEASE_NAME}: lifecycle marker is absent; use harness-k3s.sh adopt first." >&2
    return 1
  fi

  if ! marker_is_valid "$lifecycle_json"; then
    echo "Refusing cleanup of ${NAMESPACE}/${RELEASE_NAME}: lifecycle marker is invalid." >&2
    return 1
  fi
  lease_id=$(printf '%s' "$lifecycle_json" | jq -er '.metadata.annotations["tertius.io/lease-id"]') || return 1
  if [ -n "${EXPECTED_HARNESS_LEASE_ID:-}" ] && [ "$lease_id" != "$EXPECTED_HARNESS_LEASE_ID" ]; then
    echo "Refusing cleanup: lifecycle marker lease changed after janitor inventory." >&2
    return 1
  fi
  if ! printf '%s' "$secrets_json" | jq -e --arg lease "$lease_id" '
    all(.items[]?;
      .metadata.annotations["tertius.io/lease-id"] == $lease and
      (.metadata.name | type == "string" and length > 0) and
      (.metadata.uid | type == "string" and length > 0) and
      (.metadata.resourceVersion | type == "string" and length > 0))
  ' >/dev/null; then
    echo "Refusing cleanup: one or more release Secrets are not bound to this lifecycle lease." >&2
    return 1
  fi
  if ! mismatched_data=$(
    { printf '%s' "$clusters_json"; printf '\n'; printf '%s' "$pvcs_json"; } |
      jq -sr --arg lease "$lease_id" '[.[].items[]? | select(.metadata.annotations["tertius.io/lease-id"] != $lease)] | length'
  ); then
    echo "Unable to validate data leases; refusing cleanup." >&2
    return 1
  fi
  if [ "$mismatched_data" -ne 0 ]; then
    echo "Refusing cleanup: one or more release data resources have a different lifecycle lease." >&2
    return 1
  fi
  if ! { printf '%s' "$clusters_json"; printf '\n'; printf '%s' "$pvcs_json"; } | jq -se '
    all(.[].items[]?;
      (.metadata.name | type == "string" and length > 0) and
      (.metadata.uid | type == "string" and length > 0) and
      (.metadata.resourceVersion | type == "string" and length > 0))
  ' >/dev/null; then
    echo "Release data identity is incomplete; refusing cleanup." >&2
    return 1
  fi

  if ! keycloaks_json=$(kubectl get keycloaks.k8s.keycloak.org -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o json 2>/dev/null); then
    echo "Unable to inventory Keycloak resources for ${NAMESPACE}/${RELEASE_NAME}; refusing cleanup." >&2
    return 1
  fi
  [ -n "$keycloaks_json" ] || keycloaks_json='{"items":[]}'
  if ! printf '%s' "$keycloaks_json" | jq -e 'type == "object" and (.items | type == "array")' >/dev/null; then
    echo "Malformed Keycloak inventory; refusing cleanup." >&2
    return 1
  fi
  if ! namespace_objects_json=$(kubectl get deployment,statefulset,daemonset,replicaset,controllerrevision,pod,poddisruptionbudget,service,endpoints,endpointslice,job,configmap,secret,serviceaccount,role,rolebinding,networkpolicy,scaledjob,scaledobject,keycloakrealmimports.k8s.keycloak.org,pvc \
    -n "$NAMESPACE" -o json 2>/dev/null); then
    echo "Unable to inventory operator descendants for ${NAMESPACE}/${RELEASE_NAME}; refusing cleanup." >&2
    return 1
  fi
  [ -n "$namespace_objects_json" ] || namespace_objects_json='{"items":[]}'
  cluster_root_uids=$(printf '%s' "$clusters_json" | jq '[.items[]?.metadata.uid | select(type == "string" and length > 0)]') || return 1
  keycloak_root_uids=$(printf '%s' "$keycloaks_json" | jq '[.items[]?.metadata.uid | select(type == "string" and length > 0)]') || return 1
  cluster_descendants=$(operator_descendants_json "$cluster_root_uids" "$namespace_objects_json") || return 1
  keycloak_descendants=$(operator_descendants_json "$keycloak_root_uids" "$namespace_objects_json") || return 1
  operator_descendants=$(jq -n --argjson clusters "$cluster_descendants" --argjson keycloaks "$keycloak_descendants" \
    '($clusters + $keycloaks) | unique_by(.uid)') || return 1
  retained_operator_uids='[]'

  clusters=$(printf '%s' "$clusters_json" | jq -r '.items[] | "cluster.postgresql.cnpg.io/" + .metadata.name') || return 1
  pvcs=$(printf '%s' "$pvcs_json" | jq -r '.items[] | "persistentvolumeclaim/" + .metadata.name') || return 1
  retained=""
  keep_resources=""
  retained_objects=""
  if truthy "$RETAIN_DATA"; then
    retained_descendants=$(printf '%s' "$cluster_descendants" | jq -r '.[] | .kind + "/" + .name')
    retained="${clusters} ${pvcs} ${retained_descendants}"
    keep_resources="${clusters} ${pvcs}"
    retained_operator_uids=$(printf '%s' "$cluster_descendants" | jq '[.[].uid]')
    retained_objects=$(
      jq -nr --argjson clusters "$clusters_json" --argjson pvcs "$pvcs_json" --argjson descendants "$cluster_descendants" '
        ([$clusters.items[]?, $pvcs.items[]?] |
          map((.kind // "resource") + "/" + .metadata.name + "@" + (.metadata.uid // "unknown"))) +
        ($descendants | map(.kind + "/" + .name + "@" + .uid)) |
        join(",")
      '
    )
  elif truthy "$RETAIN_AUTH"; then
    retained=$(printf '%s' "$pvcs_json" | jq -r '.items[] | select(.metadata.labels["app.kubernetes.io/component"] == "pi-agent-auth") | "persistentvolumeclaim/" + .metadata.name')
    keep_resources="$retained"
    retained_objects=$(printf '%s' "$pvcs_json" | jq -r '[.items[] | select(.metadata.labels["app.kubernetes.io/component"] == "pi-agent-auth") | "PersistentVolumeClaim/" + .metadata.name + "@" + (.metadata.uid // "unknown")] | join(",")')
  fi

  inventory_test_pods || return 1
  claim_cleanup_marker "$operator_descendants" || return 1
  operator_descendants=$CLAIMED_OPERATOR_DESCENDANTS
  delete_test_pods || return 1
  for resource in $keep_resources; do
    run kubectl annotate -n "$NAMESPACE" "$resource" helm.sh/resource-policy=keep --overwrite || return 1
  done
  run helm uninstall "$RELEASE_NAME" -n "$NAMESPACE" --ignore-not-found || return 1

  while IFS=$'\t' read -r name uid rv; do
    [ -n "$name" ] || continue
    resource="cluster.postgresql.cnpg.io/${name}"
    # shellcheck disable=SC2086
    resource_is_retained "$resource" $retained || \
      delete_with_preconditions "/apis/postgresql.cnpg.io/v1/namespaces/${NAMESPACE}/clusters/${name}" "$uid" "$rv" clusters.postgresql.cnpg.io "$name" || return 1
  done < <(printf '%s' "$clusters_json" | jq -r '.items[]? | [.metadata.name,.metadata.uid,.metadata.resourceVersion] | @tsv')
  while IFS=$'\t' read -r name uid rv; do
    [ -n "$name" ] || continue
    resource="persistentvolumeclaim/${name}"
    # shellcheck disable=SC2086
    resource_is_retained "$resource" $retained || \
      delete_with_preconditions "/api/v1/namespaces/${NAMESPACE}/persistentvolumeclaims/${name}" "$uid" "$rv" pvc "$name" || return 1
  done < <(printf '%s' "$pvcs_json" | jq -r '.items[]? | [.metadata.name,.metadata.uid,.metadata.resourceVersion] | @tsv')
  while IFS=$'\t' read -r secret_name secret_uid secret_rv; do
    [ -n "$secret_name" ] || continue
    delete_with_preconditions "/api/v1/namespaces/${NAMESPACE}/secrets/${secret_name}" "$secret_uid" "$secret_rv" secret "$secret_name" || return 1
  done < <(printf '%s' "$secrets_json" | jq -r '.items[]? | [.metadata.name,.metadata.uid,.metadata.resourceVersion] | @tsv')

  if [ -n "$retained" ]; then
    finalize_retention_marker "$retained_objects" || return 1
  fi

  if helm status "$RELEASE_NAME" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "Helm release ${NAMESPACE}/${RELEASE_NAME} remains after cleanup." >&2
    return 1
  fi
  if ! listed=$(helm list -n "$NAMESPACE" -o json); then
    echo "Unable to verify Helm release list after cleanup." >&2
    return 1
  fi
  if printf '%s' "$listed" | jq -e --arg release "$RELEASE_NAME" 'any(.[]?; .name == $release)' >/dev/null; then
    echo "Helm release ${NAMESPACE}/${RELEASE_NAME} remains in Helm list output." >&2
    return 1
  else
    listed_status=$?
    [ "$listed_status" -eq 1 ] || { echo "Malformed Helm release list output." >&2; return 1; }
  fi

  poll_attempts=${HARNESS_CLEANUP_POLL_ATTEMPTS:-30}
  case "$poll_attempts" in ""|*[!0-9]*|0) echo "HARNESS_CLEANUP_POLL_ATTEMPTS must be a positive integer." >&2; return 1 ;; esac
  nonretained=""
  for attempt in $(seq 1 "$poll_attempts"); do
    remaining=""
    for kind in deployment statefulset daemonset replicaset controllerrevision pod poddisruptionbudget service endpoints endpointslice job configmap secret \
      serviceaccount role rolebinding networkpolicy scaledjob scaledobject \
      clusters.postgresql.cnpg.io keycloaks.k8s.keycloak.org keycloakrealmimports.k8s.keycloak.org pvc; do
      if ! found=$(kubectl get "$kind" -n "$NAMESPACE" \
        -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null); then
        echo "Unable to verify ${kind} absence for ${NAMESPACE}/${RELEASE_NAME}." >&2
        return 1
      fi
      [ -z "$found" ] || remaining="${remaining}${found}
"
    done
    nonretained=""
    for resource in $remaining; do
      # shellcheck disable=SC2086
      if resource_is_retained "$resource" $retained; then
        continue
      fi
      if [ "$resource" = "configmap/${LIFECYCLE_MARKER}" ]; then
        continue
      fi
      nonretained="${nonretained}${resource}
"
    done
    while IFS=$'\t' read -r kind name uid; do
      [ -n "$kind" ] || continue
      if printf '%s' "$retained_operator_uids" | jq -e --arg uid "$uid" 'index($uid)' >/dev/null; then
        continue
      fi
      if ! live_json=$(kubectl get "$kind" "$name" -n "$NAMESPACE" --ignore-not-found=true -o json 2>/dev/null); then
        echo "Unable to verify captured ${kind}/${name} UID absence." >&2
        return 1
      fi
      [ -n "$live_json" ] || continue
      live_uid=$(printf '%s' "$live_json" | jq -er '.metadata.uid // ""') || return 1
      if [ "$live_uid" = "$uid" ]; then
        nonretained="${nonretained}${kind}/${name} uid=${uid}
"
      fi
    done < <(printf '%s' "$operator_descendants" | jq -r '.[] | [.kind, .name, .uid] | @tsv')
    [ -n "$nonretained" ] || break
    [ "$attempt" -eq "$poll_attempts" ] || sleep 1
  done
  if [ -n "$nonretained" ]; then
    echo "Cleanup left non-retained resources for ${NAMESPACE}/${RELEASE_NAME}:" >&2
    printf '%b' "$nonretained" >&2
    return 1
  fi
  if [ -z "$retained" ]; then
    read_claimed_marker || return 1
    final_marker_rv=$(printf '%s' "$CLAIMED_MARKER_JSON" | jq -er '.metadata.resourceVersion') || return 1
    delete_with_preconditions "/api/v1/namespaces/${NAMESPACE}/configmaps/${LIFECYCLE_MARKER}" \
      "$CLAIMED_MARKER_UID" "$final_marker_rv" configmap "$LIFECYCLE_MARKER" || return 1
    if ! remaining_marker=$(marker_json); then
      echo "Unable to verify lifecycle marker absence." >&2
      return 1
    fi
    [ -z "$remaining_marker" ] || { echo "Lifecycle marker remains after cleanup." >&2; return 1; }
  fi
}

main() {
  detect_container_tool
  detect_k3s_container
  apply_image_defaults

  if truthy "$CLEANUP"; then
    cleanup_release
    return
  fi

  check_preflight
  ensure_namespace
  create_lifecycle_marker
  if truthy "$CLEAN_LOCAL_IMAGES_AFTER_LOAD"; then
    build_and_load_images
  else
    build_images
    load_images
  fi
  render_and_install
  wait_for_rollout
  run_smoke_tests
}

if ! truthy "${TEST_K3S_DEPLOYMENT_LIB_ONLY:-false}"; then
  main "$@"
fi
