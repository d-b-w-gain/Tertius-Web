#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/infra/charts/tertius"
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

if ! rg -q "VITE_KEYCLOAK_CLIENT_ID \|\| 'tertius-ui'" "${ROOT_DIR}/ui/src/auth/keycloak.ts"; then
  echo "UI Keycloak auth must default to the tertius-ui client when VITE_KEYCLOAK_CLIENT_ID is not set." >&2
  exit 1
fi

if ! rg -q 'map \$http_cf_visitor \$cloudflare_proto' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Proto \$forwarded_proto' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Host \$host' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Port \$forwarded_port' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template"; then
  echo "Frontend nginx must preserve Cloudflare/original forwarded scheme, host, and port for proxied API and Keycloak requests." >&2
  exit 1
fi

rendered="$(render_local)"

if ! printf '%s\n' "$rendered" | rg -q 'kind: PersistentVolumeClaim'; then
  echo "Local Helm render did not include any PersistentVolumeClaim resources." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'ARTIFACT_ROOT: "/app/cache/tertius/artifacts"'; then
  echo "Local Helm render must set ARTIFACT_ROOT to the API PVC-backed artifact path." >&2
  exit 1
fi

if rg -q 'tertius-postgres-rw' "$LOCAL_VALUES"; then
  echo "Local values must not hardcode the app database service name; release names vary in CI and local k3s." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'APP_DB_HOST: "tertius-postgres-rw"'; then
  echo "Local Helm render must derive APP_DB_HOST from the release name." >&2
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

production_rendered="$(helm template "$RELEASE_NAME" "$CHART_DIR")"

if ! printf '%s\n' "$production_rendered" | rg -q 'hostname: "https://tertius\.johnsonyuen\.com"' || ! printf '%s\n' "$production_rendered" | rg -q 'admin: "https://tertius\.johnsonyuen\.com"'; then
  echo "Production Keycloak hostname must use the public HTTPS Tertius origin." >&2
  exit 1
fi

if ! printf '%s\n' "$production_rendered" | rg -q 'image: "ghcr\.io/d-b-w-gain/tertius-api:master-[0-9]+-[a-f0-9]{7}"'; then
  echo "Production Helm defaults do not render the expected GHCR API image." >&2
  exit 1
fi

if ! printf '%s\n' "$production_rendered" | rg -q 'image: "ghcr\.io/d-b-w-gain/tertius-ui:master-[0-9]+-[a-f0-9]{7}"'; then
  echo "Production Helm defaults do not render the expected GHCR UI image." >&2
  exit 1
fi

if ! rg -q 'ghcr\.io/d-b-w-gain/tertius-api.*"\$imagepolicy": "flux-system:tertius-api:name"' "${CHART_DIR}/values.yaml"; then
  echo "infra/charts/tertius/values.yaml is missing the Flux image policy marker for the API repository." >&2
  exit 1
fi

if ! rg -q 'master-[0-9]+-[a-f0-9]{7}.*"\$imagepolicy": "flux-system:tertius-api:tag"' "${CHART_DIR}/values.yaml"; then
  echo "infra/charts/tertius/values.yaml is missing the Flux image policy marker for the API tag." >&2
  exit 1
fi

if ! rg -q 'ghcr\.io/d-b-w-gain/tertius-ui.*"\$imagepolicy": "flux-system:tertius-ui:name"' "${CHART_DIR}/values.yaml"; then
  echo "infra/charts/tertius/values.yaml is missing the Flux image policy marker for the UI repository." >&2
  exit 1
fi

if ! rg -q 'master-[0-9]+-[a-f0-9]{7}.*"\$imagepolicy": "flux-system:tertius-ui:tag"' "${CHART_DIR}/values.yaml"; then
  echo "infra/charts/tertius/values.yaml is missing the Flux image policy marker for the UI tag." >&2
  exit 1
fi

if ! rg -q 'branches:\s*$' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q -- '- master' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'workflow_dispatch:' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'paths-ignore:' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q "'infra/charts/\\*\\*'" "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'packages: write' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml is missing the master-only trigger or GHCR package write permission." >&2
  exit 1
fi

if ! rg -q 'file: Dockerfile\.api' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'file: Dockerfile\.ui' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml must build both Dockerfile.api and Dockerfile.ui." >&2
  exit 1
fi

if ! rg -q "github\.event_name != 'push' \|\| !contains\(github\.event\.head_commit\.message, '\[skip ci\]'\)" "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml skip-ci guard must allow workflow_dispatch events without reading head_commit." >&2
  exit 1
fi

if ! rg -q 'ghcr\.io/d-b-w-gain/tertius-api:\$\{\{ steps\.vars\.outputs\.image_tag \}\}' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'ghcr\.io/d-b-w-gain/tertius-api:sha-\$\{\{ steps\.vars\.outputs\.short_sha \}\}' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml does not push the expected API image tags." >&2
  exit 1
fi

if ! rg -q 'ghcr\.io/d-b-w-gain/tertius-ui:\$\{\{ steps\.vars\.outputs\.image_tag \}\}' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'ghcr\.io/d-b-w-gain/tertius-ui:sha-\$\{\{ steps\.vars\.outputs\.short_sha \}\}' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml does not push the expected UI image tags." >&2
  exit 1
fi

if ! rg -q 'VITE_API_URL=/api' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml does not pass the expected UI API base path build argument." >&2
  exit 1
fi

if ! rg -q 'VITE_KEYCLOAK_AUTHORITY=/realms/tertius' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'VITE_KEYCLOAK_CLIENT_ID=tertius-ui' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml does not pass the expected UI Keycloak build arguments." >&2
  exit 1
fi

if ! rg -q 'GIT_COMMIT=\$\{\{ steps\.vars\.outputs\.short_sha \}\}' "${ROOT_DIR}/.github/workflows/images.yml" || ! rg -q 'GIT_COMMIT_DATE=\$\{\{ steps\.vars\.outputs\.commit_date \}\}' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml does not pass UI build metadata arguments." >&2
  exit 1
fi

for flux_file in image-repositories.yaml image-policies.yaml image-update-automation.yaml; do
  if [ ! -f "${ROOT_DIR}/infra/clusters/production/flux-system/${flux_file}" ]; then
    echo "Missing Flux image automation manifest: ${flux_file}." >&2
    exit 1
  fi

  if ! rg -q "flux-system/${flux_file}" "${ROOT_DIR}/infra/clusters/production/kustomization.yaml"; then
    echo "infra/clusters/production/kustomization.yaml does not include ${flux_file}." >&2
    exit 1
  fi
done

if rg -q '^apiVersion: image\.toolkit\.fluxcd\.io/v1beta' "${ROOT_DIR}/infra/clusters/production/flux-system"/image-*.yaml; then
  echo "Flux image automation manifests must use image.toolkit.fluxcd.io/v1, not v1beta*." >&2
  exit 1
fi

if ! rg -q 'image: ghcr\.io/d-b-w-gain/tertius-api' "${ROOT_DIR}/infra/clusters/production/flux-system/image-repositories.yaml" || ! rg -q 'image: ghcr\.io/d-b-w-gain/tertius-ui' "${ROOT_DIR}/infra/clusters/production/flux-system/image-repositories.yaml"; then
  echo "Flux ImageRepository resources must scan the expected GHCR API and UI packages." >&2
  exit 1
fi

if ! rg -F -q "pattern: '^master-(?P<run>[0-9]+)-[a-f0-9]{7}$'" "${ROOT_DIR}/infra/clusters/production/flux-system/image-policies.yaml" || ! rg -F -q "extract: '\$run'" "${ROOT_DIR}/infra/clusters/production/flux-system/image-policies.yaml" || ! rg -q 'order: asc' "${ROOT_DIR}/infra/clusters/production/flux-system/image-policies.yaml"; then
  echo "Flux ImagePolicy resources must select the newest master run tag numerically." >&2
  exit 1
fi

if ! rg -q 'branch: master' "${ROOT_DIR}/infra/clusters/production/flux-system/image-update-automation.yaml" || ! rg -q 'branch: flux-image-updates' "${ROOT_DIR}/infra/clusters/production/flux-system/image-update-automation.yaml" || ! rg -q 'path: ./infra/charts/tertius' "${ROOT_DIR}/infra/clusters/production/flux-system/image-update-automation.yaml" || ! rg -q 'strategy: Setters' "${ROOT_DIR}/infra/clusters/production/flux-system/image-update-automation.yaml" || ! rg -F -q '{{range .Changed.Objects}}{{println .}}{{end}}' "${ROOT_DIR}/infra/clusters/production/flux-system/image-update-automation.yaml"; then
  echo "Flux ImageUpdateAutomation must commit setter updates for infra/charts/tertius to the image update branch." >&2
  exit 1
fi

if ! rg -q 'branches:\s*$' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q -- '- flux-image-updates' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'pull-requests: write' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'GH_TOKEN: \$\{\{ secrets\.FLUX_IMAGE_UPDATE_PAT \}\}' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'No image update commits to promote' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'changes outside infra/charts/tertius/values.yaml' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q -- '--auto' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'Unable to create Flux image update PR automatically' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml"; then
  echo ".github/workflows/flux-image-update-pr.yml must open and auto-merge PRs for Flux image update branches." >&2
  exit 1
fi

if ! rg -q 'secretRef:\s*$' "${ROOT_DIR}/infra/clusters/production/flux-system/gitrepository.yaml" || ! rg -q 'name: tertius-web-write' "${ROOT_DIR}/infra/clusters/production/flux-system/gitrepository.yaml"; then
  echo "GitRepository tertius-web is missing the write-capable PAT secretRef." >&2
  exit 1
fi

infra_parent_line="$(rg -n '^    !/infra/$' "${ROOT_DIR}/infra/clusters/production/flux-system/gitrepository.yaml" | cut -d: -f1)"
infra_charts_line="$(rg -n '^    !/infra/charts/$' "${ROOT_DIR}/infra/clusters/production/flux-system/gitrepository.yaml" | cut -d: -f1)"
infra_clusters_line="$(rg -n '^    !/infra/clusters/$' "${ROOT_DIR}/infra/clusters/production/flux-system/gitrepository.yaml" | cut -d: -f1)"
if [ -z "$infra_parent_line" ] || [ -z "$infra_charts_line" ] || [ -z "$infra_clusters_line" ] || [ "$infra_parent_line" -ge "$infra_charts_line" ] || [ "$infra_parent_line" -ge "$infra_clusters_line" ]; then
  echo "GitRepository ignore rules must re-include /infra/ before /infra/charts/ or /infra/clusters/." >&2
  exit 1
fi

if ! rg -q 'reconcileStrategy: Revision' "${ROOT_DIR}/infra/clusters/production/tertius/helmrelease.yaml"; then
  echo "HelmRelease tertius must reconcile chart content by Git revision so Flux image tag commits are deployed." >&2
  exit 1
fi
