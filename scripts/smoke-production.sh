#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${1:-https://tertius.johnsonyuen.com}"
BASE_URL="${BASE_URL%/}"
ATTEMPTS="${SMOKE_ATTEMPTS:-1}"
RETRY_DELAY="${SMOKE_RETRY_DELAY_SECONDS:-10}"
CURL_TIMEOUT="${SMOKE_CURL_TIMEOUT_SECONDS:-20}"
INITIAL_DELAY="${SMOKE_INITIAL_DELAY_SECONDS:-0}"

for setting in "$ATTEMPTS" "$RETRY_DELAY" "$CURL_TIMEOUT" "$INITIAL_DELAY"; do
  if ! [[ "$setting" =~ ^[0-9]+$ ]]; then
    echo "Smoke timing settings must be integers." >&2
    exit 2
  fi
done
if [ "$ATTEMPTS" -eq 0 ] || [ "$RETRY_DELAY" -eq 0 ] || [ "$CURL_TIMEOUT" -eq 0 ]; then
  echo "Attempts, retry delay, and curl timeout must be positive." >&2
  exit 2
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tertius-production-smoke.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT

print_response_summary() {
  local label="$1"
  local status="$2"
  local body_file="$3"
  local content_type="$4"
  local remote_ip="$5"

  echo "${label}: HTTP ${status:-unknown}; content-type=${content_type:-unknown}; remote-ip=${remote_ip:-unknown}" >&2
  if [ -s "$body_file" ]; then
    echo "Response body (first 4000 bytes):" >&2
    head -c 4000 "$body_file" >&2
    echo >&2
  fi
}

fetch() {
  local label="$1"
  local url="$2"
  local body_file="$3"
  local metadata

  if ! metadata="$(curl \
    --silent \
    --show-error \
    --location \
    --max-redirs 5 \
    --connect-timeout 10 \
    --max-time "$CURL_TIMEOUT" \
    --output "$body_file" \
    --write-out '%{http_code}\t%{content_type}\t%{remote_ip}' \
    "$url")"; then
    echo "${label}: request failed: ${url}" >&2
    [ ! -s "$body_file" ] || print_response_summary "$label" "curl-error" "$body_file" "" ""
    return 1
  fi

  local status content_type remote_ip
  IFS=$'\t' read -r status content_type remote_ip <<<"$metadata"
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    print_response_summary "$label" "$status" "$body_file" "$content_type" "$remote_ip"
    return 1
  fi

  if grep -Eiq 'cloudflare tunnel error|error code:?[[:space:]]*10(15|16|24|33)|cf-error-details' "$body_file"; then
    echo "${label}: Cloudflare error page detected despite HTTP ${status}." >&2
    print_response_summary "$label" "$status" "$body_file" "$content_type" "$remote_ip"
    return 1
  fi
}

check_once() {
  local root_body="$TEMP_DIR/root-body"
  local health_body="$TEMP_DIR/health-body"
  : >"$root_body"
  : >"$health_body"

  fetch "UI root" "${BASE_URL}/" "$root_body" || return 1
  if ! grep -Eiq '<!doctype[[:space:]]+html|<html([[:space:]>])' "$root_body"; then
    echo "UI root: expected an HTML document." >&2
    print_response_summary "UI root" "2xx" "$root_body" "" ""
    return 1
  fi

  fetch "API health" "${BASE_URL}/api/intus/health" "$health_body" || return 1
  if ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$health_body"; then
    echo 'API health: expected a JSON response containing "status": "ok".' >&2
    print_response_summary "API health" "2xx" "$health_body" "" ""
    return 1
  fi

  echo "PASS ${BASE_URL}: UI and public API health checks succeeded."
}

if [ "$INITIAL_DELAY" -gt 0 ]; then
  echo "Waiting ${INITIAL_DELAY}s for the production reconciler before checking the new deployment."
  sleep "$INITIAL_DELAY"
fi

for attempt in $(seq 1 "$ATTEMPTS"); do
  echo "Production smoke attempt ${attempt}/${ATTEMPTS}"
  if check_once; then
    exit 0
  fi
  if [ "$attempt" -lt "$ATTEMPTS" ]; then
    echo "Production is not ready; retrying in ${RETRY_DELAY}s." >&2
    sleep "$RETRY_DELAY"
  fi
done

echo "FAIL ${BASE_URL} did not become healthy after ${ATTEMPTS} attempt(s)." >&2
exit 1
