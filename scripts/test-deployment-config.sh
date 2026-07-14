#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/infra/charts/tertius"
LOCAL_VALUES="${CHART_DIR}/values-local.yaml"
RELEASE_NAME="${RELEASE_NAME:-tertius}"
legacy_provider_key_pattern='LLM_API_'"KEY"'|OPENAI_API_'"KEY"
local_tool_prefix='r''tk'

if rg -q "(^|[[:space:]])${local_tool_prefix}[[:space:]]" "${ROOT_DIR}/scripts" --glob '*.sh'; then
  echo "Repository scripts must not depend on the local ${local_tool_prefix} command wrapper." >&2
  exit 1
fi

"${ROOT_DIR}/scripts/check-runtime-parity.sh"
bash "${ROOT_DIR}/scripts/test-k3s-wffc-wait.sh"

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

render_confidential_client() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.environment=local \
    --set keycloak.realmImport.uiPublicClient=false \
    --set-string keycloak.realmImport.uiClientSecret=oidc-client-secret \
    --set app.secret.create=true \
    --set-string app.secret.oidcClientSecret=oidc-client-secret \
    --set-string app.secret.authSessionSecret=auth-session-secret
}

render_invalid_confidential_client() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set keycloak.realmImport.uiPublicClient=false
}

render_mismatched_confidential_client() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.environment=local \
    --set keycloak.realmImport.uiPublicClient=false \
    --set-string keycloak.realmImport.uiClientSecret=keycloak-secret \
    --set app.secret.create=true \
    --set-string app.secret.oidcClientSecret=api-secret \
    --set-string app.secret.authSessionSecret=auth-session-secret
}

render_missing_auth_session_secret() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.environment=local \
    --set app.secret.create=true \
    --set-string app.secret.databaseUrl=postgresql://example
}

render_production_app_secret_created() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.environment=production \
    --set app.secret.create=true \
    --set-string app.secret.authSessionSecret=auth-session-secret
}

render_network_policy_enabled() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set networkPolicy.enabled=true
}

render_network_policy_disabled() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set networkPolicy.enabled=false
}

render_external_observability_collector() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.observability.collector.enabled=false \
    --set-string app.observability.otlpEndpoint=http://shared-otel-collector:4317 \
    --set-string app.observability.collectorHttpHost=shared-otel-collector \
    --set-string app.observability.collectorHttpPort=4318
}

render_pi_worker() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set piAgent.enabled=true
}

render_pi_disabled() {
  helm template "$RELEASE_NAME" "$CHART_DIR" --set piAgent.enabled=false
}

render_pi_existing_claim() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set piAgent.enabled=true \
    --set-string piAgent.auth.existingClaim=external-pi-auth-abcdefghijklmnopqrstuvwxyz-abcdefghijklmnopqrstuvwxyz-0123456789
}

render_pi_keda_disabled() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set piAgent.enabled=true \
    --set keda.enabled=false
}

render_pi_without_auth_storage() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set piAgent.enabled=true \
    --set piAgent.auth.storage.enabled=false
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

if rg -q 'VITE_KEYCLOAK_AUTHORITY|VITE_KEYCLOAK_CLIENT_ID|VITE_API_BASE_URL=http://localhost:8000|VITE_API_URL=http://localhost:8000' "${ROOT_DIR}/README.md" "${ROOT_DIR}/ui/.env.example"; then
  echo "Frontend docs and env examples must use same-origin /api and must not expose browser Keycloak/OIDC settings." >&2
  exit 1
fi

if ! rg -q 'VITE_API_URL=/api' "${ROOT_DIR}/README.md" || ! rg -q 'VITE_API_URL=/api' "${ROOT_DIR}/ui/.env.example"; then
  echo "Frontend docs and env examples must document same-origin VITE_API_URL=/api for cookie-backed auth." >&2
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

if rg -q --glob '!test-deployment-config.sh' 'local-k3s-sync-llm-env-wsl.sh|set-k3s-llm-api-key.sh' "${ROOT_DIR}/scripts" "${ROOT_DIR}/docs/configuration-and-secrets.md"; then
  echo "Legacy API-key synchronization helpers and callers must be removed." >&2
  exit 1
fi

if [ ! -x "${ROOT_DIR}/scripts/pi-agent-auth.sh" ]; then
  echo "Pi OAuth operations require an executable scripts/pi-agent-auth.sh helper." >&2
  exit 1
fi

pi_auth_test_dir="$(mktemp -d)"
trap 'rm -rf "$pi_auth_test_dir"' EXIT
mkdir -p "$pi_auth_test_dir/bin"
cat >"$pi_auth_test_dir/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >>"$MOCK_KUBECTL_LOG"
printf '\n' >>"$MOCK_KUBECTL_LOG"
args=" $* "
if [[ "$args" == *" get pvc "* ]]; then
  printf '%s' "${MOCK_PVC_PHASE:-Bound}"
elif [[ "$args" == *" get scaledjob "* ]]; then
  case "${MOCK_SCALEDJOB_MODE:-absent}" in
    absent) ;;
    error) printf 'forbidden\n' >&2; exit 1 ;;
    paused) printf '{"metadata":{"annotations":{"autoscaling.keda.sh/paused":"true"}}}' ;;
    unsupported) printf '{"metadata":{"annotations":{"autoscaling.keda.sh/paused-replicas":"0"}}}' ;;
    active) printf '{"metadata":{"annotations":{}}}' ;;
  esac
elif [[ "$args" == *" get jobs "* ]]; then
  case "${MOCK_JOBS_MODE:-empty}" in
    empty) printf '{"items":[]}' ;;
    error) printf 'forbidden\n' >&2; exit 1 ;;
    active) printf '{"items":[{"metadata":{"name":"pi-job"},"status":{"active":1}}]}' ;;
  esac
elif [[ "$args" == *" get pods "* ]]; then
  if [ "${MOCK_PODS_ERROR:-false}" = true ]; then
    printf 'forbidden\n' >&2
    exit 1
  fi
  if [ "${MOCK_ACTIVE_WORKER:-false}" = true ]; then
    printf '{"items":[{"metadata":{"name":"pi-worker-0"},"status":{"phase":"Running"}}]}'
  else
    printf '{"items":[]}'
  fi
elif [[ "$args" == *" create -f -"* ]]; then
  manifest="$(cat)"
  printf '%s\n' "$manifest" >"$MOCK_MANIFEST"
  printf 'pod/mock created\n'
elif [[ "$args" == *" exec -it "* ]]; then
  if [ -n "${MOCK_ATTACH_TTY:-}" ]; then
    if [ -t 0 ]; then printf 'tty\n'; else printf 'not-a-tty\n'; fi >"$MOCK_ATTACH_TTY"
  fi
  if [ "${MOCK_ATTACH_SLEEP:-false}" = true ]; then
    printf '%s\n' "$$" >"$MOCK_ATTACH_PID"
    trap 'printf TERM >>"$MOCK_ATTACH_EVENTS"; exit 143' TERM
    while :; do sleep 1; done
  fi
elif [[ "$args" == *" exec "* && "$args" == *" stat "* ]]; then
  printf '%s\n' "${MOCK_AUTH_STAT:-regular file|1000|1000|600}"
elif [[ "$args" == *" exec "* ]]; then
  printf '%s\n' "${MOCK_PI_CANARY:-PI_AUTH_OK}"
fi
EOF
chmod +x "$pi_auth_test_dir/bin/kubectl"
cat >"$pi_auth_test_dir/bin/helm" <<'EOF'
#!/usr/bin/env bash
printf '%q ' "$@" >>"$MOCK_HELM_LOG"
printf '\n' >>"$MOCK_HELM_LOG"
if [ -n "${MOCK_HELM_VALUES:-}" ]; then
  printf '%s\n' "$MOCK_HELM_VALUES"
else
  printf '%s\n' '{"nameOverride":"","imagePullSecrets":[],"piAgent":{"runtimeClassName":""}}'
fi
EOF
chmod +x "$pi_auth_test_dir/bin/helm"

run_pi_auth_fixture() {
  MOCK_KUBECTL_LOG="$pi_auth_test_dir/kubectl.log" \
  MOCK_MANIFEST="$pi_auth_test_dir/manifest.yaml" \
  MOCK_ATTACH_PID="$pi_auth_test_dir/attach.pid" \
  MOCK_ATTACH_EVENTS="$pi_auth_test_dir/attach.events" \
  MOCK_ATTACH_TTY="$pi_auth_test_dir/attach.tty" \
  MOCK_HELM_LOG="$pi_auth_test_dir/helm.log" \
  PATH="$pi_auth_test_dir/bin:$PATH" \
    "${ROOT_DIR}/scripts/pi-agent-auth.sh" "$@"
}

for invalid_args in \
  'verify --namespace -bad --release release --claim claim --image image:test' \
  'verify --namespace test --release -bad --claim claim --image image:test' \
  'verify --namespace test --release release --claim -bad --image image:test'; do
  : >"$pi_auth_test_dir/kubectl.log"
  : >"$pi_auth_test_dir/helm.log"
  # shellcheck disable=SC2086
  if run_pi_auth_fixture $invalid_args >/dev/null 2>&1; then
    echo "Pi auth accepted option-like Kubernetes identifier: $invalid_args" >&2
    exit 1
  fi
  if [ -s "$pi_auth_test_dir/kubectl.log" ] || [ -s "$pi_auth_test_dir/helm.log" ]; then
    echo "Pi auth invoked Helm or kubectl before rejecting: $invalid_args" >&2
    exit 1
  fi
done

for safety_case in scaledjob_error jobs_error pods_error unsupported_pause active_scaledjob active_job; do
  : >"$pi_auth_test_dir/kubectl.log"
  : >"$pi_auth_test_dir/helm.log"
  case "$safety_case" in
    scaledjob_error)
      if MOCK_SCALEDJOB_MODE=error run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
        echo "Pi auth did not fail closed on ScaledJob discovery error." >&2; exit 1
      fi
      ;;
    pods_error)
      if MOCK_PODS_ERROR=true run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
        echo "Pi auth did not fail closed on worker pod discovery error." >&2; exit 1
      fi
      ;;
    jobs_error)
      if MOCK_JOBS_MODE=error run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
        echo "Pi auth did not fail closed on worker Job discovery error." >&2; exit 1
      fi
      ;;
    unsupported_pause)
      if MOCK_SCALEDJOB_MODE=unsupported run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
        echo "Pi auth accepted an unsupported KEDA pause annotation." >&2; exit 1
      fi
      ;;
    active_scaledjob)
      if MOCK_SCALEDJOB_MODE=active run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
        echo "Pi auth accepted an unpaused Pi ScaledJob." >&2; exit 1
      fi
      ;;
    active_job)
      if MOCK_SCALEDJOB_MODE=paused MOCK_JOBS_MODE=active run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
        echo "Pi auth accepted an active worker Job." >&2; exit 1
      fi
      ;;
  esac
  if rg -q ' create -f -' "$pi_auth_test_dir/kubectl.log"; then
    echo "Pi auth created an operator pod after unsafe probe: $safety_case" >&2; exit 1
  fi
done

: >"$pi_auth_test_dir/kubectl.log"
MOCK_SCALEDJOB_MODE=paused run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null
if ! rg -q -- '--provider openai-codex --model gpt-5\.6-sol --thinking medium -p' "$pi_auth_test_dir/kubectl.log"; then
  echo "Pi auth verification must use the fixed model and reasoning effort." >&2
  exit 1
fi

for accepted_auth_stat in 'regular file|1000|1000|600' 'regular file|1000|1000|660'; do
  : >"$pi_auth_test_dir/kubectl.log"
  MOCK_AUTH_STAT="$accepted_auth_stat" run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null
  if ! rg -q 'annotate pvc claim tertius.io/pi-agent-auth-verified=true' "$pi_auth_test_dir/kubectl.log"; then
    echo "Pi auth did not mark an accepted credential file verified: $accepted_auth_stat" >&2
    exit 1
  fi
done

for rejected_auth_stat in \
  'regular file|1000|1000|640' \
  'regular file|1000|1000|664' \
  'regular file|1001|1000|600' \
  'regular file|1000|1001|600' \
  'directory|1000|1000|600'; do
  : >"$pi_auth_test_dir/kubectl.log"
  if MOCK_AUTH_STAT="$rejected_auth_stat" run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
    echo "Pi auth accepted an unsafe credential file: $rejected_auth_stat" >&2
    exit 1
  fi
  if rg -q 'annotate pvc claim tertius.io/pi-agent-auth-verified=true' "$pi_auth_test_dir/kubectl.log"; then
    echo "Pi auth marked an unsafe credential file verified: $rejected_auth_stat" >&2
    exit 1
  fi
done

: >"$pi_auth_test_dir/kubectl.log"
if MOCK_PI_CANARY=AUTH_FAILED MOCK_AUTH_STAT='regular file|1000|1000|600' \
  run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
  echo "Pi auth accepted a failed provider canary." >&2
  exit 1
fi
if rg -q ' stat |annotate pvc claim tertius.io/pi-agent-auth-verified=true' "$pi_auth_test_dir/kubectl.log"; then
  echo "Pi auth inspected or marked credentials verified after a failed provider canary." >&2
  exit 1
fi

: >"$pi_auth_test_dir/kubectl.log"
: >"$pi_auth_test_dir/helm.log"
invalid_claim_values='{"nameOverride":"","imagePullSecrets":[],"piAgent":{"runtimeClassName":"","auth":{"existingClaim":"-helm-injected"}}}'
if MOCK_HELM_VALUES="$invalid_claim_values" run_pi_auth_fixture verify --namespace test --release release --image image:test >/dev/null 2>&1; then
  echo "Pi auth accepted an option-like Helm-derived claim." >&2
  exit 1
fi
if [ -s "$pi_auth_test_dir/kubectl.log" ]; then
  echo "Pi auth invoked kubectl before rejecting an invalid Helm-derived claim." >&2
  exit 1
fi

: >"$pi_auth_test_dir/kubectl.log"
: >"$pi_auth_test_dir/helm.log"
pi_auth_output="$(run_pi_auth_fixture login --namespace test --release release --claim claim --image image:test 2>&1)"
if ! rg -q ' exec -i ' "$pi_auth_test_dir/kubectl.log" || rg -q ' exec -it ' "$pi_auth_test_dir/kubectl.log"; then
  echo "Non-TTY Pi auth must keep stdin without requesting a remote TTY." >&2
  exit 1
fi
if ! rg -q ' delete pod ' "$pi_auth_test_dir/kubectl.log"; then
  echo "I-018: Pi login pod must be deleted after a normal exit." >&2
  exit 1
fi
PI_AUTH_MANIFEST="$pi_auth_test_dir/manifest.yaml" uv run python -c '
import json, os
pod = json.load(open(os.environ["PI_AUTH_MANIFEST"]))
assert pod["metadata"]["labels"]["app.kubernetes.io/name"] == "tertius"
assert pod["metadata"]["labels"]["app.kubernetes.io/instance"] == "release"
assert pod["metadata"]["labels"]["tertius.io/pi-agent-network"] == "true"
assert pod["spec"]["automountServiceAccountToken"] is False
assert pod["spec"]["securityContext"]["runAsUser"] == 1000
assert pod["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
'

: >"$pi_auth_test_dir/kubectl.log"
long_pi_app_name="$(printf 'a%.0s' $(seq 1 62))-suffix"
private_helm_values="$(jq -cn --arg name "$long_pi_app_name" '{nameOverride:$name,imagePullSecrets:[{name:"private-registry"}],piAgent:{runtimeClassName:"gvisor"}}')"
MOCK_HELM_VALUES="$private_helm_values" \
  run_pi_auth_fixture verify --namespace test --release release --claim claim --image 'registry.invalid/pi:test' >/dev/null
PI_AUTH_MANIFEST="$pi_auth_test_dir/manifest.yaml" uv run python -c '
import json, os
pod = json.load(open(os.environ["PI_AUTH_MANIFEST"]))
assert pod["metadata"]["labels"]["app.kubernetes.io/name"] == "a" * 62
assert pod["spec"]["imagePullSecrets"] == [{"name": "private-registry"}]
assert pod["spec"]["runtimeClassName"] == "gvisor"
'

: >"$pi_auth_test_dir/kubectl.log"
rm -f "$pi_auth_test_dir/attach.pid" "$pi_auth_test_dir/attach.events" "$pi_auth_test_dir/attach.tty"
printf -v pi_auth_pty_command '%q=%q %q=%q %q=%q %q=%q %q=%q %q=%q %q=%q %q login --namespace test --release release --claim claim --image image:test' \
  MOCK_KUBECTL_LOG "$pi_auth_test_dir/kubectl.log" \
  MOCK_MANIFEST "$pi_auth_test_dir/manifest.yaml" \
  MOCK_ATTACH_PID "$pi_auth_test_dir/attach.pid" \
  MOCK_ATTACH_EVENTS "$pi_auth_test_dir/attach.events" \
  MOCK_ATTACH_TTY "$pi_auth_test_dir/attach.tty" \
  MOCK_ATTACH_SLEEP true \
  PATH "$pi_auth_test_dir/bin:$PATH" \
  "${ROOT_DIR}/scripts/pi-agent-auth.sh"
setsid script -qfec "$pi_auth_pty_command" /dev/null >/dev/null 2>&1 &
pi_auth_pid=$!
for _ in $(seq 1 50); do
  [ -s "$pi_auth_test_dir/attach.pid" ] && break
  sleep 0.02
done
if [ "$(cat "$pi_auth_test_dir/attach.tty" 2>/dev/null)" != tty ]; then
  echo "I-019: stdin-TTY kubectl exec must run in the foreground with terminal stdin." >&2
  kill -TERM -- "-$pi_auth_pid" 2>/dev/null || true
  wait "$pi_auth_pid" 2>/dev/null || true
  exit 1
fi
if ! rg -q 'if \[ -t 0 \]; then' "${ROOT_DIR}/scripts/pi-agent-auth.sh" || rg -q '\[ -t 1 \]' "${ROOT_DIR}/scripts/pi-agent-auth.sh"; then
  echo "Pi auth TTY allocation must depend on stdin only so redirected output retains masking." >&2
  exit 1
fi
attach_pid="$(cat "$pi_auth_test_dir/attach.pid")"
auth_shell_pid="$(ps -o ppid= -p "$attach_pid" | tr -d ' ')"
kill -TERM "$attach_pid" "$auth_shell_pid"
wait "$pi_auth_pid" 2>/dev/null || true
if ! rg -q ' delete pod ' "$pi_auth_test_dir/kubectl.log"; then
  echo "I-019: Pi login pod must be deleted after interruption." >&2
  exit 1
fi
if kill -0 "$attach_pid" 2>/dev/null || ! rg -q TERM "$pi_auth_test_dir/attach.events"; then
  echo "I-019: interruption must terminate the foreground interactive kubectl process." >&2
  exit 1
fi

: >"$pi_auth_test_dir/kubectl.log"
if MOCK_ACTIVE_WORKER=true run_pi_auth_fixture login --namespace test --release release --claim claim --image image:test >/dev/null 2>&1; then
  echo "I-020: Pi login must refuse while a worker pod is active." >&2
  exit 1
fi
if rg -q ' create -f -' "$pi_auth_test_dir/kubectl.log"; then
  echo "I-020: Pi login created an operator pod before refusing an active worker." >&2
  exit 1
fi

: >"$pi_auth_test_dir/kubectl.log"
pi_login_sdk_output="$(run_pi_auth_fixture login --namespace test --release release --claim claim --image image:test 2>&1)"
pi_verify_output="$(run_pi_auth_fixture verify --namespace test --release release --claim claim --image image:test 2>&1)"
pi_logout_output="$(run_pi_auth_fixture logout --namespace test --release release --claim claim --image image:test --confirm 2>&1)"
pi_all_auth_output="${pi_auth_output}${pi_login_sdk_output}${pi_verify_output}${pi_logout_output}"
if ! rg -q 'node /app/server/pi/oauth-cli\.ts login openai-codex' "$pi_auth_test_dir/kubectl.log" || \
   ! rg -q 'node /app/server/pi/oauth-cli\.ts logout openai-codex' "$pi_auth_test_dir/kubectl.log"; then
  echo "Pi login/logout must use the non-TUI SDK OAuth helper." >&2
  exit 1
fi
if rg -q ' exec -it .* pi .*--no-session' "$pi_auth_test_dir/kubectl.log"; then
  echo "Pi login/logout must not start the interactive TUI." >&2
  exit 1
fi
if rg -qi 'auth\.json.*(cat|base64|cp|copy)|(^|[[:space:]])(cat|base64|cp)[[:space:]].*auth\.json' <<<"$pi_all_auth_output" || \
   rg -qi 'auth\.json.*(cat|base64|cp|copy)|(^|[[:space:]])(cat|base64|cp)[[:space:]].*auth\.json' "$pi_auth_test_dir/kubectl.log" || \
   rg -qi '(cat|base64|cp|copy).*auth\.json|kubectl.*(get|create|patch).*secret|secret.*(print|output|display)' "${ROOT_DIR}/scripts/pi-agent-auth.sh"; then
  echo "I-021: Pi auth operations must not display or copy auth.json." >&2
  exit 1
fi

if ! rg -q -- '--set app\.secret\.create=false' "${ROOT_DIR}/scripts/test-k3s-deployment.sh" || ! rg -q -- '--set-string app\.secretName=\$\{APP_SECRET_NAME\}' "${ROOT_DIR}/scripts/test-k3s-deployment.sh"; then
  echo "Local k3s smoke must deploy with an externally managed app Secret instead of a chart-generated app Secret." >&2
  exit 1
fi

if ! rg -q 'kubectl -n "\$NAMESPACE" create secret generic "\$APP_SECRET_NAME"' "${ROOT_DIR}/scripts/test-k3s-deployment.sh" || ! rg -q 'AUTH_SESSION_SECRET=<redacted>' "${ROOT_DIR}/scripts/test-k3s-deployment.sh"; then
  echo "Local k3s smoke must create the external app Secret without printing secret values." >&2
  exit 1
fi

if ! rg -q 'map \$http_cf_visitor \$cloudflare_proto' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'map \$http_host \$forwarded_host_port' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header Host \$http_host' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Proto \$forwarded_proto' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Host \$http_host' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_set_header X-Forwarded-Port \$forwarded_port' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template"; then
  echo "Frontend nginx must preserve Cloudflare/original forwarded scheme, host, and port for proxied API and Keycloak requests." >&2
  exit 1
fi

if ! rg -q 'location = /otel/v1/traces' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template" || ! rg -q 'proxy_pass http://\$\{OTEL_COLLECTOR_HTTP_HOST\}:\$\{OTEL_COLLECTOR_HTTP_PORT\}/v1/traces' "${ROOT_DIR}/infra/deploy/nginx/default.conf.template"; then
  echo "Frontend nginx must proxy same-origin browser OTLP traces to the collector /v1/traces endpoint." >&2
  exit 1
fi

if ! rg -q '^  otel-collector:' "${ROOT_DIR}/docker-compose.yml" || ! rg -q './infra/otel/otel-collector-local.yaml:/etc/otelcol-contrib/config.yaml:ro' "${ROOT_DIR}/docker-compose.yml"; then
  echo "Docker Compose must include the local OpenTelemetry collector service and config mount." >&2
  exit 1
fi

if ! rg -q 'OTEL_SERVICE_NAME: tertius-api' "${ROOT_DIR}/docker-compose.yml" || ! rg -q 'OTEL_SERVICE_NAME: tertius-compile-job' "${ROOT_DIR}/docker-compose.yml" || ! rg -q 'OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317' "${ROOT_DIR}/docker-compose.yml"; then
  echo "Docker Compose must wire backend and compile job OTLP environment to the local collector with distinct service names." >&2
  exit 1
fi

if ! rg -q 'VITE_OTEL_ENABLED=.*true' "${ROOT_DIR}/docker-compose.yml" || ! rg -q 'VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=.*http://localhost:4318/v1/traces' "${ROOT_DIR}/docker-compose.yml" || ! rg -q 'VITE_OTEL_SERVICE_NAME=tertius-ui' "${ROOT_DIR}/docker-compose.yml"; then
  echo "Docker Compose frontend must enable browser OpenTelemetry and point Vite at the local collector HTTP endpoint." >&2
  exit 1
fi

if ! rg -q 'endpoint: 0.0.0.0:4317' "${ROOT_DIR}/infra/otel/otel-collector-local.yaml" || ! rg -q 'endpoint: 0.0.0.0:4318' "${ROOT_DIR}/infra/otel/otel-collector-local.yaml" || ! rg -q 'host: 0.0.0.0' "${ROOT_DIR}/infra/otel/otel-collector-local.yaml" || ! rg -q 'port: 8888' "${ROOT_DIR}/infra/otel/otel-collector-local.yaml" || ! rg -q 'debug:' "${ROOT_DIR}/infra/otel/otel-collector-local.yaml"; then
  echo "Local OpenTelemetry collector config must expose OTLP gRPC, OTLP HTTP, collector metrics, and debug export." >&2
  exit 1
fi

rendered="$(render_local)"
default_rendered="$(render_default)"
keda_disabled_rendered="$(render_keda_disabled)"
compile_strategy_accurate_rendered="$(render_compile_strategy_accurate)"
app_secret_rendered="$(render_app_secret_created)"
app_secret_without_prompt_rendered="$(render_app_secret_created_without_prompt)"
confidential_client_rendered="$(render_confidential_client)"
network_policy_enabled_rendered="$(render_network_policy_enabled)"
network_policy_disabled_rendered="$(render_network_policy_disabled)"
external_observability_rendered="$(render_external_observability_collector)"
pi_worker_rendered="$(render_pi_worker)"
pi_disabled_rendered="$(render_pi_disabled)"
pi_existing_claim_rendered="$(render_pi_existing_claim)"
scaled_job="$(extract_render_doc "$rendered" 'kind: ScaledJob')"
default_scaled_job="$(extract_render_doc "$default_rendered" 'kind: ScaledJob')"
compile_strategy_accurate_scaled_job="$(extract_render_doc "$compile_strategy_accurate_rendered" 'kind: ScaledJob')"
app_configmap="$(extract_render_doc "$rendered" 'kind: ConfigMap' 'name: tertius-config')"
default_app_configmap="$(extract_render_doc "$default_rendered" 'kind: ConfigMap' 'name: tertius-config')"
api_deployment="$(extract_render_doc "$rendered" 'kind: Deployment' 'app.kubernetes.io/component: api')"
pi_enabled_api_deployment="$(extract_render_doc "$pi_worker_rendered" 'kind: Deployment' 'app.kubernetes.io/component: api')"
pi_disabled_api_deployment="$(extract_render_doc "$pi_disabled_rendered" 'kind: Deployment' 'app.kubernetes.io/component: api')"
ui_deployment="$(extract_render_doc "$rendered" 'kind: Deployment' 'app.kubernetes.io/component: ui')"
otel_collector_configmap="$(extract_render_doc "$rendered" 'kind: ConfigMap' 'app.kubernetes.io/component: otel-collector')"
otel_collector_deployment="$(extract_render_doc "$rendered" 'kind: Deployment' 'app.kubernetes.io/component: otel-collector')"
otel_collector_service="$(extract_render_doc "$rendered" 'kind: Service' 'app.kubernetes.io/component: otel-collector')"
default_otel_collector_configmap="$(extract_render_doc "$default_rendered" 'kind: ConfigMap' 'app.kubernetes.io/component: otel-collector')"
default_otel_collector_deployment="$(extract_render_doc "$default_rendered" 'kind: Deployment' 'app.kubernetes.io/component: otel-collector')"
default_otel_collector_service="$(extract_render_doc "$default_rendered" 'kind: Service' 'app.kubernetes.io/component: otel-collector')"
default_victoriametrics_deployment="$(extract_render_doc "$default_rendered" 'kind: Deployment' 'app.kubernetes.io/component: metrics-backend')"
default_victoriametrics_service="$(extract_render_doc "$default_rendered" 'kind: Service' 'app.kubernetes.io/component: metrics-backend')"
default_victoriametrics_pvc="$(extract_render_doc "$default_rendered" 'kind: PersistentVolumeClaim' 'app.kubernetes.io/component: metrics-backend')"
default_victoriatraces_deployment="$(extract_render_doc "$default_rendered" 'kind: Deployment' 'app.kubernetes.io/component: traces-backend')"
default_victoriatraces_service="$(extract_render_doc "$default_rendered" 'kind: Service' 'app.kubernetes.io/component: traces-backend')"
default_victoriatraces_pvc="$(extract_render_doc "$default_rendered" 'kind: PersistentVolumeClaim' 'app.kubernetes.io/component: traces-backend')"
external_observability_configmap="$(extract_render_doc "$external_observability_rendered" 'kind: ConfigMap' 'name: tertius-config')"
external_observability_ui_deployment="$(extract_render_doc "$external_observability_rendered" 'kind: Deployment' 'app.kubernetes.io/component: ui')"
external_observability_collector_configmap="$(extract_render_doc "$external_observability_rendered" 'kind: ConfigMap' 'app.kubernetes.io/component: otel-collector')"
external_observability_collector_deployment="$(extract_render_doc "$external_observability_rendered" 'kind: Deployment' 'app.kubernetes.io/component: otel-collector')"
external_observability_collector_service="$(extract_render_doc "$external_observability_rendered" 'kind: Service' 'app.kubernetes.io/component: otel-collector')"
api_with_llm_secret="$(extract_render_doc "$app_secret_rendered" 'app.kubernetes.io/component: api')"
api_with_llm_secret_without_prompt="$(extract_render_doc "$app_secret_without_prompt_rendered" 'app.kubernetes.io/component: api')"
ui_with_llm_secret="$(extract_render_doc "$app_secret_rendered" 'app.kubernetes.io/component: ui')"
compile_job_network_policy="$(extract_render_doc "$network_policy_enabled_rendered" 'kind: NetworkPolicy' 'name: tertius-compile-job')"
compile_job_network_policy_disabled="$(extract_render_doc "$network_policy_disabled_rendered" 'kind: NetworkPolicy' 'name: tertius-compile-job')"
pi_auth_pvc="$(extract_render_doc "$default_rendered" 'kind: PersistentVolumeClaim' 'app.kubernetes.io/component: pi-agent-auth')"
pi_worker="$(extract_render_doc "$pi_worker_rendered" 'kind: ScaledJob' 'app.kubernetes.io/component: pi-agent-worker')"
pi_existing_claim_worker="$(extract_render_doc "$pi_existing_claim_rendered" 'kind: ScaledJob' 'app.kubernetes.io/component: pi-agent-worker')"
pi_existing_claim_pvc="$(extract_render_doc "$pi_existing_claim_rendered" 'kind: PersistentVolumeClaim' 'app.kubernetes.io/component: pi-agent-auth')"
pi_network_policy="$(extract_render_doc "$default_rendered" 'kind: NetworkPolicy' 'app.kubernetes.io/component: pi-agent-network')"

# ConfigMap-backed API settings must change the pod template so Helm rolls the
# API together with workers that consume the same feature flag.
pi_enabled_config_checksum="$(printf '%s\n' "$pi_enabled_api_deployment" | awk '/checksum\/config:/ {gsub(/"/, "", $2); print $2; exit}')"
pi_disabled_config_checksum="$(printf '%s\n' "$pi_disabled_api_deployment" | awk '/checksum\/config:/ {gsub(/"/, "", $2); print $2; exit}')"
if [[ ! "$pi_enabled_config_checksum" =~ ^[0-9a-f]{64}$ ]] || [[ ! "$pi_disabled_config_checksum" =~ ^[0-9a-f]{64}$ ]]; then
  echo "API pod template must expose only an opaque SHA-256 checksum for ConfigMap rollout state." >&2
  exit 1
fi
if [ "$pi_enabled_config_checksum" = "$pi_disabled_config_checksum" ]; then
  echo "Changing piAgent.enabled must change the API pod template ConfigMap checksum." >&2
  exit 1
fi

if rg -F -q -- '- ::ffff:0:0/96' <<<"$pi_network_policy"; then
  echo "Pi network policy must not use the Kubernetes-invalid IPv4-mapped IPv6 exclusion." >&2
  exit 1
fi

# Helm validates structure only. When a cluster is reachable, ask the API server
# to validate this dependency-free fixture so CIDR semantics are covered too.
if command -v kubectl >/dev/null 2>&1 && kubectl get --raw=/readyz --request-timeout=5s >/dev/null 2>&1; then
  if ! printf '%s\n' "$pi_network_policy" | kubectl apply --dry-run=server -f - >/dev/null; then
    echo "Kubernetes API server rejected the rendered Pi NetworkPolicy." >&2
    exit 1
  fi
fi

# I-010: auth storage is retained and independent from worker enablement.
if [ -z "$pi_auth_pvc" ] || ! rg -q 'helm.sh/resource-policy: keep' <<<"$pi_auth_pvc" || ! rg -q -- '- ReadWriteOnce' <<<"$pi_auth_pvc"; then
  echo "Pi auth storage must render a retained ReadWriteOnce PVC while the worker is disabled." >&2
  exit 1
fi

# I-011: an external claim takes precedence over chart-created storage.
if [ -n "$pi_existing_claim_pvc" ] || ! rg -q 'claimName: external-pi-auth-abcdefghijklmnopqrstuvwxyz-abcdefghijklmnopqrstuvwxyz-0123456789$' <<<"$pi_existing_claim_worker"; then
  echo "Pi existingClaim must suppress the chart PVC and be mounted by the worker." >&2
  exit 1
fi

# I-012: one serial KEDA job consumes the durable Pi request queue.
if ! rg -q 'maxReplicaCount: 1' <<<"$pi_worker" || ! rg -q 'type: nats-jetstream' <<<"$pi_worker" || ! rg -q 'stream: "?TERTIUS_PI_AGENT"?' <<<"$pi_worker" || ! rg -q 'consumer: "?pi-agent-workers"?' <<<"$pi_worker"; then
  echo "Pi worker must render a serial KEDA NATS JetStream ScaledJob." >&2
  exit 1
fi
if ! rg -q 'activeDeadlineSeconds: 540' <<<"$pi_worker" || ! printf '%s\n' "$pi_worker" | rg -A 2 'name: workspace' | rg -q 'sizeLimit: "128Mi"'; then
  echo "Pi worker must use the fixed 540-second deadline and 128Mi workspace bound." >&2
  exit 1
fi
if ! rg -q 'name: PI_SKIP_VERSION_CHECK' <<<"$pi_worker" || ! rg -q 'name: PI_TELEMETRY' <<<"$pi_worker"; then
  echo "Pi worker must disable upstream version checks and telemetry." >&2
  exit 1
fi

compose_canary_tmp="$(mktemp -d "${TMPDIR:-/tmp}/tertius-compose-canary.XXXXXX")"
cat >"${compose_canary_tmp}/docker" <<'SH'
#!/usr/bin/env sh
printf '%s\n' "$*" >>"$MOCK_DOCKER_LOG"
printf '%s\n' "${MOCK_CANARY_OUTPUT:-PI_AUTH_OK}"
SH
chmod +x "${compose_canary_tmp}/docker"
MOCK_DOCKER_LOG="${compose_canary_tmp}/docker.log" PATH="${compose_canary_tmp}:$PATH" \
  PI_AGENT_AUTH_CANARY_TIMEOUT_SECONDS=5 "${ROOT_DIR}/scripts/harness-compose.sh" auth-preflight
if ! rg -q -- '--no-tools.*--provider openai-codex --model gpt-5\.6-sol --thinking medium.*Reply with exactly PI_AUTH_OK' "${compose_canary_tmp}/docker.log"; then
  echo "Compose auth preflight must run the no-tool OpenAI Codex canary." >&2
  rm -rf "$compose_canary_tmp"
  exit 1
fi
if MOCK_DOCKER_LOG="${compose_canary_tmp}/docker.log" MOCK_CANARY_OUTPUT=NOT_AUTHENTICATED \
  PATH="${compose_canary_tmp}:$PATH" PI_AGENT_AUTH_CANARY_TIMEOUT_SECONDS=5 \
  "${ROOT_DIR}/scripts/harness-compose.sh" auth-preflight >/dev/null 2>&1; then
  echo "Compose auth preflight must reject any response other than PI_AUTH_OK." >&2
  rm -rf "$compose_canary_tmp"
  exit 1
fi
rm -rf "$compose_canary_tmp"

pi_stream_max_bytes="$(printf '%s\n' "$app_configmap" | awk '/PI_AGENT_STREAM_MAX_BYTES:/ {gsub(/"/, "", $2); print $2; exit}')"
if [ "$pi_stream_max_bytes" != "67108864" ] || ! PYTHONPATH="${ROOT_DIR}/server" PI_AGENT_STREAM_MAX_BYTES="$pi_stream_max_bytes" uv run python -c 'from core.config import Settings; assert Settings().pi_agent_stream_max_bytes == 67108864' 2>/dev/null; then
  echo "PI_AGENT_STREAM_MAX_BYTES must render as an exact decimal accepted by Pydantic Settings." >&2
  exit 1
fi
if ! printf '%s\n' "$pi_worker" | rg -A 1 'name: PI_AGENT_STREAM_MAX_BYTES' | rg -q 'value: "67108864"'; then
  echo "Pi worker must receive PI_AGENT_STREAM_MAX_BYTES as an exact decimal string." >&2
  exit 1
fi

# I-013: the production worker preserves the compile-job pod boundary.
for required in 'runtimeClassName: "gvisor"' 'runAsNonRoot: true' 'runAsUser: 1000' 'runAsGroup: 1000' 'fsGroup: 1000' 'automountServiceAccountToken: false' 'readOnlyRootFilesystem: true' 'allowPrivilegeEscalation: false' 'drop:' '- ALL'; do
  if ! rg -F -q -- "$required" <<<"$pi_worker"; then
    echo "Pi worker is missing hardening setting: $required" >&2
    exit 1
  fi
done

# I-014: mutable OAuth/API provider secrets never enter API or worker pods.
if rg -q "${legacy_provider_key_pattern}|DATABASE_URL|APP_DB_|KEYCLOAK|AUTH_SESSION_SECRET|OIDC_CLIENT_SECRET" <<<"$pi_worker"; then
  echo "Pi provider credentials must be absent from the worker." >&2
  exit 1
fi

if rg -q 'PI_AGENT_SYSTEM_PROMPT|piAgent\.systemPrompt' \
  "$ROOT_DIR/server/.env.example" \
  "$ROOT_DIR/infra/charts/tertius/values.yaml" \
  "$ROOT_DIR/infra/charts/tertius/templates" \
  "$ROOT_DIR/docker-compose.yml" \
  "$ROOT_DIR/docker-compose.parity.yml"; then
  echo 'Legacy Pi system prompt runtime configuration is still present.' >&2
  exit 1
fi

# I-015: writable storage is limited to the whole auth directory and bounded scratch volumes.
for mount in 'mountPath: /var/lib/pi-agent' 'mountPath: /workspace' 'mountPath: /tmp' 'mountPath: /tmp/home'; do
  if ! rg -F -q "$mount" <<<"$pi_worker"; then
    echo "Pi worker is missing volume mount: $mount" >&2
    exit 1
  fi
done
if rg -q 'subPath:|hostPath:|privileged: true' <<<"$pi_worker" || [ "$(rg -c 'sizeLimit:' <<<"$pi_worker")" -lt 3 ]; then
  echo "Pi worker scratch volumes must be bounded emptyDirs and auth must be a whole-directory mount." >&2
  exit 1
fi

# I-016: login and worker pods share a default-deny ingress/public-only egress policy.
for required in 'tertius.io/pi-agent-network: "true"' 'ingress: []' 'port: 53' 'port: 4222' 'port: 4317' 'port: 443' 'except:' '0.0.0.0/8' '10.0.0.0/8' '100.64.0.0/10' '127.0.0.0/8' '169.254.0.0/16' '172.16.0.0/12' '192.0.0.0/24' '192.0.2.0/24' '192.31.196.0/24' '192.52.193.0/24' '192.88.99.0/24' '192.168.0.0/16' '192.175.48.0/24' '198.18.0.0/15' '198.51.100.0/24' '203.0.113.0/24' '224.0.0.0/4' '240.0.0.0/4' '::/128' '::1/128' '64:ff9b::/96' '64:ff9b:1::/48' '100::/64' '2001::/32' '2001:1::1/128' '2001:1::2/128' '2001:2::/48' '2001:3::/32' '2001:4:112::/48' '2001:20::/28' '2001:30::/28' '2001:db8::/32' '2002::/16' '2620:4f:8000::/48' '3fff::/20' '5f00::/16' 'fc00::/7' 'fe80::/10' 'ff00::/8'; do
  if ! rg -F -q "$required" <<<"$pi_network_policy"; then
    echo "Pi network policy is missing required isolation rule: $required" >&2
    exit 1
  fi
done
if ! PI_NETWORK_POLICY_YAML="$pi_network_policy" uv run python -c '
import os, yaml
policy = yaml.safe_load(os.environ["PI_NETWORK_POLICY_YAML"])
dns_rules = [rule for rule in policy["spec"]["egress"] if {p.get("port") for p in rule.get("ports", [])} == {53}]
assert len(dns_rules) == 1
peers = dns_rules[0].get("to", [])
assert peers == [{
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
}]
' 2>/dev/null; then
  echo "Pi DNS egress must select only kube-system pods labelled k8s-app=kube-dns." >&2
  exit 1
fi

other_release_rendered="$(RELEASE_NAME=other-tertius render_default)"
other_pi_network_policy="$(extract_render_doc "$other_release_rendered" 'kind: NetworkPolicy' 'app.kubernetes.io/component: pi-agent-network')"
if ! PI_NETWORK_POLICIES_YAML="$pi_network_policy
---
$other_pi_network_policy" uv run python -c '
import os, yaml
policies = list(yaml.safe_load_all(os.environ["PI_NETWORK_POLICIES_YAML"]))
assert len(policies) == 2
for policy, release in zip(policies, ("tertius", "other-tertius"), strict=True):
    selector = policy["spec"]["podSelector"]["matchLabels"]
    assert selector["app.kubernetes.io/name"] == "tertius"
    assert selector["app.kubernetes.io/instance"] == release
    assert selector["tertius.io/pi-agent-network"] == "true"
    nats = next(rule for rule in policy["spec"]["egress"] if any(port.get("port") == 4222 for port in rule.get("ports", [])))
    assert nats["to"] == [{"podSelector": {"matchLabels": {
        "app.kubernetes.io/name": "nats",
        "app.kubernetes.io/instance": release,
        "app.kubernetes.io/component": "nats",
    }}}]
' 2>/dev/null; then
  echo "Pi worker/login and NATS policy selectors must remain isolated per Helm release." >&2
  exit 1
fi

# I-017: an enabled API must not render without its KEDA worker runtime.
if pi_keda_disabled_error="$(render_pi_keda_disabled 2>&1)"; then
  echo "Pi-enabled renders must fail when KEDA is disabled." >&2
  exit 1
fi
if ! rg -q 'piAgent.enabled requires keda.enabled=true' <<<"$pi_keda_disabled_error"; then
  echo "KEDA-disabled Pi renders must report the precise worker precondition error." >&2
  exit 1
fi

if invalid_pi_auth_error="$(render_pi_without_auth_storage 2>&1)"; then
  echo "Pi worker render must fail when neither auth storage nor existingClaim is configured." >&2
  exit 1
fi
if ! rg -q 'piAgent.enabled requires piAgent.auth.storage.enabled=true or piAgent.auth.existingClaim' <<<"$invalid_pi_auth_error"; then
  echo "Invalid Pi auth storage render must report the precise configuration error." >&2
  exit 1
fi

if invalid_confidential_error="$(render_invalid_confidential_client 2>&1)"; then
  echo "Helm render must fail when the UI client is confidential but no Keycloak client secret is configured." >&2
  exit 1
fi

if ! rg -q 'keycloak.realmImport.uiClientSecret must be set' <<<"$invalid_confidential_error"; then
  echo "Invalid confidential-client render must explain the missing Keycloak client secret." >&2
  exit 1
fi

if mismatched_confidential_error="$(render_mismatched_confidential_client 2>&1)"; then
  echo "Helm render must fail when generated API and Keycloak OIDC client secrets differ." >&2
  exit 1
fi

if ! rg -q 'keycloak.realmImport.uiClientSecret must match app.secret.oidcClientSecret' <<<"$mismatched_confidential_error"; then
  echo "Mismatched confidential-client render must explain the secret mismatch." >&2
  exit 1
fi

if missing_auth_session_secret_error="$(render_missing_auth_session_secret 2>&1)"; then
  echo "Helm render must fail when a generated app Secret would have an empty AUTH_SESSION_SECRET." >&2
  exit 1
fi

if ! rg -q 'app.secret.authSessionSecret must be set when app.secret.create=true' <<<"$missing_auth_session_secret_error"; then
  echo "Missing auth session secret render must explain the empty AUTH_SESSION_SECRET." >&2
  exit 1
fi

if production_app_secret_created_error="$(render_production_app_secret_created 2>&1)"; then
  echo "Helm render must fail when production values ask the chart to generate the app Secret." >&2
  exit 1
fi

if ! rg -q 'app.secret.create must be false in production' <<<"$production_app_secret_created_error"; then
  echo "Production generated app Secret render must explain that production uses an externally managed Secret." >&2
  exit 1
fi

if ! rg -q 'kind: PersistentVolumeClaim' <<<"$rendered"; then
  echo "Local Helm render did not include any PersistentVolumeClaim resources." >&2
  exit 1
fi

if rg -q 'name: tertius-api-cache|claimName: tertius-api-cache|mountPath: /app/cache/tertius|ARTIFACT_ROOT' <<<"$rendered"; then
  echo "Local Helm render must not include the API artifact PVC, API cache mount, or ARTIFACT_ROOT." >&2
  exit 1
fi

if rg -q 'tertius-postgres-rw' "$LOCAL_VALUES"; then
  echo "Local values must not hardcode the app database service name; release names vary in CI and local k3s." >&2
  exit 1
fi

if ! rg -q 'APP_DB_HOST: "tertius-postgres-rw"' <<<"$rendered"; then
  echo "Local Helm render must derive APP_DB_HOST from the release name." >&2
  exit 1
fi

if ! rg -q 'name: tertius-valkey' <<<"$rendered"; then
  echo "Local Helm render did not include the Valkey data PVC." >&2
  exit 1
fi

if ! rg -q 'app.kubernetes.io/name: nats' <<<"$rendered"; then
  echo "Local Helm render did not include NATS resources." >&2
  exit 1
fi

if ! rg -q 'NATS_URL: "nats://tertius-nats:4222"' <<<"$rendered"; then
  echo "Local Helm render did not derive the expected release-local NATS_URL." >&2
  exit 1
fi

if ! rg -q 'KEYCLOAK_ISSUER: "http://keycloak.localhost/realms/tertius"' <<<"$rendered" || ! rg -q 'KEYCLOAK_JWKS_URL_OVERRIDE: "http://tertius-keycloak-service:8080/realms/tertius/protocol/openid-connect/certs"' <<<"$rendered"; then
  echo "Local ConfigMap must validate the public Keycloak issuer while fetching JWKS through the in-cluster service URL." >&2
  exit 1
fi

if ! rg -q 'KEYCLOAK_ACCESS_TOKEN_LIFESPAN_SECONDS: "300"' <<<"$app_configmap" || ! rg -q 'KEYCLOAK_SSO_SESSION_IDLE_TIMEOUT_SECONDS: "604800"' <<<"$app_configmap" || ! rg -q 'KEYCLOAK_SSO_SESSION_MAX_LIFESPAN_SECONDS: "2592000"' <<<"$app_configmap" || ! rg -q 'KEYCLOAK_CLIENT_SESSION_IDLE_TIMEOUT_SECONDS: "604800"' <<<"$app_configmap" || ! rg -q 'KEYCLOAK_CLIENT_SESSION_MAX_LIFESPAN_SECONDS: "2592000"' <<<"$app_configmap"; then
  echo "ConfigMap must render Keycloak session lifetime settings." >&2
  exit 1
fi

if ! rg -q 'AUTH_COOKIE_SECURE: "false"' <<<"$app_configmap" || ! rg -q 'AUTH_SESSION_IDLE_SECONDS: "604800"' <<<"$app_configmap" || ! rg -q 'AUTH_SESSION_MAX_SECONDS: "2592000"' <<<"$app_configmap"; then
  echo "ConfigMap must render API cookie session settings." >&2
  exit 1
fi

if ! rg -q 'OTEL_ENABLED: "true"' <<<"$app_configmap" || ! rg -q 'OTEL_EXPORTER_OTLP_ENDPOINT: "http://tertius-otel-collector:4317"' <<<"$app_configmap" || ! rg -q 'OTEL_EXPORTER_OTLP_PROTOCOL: "grpc"' <<<"$app_configmap" || ! rg -q 'OTEL_TRACES_SAMPLER: "parentbased_traceidratio"' <<<"$app_configmap" || ! rg -q 'OTEL_TRACES_SAMPLER_ARG: "1.0"' <<<"$app_configmap" || ! rg -q 'OTEL_LOG_JSON: "true"' <<<"$app_configmap"; then
  echo "Local ConfigMap must render shared OpenTelemetry configuration pointing to the local collector." >&2
  exit 1
fi

if ! rg -q 'OTEL_EXPORTER_OTLP_ENDPOINT: "http://tertius-otel-collector:4317"' <<<"$default_app_configmap" || ! rg -q 'OTEL_RESOURCE_ATTRIBUTES: "service.version=0.1.0,deployment.environment=production"' <<<"$default_app_configmap"; then
  echo "Default production ConfigMap must point OTLP at the in-chart collector while rendering shared resource attributes." >&2
  exit 1
fi

if ! printf '%s\n' "$api_deployment" | rg -A 1 'name: OTEL_SERVICE_NAME' | rg -q 'value: "tertius-api"' || ! rg -q 'container.name=api' <<<"$api_deployment" || ! rg -q 'k8s.pod.name=\$\(POD_NAME\)' <<<"$api_deployment"; then
  echo "API Deployment must set per-workload OpenTelemetry service name and Kubernetes resource attributes." >&2
  exit 1
fi

if ! printf '%s\n' "$ui_deployment" | rg -A 1 'name: OTEL_COLLECTOR_HTTP_HOST' | rg -q 'value: "tertius-otel-collector"' || ! printf '%s\n' "$ui_deployment" | rg -A 1 'name: OTEL_COLLECTOR_HTTP_PORT' | rg -q 'value: "4318"' || ! rg -q 'OTEL_COLLECTOR_HTTP_HOST|OTEL_COLLECTOR_HTTP_PORT' <<<"$ui_deployment"; then
  echo "UI Deployment must provide collector HTTP env vars for nginx envsubst." >&2
  exit 1
fi

if ! rg -q 'name: tertius-otel-collector-config' <<<"$otel_collector_configmap" || ! rg -q 'endpoint: 0.0.0.0:4317' <<<"$otel_collector_configmap" || ! rg -q 'endpoint: 0.0.0.0:4318' <<<"$otel_collector_configmap" || ! rg -q 'port: 8888' <<<"$otel_collector_configmap" || ! rg -q 'debug:' <<<"$otel_collector_configmap"; then
  echo "Local Helm render must include an OpenTelemetry Collector config with OTLP gRPC, OTLP HTTP, metrics, and debug export." >&2
  exit 1
fi

if ! rg -q 'name: tertius-otel-collector' <<<"$otel_collector_deployment" || ! rg -q 'image: "otel/opentelemetry-collector-contrib:0.133.0"' <<<"$otel_collector_deployment" || ! rg -q 'containerPort: 4317' <<<"$otel_collector_deployment" || ! rg -q 'containerPort: 4318' <<<"$otel_collector_deployment" || ! rg -q 'containerPort: 8888' <<<"$otel_collector_deployment"; then
  echo "Local Helm render must include an OpenTelemetry Collector Deployment for the nginx and backend OTLP targets." >&2
  exit 1
fi

if ! rg -q 'name: tertius-otel-collector' <<<"$otel_collector_service" || ! rg -q 'port: 4317' <<<"$otel_collector_service" || ! rg -q 'port: 4318' <<<"$otel_collector_service" || ! rg -q 'port: 8888' <<<"$otel_collector_service"; then
  echo "Local Helm render must include an OpenTelemetry Collector Service so UI nginx can resolve the collector upstream." >&2
  exit 1
fi

if ! rg -q 'name: tertius-otel-collector' <<<"$default_otel_collector_deployment" || ! rg -q 'image: "otel/opentelemetry-collector-contrib:0.133.0"' <<<"$default_otel_collector_deployment" || ! rg -q 'name: tertius-otel-collector' <<<"$default_otel_collector_service"; then
  echo "Default production Helm render must include the in-chart OpenTelemetry Collector Deployment and Service." >&2
  exit 1
fi

if ! rg -q 'prometheusremotewrite:' <<<"$default_otel_collector_configmap" || ! rg -q 'endpoint: http://tertius-victoriametrics:8428/api/v1/write' <<<"$default_otel_collector_configmap" || ! rg -q -- '- prometheusremotewrite' <<<"$default_otel_collector_configmap"; then
  echo "Default production collector must export metrics to bundled VictoriaMetrics." >&2
  exit 1
fi

if ! rg -q 'otlphttp/victoriatraces:' <<<"$default_otel_collector_configmap" || ! rg -q 'traces_endpoint: http://tertius-victoriatraces:10428/insert/opentelemetry/v1/traces' <<<"$default_otel_collector_configmap" || ! rg -q -- '- otlphttp/victoriatraces' <<<"$default_otel_collector_configmap"; then
  echo "Default production collector must export traces to bundled VictoriaTraces." >&2
  exit 1
fi

if ! rg -q 'name: tertius-victoriametrics' <<<"$default_victoriametrics_deployment" || ! rg -q 'image: "victoriametrics/victoria-metrics:v1.129.1"' <<<"$default_victoriametrics_deployment" || ! rg -q 'port: 8428' <<<"$default_victoriametrics_service" || ! rg -q 'name: tertius-victoriametrics' <<<"$default_victoriametrics_pvc"; then
  echo "Default production Helm render must deploy bundled VictoriaMetrics." >&2
  exit 1
fi

if ! rg -q 'name: tertius-victoriatraces' <<<"$default_victoriatraces_deployment" || ! rg -q 'image: "victoriametrics/victoria-traces:v0.9.2"' <<<"$default_victoriatraces_deployment" || ! rg -q 'port: 10428' <<<"$default_victoriatraces_service" || ! rg -q 'name: tertius-victoriatraces' <<<"$default_victoriatraces_pvc"; then
  echo "Default production Helm render must deploy bundled VictoriaTraces." >&2
  exit 1
fi

if [ -n "$external_observability_collector_configmap" ] || [ -n "$external_observability_collector_deployment" ] || [ -n "$external_observability_collector_service" ] || ! rg -q 'OTEL_EXPORTER_OTLP_ENDPOINT: "http://shared-otel-collector:4317"' <<<"$external_observability_configmap" || ! printf '%s\n' "$external_observability_ui_deployment" | rg -A 1 'name: OTEL_COLLECTOR_HTTP_HOST' | rg -q 'value: "shared-otel-collector"'; then
  echo "Helm render must allow disabling the in-chart collector while pointing backend and browser telemetry at a shared collector." >&2
  exit 1
fi

if ! rg -q 'accessTokenLifespan: 300' <<<"$rendered" || ! rg -q 'ssoSessionIdleTimeout: 604800' <<<"$rendered" || ! rg -q 'ssoSessionMaxLifespan: 2592000' <<<"$rendered" || ! rg -q 'clientSessionIdleTimeout: 604800' <<<"$rendered" || ! rg -q 'clientSessionMaxLifespan: 2592000' <<<"$rendered"; then
  echo "Keycloak RealmImport must apply the configured rolling one-week session idle window and refresh hard cap." >&2
  exit 1
fi

if ! rg -q 'PI_AGENT_MODEL: "gpt-5.6-sol"' <<<"$rendered" || ! rg -q 'PI_AGENT_THINKING: "medium"' <<<"$rendered" || ! rg -q 'PI_AGENT_STREAM_NAME: "TERTIUS_PI_AGENT"' <<<"$rendered"; then
  echo "ConfigMap must render the fixed Pi model and durable transport contract." >&2
  exit 1
fi

if ! rg -q 'LLM_USER_RATE_LIMIT_PER_MINUTE: "10"' <<<"$rendered" || ! rg -q 'LLM_TENANT_DAILY_TOKEN_QUOTA: "3200000"' <<<"$rendered" || ! rg -q 'LLM_USER_DAILY_TOKEN_QUOTA: "3200000"' <<<"$rendered"; then
  echo "ConfigMap must render paid LLM rate and quota settings." >&2
  exit 1
fi

if ! rg -q 'PI_AGENT_ESTIMATED_OUTPUT_TOKENS: "65536"' <<<"$rendered" || ! rg -q 'LLM_FILE_EDIT_MAX_CONTEXT_FILES: "20"' <<<"$rendered" || ! rg -q 'LLM_FILE_EDIT_MAX_CONTEXT_CHARS: "80000"' <<<"$rendered"; then
  echo "ConfigMap must render Pi output reservation and file-selection limits." >&2
  exit 1
fi

if ! rg -q 'PI_AGENT_MAX_TURNS: "12"' <<<"$rendered" || ! rg -q 'PI_AGENT_MAX_TOOL_CALLS: "48"' <<<"$rendered"; then
  echo "ConfigMap must render bounded Pi execution controls." >&2
  exit 1
fi

if rg -q "${legacy_provider_key_pattern}|PI_AGENT_SYSTEM_PROMPT|AUTH_SESSION_SECRET|OIDC_CLIENT_SECRET" <<<"$app_configmap"; then
  echo "ConfigMap must not render LLM provider secrets, prompts, or auth client/session secrets." >&2
  exit 1
fi

if ! rg -q 'AUTH_SESSION_SECRET: "local-auth-session-secret-change-me"' <<<"$rendered"; then
  echo "Local app Secret must render AUTH_SESSION_SECRET for stable cookie-backed auth sessions." >&2
  exit 1
fi

if rg -q "${legacy_provider_key_pattern}|LLM_FILE_EDIT_SYSTEM_PROMPT" <<<"$app_secret_rendered$api_with_llm_secret$api_with_llm_secret_without_prompt"; then
  echo "Chart renders must not create or inject direct provider credentials." >&2
  exit 1
fi

if rg -q "${legacy_provider_key_pattern}|PI_AGENT_SYSTEM_PROMPT|LLM_MODELS_JSON|LLM_DEFAULT_MODEL_ID|LLM_WEEKLY_BUDGET_USD|BILLING_LLM_USAGE_SUBJECT|llm|envFrom:|configMapRef:|secretRef:" <<<"$ui_with_llm_secret"; then
  echo "UI Deployment must not receive or reference LLM provider credentials." >&2
  exit 1
fi

if ! rg -q 'directAccessGrantsEnabled: true' <<<"$rendered" || ! rg -q 'username: "demo"' <<<"$rendered"; then
  echo "Local Helm render must include the k3s smoke user and direct access grant support." >&2
  exit 1
fi

if ! rg -q 'publicClient: true' <<<"$rendered"; then
  echo "Local Helm render must keep the UI OIDC client public for local PKCE auth." >&2
  exit 1
fi

if rg -q 'directAccessGrantsEnabled: true|username: "demo"' <<<"$default_rendered"; then
  echo "Default Helm render must not enable the k3s smoke user or direct access grants." >&2
  exit 1
fi

if ! rg -q 'publicClient: true' <<<"$default_rendered"; then
  echo "Default Helm render must keep the UI OIDC client public unless confidential client secrets are explicitly configured." >&2
  exit 1
fi

if ! rg -q 'publicClient: false' <<<"$confidential_client_rendered" || ! rg -q 'secret: "oidc-client-secret"' <<<"$confidential_client_rendered" || ! rg -q 'OIDC_CLIENT_SECRET: "oidc-client-secret"' <<<"$confidential_client_rendered"; then
  echo "Confidential-client Helm render must include matching Keycloak and API OIDC client secrets." >&2
  exit 1
fi

if ! rg -q 'kind: ScaledJob' <<<"$scaled_job"; then
  echo "Local Helm render did not include the compile KEDA ScaledJob." >&2
  exit 1
fi

if rg -q 'kind: ScaledJob' <<<"$keda_disabled_rendered"; then
  echo "Helm render with keda.enabled=false must not include the compile KEDA ScaledJob." >&2
  exit 1
fi

if rg -q 'kind: Deployment' <<<"$rendered" && rg -q 'app.kubernetes.io/component: compile-worker' <<<"$rendered"; then
  echo "Local Helm render must not include the old compile-worker Deployment." >&2
  exit 1
fi

if ! rg -q 'type: nats-jetstream' <<<"$scaled_job"; then
  echo "Compile ScaledJob must use the KEDA nats-jetstream scaler." >&2
  exit 1
fi

if rg -q 'strategy: eager|scalingStrategy:' <<<"$scaled_job"; then
  echo "Compile ScaledJob must omit scalingStrategy by default so KEDA uses its non-eager default behavior for single queued compiles." >&2
  exit 1
fi

if ! rg -q 'strategy: accurate' <<<"$compile_strategy_accurate_scaled_job"; then
  echo "Compile ScaledJob must allow overriding the KEDA scaling strategy from values." >&2
  exit 1
fi

if ! rg -q 'natsServerMonitoringEndpoint: "tertius-nats-headless.default.svc.cluster.local:8222"' <<<"$scaled_job"; then
  echo "Compile ScaledJob must point KEDA at the NATS headless service monitoring endpoint." >&2
  exit 1
fi

if ! rg -q 'app.kubernetes.io/component: compile-job' <<<"$scaled_job"; then
  echo "Compile ScaledJob must label pods with app.kubernetes.io/component: compile-job." >&2
  exit 1
fi

if ! rg -q 'runtimeClassName: "gvisor"' <<<"$default_scaled_job"; then
  echo "Compile ScaledJob must use the gvisor RuntimeClass in default values." >&2
  exit 1
fi

if ! rg -q 'automountServiceAccountToken: false' <<<"$scaled_job"; then
  echo "Compile ScaledJob must not mount a service account token." >&2
  exit 1
fi

if ! rg -q 'backoffLimit: 0' <<<"$scaled_job" || ! rg -q 'activeDeadlineSeconds:' <<<"$scaled_job"; then
  echo "Compile ScaledJob must render backoffLimit: 0 and activeDeadlineSeconds." >&2
  exit 1
fi

if ! rg -q 'command: \["sh", "/app/server/start-compile-job.sh"\]' <<<"$scaled_job"; then
  echo "Compile ScaledJob must run the one-shot compile job startup script." >&2
  exit 1
fi

if rg -q "envFrom:|secretRef:|APP_DB_PASSWORD|APP_DB_OWNER|APP_DB_HOST|APP_DB_NAME|DATABASE_URL|AUTH_SESSION_SECRET|OIDC_CLIENT_SECRET|${legacy_provider_key_pattern}|LLM_FILE_EDIT_SYSTEM_PROMPT|LLM_MODELS_JSON|LLM_DEFAULT_MODEL_ID|LLM_WEEKLY_BUDGET_USD" <<<"$scaled_job"; then
  echo "Compile ScaledJob must not receive app secrets, database environment, or LLM provider configuration." >&2
  exit 1
fi

if ! rg -q 'COMPILE_STREAM_NAME' <<<"$scaled_job" || ! rg -q 'COMPILE_RESULT_SUBJECT' <<<"$scaled_job"; then
  echo "Local Helm render did not include compile job NATS stream/result configuration." >&2
  exit 1
fi

if ! rg -q 'COMPILE_ACK_WAIT_SECONDS: "900"' <<<"$rendered" || ! printf '%s\n' "$scaled_job" | rg -A 1 'name: COMPILE_ACK_WAIT_SECONDS' | rg -q 'value: "900"'; then
  echo "Compile ack wait must render as 900 seconds so it exceeds compile timeout plus publish/ack margin." >&2
  exit 1
fi

if ! printf '%s\n' "$scaled_job" | rg -A 1 'name: OTEL_SERVICE_NAME' | rg -q 'value: "tertius-compile-job"' || ! printf '%s\n' "$scaled_job" | rg -A 1 'name: OTEL_EXPORTER_OTLP_ENDPOINT' | rg -q 'value: "http://tertius-otel-collector:4317"' || ! rg -q 'container.name=compile' <<<"$scaled_job"; then
  echo "Compile ScaledJob must set compile-specific OpenTelemetry service name, endpoint, and resource attributes." >&2
  exit 1
fi

if rg -q 'COMPILE_(REQUEST|RESULT)_MAX_BYTES: "?[0-9]+e[+-]?[0-9]+"?' <<<"$rendered" || rg -q 'value: "?[0-9]+e[+-]?[0-9]+"?' <<<"$scaled_job"; then
  echo "Compile byte limits must render as plain integer strings, not scientific notation." >&2
  exit 1
fi

if ! rg -q 'COMPILE_REQUEST_MAX_BYTES: "8388608"' <<<"$rendered" || ! rg -q 'COMPILE_RESULT_MAX_BYTES: "33554432"' <<<"$rendered"; then
  echo "ConfigMap compile byte limits must render request as \"8388608\" and result as \"33554432\"." >&2
  exit 1
fi

if ! rg -q 'name: COMPILE_REQUEST_MAX_BYTES' <<<"$scaled_job" || ! printf '%s\n' "$scaled_job" | rg -A 1 'name: COMPILE_REQUEST_MAX_BYTES' | rg -q 'value: "8388608"' || ! printf '%s\n' "$scaled_job" | rg -A 1 'name: COMPILE_RESULT_MAX_BYTES' | rg -q 'value: "33554432"'; then
  echo "Compile ScaledJob byte limits must render request as \"8388608\" and result as \"33554432\"." >&2
  exit 1
fi

if ! rg -q '"max_payload": 33554432' <<<"$rendered"; then
  echo "NATS must render max_payload 33554432 so it can accept larger compile result messages." >&2
  exit 1
fi

if ! rg -q 'name: tertius-compile-job' <<<"$compile_job_network_policy" || ! rg -q 'port: 4222' <<<"$compile_job_network_policy"; then
  echo "Helm render with networkPolicy.enabled=true did not include the NATS-only compile Job NetworkPolicy." >&2
  exit 1
fi

if ! rg -q 'app.kubernetes.io/instance: tertius' <<<"$compile_job_network_policy" || ! rg -q 'app.kubernetes.io/component: nats' <<<"$compile_job_network_policy"; then
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

if ! rg -q 'claimName: .*js|storageClassName: local-path' <<<"$rendered"; then
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

if ! rg -q 'hostname: "https://tertius\.johnsonyuen\.com"' <<<"$production_rendered" || ! rg -q 'admin: "https://tertius\.johnsonyuen\.com"' <<<"$production_rendered"; then
  echo "Production Keycloak hostname must use the public HTTPS Tertius origin." >&2
  exit 1
fi

if ! rg -q 'KEYCLOAK_ISSUER: "https://tertius\.johnsonyuen\.com/realms/tertius"' <<<"$production_rendered" || ! rg -q 'KEYCLOAK_JWKS_URL_OVERRIDE: "http://tertius-keycloak-service:8080/realms/tertius/protocol/openid-connect/certs"' <<<"$production_rendered"; then
  echo "Production ConfigMap must validate the public Keycloak issuer while fetching JWKS through the in-cluster service URL." >&2
  exit 1
fi

if ! rg -q 'image: "ghcr\.io/d-b-w-gain/tertius-api:(master-107-5d1e30c|master-[0-9]+-[0-9]+-[a-f0-9]{7})"' <<<"$production_rendered"; then
  echo "Production Helm defaults do not render the expected GHCR API image." >&2
  exit 1
fi

if ! rg -q 'image: "ghcr\.io/d-b-w-gain/tertius-ui:(master-107-5d1e30c|master-[0-9]+-[0-9]+-[a-f0-9]{7})"' <<<"$production_rendered"; then
  echo "Production Helm defaults do not render the expected GHCR UI image." >&2
  exit 1
fi

if printf '%s\n' "$production_rendered" | rg -C 3 -i 'app.kubernetes.io/name: nats|name: nats' | rg -q 'NodePort|LoadBalancer|Ingress|cloudflare|cloudflared'; then
  echo "Production Helm defaults must keep NATS internal-only and avoid public NATS routing." >&2
  exit 1
fi

if rg -q '\$imagepolicy' "${CHART_DIR}/values.yaml"; then
  echo "infra/charts/tertius/values.yaml must not contain Flux image policy markers." >&2
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

if rg -q '\[skip ci\]|head_commit\.message' "${ROOT_DIR}/.github/workflows/images.yml"; then
  echo ".github/workflows/images.yml must not let a master commit bypass image publication and promotion." >&2
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

PRODUCTION_DIR="${ROOT_DIR}/infra/clusters/production"
PRODUCTION_KUSTOMIZATION="${PRODUCTION_DIR}/kustomization.yaml"
FLUX_GIT_REPOSITORY="${PRODUCTION_DIR}/flux-system/gitrepository.yaml"
IMAGE_WORKFLOW="${ROOT_DIR}/.github/workflows/images.yml"
CHART_WORKFLOW="${ROOT_DIR}/.github/workflows/chart-tests.yml"

if ! rg -q 'astral-sh/setup-uv@v4' "$CHART_WORKFLOW"; then
  echo ".github/workflows/chart-tests.yml must install uv before running Python-backed configuration checks." >&2
  exit 1
fi

extract_workflow_job() {
  local workflow="$1"
  local job="$2"

  awk -v job="$job" '
    $0 ~ ("^  " job ":[[:space:]]*$") { in_job = 1 }
    in_job && $0 ~ /^  [[:alnum:]_-]+:[[:space:]]*$/ && $0 !~ ("^  " job ":[[:space:]]*$") { exit }
    in_job { print }
  ' "$workflow"
}

extract_workflow_trigger() {
  local workflow="$1"
  local trigger="$2"

  awk -v trigger="$trigger" '
    $0 ~ ("^  " trigger ":[[:space:]]*$") { in_trigger = 1 }
    in_trigger && $0 ~ /^  [[:alnum:]_-]+:[[:space:]]*$/ && $0 !~ ("^  " trigger ":[[:space:]]*$") { exit }
    in_trigger { print }
  ' "$workflow"
}

extract_job_if() {
  awk '
    /^    if:/ { in_if = 1 }
    in_if && /^    [[:alnum:]_-]+:/ && $0 !~ /^    if:/ { exit }
    in_if { print }
  '
}

promotion_tmp_dir="$(mktemp -d)"
trap 'rm -rf "$promotion_tmp_dir"' EXIT
promotion_tag="master-999-1-abcdef0"

assert_promotion_fails_without_write() {
  local case_name="$1"
  local values_path="$2"
  local tag="$3"
  local before_path="${promotion_tmp_dir}/${case_name}.before.yaml"

  cp -p "$values_path" "$before_path"
  if python3 "${ROOT_DIR}/scripts/promote_images.py" \
    --values "$values_path" \
    --tag "$tag" >"${promotion_tmp_dir}/${case_name}.stdout" 2>"${promotion_tmp_dir}/${case_name}.stderr"; then
    echo "scripts/promote_images.py must reject the ${case_name} case." >&2
    exit 1
  fi

  if ! cmp -s "$before_path" "$values_path"; then
    echo "scripts/promote_images.py modified values for the rejected ${case_name} case." >&2
    exit 1
  fi
}

promotion_values="${promotion_tmp_dir}/values.yaml"
promotion_expected="${promotion_tmp_dir}/expected-values.yaml"
cp -p "${CHART_DIR}/values.yaml" "$promotion_values"
chmod 6755 "$promotion_values"
promotion_mode_before="$(stat -c '%a' "$promotion_values")"
sed -E \
  '/"\$imagepromoter": "tertius-(api|pi-agent|ui)"/s/(tag:[[:space:]]*)[^[:space:]]+/\1master-999-1-abcdef0/' \
  "${CHART_DIR}/values.yaml" >"$promotion_expected"
if ! python3 "${ROOT_DIR}/scripts/promote_images.py" \
  --values "$promotion_values" \
  --tag "$promotion_tag"; then
  echo "scripts/promote_images.py failed to promote the temporary chart values copy." >&2
  exit 1
fi

if ! cmp -s "$promotion_expected" "$promotion_values"; then
  echo "scripts/promote_images.py must preserve all bytes outside the three marked tag scalars." >&2
  exit 1
fi

if [ "$(stat -c '%a' "$promotion_values")" != "$promotion_mode_before" ]; then
  echo "scripts/promote_images.py must preserve the values file mode." >&2
  exit 1
fi

invalid_tag_values="${promotion_tmp_dir}/invalid-tag.yaml"
cp -p "${CHART_DIR}/values.yaml" "$invalid_tag_values"
assert_promotion_fails_without_write \
  "invalid-tag" "$invalid_tag_values" "master-999-abcdef0"

missing_marker_values="${promotion_tmp_dir}/missing-marker.yaml"
sed '/"\$imagepromoter": "tertius-ui"/s/[[:space:]]*#.*$//' \
  "${CHART_DIR}/values.yaml" >"$missing_marker_values"
assert_promotion_fails_without_write \
  "missing-marker" "$missing_marker_values" "$promotion_tag"

duplicate_marker_values="${promotion_tmp_dir}/duplicate-marker.yaml"
cp -p "${CHART_DIR}/values.yaml" "$duplicate_marker_values"
rg '"\$imagepromoter": "tertius-api"' "${CHART_DIR}/values.yaml" \
  >>"$duplicate_marker_values"
assert_promotion_fails_without_write \
  "duplicate-marker" "$duplicate_marker_values" "$promotion_tag"

malformed_marker_values="${promotion_tmp_dir}/malformed-marker.yaml"
sed 's/tag: \([^[:space:]]*\) # {"\$imagepromoter": "tertius-api"}/repository: \1 # {"$imagepromoter": "tertius-api"}/' \
  "${CHART_DIR}/values.yaml" >"$malformed_marker_values"
assert_promotion_fails_without_write \
  "malformed-marker" "$malformed_marker_values" "$promotion_tag"

symlink_target="${promotion_tmp_dir}/symlink-target.yaml"
symlink_values="${promotion_tmp_dir}/symlink-values.yaml"
cp -p "${CHART_DIR}/values.yaml" "$symlink_target"
cp -p "$symlink_target" "${promotion_tmp_dir}/symlink-target.before.yaml"
ln -s "$(basename "$symlink_target")" "$symlink_values"
symlink_before="$(readlink "$symlink_values")"
if python3 "${ROOT_DIR}/scripts/promote_images.py" \
  --values "$symlink_values" \
  --tag "$promotion_tag" >"${promotion_tmp_dir}/symlink.stdout" 2>"${promotion_tmp_dir}/symlink.stderr"; then
  echo "scripts/promote_images.py must reject a symlink --values path." >&2
  exit 1
fi
if [ ! -L "$symlink_values" ] || [ "$(readlink "$symlink_values")" != "$symlink_before" ] ||
   ! cmp -s "${promotion_tmp_dir}/symlink-target.before.yaml" "$symlink_target"; then
  echo "scripts/promote_images.py must not replace a values symlink or modify its target." >&2
  exit 1
fi

rm -rf "$promotion_tmp_dir"
trap - EXIT

for flux_file in image-repositories.yaml image-policies.yaml image-update-automation.yaml; do
  if [ -e "${PRODUCTION_DIR}/flux-system/${flux_file}" ]; then
    echo "Production must not contain the Flux image automation manifest ${flux_file}." >&2
    exit 1
  fi

  if rg -F -q "flux-system/${flux_file}" "$PRODUCTION_KUSTOMIZATION"; then
    echo "Production kustomization must not include ${flux_file}." >&2
    exit 1
  fi
done

if rg -q '^kind:[[:space:]]*(ImageRepository|ImagePolicy|ImageUpdateAutomation)[[:space:]]*$' "$PRODUCTION_DIR" --glob '*.yaml'; then
  echo "Production YAML must not contain Flux image repository, policy, or update automation resources." >&2
  exit 1
fi

if ! rg -q 'url:[[:space:]]*https://github\.com/d-b-w-gain/Tertius-Web\.git[[:space:]]*$' "$FLUX_GIT_REPOSITORY" ||
   ! rg -q 'branch:[[:space:]]*master[[:space:]]*$' "$FLUX_GIT_REPOSITORY"; then
  echo "GitRepository tertius-web must read the public Tertius-Web master branch." >&2
  exit 1
fi

if rg -q 'secretRef:|tertius-web-write' "$FLUX_GIT_REPOSITORY"; then
  echo "GitRepository tertius-web must not reference a Git write credential." >&2
  exit 1
fi

if [ -e "${ROOT_DIR}/.github/workflows/flux-image-update-pr.yml" ]; then
  echo "The obsolete Flux image update PR workflow must be removed." >&2
  exit 1
fi

if rg -q 'FLUX_IMAGE_UPDATE_PAT' "${ROOT_DIR}/.github/workflows"; then
  echo "GitHub workflows must not use FLUX_IMAGE_UPDATE_PAT." >&2
  exit 1
fi

if rg -q 'flux-image-updates' "${ROOT_DIR}/.github/workflows"; then
  echo "GitHub workflows must not reference the obsolete flux-image-updates branch." >&2
  exit 1
fi

images_job="$(extract_workflow_job "$IMAGE_WORKFLOW" images | sed '/^[[:space:]]*#/d')"
promote_job="$(extract_workflow_job "$IMAGE_WORKFLOW" promote | sed '/^[[:space:]]*#/d')"
app_token_count="$( (rg -c 'actions/create-github-app-token@v3' <<<"$promote_job" || true) | tr -d ' ' )"
client_id_count="$( (rg -F -c 'client-id: ${{ vars.IMAGE_PROMOTION_APP_CLIENT_ID }}' <<<"$promote_job" || true) | tr -d ' ' )"
private_key_count="$( (rg -F -c 'private-key: ${{ secrets.IMAGE_PROMOTION_APP_PRIVATE_KEY }}' <<<"$promote_job" || true) | tr -d ' ' )"

if [ "$app_token_count" -lt 2 ] || [ "$client_id_count" -lt 2 ] || [ "$private_key_count" -lt 2 ] ||
   [ "$( (rg -c 'permission-checks:[[:space:]]*read' <<<"$promote_job" || true) | tr -d ' ' )" -lt 2 ] ||
   [ "$( (rg -c 'permission-contents:[[:space:]]*write' <<<"$promote_job" || true) | tr -d ' ' )" -lt 2 ] ||
   [ "$( (rg -c 'permission-pull-requests:[[:space:]]*write' <<<"$promote_job" || true) | tr -d ' ' )" -lt 2 ] ||
   ! rg -F -q 'GH_TOKEN: ${{ steps.merge-app-token.outputs.token }}' <<<"$promote_job"; then
  echo "Build Images must mint a promotion token from the configured GitHub App credentials." >&2
  exit 1
fi

if ! rg -F -q 'GITHUB_RUN_ATTEMPT' <<<"$images_job" ||
   ! rg -q 'needs:[[:space:]]*images[[:space:]]*$' <<<"$promote_job" ||
   ! rg -q '^[[:space:]]*python3 scripts/promote_images\.py([[:space:]]|$)' <<<"$promote_job" ||
   ! rg -q 'image-promotion' <<<"$promote_job" ||
   ! rg -q -- '--force-with-lease=' <<<"$promote_job" ||
   rg -q -- '--force([="[:space:]]|$)' <<<"$promote_job" ||
   ! rg -F -q 'commits/${head_sha}/check-runs' <<<"$promote_job" ||
   ! rg -F -q 'Chart render/config checks' <<<"$promote_job" ||
   ! rg -F -q '.app.slug == "github-actions"' <<<"$promote_job" ||
   ! rg -F -q 'if [ "${conclusion}" = success ]' <<<"$promote_job" ||
   ! rg -q '^[[:space:]]*sleep[[:space:]]+[0-9]+' <<<"$promote_job" ||
   ! rg -q '^[[:space:]]*gh pr create([[:space:]]|$)' <<<"$promote_job" ||
   ! rg -q '^[[:space:]]*gh pr merge([[:space:]]|$)' <<<"$promote_job" ||
   ! rg -q -- '--match-head-commit' <<<"$promote_job" ||
   rg -q -- '--delete-branch' <<<"$promote_job"; then
  echo "Build Images promotion must update the chart, open a PR, poll its named config check, and merge the checked commit." >&2
  exit 1
fi

images_master_check_line="$(rg -n -m1 'git/ref/heads/master' <<<"$images_job" | cut -d: -f1 || true)"
images_publish_line="$(rg -n -m1 'push:[[:space:]]*true[[:space:]]*$' <<<"$images_job" | cut -d: -f1 || true)"
promote_check_poll_line="$(rg -n -F -m1 'commits/${head_sha}/check-runs' <<<"$promote_job" | cut -d: -f1 || true)"
promote_master_recheck_line="$(rg -n -m1 'git/ref/heads/master' <<<"$promote_job" | cut -d: -f1 || true)"
promote_merge_line="$(rg -n -m1 '^[[:space:]]*gh pr merge([[:space:]]|$)' <<<"$promote_job" | cut -d: -f1 || true)"
images_source_compare_count="$( (rg -F -c '"${current_master_sha}" != "${GITHUB_SHA}"' <<<"$images_job" || true) | tr -d ' ' )"
promote_source_compare_count="$( (rg -F -c '"${current_master_sha}" != "${SOURCE_SHA}"' <<<"$promote_job" || true) | tr -d ' ' )"
if [ -z "$images_master_check_line" ] || [ -z "$images_publish_line" ] ||
   [ "$images_source_compare_count" -lt 1 ] || [ "$promote_source_compare_count" -lt 2 ] ||
   [ "$images_master_check_line" -ge "$images_publish_line" ] ||
   [ -z "$promote_check_poll_line" ] || [ -z "$promote_master_recheck_line" ] ||
   [ -z "$promote_merge_line" ] || [ "$promote_check_poll_line" -ge "$promote_master_recheck_line" ] ||
   [ "$promote_master_recheck_line" -ge "$promote_merge_line" ]; then
  echo "Build Images promotion must verify the current master SHA before promotion and again before merge." >&2
  exit 1
fi

branch_protection_job="$(extract_workflow_job "${ROOT_DIR}/.github/workflows/tests.yml" branch-protection)"
if ! rg -q 'name:[[:space:]]*Branch protection gate[[:space:]]*$' <<<"$branch_protection_job" ||
   rg -q '^    if:' <<<"$branch_protection_job"; then
  echo ".github/workflows/tests.yml must register the always-present strict branch protection check." >&2
  exit 1
fi

for test_job_name in pi-agent images ui python; do
  test_job="$(extract_workflow_job "${ROOT_DIR}/.github/workflows/tests.yml" "$test_job_name")"
  test_job_if="$(extract_job_if <<<"$test_job")"
  if ! rg -F -q "github.ref != 'refs/heads/image-promotion'" <<<"$test_job_if" ||
     ! rg -F -q "github.head_ref != 'image-promotion'" <<<"$test_job_if"; then
    echo ".github/workflows/tests.yml job ${test_job_name} must skip image-promotion pushes and pull requests." >&2
    exit 1
  fi
done

integration_job="$(extract_workflow_job "${ROOT_DIR}/.github/workflows/integration.yml" docker-smoke-test)"
integration_job_if="$(extract_job_if <<<"$integration_job")"
if ! rg -F -q "github.ref != 'refs/heads/image-promotion'" <<<"$integration_job_if" ||
   ! rg -F -q "github.head_ref != 'image-promotion'" <<<"$integration_job_if"; then
  echo ".github/workflows/integration.yml job docker-smoke-test must skip image-promotion pushes and pull requests." >&2
  exit 1
fi

chart_config_job="$(extract_workflow_job "$CHART_WORKFLOW" deployment-config-check)"
chart_k3s_job="$(extract_workflow_job "$CHART_WORKFLOW" k3s-deployment-smoke)"
chart_k3s_job_if="$(extract_job_if <<<"$chart_k3s_job")"
if ! rg -F -q "github.head_ref != 'image-promotion'" <<<"$chart_k3s_job_if"; then
  echo ".github/workflows/chart-tests.yml must skip the k3s smoke job for image-promotion pull requests." >&2
  exit 1
fi

chart_pull_request_trigger="$(extract_workflow_trigger "$CHART_WORKFLOW" pull_request)"
if ! rg -F -q -- "- 'infra/charts/**'" <<<"$chart_pull_request_trigger" ||
   rg -q '^    if:' <<<"$chart_config_job"; then
  echo ".github/workflows/chart-tests.yml must run chart render/config checks on image promotion pull requests." >&2
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
  echo "HelmRelease tertius must reconcile chart content by Git revision so CI image promotion commits are deployed." >&2
  exit 1
fi
