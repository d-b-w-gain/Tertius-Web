#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/infra/charts/tertius"
VALUES_FILE="${CHART_DIR}/values-local.yaml"

NAMESPACE="${NAMESPACE:-tertius}"
RELEASE_NAME="${RELEASE_NAME:-tertius}"
API_IMAGE="${API_IMAGE:-}"
UI_IMAGE="${UI_IMAGE:-}"
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
METRICS_LOCAL_PORT="${METRICS_LOCAL_PORT:-8428}"
TIMEOUT="${TIMEOUT:-10m}"
DOCKER="${DOCKER:-}"
K3S_CONTAINER="${K3S_CONTAINER:-}"
BUILD_TAG="${BUILD_TAG:-$(date +%Y%m%d%H%M%S)}"

CLEANUP=false
DELETE_DATA=false
PORT_FORWARD_PIDS=""
TEMP_FILES=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--cleanup] [--delete-data] [--help]

Runs the Tertius Helm chart end-to-end against the current k3s context.

Environment:
  KUBECONFIG                    Optional; kubectl uses the current context by default.
  NAMESPACE                     Default: tertius
  RELEASE_NAME                  Default: tertius
  API_IMAGE                     Default: tertius-api:local (auto-suffixed with :local-<timestamp> for fresh rollout)
  UI_IMAGE                      Default: tertius-ui:local (auto-suffixed with :local-<timestamp> for fresh rollout)
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
  METRICS_LOCAL_PORT            Default: 8428
  TIMEOUT                       Default: 10m
  DOCKER                        Default: docker when available, otherwise podman.
  K3S_CONTAINER                 Optional k3s Podman/Docker container name for image imports.

Cleanup:
  --cleanup       Uninstall the Helm release and remove test pods.
  --delete-data   With --cleanup, also delete CloudNativePG clusters and PVCs for this release.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cleanup)
      CLEANUP=true
      ;;
    --delete-data)
      DELETE_DATA=true
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

  if [ -z "$API_IMAGE" ]; then
    API_IMAGE=$(values_image_for api tertius-api:local)
    api_from_default=1
  fi
  if [ -z "$UI_IMAGE" ]; then
    UI_IMAGE=$(values_image_for ui tertius-ui:local)
    ui_from_default=1
  fi
  [ -n "$VALKEY_CHECK_IMAGE" ] || VALKEY_CHECK_IMAGE=$(values_image_for valkey valkey/valkey:9.0.0)

  if [ "$api_from_default" -eq 1 ]; then
    API_IMAGE=$(refresh_local_image_tag "$API_IMAGE")
  fi
  if [ "$ui_from_default" -eq 1 ]; then
    UI_IMAGE=$(refresh_local_image_tag "$UI_IMAGE")
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
  cleanup_local
  echo "Command failed at line ${line} with exit status ${status}." >&2
  failure_context >&2
  exit "$status"
}

trap 'on_error $LINENO' ERR
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
}

build_and_load_images() {
  build_image tertius-api "${ROOT_DIR}/Dockerfile.api" "$API_IMAGE"
  load_image "$API_IMAGE"

  build_image tertius-ui "${ROOT_DIR}/Dockerfile.ui" "$UI_IMAGE" --build-arg VITE_API_URL=/api --build-arg VITE_OTEL_ENABLED=true --build-arg VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=/otel/v1/traces
  load_image "$UI_IMAGE"
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
}

helm_set_args() {
  api_repo=$(image_repo "$API_IMAGE")
  api_tag=$(image_tag "$API_IMAGE")
  ui_repo=$(image_repo "$UI_IMAGE")
  ui_tag=$(image_tag "$UI_IMAGE")

  HELM_EXTRA_ARGS="
--set-string api.image.repository=${api_repo}
--set-string api.image.tag=${api_tag}
--set-string ui.image.repository=${ui_repo}
--set-string ui.image.tag=${ui_tag}
--set keda.enabled=${KEDA_ENABLED}
--set app.secret.create=false
--set-string app.secretName=${APP_SECRET_NAME}
--set-string postgres.appUserSecretName=${RELEASE_NAME}-app-db
--set-string keycloak.database.appUserSecretName=${RELEASE_NAME}-keycloak-db
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

ensure_app_secret() {
  quote_cmd kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml '|' kubectl apply -f - >&2
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

  printf '+ kubectl -n %q create secret generic %q --from-literal=DATABASE_URL=<redacted> --from-literal=VALKEY_URL=<redacted> --from-literal=OIDC_CLIENT_SECRET=<redacted> --from-literal=AUTH_SESSION_SECRET=<redacted> --dry-run=client -o yaml | kubectl apply -f -\n' "$NAMESPACE" "$APP_SECRET_NAME" >&2
  kubectl -n "$NAMESPACE" create secret generic "$APP_SECRET_NAME" \
    --from-literal=DATABASE_URL="$APP_DATABASE_URL" \
    --from-literal=VALKEY_URL="$APP_VALKEY_URL" \
    --from-literal=OIDC_CLIENT_SECRET="$APP_OIDC_CLIENT_SECRET" \
    --from-literal=AUTH_SESSION_SECRET="$APP_AUTH_SESSION_SECRET" \
    --dry-run=client \
    -o yaml | kubectl apply -f -
}

render_and_install() {
  helm_set_args
  helm_cmd_with_extra helm lint "$CHART_DIR" --values "$VALUES_FILE"
  quote_cmd helm template "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" --values "$VALUES_FILE" '>/tmp/tertius-helm-template.yaml'
  # shellcheck disable=SC2086
  helm template "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" --values "$VALUES_FILE" $HELM_EXTRA_ARGS >/tmp/tertius-helm-template.yaml
  ensure_app_secret
  helm_cmd_with_extra helm upgrade --install "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" --create-namespace --values "$VALUES_FILE" --wait --timeout "$TIMEOUT"
}

wait_for_rollout() {
  run kubectl wait --for=condition=Available deployment -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --timeout="$TIMEOUT"
  statefulsets=$(kubectl get statefulset -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
  [ -z "$statefulsets" ] || run kubectl rollout status -n "$NAMESPACE" $statefulsets --timeout="$TIMEOUT"
  valkey_pods=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=valkey" -o name 2>/dev/null || true)
  [ -z "$valkey_pods" ] || run kubectl wait --for=condition=Ready -n "$NAMESPACE" $valkey_pods --timeout="$TIMEOUT"
  nats_pods=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=nats" -o name 2>/dev/null || true)
  [ -z "$nats_pods" ] || run kubectl wait --for=condition=Ready -n "$NAMESPACE" $nats_pods --timeout="$TIMEOUT"
  run kubectl wait --for=condition=Ready clusters.postgresql.cnpg.io -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --timeout="$TIMEOUT"
  run kubectl wait --for=condition=Ready keycloaks.k8s.keycloak.org -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --timeout="$TIMEOUT"
  if truthy "$ENABLE_TUNNEL"; then
    run kubectl wait --for=condition=Available deployment -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=cloudflared" --timeout="$TIMEOUT"
  fi
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
  svc=$1
  local_port=$2
  remote_port=$3
  log_file=$(mktemp "${TMPDIR:-/tmp}/tertius-port-forward.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${log_file}"
  if [ "$local_port" = "0" ]; then
    port_spec=":${remote_port}"
  else
    port_spec="${local_port}:${remote_port}"
  fi
  quote_cmd kubectl port-forward -n "$NAMESPACE" "svc/${svc}" "$port_spec" >&2
  kubectl port-forward -n "$NAMESPACE" "svc/${svc}" "$port_spec" >"$log_file" 2>&1 &
  pid=$!
  PORT_FORWARD_PIDS="${PORT_FORWARD_PIDS} ${pid}"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if grep -q 'Forwarding from' "$log_file"; then
      if [ "$local_port" = "0" ]; then
        awk '
          /^Forwarding from 127\.0\.0\.1:[0-9][0-9]* -> / {
            sub(/^Forwarding from 127\.0\.0\.1:/, "")
            sub(/ -> .*$/, "")
            print
            exit
          }
        ' "$log_file"
      else
        printf '%s\n' "$local_port"
      fi
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

check_release_pvcs_bound() {
  pvc_names=$(capture kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' || true)
  [ -n "$pvc_names" ] || {
    echo "No PVCs found for release ${RELEASE_NAME}." >&2
    exit 1
  }
  printf '%s\n' "$pvc_names"
  if printf '%s\n' "$pvc_names" | awk '$2 != "Bound" { found=1 } END { exit found ? 0 : 1 }'; then
    echo "At least one PVC is not Bound." >&2
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
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never --rm -i --image="$image_name" --env="PGPASSWORD=${password}" --command -- psql -h "${cluster}-rw" -U "$username" -d "$dbname" -c "$sql"
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
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never --rm -i --image="$VALKEY_CHECK_IMAGE" --command -- valkey-cli -h "$svc" PING
}

check_nats() {
  svc=$(first_resource_by_label svc "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/name=nats")
  [ -n "$svc" ] || svc=$(capture kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -Ei 'nats' | head -1 || true)
  [ -n "$svc" ] || {
    echo "Unable to find NATS service for release ${RELEASE_NAME}." >&2
    exit 1
  }
  pod_name="${RELEASE_NAME}-nats-check-$(date +%s)"
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never --rm -i --image="$NATS_CHECK_IMAGE" --command -- nats server check jetstream --server "nats://${svc}:4222"
}

keycloak_probe() {
  url=$1
  pod_name="${RELEASE_NAME}-keycloak-check-$(date +%s)"
  run kubectl run "$pod_name" -n "$NAMESPACE" --restart=Never --image="$KEYCLOAK_CHECK_IMAGE" --command -- wget -qO- "$url"
  quote_cmd kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${pod_name}" -n "$NAMESPACE" --timeout="$TIMEOUT"
  if kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${pod_name}" -n "$NAMESPACE" --timeout="$TIMEOUT"; then
    quote_cmd kubectl logs "$pod_name" -n "$NAMESPACE"
    kubectl logs "$pod_name" -n "$NAMESPACE" || true
    quote_cmd kubectl delete pod "$pod_name" -n "$NAMESPACE" --ignore-not-found=true
    kubectl delete pod "$pod_name" -n "$NAMESPACE" --ignore-not-found=true || true
    return 0
  fi
  kubectl logs "$pod_name" -n "$NAMESPACE" 2>/dev/null || true
  kubectl delete pod "$pod_name" -n "$NAMESPACE" --ignore-not-found=true >/dev/null 2>&1 || true
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
  KEYCLOAK_LOCAL_PORT=$(start_port_forward "$svc" "$KEYCLOAK_LOCAL_PORT" "$remote_port")

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

  UI_LOCAL_PORT=$(start_port_forward "$ui_svc" "$UI_LOCAL_PORT" "$ui_remote_port")
  API_LOCAL_PORT=$(start_port_forward "$api_svc" "$API_LOCAL_PORT" "$api_remote_port")

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
  check_release_pvcs_bound
  check_api_has_no_pvc_mount
  check_postgres
  check_valkey
  check_nats
  check_keycloak
  smoke_test_compile_job
  check_tunnel
}

delete_test_pods() {
  pods=$(kubectl get pods -n "$NAMESPACE" -o name 2>/dev/null | grep -E "/${RELEASE_NAME}-(pg|valkey|nats|keycloak)-check-" || true)
  [ -z "$pods" ] || run kubectl delete -n "$NAMESPACE" $pods --ignore-not-found=true
}

cleanup_release() {
  need kubectl
  need helm

  delete_test_pods

  if ! truthy "$DELETE_DATA"; then
    clusters=$(kubectl get clusters.postgresql.cnpg.io -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
    pvcs=$(kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
    [ -z "$clusters" ] || run kubectl annotate -n "$NAMESPACE" $clusters helm.sh/resource-policy=keep --overwrite
    [ -z "$pvcs" ] || run kubectl annotate -n "$NAMESPACE" $pvcs helm.sh/resource-policy=keep --overwrite
  fi

  run helm uninstall "$RELEASE_NAME" -n "$NAMESPACE" --ignore-not-found

  if truthy "$DELETE_DATA"; then
    clusters=$(kubectl get clusters.postgresql.cnpg.io -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
    pvcs=$(kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o name 2>/dev/null || true)
    [ -z "$clusters" ] || run kubectl delete -n "$NAMESPACE" $clusters --ignore-not-found=true
    [ -z "$pvcs" ] || run kubectl delete -n "$NAMESPACE" $pvcs --ignore-not-found=true
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

main "$@"
