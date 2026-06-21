#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/infra/charts/tertius"
LOCAL_VALUES="${CHART_DIR}/values-local.yaml"
TMP_DIR="${TMPDIR:-/tmp}/tertius-runtime-parity.$$"
mkdir -p "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT

failures=0

fail() {
  failures=$((failures + 1))
  echo "FAIL: $*" >&2
}

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "SKIP: missing $1; run this check locally after installing required tooling." >&2
    exit 0
  }
}

contains() {
  file=$1
  pattern=$2
  message=$3
  if ! grep -Eq "$pattern" "$file"; then
    fail "$message"
  fi
}

not_contains() {
  file=$1
  pattern=$2
  message=$3
  if grep -Eq "$pattern" "$file"; then
    fail "$message"
  fi
}

contains "$ROOT_DIR/docker-compose.yml" 'LLM_WEEKLY_BUDGET_USD' "Compose dev must include LLM_WEEKLY_BUDGET_USD"
contains "$ROOT_DIR/docker-compose.parity.yml" 'LLM_WEEKLY_BUDGET_USD' "Compose parity must include LLM_WEEKLY_BUDGET_USD"
not_contains "$ROOT_DIR/docker-compose.parity.yml" 'LLM_DAILY_BUDGET_USD' "Compose parity must not use legacy LLM_DAILY_BUDGET_USD"
contains "$CHART_DIR/templates/configmap.yaml" 'LLM_WEEKLY_BUDGET_USD' "Helm ConfigMap must include LLM_WEEKLY_BUDGET_USD"
contains "$CHART_DIR/values.yaml" 'tracesBackend:' "Helm values must define tracesBackend"
contains "$CHART_DIR/templates/otel-collector.yaml" 'otlphttp/victoriatraces' "Helm collector must define VictoriaTraces exporter"
contains "$ROOT_DIR/infra/otel/otel-collector-local.yaml" 'otlphttp/victoriatraces' "Local collector must define VictoriaTraces exporter"
contains "$ROOT_DIR/docker-compose.yml" 'victoriatraces' "Compose dev must include VictoriaTraces"

if [ "$failures" -ne 0 ]; then
  echo "Runtime parity static check failed with ${failures} issue(s)." >&2
  exit 1
fi

need helm
need docker

if ! docker compose version >/dev/null 2>&1; then
  echo "SKIP: docker compose plugin is unavailable; run scripts/check-runtime-parity.sh where 'docker compose config' works." >&2
  exit 0
fi

helm template tertius "$CHART_DIR" --values "$LOCAL_VALUES" >"$TMP_DIR/helm.yaml"
docker compose -f "$ROOT_DIR/docker-compose.yml" config >"$TMP_DIR/compose-dev.yaml"
COMPOSE_PARITY_UI_PORT=18080 COMPOSE_PARITY_API_PORT=18000 \
  docker compose -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.parity.yml" config >"$TMP_DIR/compose-parity.yaml"

for file in "$TMP_DIR/helm.yaml" "$TMP_DIR/compose-dev.yaml" "$TMP_DIR/compose-parity.yaml"; do
  contains "$file" 'TERTIUS_COMPILE' "${file} must include compile stream name"
  contains "$file" 'tertius\.compile\.request' "${file} must include compile request subject"
  contains "$file" 'tertius\.compile\.result' "${file} must include compile result subject"
  contains "$file" 'compile-workers' "${file} must include compile worker queue"
  contains "$file" 'compile-result-api' "${file} must include compile result consumer"
  contains "$file" '8388608' "${file} must include compile request max bytes"
  contains "$file" '33554432' "${file} must include compile result max bytes/NATS max payload"
  contains "$file" 'TERTIUS_BILLING' "${file} must include billing stream name"
  contains "$file" 'tertius\.billing\.usage\.llm\.tokens' "${file} must include billing subject"
  contains "$file" 'tertius-api' "${file} must include API service name"
  contains "$file" 'tertius-ui' "${file} must include UI service name"
  contains "$file" '4317|grpc' "${file} must include OTEL gRPC contract"
  contains "$file" 'victoriatraces' "${file} must include VictoriaTraces"
  contains "$file" '10428' "${file} must include VictoriaTraces port"
  contains "$file" 'insert/opentelemetry/v1/traces' "${file} must include VictoriaTraces OTLP HTTP ingest path"
done

contains "$TMP_DIR/helm.yaml" 'API_BASE_PATH: "/api"|API_BASE_PATH[^[:alnum:]_/.-]*/api' "Helm local runtime must use API_BASE_PATH=/api"
contains "$TMP_DIR/compose-parity.yaml" 'VITE_API_URL: /api|VITE_API_URL=/api' "Compose parity UI must use VITE_API_URL=/api"
contains "$TMP_DIR/compose-dev.yaml" '5173' "Compose dev should expose Vite/HMR port 5173"

not_contains "$TMP_DIR/compose-parity.yaml" '5173:5173|published: "5173"|target: 5173' "Compose parity must not expose Vite port 5173"
not_contains "$TMP_DIR/compose-parity.yaml" 'node:20|npm install|npm run dev|CHOKIDAR_USEPOLLING|source: .*/ui|source: .*/server' "Compose parity must not retain dev image, commands, HMR env, or API/UI bind mounts"
contains "$TMP_DIR/compose-parity.yaml" '18080|published: "18080"' "Compose parity must expose default UI port 18080"
contains "$TMP_DIR/compose-parity.yaml" '18000|published: "18000"' "Compose parity must expose default API port 18000"

contains "$ROOT_DIR/docs/harness/local-harness.md" 'http://localhost:18080' "Harness docs must document UI port 18080"
contains "$ROOT_DIR/docs/harness/local-harness.md" 'http://localhost:18000' "Harness docs must document API port 18000"
contains "$ROOT_DIR/docs/harness/local-harness.md" 'http://localhost:10428' "Harness docs must document traces port 10428"
contains "$ROOT_DIR/docs/harness/runtime-parity.md" 'Compose dev' "Runtime parity docs must describe Compose dev differences"
contains "$ROOT_DIR/docs/harness/runtime-parity.md" 'traces backend' "Runtime parity docs must describe traces backend differences"

if [ "$failures" -ne 0 ]; then
  echo "Runtime parity check failed with ${failures} issue(s)." >&2
  exit 1
fi

echo "Runtime parity check passed."
