#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT_DIR}/infra/charts/tertius"
LOCAL_VALUES="${CHART_DIR}/values-local.yaml"
TMP_DIR="${TMPDIR:-/tmp}/tertius-runtime-parity.$$"
mkdir -p "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT

failures=0
legacy_provider_key_pattern='LLM_API_'"KEY"'|OPENAI_API_'"KEY"'|ANTHROPIC_API_'"KEY"'|GOOGLE_API_'"KEY"

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

not_contains "$CHART_DIR/templates/configmap.yaml" 'LLM_WEEKLY_BUDGET_USD' "Helm ConfigMap must not include direct-provider dollar budgets"
contains "$CHART_DIR/templates/configmap.yaml" 'PI_AGENT_STREAM_NAME' "Helm ConfigMap must include Pi agent transport settings"
contains "$CHART_DIR/templates/configmap.yaml" 'PI_AGENT_MODELS_JSON' "Helm ConfigMap must include the Pi model catalog"
contains "$CHART_DIR/templates/pi-agent-worker.yaml" 'key:[[:space:]]*PI_AGENT_MODELS_JSON' "Helm Pi worker must reference the ConfigMap model catalog"
python3 - "$ROOT_DIR/Dockerfile.api" "$ROOT_DIR/server/core/pi_agent_system_prompt.md" <<'PY' || fail "API and Pi worker images must inherit the same immutable checked-in prompt artifact"
from pathlib import Path
import sys

dockerfile = Path(sys.argv[1]).read_text(encoding="utf-8")
prompt = Path(sys.argv[2])
assert prompt.is_file()
assert prompt.read_text(encoding="utf-8").startswith("Tertius file-edit policy:")
assert "FROM python-app AS pi-agent" in dockerfile
assert "FROM python-app AS api" in dockerfile
common = dockerfile.split("FROM python-app AS pi-agent", 1)[0]
assert "COPY server/core/ ./server/core/" in common
assert "chmod 0444 /app/server/core/pi_agent_system_prompt.md" in common
PY
for file in \
  "$ROOT_DIR/server/.env.example" \
  "$CHART_DIR/values.yaml" \
  "$CHART_DIR/templates/pi-agent-worker.yaml" \
  "$ROOT_DIR/docker-compose.yml" \
  "$ROOT_DIR/docker-compose.parity.yml"; do
  not_contains "$file" 'PI_AGENT_SYSTEM_PROMPT|piAgent\.systemPrompt|systemPrompt:' "$file must not expose a runtime Pi prompt override"
done
for file in \
  "$ROOT_DIR/server/.env.example" \
  "$CHART_DIR/values.yaml" \
  "$CHART_DIR/templates/configmap.yaml" \
  "$CHART_DIR/templates/pi-agent-worker.yaml" \
  "$ROOT_DIR/docker-compose.yml" \
  "$ROOT_DIR/docker-compose.parity.yml"; do
  not_contains "$file" 'PI_AGENT_MODEL_LABEL|piAgentModelLabel' "$file must not expose the obsolete Pi model label setting"
done
not_contains "$CHART_DIR/templates/pi-agent-worker.yaml" 'pi_agent_system_prompt\.md|/app/server/core' "Helm must not mount over the image-owned Pi prompt"
not_contains "$ROOT_DIR/docker-compose.yml" 'pi_agent_system_prompt\.md|/app/server/core' "Compose dev must not mount over the image-owned Pi prompt"
not_contains "$ROOT_DIR/docker-compose.parity.yml" 'pi_agent_system_prompt\.md|/app/server/core' "Compose parity must not mount over the image-owned Pi prompt"
contains "$ROOT_DIR/docker-compose.yml" 'pi-agent-worker:' "Compose dev must define the Pi agent worker"
contains "$ROOT_DIR/docker-compose.yml" 'pi-agent-auth:' "Compose dev must define the retained Pi auth volume"
contains "$ROOT_DIR/docker-compose.yml" 'target:[[:space:]]*pi-agent' "Compose Pi worker must build the pi-agent image target"
contains "$ROOT_DIR/scripts/test-k3s-deployment.sh" 'PI_AGENT_IMAGE' "k3s harness must build and import the Pi agent image"
contains "$ROOT_DIR/scripts/test-k3s-deployment.sh" 'PI_AGENT_ENABLED' "k3s harness must gate Pi worker enablement separately from KEDA"
contains "$ROOT_DIR/scripts/harness-k3s.sh" 'pi-agent-auth.*verify|verify.*pi-agent-auth' "k3s live-flow must preflight Pi auth"
contains "$ROOT_DIR/scripts/harness-compose.sh" 'pi-agent-auth' "Compose harness must preserve or explicitly delete Pi auth"
contains "$ROOT_DIR/server/workflows/intus/pi_agent_job.py" 'finally:' "Pi worker must clean its temporary workspace on every outcome"
contains "$ROOT_DIR/server/workflows/intus/pi_agent_job.py" 'shutil\.rmtree\(root\)' "Pi worker must remove each temporary workspace"
contains "$ROOT_DIR/ci/k3s-images.txt" 'tertius-pi-agent:local' "k3s CI image list must preload the Pi agent image"
contains "$ROOT_DIR/docker-compose.yml" 'gis-cache:' "Compose dev must define the GIS cache"
contains "$ROOT_DIR/docker-compose.yml" 'GIS_CACHE_URL:[[:space:]]*http://gis-cache:8000' "Compose API must use the internal GIS cache service"
contains "$ROOT_DIR/server/.env.example" '^GIS_CACHE_URL=' "API env example must document the GIS cache endpoint"
contains "$ROOT_DIR/server/core/config.py" 'gis_cache_url:' "API settings must expose the GIS cache endpoint"
contains "$ROOT_DIR/Dockerfile.gis" 'USER 1000:1000' "GIS cache image must run as non-root"
contains "$CHART_DIR/templates/gis-cache.yaml" 'readOnlyRootFilesystem:[[:space:]]*true' "Helm GIS cache must use a read-only root filesystem"
contains "$CHART_DIR/templates/gis-cache-networkpolicy.yaml" 'app.kubernetes.io/component: api' "Helm GIS cache ingress must be API-only"
contains "$ROOT_DIR/ci/k3s-images.txt" 'tertius-gis-cache:local' "k3s CI image list must preload the GIS cache image"
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

helm template tertius "$CHART_DIR" --values "$LOCAL_VALUES" --set piAgent.enabled=true >"$TMP_DIR/helm.yaml"
docker compose -f "$ROOT_DIR/docker-compose.yml" config >"$TMP_DIR/compose-dev.yaml"
COMPOSE_PARITY_UI_PORT=18080 COMPOSE_PARITY_API_PORT=18000 \
  docker compose -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.parity.yml" config >"$TMP_DIR/compose-parity.yaml"
docker compose -f "$ROOT_DIR/docker-compose.yml" config --format json >"$TMP_DIR/compose-dev.json"
COMPOSE_PARITY_UI_PORT=18080 COMPOSE_PARITY_API_PORT=18000 \
  docker compose -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.parity.yml" config --format json >"$TMP_DIR/compose-parity.json"
docker compose -p tertius-parity-a -f "$ROOT_DIR/docker-compose.yml" config --format json >"$TMP_DIR/compose-project-a.json"
docker compose -p tertius-parity-b -f "$ROOT_DIR/docker-compose.yml" config --format json >"$TMP_DIR/compose-project-b.json"

python3 - "$TMP_DIR/compose-dev.json" "$TMP_DIR/compose-parity.json" <<'PY' || fail "Compose Pi worker scoped security/network and model catalog contract is invalid"
import copy
import json
import sys

def validate(config):
    services = config["services"]
    worker = services["pi-agent-worker"]
    assert set(worker["networks"]) == {"pi-agent-egress"}
    assert {"default", "pi-agent-egress"} <= set(services["nats"]["networks"])
    assert {"default", "pi-agent-egress"} <= set(services["otel-collector"]["networks"])
    for name in ("backend", "postgres", "keycloak"):
        assert "pi-agent-egress" not in services[name].get("networks", {})
    assert worker["user"] == "1000:1000"
    assert worker["read_only"] is True and worker["init"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert worker["pids_limit"] == 128
    assert worker["mem_limit"] == "1073741824"
    assert worker["cpus"] == 2.0
    assert worker["security_opt"] == ["no-new-privileges:true"]
    assert len(worker["volumes"]) == 1
    assert worker["volumes"][0]["source"] == "pi-agent-auth"
    assert worker["volumes"][0]["target"] == "/var/lib/pi-agent"
    tmpfs = set(worker["tmpfs"])
    assert any(item.startswith("/workspace:") and "size=128m" in item and "mode=0700" in item for item in tmpfs)
    assert any(item.startswith("/tmp:") and "size=256m" in item and "mode=0700" in item for item in tmpfs)
    assert any(item.startswith("/tmp/home:") and "size=16m" in item and "mode=0700" in item for item in tmpfs)
    env = worker["environment"]
    expected_catalog = [
        {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"},
        {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna"},
        {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
    ]
    expected = {
        "PI_AGENT_STREAM_NAME": "TERTIUS_PI_AGENT",
        "PI_AGENT_REQUEST_SUBJECT": "tertius.pi.request",
        "PI_AGENT_RESULT_SUBJECT": "tertius.pi.result",
        "PI_AGENT_WORKER_QUEUE": "pi-agent-workers",
        "PI_CODING_AGENT_DIR": "/var/lib/pi-agent",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
        "HOME": "/tmp/home",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317",
    }
    assert all(env.get(key) == value for key, value in expected.items())
    forbidden = {
        "LLM_API_" "KEY", "OPENAI_API_" "KEY", "ANTHROPIC_API_" "KEY", "GOOGLE_API_" "KEY",
        "LLM_WEEKLY_BUDGET_USD", "LLM_TIMEOUT_SECONDS", "LLM_MAX_OUTPUT_TOKENS",
    }
    assert not (forbidden & set(env))
    api_env = services["backend"]["environment"]
    assert api_env["PI_AGENT_RESULT_CONSUMER"] == "pi-agent-result-api"
    assert api_env["GIS_CACHE_URL"] == "http://gis-cache:8000"
    assert not (forbidden & set(api_env))
    gis = services["gis-cache"]
    assert gis["user"] == "1000:1000"
    assert gis["read_only"] is True and gis["init"] is True
    assert gis["cap_drop"] == ["ALL"]
    assert gis["pids_limit"] == 128
    assert gis["security_opt"] == ["no-new-privileges:true"]
    assert gis["environment"]["GIS_CACHE_ROOT"] == "/var/lib/tertius-gis"
    assert gis["environment"]["GIS_GNAF_STATES"] == "NSW"
    assert gis["environment"]["GIS_TERRAIN_DEFAULT_RADIUS_M"] == "2000"
    assert gis["environment"]["GIS_NSW_TERRAIN_ENABLED"] == "true"
    assert gis["environment"]["GIS_GA_WIND_MULTIPLIERS_ENABLED"] == "true"
    assert gis["environment"]["GIS_GA_WIND_MULTIPLIERS_BASE_URL"] == "https://thredds.nci.org.au/thredds"
    assert gis["environment"]["GIS_GA_WIND_MULTIPLIER_TIMEOUT_SECONDS"] == "30"
    assert gis["environment"]["GIS_GA_WIND_MULTIPLIER_WORKERS"] == "8"
    assert gis["environment"]["GIS_NSW_ELEVATION_INDEX_URL"].endswith("/Elevation_Index_Public/FeatureServer/0/query")
    assert gis["environment"]["GIS_NSW_DEM_DOWNLOAD_BASE_URL"] == "https://portal.spatial.nsw.gov.au/download/dem"
    assert gis["environment"]["GIS_NSW_PROPERTY_FEATURE_URL"].endswith("/NSW_Land_Parcel_Property_Theme/FeatureServer/12/query")
    assert gis["environment"]["GIS_NSW_PROPERTY_TIMEOUT_SECONDS"] == "30"
    assert gis["environment"]["GIS_MICROSOFT_BUILDINGS_ENABLED"] == "true"
    assert gis["environment"]["GIS_OVERTURE_BUILDINGS_ENABLED"] == "true"
    assert gis["environment"]["GIS_OVERTURE_BUILDINGS_TIMEOUT_SECONDS"] == "90"
    assert gis["environment"]["GIS_MICROSOFT_BUILDINGS_INDEX_URL"].endswith("/2026-07-24/dataset-links.csv")
    assert gis["environment"]["GIS_MICROSOFT_BUILDINGS_TIMEOUT_SECONDS"] == "90"
    assert gis["environment"]["GIS_ELVIS_BUILDING_HEIGHTS_ENABLED"] == "true"
    assert gis["environment"]["GIS_ELVIS_DOWNLOADABLES_URL"] == "https://api.elevation.fsdf.org.au/elevation/downloadables"
    assert gis["environment"]["GIS_ELVIS_BUILDING_HEIGHT_RADIUS_M"] == "120"
    assert gis["environment"]["GIS_ELVIS_POINT_CLOUD_TIMEOUT_SECONDS"] == "180"
    assert gis["environment"]["GIS_ELVIS_POINT_CLOUD_MAX_BYTES"] == "268435456"
    assert gis["environment"]["GIS_ELVIS_POINT_CLOUD_TOTAL_MAX_BYTES"] == "536870912"
    assert gis["environment"]["GIS_ELVIS_AWS_REGION"] == "ap-southeast-2"
    assert gis["environment"]["GIS_ELVIS_IDENTITY_POOL_ID"] == "ap-southeast-2:56462c13-533a-4f84-9a68-631dcd3345ad"
    assert len(gis["volumes"]) == 1
    assert gis["volumes"][0]["target"] == "/var/lib/tertius-gis"
    assert json.loads(api_env["PI_AGENT_MODELS_JSON"]) == expected_catalog
    assert json.loads(env["PI_AGENT_MODELS_JSON"]) == expected_catalog
    assert api_env["PI_AGENT_MODELS_JSON"] == env["PI_AGENT_MODELS_JSON"]
    assert api_env["PI_AGENT_MODEL"] == "gpt-5.6-sol"
    assert env["PI_AGENT_MODEL"] == "gpt-5.6-sol"
    assert "PI_AGENT_MODEL_LABEL" not in api_env
    assert "PI_AGENT_MODEL_LABEL" not in env

configs = []
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    validate(config)
    configs.append(config)

compose_dev, compose_parity = configs
dev_api_mounts = compose_dev["services"]["backend"].get("volumes", [])
dev_worker_mounts = compose_dev["services"]["pi-agent-worker"].get("volumes", [])
assert any(mount["target"] == "/app/server" for mount in dev_api_mounts)
assert all(mount["target"] != "/app/server" for mount in dev_worker_mounts)
assert all(
    mount["target"] != "/app/server"
    for mount in compose_parity["services"]["backend"].get("volumes", [])
)

# Mutation fixtures prove the validator is scoped to the worker contract.
mutations = []
network_mutation = copy.deepcopy(configs[1])
network_mutation["services"]["pi-agent-worker"]["networks"]["default"] = None
mutations.append(network_mutation)
user_mutation = copy.deepcopy(configs[1])
user_mutation["services"]["pi-agent-worker"]["user"] = "0:0"
mutations.append(user_mutation)
mount_mutation = copy.deepcopy(configs[1])
mount_mutation["services"]["pi-agent-worker"]["volumes"] = []
mutations.append(mount_mutation)
env_mutation = copy.deepcopy(configs[1])
env_mutation["services"]["pi-agent-worker"]["environment"]["PI_TELEMETRY"] = "1"
mutations.append(env_mutation)
for mutation in mutations:
    try:
        validate(mutation)
    except AssertionError:
        continue
    raise AssertionError("worker contract validator accepted a negative mutation")
PY

python3 - "$TMP_DIR/helm.yaml" <<'PY' || fail "Helm API and Pi worker model catalog contract is invalid"
import json
import re
import sys

rendered = open(sys.argv[1], encoding="utf-8").read()
documents = rendered.split("\n---\n")
expected = [
    {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"},
    {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna"},
    {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
]
catalog_match = re.search(r"^\s*PI_AGENT_MODELS_JSON:\s*(.+)$", rendered, re.MULTILINE)
assert catalog_match, "ConfigMap must render PI_AGENT_MODELS_JSON"
raw_catalog = catalog_match.group(1).strip()
if raw_catalog.startswith("'") and raw_catalog.endswith("'"):
    decoded = raw_catalog[1:-1].replace("''", "'")
else:
    decoded = json.loads(raw_catalog)
catalog = json.loads(decoded) if isinstance(decoded, str) else decoded
assert catalog == expected
config_doc = next(doc for doc in documents if "kind: ConfigMap" in doc and "PI_AGENT_MODELS_JSON:" in doc)
config_name = re.search(r"^metadata:\s*$\n\s+name:\s*([^\s]+)", config_doc, re.MULTILINE).group(1)
api_doc = next(doc for doc in documents if "kind: Deployment" in doc and "app.kubernetes.io/component: api" in doc)
worker_doc = next(doc for doc in documents if "kind: ScaledJob" in doc and "app.kubernetes.io/component: pi-agent-worker" in doc)
assert re.search(rf"configMapRef:\s+name:\s*{re.escape(config_name)}(?:\s|$)", api_doc)
assert re.search(
    r"- name:\s*PI_AGENT_MODELS_JSON\s+valueFrom:\s+configMapKeyRef:\s+"
    rf"name:\s*{re.escape(config_name)}\s+key:\s*PI_AGENT_MODELS_JSON(?:\s|$)",
    worker_doc,
)
assert re.search(r"- name:\s*PI_AGENT_MODEL\s+value:\s*[\"']?gpt-5\.6-sol[\"']?(?:\s|$)", worker_doc)
assert "PI_AGENT_MODEL_LABEL" not in rendered
PY

python3 - "$TMP_DIR/compose-project-a.json" "$TMP_DIR/compose-project-b.json" <<'PY' || fail "Compose Pi egress network must be project-scoped"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    first = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    second = json.load(handle)
first_name = first["networks"]["pi-agent-egress"]["name"]
second_name = second["networks"]["pi-agent-egress"]["name"]
assert first_name == "tertius-parity-a_pi-agent-egress"
assert second_name == "tertius-parity-b_pi-agent-egress"
assert first_name != second_name
PY

for file in "$TMP_DIR/helm.yaml" "$TMP_DIR/compose-dev.yaml" "$TMP_DIR/compose-parity.yaml"; do
  contains "$file" 'TERTIUS_COMPILE' "${file} must include compile stream name"
  contains "$file" 'tertius\.compile\.request' "${file} must include compile request subject"
  contains "$file" 'tertius\.compile\.result' "${file} must include compile result subject"
  contains "$file" 'compile-workers' "${file} must include compile worker queue"
  contains "$file" 'compile-result-api' "${file} must include compile result consumer"
  contains "$file" 'TERTIUS_PI_AGENT' "${file} must include Pi agent stream name"
  contains "$file" 'tertius\.pi\.request' "${file} must include Pi agent request subject"
  contains "$file" 'tertius\.pi\.result' "${file} must include Pi agent result subject"
  contains "$file" 'pi-agent-workers' "${file} must include Pi agent worker queue"
  contains "$file" 'pi-agent-result-api' "${file} must include Pi result consumer"
  contains "$file" '8388608' "${file} must include compile request max bytes"
  contains "$file" '33554432' "${file} must include compile result max bytes/NATS max payload"
  contains "$file" 'COMPILE_SIDECAR_TTL_SECONDS' "${file} must include compile sidecar TTL"
  contains "$file" 'COMPILE_SIDECAR_MAX_BYTES' "${file} must include compile sidecar capacity"
  contains "$file" '8589934592' "${file} must include the 8 GiB compile sidecar capacity"
  contains "$file" 'TERTIUS_BILLING' "${file} must include billing stream name"
  contains "$file" 'tertius\.billing\.usage\.llm\.tokens' "${file} must include billing subject"
  contains "$file" 'tertius-api' "${file} must include API service name"
  contains "$file" 'tertius-ui' "${file} must include UI service name"
  contains "$file" 'tertius-gis-cache|gis-cache' "${file} must include GIS cache service name"
  contains "$file" 'GIS_CACHE_URL' "${file} must include the internal GIS cache URL"
  contains "$file" 'GIS_NSW_PROPERTY_FEATURE_URL' "${file} must include the NSW property boundary service contract"
  contains "$file" 'GIS_MICROSOFT_BUILDINGS_INDEX_URL' "${file} must include the reusable open building-data contract"
  contains "$file" 'GIS_OVERTURE_BUILDINGS_ENABLED' "${file} must include the reconciled building-data contract"
  contains "$file" 'GIS_ELVIS_BUILDING_HEIGHTS_ENABLED' "${file} must include the classified point-cloud height contract"
  contains "$file" '4317|grpc' "${file} must include OTEL gRPC contract"
  contains "$file" 'victoriatraces' "${file} must include VictoriaTraces"
  contains "$file" '10428' "${file} must include VictoriaTraces port"
  not_contains "$file" 'PI_AGENT_SYSTEM_PROMPT|piAgent\.systemPrompt|pi_agent_system_prompt\.md|/app/server/core' "${file} must use the image-owned Pi prompt without runtime overrides or mounts"
done

for file in "$TMP_DIR/compose-dev.yaml" "$TMP_DIR/compose-parity.yaml"; do
  contains "$file" 'pi-agent-worker' "${file} must include the serial Pi agent worker"
  contains "$file" 'pi-agent-auth' "${file} must include the retained Pi auth volume"
  contains "$file" '/var/lib/pi-agent' "${file} must mount the Pi auth directory"
  not_contains "$file" "${legacy_provider_key_pattern}|LLM_WEEKLY_BUDGET_USD|LLM_TIMEOUT_SECONDS|LLM_MAX_OUTPUT_TOKENS|LLM_FILE_EDIT_MAX_OUTPUT_TOKENS|LLM_FILE_EDIT_MAX_GENERATION_ATTEMPTS|LLM_FILE_EDIT_MAX_RATE_LIMIT_ATTEMPTS|LLM_FILE_EDIT_RATE_LIMIT_BACKOFF" "${file} must not include direct-provider configuration"
  not_contains "$file" 'source: .*/\.pi([/:]|$)' "${file} must not bind-mount host ~/.pi"
done

contains "$TMP_DIR/compose-dev.yaml" 'target: pi-agent' "Compose Pi worker must render the pi-agent build target"
not_contains "$TMP_DIR/compose-dev.yaml" 'pi-agent-worker:[[:space:][:print:]]*default:' "Compose Pi worker must not join the default application network"

contains "$TMP_DIR/helm.yaml" 'insert/opentelemetry/v1/traces' "Helm collector render must include VictoriaTraces OTLP HTTP ingest path"
contains "$ROOT_DIR/infra/otel/otel-collector-local.yaml" 'insert/opentelemetry/v1/traces' "Compose collector config must include VictoriaTraces OTLP HTTP ingest path"

contains "$TMP_DIR/helm.yaml" 'API_BASE_PATH: "/api"|API_BASE_PATH[^[:alnum:]_/.-]*/api' "Helm local runtime must use API_BASE_PATH=/api"
contains "$TMP_DIR/compose-parity.yaml" 'VITE_API_URL: /api|VITE_API_URL=/api' "Compose parity UI must use VITE_API_URL=/api"
contains "$TMP_DIR/compose-dev.yaml" '5173' "Compose dev should expose Vite/HMR port 5173"

not_contains "$TMP_DIR/compose-parity.yaml" '5173:5173|published: "5173"|target: 5173' "Compose parity must not expose Vite port 5173"
not_contains "$TMP_DIR/compose-parity.yaml" 'node:20|npm install|npm run dev|CHOKIDAR_USEPOLLING|source: .*/ui|source: .*/server' "Compose parity must not retain dev image, commands, HMR env, or API/UI bind mounts"
contains "$TMP_DIR/compose-parity.yaml" '18080|published: "18080"' "Compose parity must expose default UI port 18080"
contains "$TMP_DIR/compose-parity.yaml" '18000|published: "18000"' "Compose parity must expose default API port 18000"
contains "$TMP_DIR/compose-parity.yaml" '18004|published: "18004"' "Compose parity must expose default GIS cache port 18004"

contains "$ROOT_DIR/docs/harness/local-harness.md" 'http://localhost:18080' "Harness docs must document UI port 18080"
contains "$ROOT_DIR/docs/harness/local-harness.md" 'http://localhost:18000' "Harness docs must document API port 18000"
contains "$ROOT_DIR/docs/harness/local-harness.md" 'http://localhost:10428' "Harness docs must document traces port 10428"
contains "$ROOT_DIR/docs/harness/runtime-parity.md" 'Compose dev' "Runtime parity docs must describe Compose dev differences"
contains "$ROOT_DIR/docs/harness/runtime-parity.md" 'bind-mounted API.*image-backed worker' "Runtime parity docs must describe Compose dev Pi prompt drift"
contains "$ROOT_DIR/docs/harness/runtime-parity.md" 'rebuild.*worker' "Runtime parity docs must require rebuilding the Compose dev Pi worker after prompt changes"
contains "$ROOT_DIR/docs/harness/runtime-parity.md" 'no environment, Secret, ConfigMap, workspace, or OAuth-PVC copy' "Runtime parity docs must preserve the immutable Pi prompt distribution contract"
contains "$ROOT_DIR/docs/harness/runtime-parity.md" 'traces backend' "Runtime parity docs must describe traces backend differences"

if [ "$failures" -ne 0 ]; then
  echo "Runtime parity check failed with ${failures} issue(s)." >&2
  exit 1
fi

echo "Runtime parity check passed."
