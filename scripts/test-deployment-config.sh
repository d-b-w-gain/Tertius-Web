#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/infra/charts/tertius"
LOCAL_VALUES="${CHART_DIR}/values-local.yaml"
RELEASE_NAME="${RELEASE_NAME:-tertius}"

render_local() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --values "$LOCAL_VALUES"
}

render_default() {
  helm template "$RELEASE_NAME" "$CHART_DIR"
}

render_keda_disabled() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set keda.enabled=false
}

render_compile_strategy_accurate() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set compileJobs.scalingStrategy=accurate
}

render_app_secret_created() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.llmSecret.create=true \
    --set-string app.llmSecret.apiKey=openai-compatible-test-key \
    --set-string app.llmSecret.fileEditSystemPrompt='test file edit prompt'
}

render_app_secret_created_without_prompt() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.llmSecret.create=true \
    --set-string app.llmSecret.apiKey=openai-compatible-test-key
}

render_network_policy_enabled() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set networkPolicy.enabled=true
}

render_network_policy_disabled() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set networkPolicy.enabled=false
}

extract_render_doc() {
  local content="$1"
  local kind_pattern="$2"
  local extra_pattern="${3:-}"

  printf '%s\n' "$content" | awk -v kind_pattern="$kind_pattern" -v extra_pattern="$extra_pattern" '
    BEGIN { doc = "" }
    /^---$/ {
      if (doc ~ kind_pattern && (extra_pattern == "" || doc ~ extra_pattern)) print doc
      doc = ""
      next
    }
    { doc = doc $0 "\n" }
    END {
      if (doc ~ kind_pattern && (extra_pattern == "" || doc ~ extra_pattern)) print doc
    }
  '
}

api_url_occurrences=$((rg -n 'const serverUrl = `\$\{baseUrl\}/api/\$\{workflowBase\}`' "${ROOT_DIR}/ui/src" || true) | wc -l | tr -d ' ')
if [ "$api_url_occurrences" -ne 0 ]; then
  echo "UI launchers still append /api after VITE_API_URL; this produces /api/api/<workflow> when VITE_API_URL=/api." >&2
  exit 1
fi

if rg -q 'oidc-client-ts|UserManager|signinSilent|signinRedirect|signinRedirectCallback|Authorization.*Bearer' "${ROOT_DIR}/ui/src/auth" "${ROOT_DIR}/ui/src/api"; then
  echo "UI auth must not use browser OIDC clients, browser refresh flows, or bearer-token headers." >&2
  exit 1
fi

if rg -q 'VITE_KEYCLOAK_AUTHORITY|VITE_KEYCLOAK_CLIENT_ID' "${ROOT_DIR}/Dockerfile.ui"; then
  echo "UI image build must not bake browser Keycloak/OIDC client settings; auth is handled by the API BFF." >&2
  exit 1
fi

if rg -q 'userStore: new WebStorageStateStore\(\{ store: window.localStorage \}\)' "${ROOT_DIR}/ui/src/auth" "${ROOT_DIR}/ui/src/api"; then
  echo "UI auth must not persist OIDC tokens in localStorage; browser auth should use the API cookie session." >&2
  exit 1
fi

if ! rg -q '/api/auth/login' "${ROOT_DIR}/ui/src/auth/AuthProvider.tsx" || ! rg -q '/api/auth/me' "${ROOT_DIR}/ui/src/auth/AuthProvider.tsx" || ! rg -q '/api/auth/logout' "${ROOT_DIR}/ui/src/auth/AuthProvider.tsx"; then
  echo "UI auth must use API BFF login, me, and logout endpoints." >&2
  exit 1
fi

if ! rg -q 'local-k3s-sync-llm-env-wsl.sh' "${ROOT_DIR}/scripts/local-k3s-start-wsl.sh" || ! rg -q 'local-k3s-sync-llm-env-wsl.sh' "${ROOT_DIR}/scripts/local-k3s-patch-api.ps1"; then
  echo "Local k3s start and API patch helpers must sync local API-only LLM settings automatically." >&2
  exit 1
fi

if ! rg -q 'kubectl -n "\$NAMESPACE" set env "deployment/\$\{DEPLOYMENT\}" --containers=api' "${ROOT_DIR}/scripts/local-k3s-sync-llm-env-wsl.sh" || ! rg -q 'kubectl -n "\$NAMESPACE" create secret generic "\$LLM_SECRET_NAME"' "${ROOT_DIR}/scripts/local-k3s-sync-llm-env-wsl.sh"; then
  echo "Local k3s LLM sync must apply model/base URL to the API deployment and credentials to the dedicated LLM Secret." >&2
  exit 1
fi

if ! rg -q 'map \$http_cf_visitor \$cloudflare_proto' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'map \$http_host \$forwarded_host_port' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header Host \$http_host' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Proto \$forwarded_proto' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Host \$http_host' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Port \$forwarded_port' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template"; then
  echo "Frontend nginx must preserve Cloudflare/original forwarded scheme, host, and port for proxied API and Keycloak requests." >&2
  exit 1
fi

rendered="$(render_local)"
default_rendered="$(render_default)"
keda_disabled_rendered="$(render_keda_disabled)"
compile_strategy_accurate_rendered="$(render_compile_strategy_accurate)"
app_secret_rendered="$(render_app_secret_created)"
app_secret_without_prompt_rendered="$(render_app_secret_created_without_prompt)"
network_policy_enabled_rendered="$(render_network_policy_enabled)"
network_policy_disabled_rendered="$(render_network_policy_disabled)"
scaled_job="$(extract_render_doc "$rendered" 'kind: ScaledJob')"
default_scaled_job="$(extract_render_doc "$default_rendered" 'kind: ScaledJob')"
compile_strategy_accurate_scaled_job="$(extract_render_doc "$compile_strategy_accurate_rendered" 'kind: ScaledJob')"
app_configmap="$(extract_render_doc "$rendered" 'kind: ConfigMap' 'name: tertius-config')"
api_with_llm_secret="$(extract_render_doc "$app_secret_rendered" 'app.kubernetes.io/component: api')"
api_with_llm_secret_without_prompt="$(extract_render_doc "$app_secret_without_prompt_rendered" 'app.kubernetes.io/component: api')"
ui_with_llm_secret="$(extract_render_doc "$app_secret_rendered" 'app.kubernetes.io/component: ui')"
compile_job_network_policy="$(extract_render_doc "$network_policy_enabled_rendered" 'kind: NetworkPolicy' 'name: tertius-compile-job')"
compile_job_network_policy_disabled="$(extract_render_doc "$network_policy_disabled_rendered" 'kind: NetworkPolicy' 'name: tertius-compile-job')"

if ! printf '%s\n' "$rendered" | rg -q 'kind: PersistentVolumeClaim'; then
  echo "Local Helm render did not include any PersistentVolumeClaim resources." >&2
  exit 1
fi

if printf '%s\n' "$rendered" | rg -q 'name: tertius-api-cache|claimName: tertius-api-cache|mountPath: /app/cache/tertius|ARTIFACT_ROOT'; then
  echo "Local Helm render must not include the API artifact PVC, API cache mount, or ARTIFACT_ROOT." >&2
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

if ! printf '%s\n' "$rendered" | rg -q 'app.kubernetes.io/name: nats'; then
  echo "Local Helm render did not include NATS resources." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'NATS_URL: "nats://tertius-nats:4222"'; then
  echo "Local Helm render did not derive the expected release-local NATS_URL." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'KEYCLOAK_ISSUER: "http://keycloak.localhost/realms/tertius"' || ! printf '%s\n' "$rendered" | rg -q 'KEYCLOAK_JWKS_URL_OVERRIDE: "http://tertius-keycloak-service:8080/realms/tertius/protocol/openid-connect/certs"'; then
  echo "Local ConfigMap must validate the public Keycloak issuer while fetching JWKS through the in-cluster service URL." >&2
  exit 1
fi

if ! printf '%s\n' "$app_configmap" | rg -q 'KEYCLOAK_ACCESS_TOKEN_LIFESPAN_SECONDS: "300"' || ! printf '%s\n' "$app_configmap" | rg -q 'KEYCLOAK_SSO_SESSION_IDLE_TIMEOUT_SECONDS: "604800"' || ! printf '%s\n' "$app_configmap" | rg -q 'KEYCLOAK_SSO_SESSION_MAX_LIFESPAN_SECONDS: "2592000"' || ! printf '%s\n' "$app_configmap" | rg -q 'KEYCLOAK_CLIENT_SESSION_IDLE_TIMEOUT_SECONDS: "604800"' || ! printf '%s\n' "$app_configmap" | rg -q 'KEYCLOAK_CLIENT_SESSION_MAX_LIFESPAN_SECONDS: "2592000"'; then
  echo "ConfigMap must render Keycloak session lifetime settings." >&2
  exit 1
fi

if ! printf '%s\n' "$app_configmap" | rg -q 'AUTH_COOKIE_SECURE: "false"' || ! printf '%s\n' "$app_configmap" | rg -q 'AUTH_SESSION_IDLE_SECONDS: "604800"' || ! printf '%s\n' "$app_configmap" | rg -q 'AUTH_SESSION_MAX_SECONDS: "2592000"'; then
  echo "ConfigMap must render API cookie session settings." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'accessTokenLifespan: 300' || ! printf '%s\n' "$rendered" | rg -q 'ssoSessionIdleTimeout: 604800' || ! printf '%s\n' "$rendered" | rg -q 'ssoSessionMaxLifespan: 2592000' || ! printf '%s\n' "$rendered" | rg -q 'clientSessionIdleTimeout: 604800' || ! printf '%s\n' "$rendered" | rg -q 'clientSessionMaxLifespan: 2592000'; then
  echo "Keycloak RealmImport must apply the configured rolling one-week session idle window and refresh hard cap." >&2
  exit 1
fi

if ! rg -q 'LLM_MODELS_JSON: ".*kimi-k2.7-code.*minimax-m3' <<<"$rendered" || ! rg -q 'LLM_DEFAULT_MODEL_ID: "kimi-k2.7-code"' <<<"$rendered" || ! rg -q 'LLM_DAILY_BUDGET_USD: "2.00"' <<<"$rendered"; then
  echo "ConfigMap must render the LLM model catalog, default model, and daily dollar budget." >&2
  exit 1
fi

if ! rg -q 'LLM_USER_RATE_LIMIT_PER_MINUTE: "10"' <<<"$rendered" || ! rg -q 'LLM_TENANT_DAILY_TOKEN_QUOTA: "3200000"' <<<"$rendered" || ! rg -q 'LLM_USER_DAILY_TOKEN_QUOTA: "3200000"' <<<"$rendered"; then
  echo "ConfigMap must render paid LLM rate and quota settings." >&2
  exit 1
fi

if ! rg -q 'LLM_FILE_EDIT_MAX_OUTPUT_TOKENS: "65536"' <<<"$rendered" || ! rg -q 'LLM_FILE_EDIT_MAX_CONTEXT_FILES: "20"' <<<"$rendered" || ! rg -q 'LLM_FILE_EDIT_MAX_CONTEXT_CHARS: "80000"' <<<"$rendered"; then
  echo "ConfigMap must render file-edit-specific LLM output and context limits." >&2
  exit 1
fi

if printf '%s\n' "$app_configmap" | rg -q 'LLM_API_KEY|LLM_FILE_EDIT_SYSTEM_PROMPT|AUTH_SESSION_SECRET|OIDC_CLIENT_SECRET'; then
  echo "ConfigMap must not render LLM provider secrets, prompts, or auth client/session secrets." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'AUTH_SESSION_SECRET: "local-auth-session-secret-change-me"'; then
  echo "Local app Secret must render AUTH_SESSION_SECRET for stable cookie-backed auth sessions." >&2
  exit 1
fi

if ! printf '%s\n' "$app_secret_rendered" | rg -q 'kind: Secret' || ! printf '%s\n' "$app_secret_rendered" | rg -q 'LLM_API_KEY: "openai-compatible-test-key"' || ! printf '%s\n' "$app_secret_rendered" | rg -q 'LLM_FILE_EDIT_SYSTEM_PROMPT: "test file edit prompt"'; then
  echo "Dedicated LLM Secret must render LLM_API_KEY and LLM_FILE_EDIT_SYSTEM_PROMPT when app.llmSecret.create=true." >&2
  exit 1
fi

if ! printf '%s\n' "$app_secret_without_prompt_rendered" | rg -q 'kind: Secret' || ! printf '%s\n' "$app_secret_without_prompt_rendered" | rg -q 'LLM_API_KEY: "openai-compatible-test-key"' || printf '%s\n' "$app_secret_without_prompt_rendered" | rg -q 'LLM_FILE_EDIT_SYSTEM_PROMPT:'; then
  echo "Dedicated LLM Secret must omit LLM_FILE_EDIT_SYSTEM_PROMPT when no prompt value is configured." >&2
  exit 1
fi

if ! printf '%s\n' "$api_with_llm_secret" | rg -q 'name: LLM_API_KEY' || ! printf '%s\n' "$api_with_llm_secret" | rg -q 'key: LLM_API_KEY' || ! printf '%s\n' "$api_with_llm_secret" | rg -q 'name: LLM_FILE_EDIT_SYSTEM_PROMPT' || ! printf '%s\n' "$api_with_llm_secret" | rg -q 'key: LLM_FILE_EDIT_SYSTEM_PROMPT'; then
  echo "API Deployment must reference LLM_API_KEY and LLM_FILE_EDIT_SYSTEM_PROMPT from the dedicated LLM Secret." >&2
  exit 1
fi

if ! printf '%s\n' "$api_with_llm_secret_without_prompt" | rg -q 'name: LLM_FILE_EDIT_SYSTEM_PROMPT' || ! printf '%s\n' "$api_with_llm_secret_without_prompt" | rg -A 5 'name: LLM_FILE_EDIT_SYSTEM_PROMPT' | rg -q 'optional: true'; then
  echo "API Deployment must keep the LLM_FILE_EDIT_SYSTEM_PROMPT secret key reference optional." >&2
  exit 1
fi

if printf '%s\n' "$ui_with_llm_secret" | rg -q 'LLM_API_KEY|LLM_FILE_EDIT_SYSTEM_PROMPT|LLM_MODELS_JSON|LLM_DEFAULT_MODEL_ID|LLM_DAILY_BUDGET_USD|BILLING_LLM_USAGE_SUBJECT|llm|envFrom:|configMapRef:|secretRef:'; then
  echo "UI Deployment must not receive or reference LLM provider credentials." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'directAccessGrantsEnabled: true' || ! printf '%s\n' "$rendered" | rg -q 'username: "demo"'; then
  echo "Local Helm render must include the k3s smoke user and direct access grant support." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'publicClient: true'; then
  echo "Local Helm render must keep the UI OIDC client public for local PKCE auth." >&2
  exit 1
fi

if printf '%s\n' "$default_rendered" | rg -q 'directAccessGrantsEnabled: true|username: "demo"'; then
  echo "Default Helm render must not enable the k3s smoke user or direct access grants." >&2
  exit 1
fi

if ! printf '%s\n' "$default_rendered" | rg -q 'publicClient: false'; then
  echo "Default Helm render must configure the UI OIDC client as confidential for API BFF auth." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'kind: ScaledJob'; then
  echo "Local Helm render did not include the compile KEDA ScaledJob." >&2
  exit 1
fi

if printf '%s\n' "$keda_disabled_rendered" | rg -q 'kind: ScaledJob'; then
  echo "Helm render with keda.enabled=false must not include the compile KEDA ScaledJob." >&2
  exit 1
fi

if printf '%s\n' "$rendered" | rg -q 'kind: Deployment' && printf '%s\n' "$rendered" | rg -q 'app.kubernetes.io/component: compile-worker'; then
  echo "Local Helm render must not include the old compile-worker Deployment." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'type: nats-jetstream'; then
  echo "Compile ScaledJob must use the KEDA nats-jetstream scaler." >&2
  exit 1
fi

if printf '%s\n' "$scaled_job" | rg -q 'strategy: eager|scalingStrategy:'; then
  echo "Compile ScaledJob must omit scalingStrategy by default so KEDA uses its non-eager default behavior for single queued compiles." >&2
  exit 1
fi

if ! printf '%s\n' "$compile_strategy_accurate_scaled_job" | rg -q 'strategy: accurate'; then
  echo "Compile ScaledJob must allow overriding the KEDA scaling strategy from values." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'natsServerMonitoringEndpoint: "tertius-nats-headless.default.svc.cluster.local:8222"'; then
  echo "Compile ScaledJob must point KEDA at the NATS headless service monitoring endpoint." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'app.kubernetes.io/component: compile-job'; then
  echo "Compile ScaledJob must label pods with app.kubernetes.io/component: compile-job." >&2
  exit 1
fi

if ! printf '%s\n' "$default_scaled_job" | rg -q 'runtimeClassName: "gvisor"'; then
  echo "Compile ScaledJob must use the gvisor RuntimeClass in default values." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'automountServiceAccountToken: false'; then
  echo "Compile ScaledJob must not mount a service account token." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'backoffLimit: 0' || ! printf '%s\n' "$scaled_job" | rg -q 'activeDeadlineSeconds:'; then
  echo "Compile ScaledJob must render backoffLimit: 0 and activeDeadlineSeconds." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'command: \["sh", "/app/server/start-compile-job.sh"\]'; then
  echo "Compile ScaledJob must run the one-shot compile job startup script." >&2
  exit 1
fi

if printf '%s\n' "$scaled_job" | rg -q 'envFrom:|secretRef:|APP_DB_PASSWORD|APP_DB_OWNER|APP_DB_HOST|APP_DB_NAME|DATABASE_URL|AUTH_SESSION_SECRET|OIDC_CLIENT_SECRET|LLM_API_KEY|LLM_FILE_EDIT_SYSTEM_PROMPT|LLM_MODELS_JSON|LLM_DEFAULT_MODEL_ID|LLM_DAILY_BUDGET_USD'; then
  echo "Compile ScaledJob must not receive app secrets, database environment, or LLM provider configuration." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'COMPILE_STREAM_NAME' || ! printf '%s\n' "$scaled_job" | rg -q 'COMPILE_RESULT_SUBJECT'; then
  echo "Local Helm render did not include compile job NATS stream/result configuration." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'COMPILE_ACK_WAIT_SECONDS: "900"' || ! printf '%s\n' "$scaled_job" | rg -A 1 'name: COMPILE_ACK_WAIT_SECONDS' | rg -q 'value: "900"'; then
  echo "Compile ack wait must render as 900 seconds so it exceeds compile timeout plus publish/ack margin." >&2
  exit 1
fi

if printf '%s\n' "$rendered" | rg -q 'COMPILE_(REQUEST|RESULT)_MAX_BYTES: "?[0-9]+e[+-]?[0-9]+"?' || printf '%s\n' "$scaled_job" | rg -q 'value: "?[0-9]+e[+-]?[0-9]+"?'; then
  echo "Compile byte limits must render as plain integer strings, not scientific notation." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'COMPILE_REQUEST_MAX_BYTES: "8388608"' || ! printf '%s\n' "$rendered" | rg -q 'COMPILE_RESULT_MAX_BYTES: "33554432"'; then
  echo "ConfigMap compile byte limits must render request as \"8388608\" and result as \"33554432\"." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -q 'name: COMPILE_REQUEST_MAX_BYTES' || ! printf '%s\n' "$scaled_job" | rg -A 1 'name: COMPILE_REQUEST_MAX_BYTES' | rg -q 'value: "8388608"' || ! printf '%s\n' "$scaled_job" | rg -A 1 'name: COMPILE_RESULT_MAX_BYTES' | rg -q 'value: "33554432"'; then
  echo "Compile ScaledJob byte limits must render request as \"8388608\" and result as \"33554432\"." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q '"max_payload": 33554432'; then
  echo "NATS must render max_payload 33554432 so it can accept larger compile result messages." >&2
  exit 1
fi

if ! printf '%s\n' "$compile_job_network_policy" | rg -q 'name: tertius-compile-job' || ! printf '%s\n' "$compile_job_network_policy" | rg -q 'port: 4222'; then
  echo "Helm render with networkPolicy.enabled=true did not include the NATS-only compile Job NetworkPolicy." >&2
  exit 1
fi

if ! printf '%s\n' "$compile_job_network_policy" | rg -q 'app.kubernetes.io/instance: tertius' || ! printf '%s\n' "$compile_job_network_policy" | rg -q 'app.kubernetes.io/component: nats'; then
  echo "Compile Job NetworkPolicy must restrict NATS egress to release-local NATS pods." >&2
  exit 1
fi

if [ -n "$compile_job_network_policy_disabled" ]; then
  echo "Compile Job NetworkPolicy must not render when global networkPolicy.enabled=false." >&2
  exit 1
fi

if ! rg -q 'jetstream' <<<"$rendered"; then
  echo "Local Helm render did not include JetStream configuration." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'claimName: .*js|storageClassName: local-path'; then
  echo "Local Helm render did not include the expected NATS fileStore PVC configuration." >&2
  exit 1
fi

if rg -q 'nats://tertius-nats:4222' "$LOCAL_VALUES"; then
  echo "Local values must not hardcode the NATS service URL; release names vary in CI and local k3s." >&2
  exit 1
fi

if [ ! -f "${CHART_DIR}/Chart.lock" ]; then
  echo "Missing Helm Chart.lock; run helm dependency update infra/charts/tertius." >&2
  exit 1
fi

missing_dependency_archive=0
for archive in $(awk '
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
' "${CHART_DIR}/Chart.lock"); do
  if [ ! -f "${CHART_DIR}/charts/${archive}" ]; then
    echo "Missing vendored Helm dependency archive: infra/charts/tertius/charts/${archive}" >&2
    missing_dependency_archive=1
  fi
done
if [ "$missing_dependency_archive" -ne 0 ]; then
  exit 1
fi

if ! rg -q 'requestedSize|storage: "1Gi"|storage: 1Gi' <<<"$rendered"; then
  echo "Local Helm render did not include the expected Valkey 1Gi storage request." >&2
  exit 1
fi

if ! rg -q 'cpu: 50m' <<<"$rendered"; then
  echo "Local Helm render did not include the expected Valkey CPU request." >&2
  exit 1
fi

if ! rg -q '^USER 1000:1000$' "${ROOT_DIR}/Dockerfile.api"; then
  echo "Dockerfile.api does not switch the API runtime to the non-root UID/GID 1000." >&2
  exit 1
fi

if ! rg -q -- '--version 2\.20\.1' "${ROOT_DIR}/.github/workflows/chart-tests.yml"; then
  echo ".github/workflows/chart-tests.yml must pin the KEDA Helm chart version used by CI." >&2
  exit 1
fi

if ! rg -q 'docker/setup-buildx-action@v3' "${ROOT_DIR}/.github/workflows/chart-tests.yml" || ! rg -q 'BUILDX_GHA_CACHE: "true"' "${ROOT_DIR}/.github/workflows/chart-tests.yml"; then
  echo ".github/workflows/chart-tests.yml must enable Buildx and opt the k3s smoke image builds into the GitHub Actions cache." >&2
  exit 1
fi

if ! rg -q 'type=gha,scope=\$\{scope\}' "${ROOT_DIR}/scripts/test-k3s-deployment.sh" || ! rg -q 'build_image tertius-api' "${ROOT_DIR}/scripts/test-k3s-deployment.sh" || ! rg -q 'build_image tertius-ui' "${ROOT_DIR}/scripts/test-k3s-deployment.sh" || ! rg -q -- '--load' "${ROOT_DIR}/scripts/test-k3s-deployment.sh"; then
  echo "scripts/test-k3s-deployment.sh must build k3s smoke images with Buildx GHA cache and --load when enabled." >&2
  exit 1
fi

production_rendered="$(helm template "$RELEASE_NAME" "$CHART_DIR")"

if ! printf '%s\n' "$production_rendered" | rg -q 'hostname: "https://tertius\.johnsonyuen\.com"' || ! printf '%s\n' "$production_rendered" | rg -q 'admin: "https://tertius\.johnsonyuen\.com"'; then
  echo "Production Keycloak hostname must use the public HTTPS Tertius origin." >&2
  exit 1
fi

if ! printf '%s\n' "$production_rendered" | rg -q 'KEYCLOAK_ISSUER: "https://tertius\.johnsonyuen\.com/realms/tertius"' || ! printf '%s\n' "$production_rendered" | rg -q 'KEYCLOAK_JWKS_URL_OVERRIDE: "http://tertius-keycloak-service:8080/realms/tertius/protocol/openid-connect/certs"'; then
  echo "Production ConfigMap must validate the public Keycloak issuer while fetching JWKS through the in-cluster service URL." >&2
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

if printf '%s\n' "$production_rendered" | rg -C 3 -i 'app.kubernetes.io/name: nats|name: nats' | rg -q 'NodePort|LoadBalancer|Ingress|cloudflare|cloudflared'; then
  echo "Production Helm defaults must keep NATS internal-only and avoid public NATS routing." >&2
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

if ! rg -q 'branches:\s*$' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q -- '- flux-image-updates' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'workflow_run:' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'pull-requests: write' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'GH_TOKEN: \$\{\{ secrets\.FLUX_IMAGE_UPDATE_PAT \}\}' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'No image update commits to promote' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'changes outside infra/charts/tertius/values.yaml' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'Merge image update PR' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q -- '--delete-branch=false' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || rg -q -- '--auto' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" || ! rg -q 'Unable to create Flux image update PR automatically' "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml"; then
  echo ".github/workflows/flux-image-update-pr.yml must open and check-gate merges for Flux image update branches without GitHub auto-merge." >&2
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
