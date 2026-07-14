#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export TEST_K3S_DEPLOYMENT_LIB_ONLY=true
# shellcheck source=test-k3s-deployment.sh
source "${ROOT_DIR}/scripts/test-k3s-deployment.sh"

jq() {
  if [ "${1:-}" != "--slurp" ] || [ "${2:-}" != "--raw-output" ]; then
    echo "Pi auth manifest parsing must use portable jq long options." >&2
    return 2
  fi
  command jq "$@"
}

manifest_fixture=$(mktemp "${TMPDIR:-/tmp}/tertius-pi-pvc.XXXXXX")
trap 'rm -f "$manifest_fixture"' EXIT
printf '%s\n' \
  'apiVersion: v1' \
  'kind: PersistentVolumeClaim' \
  'metadata:' \
  '  name: structural-claim' \
  '  labels:' \
  '    app.kubernetes.io/component: pi-agent-auth' \
  'spec:' \
  '  storageClassName: immediate-class' \
  '  accessModes: [ReadWriteOnce]' \
  '  resources:' \
  '    requests:' \
  '      storage: 64Mi' >"$manifest_fixture"
manifest_fields=$(pi_auth_manifest_fields "$manifest_fixture")
if [ "$manifest_fields" != $'structural-claim\timmediate-class' ]; then
  echo "Pi auth manifest fields must be decoded structurally, including storageClassName." >&2
  exit 1
fi

NAMESPACE=test
RELEASE_NAME=release
PI_AGENT_ENABLED=false
PI_AUTH_CLAIM=release-pi-agent-auth
PI_AUTH_RENDERED_STORAGE_CLASS=local-path
MOCK_CLAIM_EXISTS=true
MOCK_CLAIM_PHASE=Pending
MOCK_CLAIM_STORAGE_CLASS=local-path
MOCK_DEFAULT_STORAGE_CLASS=local-path
MOCK_PVCS='release-pi-agent-auth Pending
release-postgres-1 Bound'

kubectl() {
  if [ "$1 $2" = "get pvc" ] && [ "${3:-}" = "$PI_AUTH_CLAIM" ]; then
    [ "${MOCK_PVC_API_ERROR:-false}" != true ] || return 1
    [ "$MOCK_CLAIM_EXISTS" = true ] || return 0
    case "$*" in
      *status.phase*) printf '%s\n' "$MOCK_CLAIM_PHASE" ;;
      *spec.storageClassName*) printf '%s\n' "$MOCK_CLAIM_STORAGE_CLASS" ;;
    esac
    return
  fi
  if [ "$1 $2" = "get storageclass" ] && [ "$#" -gt 2 ] && [ "${3:0:1}" != - ]; then
    [ "${MOCK_SC_API_ERROR:-false}" != true ] || return 1
    case "$3" in
      local-path) printf '%s\n' WaitForFirstConsumer ;;
      immediate-class) printf '%s\n' Immediate ;;
      *) return 1 ;;
    esac
    return
  fi
  if [ "$1 $2" = "get storageclass" ]; then
    [ "${MOCK_SC_API_ERROR:-false}" != true ] || return 1
    printf '%s\n' "$MOCK_DEFAULT_STORAGE_CLASS"
    return
  fi
  if [ "$1 $2" = "get pvc" ]; then
    printf '%s\n' "$MOCK_PVCS"
    return
  fi
  return 1
}

if helm_wait_required; then
  echo "Expected disabled Pi with its Pending chart-managed auth claim to bypass Helm --wait." >&2
  exit 1
fi
check_release_pvcs_ready

MOCK_PVCS='release-pi-agent-auth Pending
release-postgres-1 Pending'
if (check_release_pvcs_ready) >/dev/null 2>&1; then
  echo "An unexpected Pending PVC must fail readiness." >&2
  exit 1
fi

MOCK_CLAIM_EXISTS=false
MOCK_PVCS='release-postgres-1 Bound'
PI_AUTH_RENDERED_STORAGE_CLASS=immediate-class
if ! helm_wait_required; then
  echo "A fresh claim with an explicit Immediate class must ignore the WFFC default and preserve Helm --wait." >&2
  exit 1
fi

PI_AUTH_RENDERED_STORAGE_CLASS=local-path
if helm_wait_required; then
  echo "A fresh claim with an explicit WFFC class must bypass Helm --wait." >&2
  exit 1
fi

MOCK_PVC_API_ERROR=true
if ! helm_wait_required; then
  echo "A PVC API error must fail closed and preserve Helm --wait." >&2
  exit 1
fi
MOCK_PVC_API_ERROR=false

MOCK_SC_API_ERROR=true
if ! helm_wait_required; then
  echo "A storage class API error must fail closed and preserve Helm --wait." >&2
  exit 1
fi
MOCK_SC_API_ERROR=false

PI_AGENT_ENABLED=true
if ! helm_wait_required; then
  echo "Enabled Pi must preserve Helm --wait." >&2
  exit 1
fi

PI_AGENT_ENABLED=false
MOCK_CLAIM_EXISTS=true
MOCK_CLAIM_PHASE=Bound
if ! helm_wait_required; then
  echo "A bound Pi auth claim must preserve Helm --wait." >&2
  exit 1
fi

MOCK_CLAIM_PHASE=Pending
MOCK_CLAIM_STORAGE_CLASS=immediate-class
if ! helm_wait_required; then
  echo "A Pending Pi claim on a non-WFFC storage class must preserve Helm --wait." >&2
  exit 1
fi

printf '%s\n' 'k3s WFFC Helm wait tests passed'
