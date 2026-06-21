#!/usr/bin/env bash
set -Eeuo pipefail

COMPILE_ONLY=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [--compile-only] <ui-base-url>

Runs an authenticated live workflow through the UI origin:
  UI /api proxy -> Intus project save -> compile queue/status
  UI /api proxy -> LLM file edit job -> compile queue/status

Required environment:
  KEYCLOAK_TOKEN_URL
  KEYCLOAK_SMOKE_USERNAME (default: demo)
  KEYCLOAK_SMOKE_PASSWORD (default: demo)
  KEYCLOAK_CLIENT_ID      (default: tertius-ui)

Optional environment:
  LIVE_FLOW_PROJECT
  LIVE_FLOW_MODEL_ID
  LIVE_FLOW_COMPILE_TIMEOUT_SECONDS (default: 240)
  LIVE_FLOW_AI_TIMEOUT_SECONDS      (default: 300)
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --compile-only) COMPILE_ONLY=true ;;
    --help|-h) usage; exit 0 ;;
    *) break ;;
  esac
  shift
done

[ "$#" -eq 1 ] || {
  usage >&2
  exit 2
}

UI_BASE_URL="${1%/}"
API_BASE_URL="${UI_BASE_URL}/api/intus"
PROJECT_NAME="${LIVE_FLOW_PROJECT:-agent_live_flow_$(date -u +%Y%m%d%H%M%S)}"
COMPILE_TIMEOUT_SECONDS="${LIVE_FLOW_COMPILE_TIMEOUT_SECONDS:-240}"
AI_TIMEOUT_SECONDS="${LIVE_FLOW_AI_TIMEOUT_SECONDS:-300}"
TEMP_FILES=""
TOKEN=""

cleanup() {
  for file in $TEMP_FILES; do
    [ -f "$file" ] && rm -f "$file"
  done
}
trap cleanup EXIT

tmpfile() {
  file=$(mktemp "${TMPDIR:-/tmp}/tertius-live-flow.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${file}"
  printf '%s\n' "$file"
}

json_get() {
  file=$1
  expr=$2
  python3 - "$file" "$expr" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

value = data
for part in sys.argv[2].split("."):
    if not part:
        continue
    if isinstance(value, list):
        value = value[int(part)]
    else:
        value = value.get(part)
    if value is None:
        break

if isinstance(value, (dict, list)):
    print(json.dumps(value))
elif value is not None:
    print(value)
PY
}

request_token() {
  [ -n "${KEYCLOAK_TOKEN_URL:-}" ] || {
    echo "FAIL KEYCLOAK_TOKEN_URL is required for live-flow validation" >&2
    exit 1
  }
  token_body=$(tmpfile)
  status=$(curl --silent --show-error --max-time 20 \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=password" \
    -d "client_id=${KEYCLOAK_CLIENT_ID:-tertius-ui}" \
    -d "username=${KEYCLOAK_SMOKE_USERNAME:-demo}" \
    -d "password=${KEYCLOAK_SMOKE_PASSWORD:-demo}" \
    "$KEYCLOAK_TOKEN_URL" \
    -o "$token_body" \
    --write-out '%{http_code}') || {
    echo "FAIL auth token request failed" >&2
    cat "$token_body" >&2 || true
    exit 1
  }
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "FAIL auth token request returned HTTP ${status}" >&2
    cat "$token_body" >&2
    exit 1
  fi
  TOKEN=$(json_get "$token_body" access_token)
  [ -n "$TOKEN" ] || {
    echo "FAIL auth token response did not include access_token" >&2
    cat "$token_body" >&2
    exit 1
  }
  echo "PASS auth token request"
}

api_request() {
  method=$1
  url=$2
  body_file=${3:-}
  out=$(tmpfile)
  args=(
    --silent --show-error --max-time 60
    -H "Authorization: Bearer ${TOKEN}"
    -H "Content-Type: application/json"
    -X "$method"
    -o "$out"
    --write-out "%{http_code}"
  )
  if [ -n "$body_file" ]; then
    args+=(--data-binary "@${body_file}")
  fi
  status=$(curl "${args[@]}" "$url") || {
    echo "FAIL ${method} ${url}: curl failed" >&2
    cat "$out" >&2 || true
    exit 1
  }
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "FAIL ${method} ${url}: HTTP ${status}" >&2
    cat "$out" >&2
    exit 1
  fi
  printf '%s\n' "$out"
}

api_request_allow_exists() {
  method=$1
  url=$2
  out=$(tmpfile)
  status=$(curl --silent --show-error --max-time 60 \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -X "$method" \
    -o "$out" \
    --write-out "%{http_code}" \
    "$url") || {
    echo "FAIL ${method} ${url}: curl failed" >&2
    cat "$out" >&2 || true
    exit 1
  }
  if [ "$status" -ge 200 ] && [ "$status" -lt 300 ]; then
    printf '%s\n' "$out"
    return
  fi
  if [ "$status" = "400" ] && grep -qi "already exists" "$out"; then
    printf '%s\n' "$out"
    return
  fi
  echo "FAIL ${method} ${url}: HTTP ${status}" >&2
  cat "$out" >&2
  exit 1
}

write_json() {
  file=$1
  shift
  python3 - "$file" "$@" <<'PY'
import json
import sys

target = sys.argv[1]
kind = sys.argv[2]

if kind == "save":
    code = """import build123d as bd
box = bd.Box(10, 10, 10)
"""
    payload = {"code": code, "file": "design.py"}
elif kind == "compile":
    code = sys.argv[3]
    originating = sys.argv[4] if len(sys.argv) > 4 else ""
    payload = {
        "code": code,
        "export_format": "stl",
        "quality": "draft",
        "file": "design.py",
    }
    if originating:
        payload["originating_llm_edit_job_id"] = originating
elif kind == "llm_edit":
    files = json.loads(sys.argv[3])
    active = ""
    for item in files:
        if item.get("filename") == "design.py":
            active = item.get("id", "")
            break
    if not active and files:
        active = files[0].get("id", "")
    payload = {
        "prompt": "Add a single harmless Python comment '# live AI edit smoke' near the top of design.py. Do not change geometry.",
        "files": files[:20],
        "active_file_id": active or None,
        "metadata": {"source": "smoke-live-flow"},
    }
    model_id = sys.argv[4] if len(sys.argv) > 4 else ""
    if model_id:
        payload["model_id"] = model_id
else:
    raise SystemExit(f"unknown payload kind: {kind}")

with open(target, "w", encoding="utf-8") as f:
    json.dump(payload, f)
PY
}

ensure_project() {
  api_request_allow_exists POST "${API_BASE_URL}/projects/${PROJECT_NAME}/new" >/dev/null
  echo "PASS project available: ${PROJECT_NAME}"
}

save_seed_code() {
  request=$(tmpfile)
  write_json "$request" save
  api_request POST "${API_BASE_URL}/projects/${PROJECT_NAME}/save" "$request" >/dev/null
  echo "PASS seed code saved through UI /api proxy"
}

load_design_code() {
  encoded_file="design.py"
  body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/code?file=${encoded_file}")
  json_get "$body" code
}

file_metadata_json() {
  body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/files")
  python3 - "$body" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

metadata = data.get("file_metadata") or []
if not metadata:
    raise SystemExit("file metadata response is empty")
print(json.dumps(metadata))
PY
}

compile_and_wait() {
  label=$1
  originating=${2:-}
  code=$(load_design_code)
  request=$(tmpfile)
  response=$(tmpfile)
  write_json "$request" compile "$code" "$originating"
  response=$(api_request POST "${API_BASE_URL}/projects/${PROJECT_NAME}/compile" "$request")
  job_id=$(json_get "$response" job_id)
  [ -n "$job_id" ] || {
    echo "FAIL ${label}: compile response did not include job_id" >&2
    cat "$response" >&2
    exit 1
  }

  deadline=$((SECONDS + COMPILE_TIMEOUT_SECONDS))
  status_body=$(tmpfile)
  while [ "$SECONDS" -lt "$deadline" ]; do
    status_body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/compile/jobs/${job_id}")
    job_status=$(json_get "$status_body" status)
    case "$job_status" in
      succeeded)
        artifact_id=$(json_get "$status_body" artifact_id)
        [ -n "$artifact_id" ] || {
          echo "FAIL ${label}: compile succeeded without artifact_id" >&2
          cat "$status_body" >&2
          exit 1
        }
        echo "PASS ${label}: compile job succeeded (${job_id})"
        return
        ;;
      failed)
        echo "FAIL ${label}: compile job failed" >&2
        cat "$status_body" >&2
        exit 1
        ;;
    esac
    sleep 3
  done
  echo "FAIL ${label}: compile job timed out" >&2
  cat "$status_body" >&2
  exit 1
}

ai_edit_and_wait() {
  metadata=$(file_metadata_json)
  request=$(tmpfile)
  response=$(tmpfile)
  write_json "$request" llm_edit "$metadata" "${LIVE_FLOW_MODEL_ID:-}"
  response=$(api_request POST "${API_BASE_URL}/projects/${PROJECT_NAME}/files/llm-edit/jobs" "$request")
  job_id=$(json_get "$response" job_id)
  [ -n "$job_id" ] || {
    echo "FAIL AI edit: response did not include job_id" >&2
    cat "$response" >&2
    exit 1
  }

  deadline=$((SECONDS + AI_TIMEOUT_SECONDS))
  status_body=$(tmpfile)
  while [ "$SECONDS" -lt "$deadline" ]; do
    status_body=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/files/llm-edit/jobs/${job_id}")
    job_status=$(json_get "$status_body" status)
    case "$job_status" in
      succeeded)
        outcome=$(json_get "$status_body" result.outcome)
        [ -n "$outcome" ] || {
          echo "FAIL AI edit: completed without result outcome" >&2
          cat "$status_body" >&2
          exit 1
        }
        echo "PASS AI edit job succeeded (${job_id}, outcome=${outcome})" >&2
        printf '%s\n' "$job_id"
        return
        ;;
      failed)
        echo "FAIL AI edit job failed" >&2
        cat "$status_body" >&2
        exit 1
        ;;
    esac
    sleep 5
  done
  echo "FAIL AI edit job timed out" >&2
  cat "$status_body" >&2
  exit 1
}

request_token
ensure_project
save_seed_code
compile_and_wait "pre-edit"

if [ "$COMPILE_ONLY" = true ]; then
  echo "SKIP AI edit flow because --compile-only was requested"
  exit 0
fi

llm_job_id=$(ai_edit_and_wait)
compile_and_wait "post-AI-edit" "$llm_job_id"
echo "PASS live frontend proxy -> backend -> compile/AI edit flow"
