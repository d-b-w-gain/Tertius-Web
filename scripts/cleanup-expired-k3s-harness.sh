#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEANUP_COMMAND="${HARNESS_CLEANUP_COMMAND:-${ROOT_DIR}/scripts/harness-k3s.sh}"
NOW_EPOCH="${NOW_EPOCH:-$(date -u +%s)}"
DRY_RUN=false

usage() { echo "Usage: $(basename "$0") [--dry-run]"; }
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=true ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
case "$NOW_EPOCH" in ""|*[!0-9]*) echo "NOW_EPOCH must be an epoch integer." >&2; exit 2 ;; esac

for command in kubectl jq date; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing required command: $command" >&2; exit 1; }
done
[ -x "$CLEANUP_COMMAND" ] || { echo "Cleanup command is not executable: $CLEANUP_COMMAND" >&2; exit 1; }

markers_json=$(kubectl get configmaps --all-namespaces -l tertius.io/harness-managed=true -o json) || {
  echo "Unable to list harness lifecycle markers." >&2
  exit 1
}
flux_json=$(kubectl get helmreleases.helm.toolkit.fluxcd.io --all-namespaces -o json) || {
  echo "Unable to inspect Flux HelmRelease ownership; refusing janitor cleanup." >&2
  exit 1
}
if ! jq -e 'type == "object" and (.items | type == "array")' <<<"$markers_json" >/dev/null ||
   ! jq -e 'type == "object" and (.items | type == "array")' <<<"$flux_json" >/dev/null; then
  echo "Malformed Kubernetes inventory; refusing janitor cleanup." >&2
  exit 1
fi

failures=0
mapfile -t marker_records < <(jq -c '.items[]' <<<"$markers_json")
for marker_record in "${marker_records[@]}"; do
  mapfile -t marker_fields < <(jq -r '[
    (.metadata.namespace // ""), (.metadata.name // ""),
    (.metadata.uid // ""), (.metadata.resourceVersion // ""),
    (.metadata.labels["tertius.io/harness-managed"] // ""),
    (.metadata.labels["app.kubernetes.io/instance"] // ""),
    (.metadata.annotations["tertius.io/lease-id"] // ""),
    (.metadata.annotations["tertius.io/release-name"] // ""),
    (.metadata.annotations["tertius.io/expires-at"] // ""),
    (.metadata.annotations["tertius.io/cleanup-policy"] // "")
  ] | .[]' <<<"$marker_record")
  namespace=${marker_fields[0]}
  name=${marker_fields[1]}
  marker_uid=${marker_fields[2]}
  marker_resource_version=${marker_fields[3]}
  managed=${marker_fields[4]}
  instance=${marker_fields[5]}
  lease=${marker_fields[6]}
  release=${marker_fields[7]}
  expires=${marker_fields[8]}
  policy=${marker_fields[9]}
  valid=true
  if ! [[ "$namespace" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] ||
     ! [[ "$release" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] ||
     [ "$managed" != true ] || [ "$instance" != "$release" ] ||
     [ "$name" != "${release}-harness-lifecycle" ] ||
     [ -z "$marker_uid" ] || [ -z "$marker_resource_version" ] ||
     ! [[ "$lease" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] ||
     ! [[ "$expires" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    valid=false
  fi
  if [ "$valid" = false ] || ! expires_epoch=$(date -u -d "$expires" +%s 2>/dev/null); then
    echo "Malformed lifecycle marker ${namespace}/${name}; skipping." >&2
    failures=$((failures + 1))
    continue
  fi
  if [ "$policy" = retain ]; then
    echo "Retained lifecycle tombstone ${namespace}/${name}; skipping."
    continue
  fi
  if [ "$policy" != delete ]; then
    echo "Unsupported cleanup policy on ${namespace}/${name}; skipping." >&2
    failures=$((failures + 1))
    continue
  fi
  if [ "$release" = tertius ]; then
    echo "Protected production marker ${namespace}/${name}; skipping." >&2
    failures=$((failures + 1))
    continue
  fi
  if jq -e --arg namespace "$namespace" --arg release "$release" '
      any(.items[]?;
        ((.spec.targetNamespace // .metadata.namespace) == $namespace) and
        ((.spec.releaseName // .metadata.name) == $release))
    ' <<<"$flux_json" >/dev/null; then
    echo "Flux-managed marker ${namespace}/${name}; skipping." >&2
    failures=$((failures + 1))
    continue
  else
    flux_match_status=$?
    if [ "$flux_match_status" -ne 1 ]; then
      echo "Unable to evaluate Flux ownership for ${namespace}/${name}; skipping." >&2
      failures=$((failures + 1))
      continue
    fi
  fi
  if [ "$expires_epoch" -gt "$NOW_EPOCH" ]; then
    continue
  fi
  if [ "$DRY_RUN" = true ]; then
    echo "Would clean expired harness release ${namespace}/${release}."
    continue
  fi
  echo "Cleaning expired harness release ${namespace}/${release}."
  if ! NAMESPACE="$namespace" RELEASE_NAME="$release" \
    EXPECTED_HARNESS_MARKER_UID="$marker_uid" \
    EXPECTED_HARNESS_MARKER_RESOURCE_VERSION="$marker_resource_version" \
    EXPECTED_HARNESS_LEASE_ID="$lease" EXPECTED_HARNESS_EXPIRES_AT="$expires" \
    EXPECTED_HARNESS_NOW_EPOCH="$NOW_EPOCH" \
    "$CLEANUP_COMMAND" down; then
    echo "Cleanup failed for ${namespace}/${release}; continuing." >&2
    failures=$((failures + 1))
  fi
done

[ "$failures" -eq 0 ] || { echo "Janitor completed with ${failures} refused or failed marker(s)." >&2; exit 1; }
