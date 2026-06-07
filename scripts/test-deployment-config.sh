#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/charts/tertius"
LOCAL_VALUES="${CHART_DIR}/values-local.yaml"
RELEASE_NAME="${RELEASE_NAME:-tertius}"

render_local() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --values "$LOCAL_VALUES"
}

api_url_occurrences=$((rg -n 'const serverUrl = `\$\{baseUrl\}/api/\$\{workflowBase\}`' "${ROOT_DIR}/ui/src" || true) | wc -l | tr -d ' ')
if [ "$api_url_occurrences" -ne 0 ]; then
  echo "UI launchers still append /api after VITE_API_URL; this produces /api/api/<workflow> when VITE_API_URL=/api." >&2
  exit 1
fi

rendered="$(render_local)"

if ! printf '%s\n' "$rendered" | rg -q 'kind: PersistentVolumeClaim'; then
  echo "Local Helm render did not include any PersistentVolumeClaim resources." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'name: tertius-valkey'; then
  echo "Local Helm render did not include the Valkey data PVC." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'requestedSize|storage: "1Gi"|storage: 1Gi'; then
  echo "Local Helm render did not include the expected Valkey 1Gi storage request." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'cpu: 50m'; then
  echo "Local Helm render did not include the expected Valkey CPU request." >&2
  exit 1
fi

if ! rg -q '^USER 1000:1000$' "${ROOT_DIR}/Dockerfile.api"; then
  echo "Dockerfile.api does not switch the API runtime to the non-root UID/GID 1000." >&2
  exit 1
fi
