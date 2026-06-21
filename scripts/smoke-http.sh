#!/usr/bin/env bash
set -Eeuo pipefail

AUTH=false
COMPILE=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [--auth] [--compile] <ui-base-url> <api-base-url>

Runs shared HTTP smoke checks. Optional auth/compile checks use:
  KEYCLOAK_TOKEN_URL
  KEYCLOAK_SMOKE_USERNAME (default: demo)
  KEYCLOAK_SMOKE_PASSWORD (default: demo)
  KEYCLOAK_CLIENT_ID      (default: tertius-ui)
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --auth) AUTH=true ;;
    --compile) COMPILE=true ;;
    --help|-h) usage; exit 0 ;;
    *) break ;;
  esac
  shift
done

[ "$#" -eq 2 ] || {
  usage >&2
  exit 2
}

UI_BASE_URL="${1%/}"
API_BASE_URL="${2%/}"
TEMP_FILES=""
TOKEN=""

cleanup() {
  for file in $TEMP_FILES; do
    [ -f "$file" ] && rm -f "$file"
  done
}
trap cleanup EXIT

tmpfile() {
  file=$(mktemp "${TMPDIR:-/tmp}/tertius-smoke.XXXXXX")
  TEMP_FILES="${TEMP_FILES} ${file}"
  printf '%s\n' "$file"
}

fetch() {
  url=$1
  out=$(tmpfile)
  status=$(curl --silent --show-error --max-time 20 -o "$out" --write-out '%{http_code}' "$url") || {
    echo "FAIL ${url}: curl failed" >&2
    cat "$out" >&2 || true
    exit 1
  }
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "FAIL ${url}: HTTP ${status}" >&2
    cat "$out" >&2 || true
    exit 1
  fi
  printf '%s\n' "$out"
}

expect_html() {
  body=$(fetch "$1")
  if ! grep -Eiq '<html|<!doctype html' "$body"; then
    echo "FAIL $1: expected HTML" >&2
    cat "$body" >&2
    exit 1
  fi
  echo "PASS UI root returns HTML"
}

expect_same_body() {
  left_url=$1
  right_url=$2
  label=$3
  left=$(fetch "$left_url")
  right=$(fetch "$right_url")
  if ! cmp -s "$left" "$right"; then
    echo "FAIL ${label}: proxied and direct responses differ" >&2
    echo "Proxied response:" >&2
    cat "$left" >&2
    echo >&2
    echo "Direct response:" >&2
    cat "$right" >&2
    exit 1
  fi
  echo "PASS ${label}"
}

request_token() {
  [ -n "${KEYCLOAK_TOKEN_URL:-}" ] || {
    echo "KEYCLOAK_TOKEN_URL is required for --auth/--compile" >&2
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
  TOKEN=$(python3 - "$token_body" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("access_token", ""))
PY
)
  [ -n "$TOKEN" ] || {
    echo "FAIL auth token response did not include access_token" >&2
    cat "$token_body" >&2
    exit 1
  }
  echo "PASS auth token request"
}

compile_smoke() {
  [ -n "$TOKEN" ] || request_token
  request=$(tmpfile)
  response=$(tmpfile)
  status_body=$(tmpfile)
  python3 - "$request" <<'PY'
import json
import sys

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({
        "code": "import build123d as bd\nbox = bd.Box(10, 10, 10)\n",
        "export_format": "stl",
        "file": "design.py",
    }, f)
PY
  project="${COMPILE_SMOKE_PROJECT:-default_purlin}"
  enqueue_url="${API_BASE_URL}/api/intus/projects/${project}/compile"
  curl --fail --silent --show-error --max-time 30 \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -X POST --data-binary "@${request}" \
    "$enqueue_url" -o "$response"
  job_id=$(python3 - "$response" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    body = json.load(f)
if body.get("success") is not True:
    raise SystemExit(f"Unexpected compile response: {body}")
print(body.get("job_id", ""))
PY
)
  [ -n "$job_id" ] || {
    echo "FAIL compile response did not include job_id" >&2
    cat "$response" >&2
    exit 1
  }
  status_url="${API_BASE_URL}/api/intus/projects/${project}/compile/jobs/${job_id}"
  for _ in $(seq 1 60); do
    curl --fail --silent --show-error --max-time 20 \
      -H "Authorization: Bearer ${TOKEN}" \
      "$status_url" -o "$status_body"
    job_status=$(python3 - "$status_body" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("status", ""))
PY
)
    [ "$job_status" = "succeeded" ] && {
      echo "PASS compile job succeeded"
      return
    }
    [ "$job_status" = "failed" ] && {
      echo "FAIL compile job failed" >&2
      cat "$status_body" >&2
      exit 1
    }
    sleep 3
  done
  echo "FAIL compile job timed out" >&2
  cat "$status_body" >&2
  exit 1
}

expect_html "${UI_BASE_URL}/"
fetch "${API_BASE_URL}/" >/dev/null
echo "PASS direct API root responds"
fetch "${API_BASE_URL}/api/intus/health" >/dev/null
echo "PASS direct API health responds"
expect_same_body "${UI_BASE_URL}/api/" "${API_BASE_URL}/" "frontend /api/ proxy"
expect_same_body "${UI_BASE_URL}/api/intus/health" "${API_BASE_URL}/api/intus/health" "frontend /api/intus/health proxy"

if [ "$AUTH" = true ]; then
  request_token
fi
if [ "$COMPILE" = true ]; then
  compile_smoke
fi
