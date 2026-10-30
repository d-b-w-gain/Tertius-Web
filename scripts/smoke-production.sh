#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${1:-https://tertius.gainengineering.com.au}"
BASE_URL="${BASE_URL%/}"
ATTEMPTS="${SMOKE_ATTEMPTS:-1}"
RETRY_DELAY="${SMOKE_RETRY_DELAY_SECONDS:-10}"
CURL_TIMEOUT="${SMOKE_CURL_TIMEOUT_SECONDS:-20}"
INITIAL_DELAY="${SMOKE_INITIAL_DELAY_SECONDS:-0}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The UI bundle must contain the commit of the promoted UI image tag
# (master-<run>-<attempt>-<sha>), so a site that stops deploying fails here.
# SMOKE_EXPECTED_COMMIT overrides the tag; "skip" disables the check.
VALUES_FILE="${SMOKE_VALUES_FILE:-${ROOT_DIR}/infra/charts/tertius/values.yaml}"
EXPECTED_COMMIT="${SMOKE_EXPECTED_COMMIT:-}"
# A mismatch within this many seconds of the promotion commit only warns,
# leaving the production reconciler time to roll the new images out.
VERSION_GRACE="${SMOKE_VERSION_GRACE_SECONDS:-0}"

for setting in "$ATTEMPTS" "$RETRY_DELAY" "$CURL_TIMEOUT" "$INITIAL_DELAY" "$VERSION_GRACE"; do
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

promoted_ui_commit() {
  [ -f "$VALUES_FILE" ] || return 0
  local tag
  tag="$(awk '
    /^ui:/ { in_ui = 1; next }
    /^[^[:space:]#]/ { in_ui = 0 }
    in_ui && $1 == "tag:" { print $2; exit }
  ' "$VALUES_FILE")"
  tag="${tag//\"/}"
  if [[ "$tag" =~ ^master-[0-9]+-[0-9]+-([0-9a-f]{7,40})$ ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

promotion_age_seconds() {
  local committed
  committed="$(git -C "$ROOT_DIR" log -1 --format=%ct -- "$VALUES_FILE" 2>/dev/null || true)"
  [ -n "$committed" ] || return 1
  echo $(($(date +%s) - committed))
}

check_version() {
  local root_body="$1"
  local bundle_body="$TEMP_DIR/bundle-body"
  : >"$bundle_body"

  local bundle_path
  bundle_path="$(grep -oE 'src="/assets/index-[^"]+\.js"' "$root_body" | head -n 1 | sed -E 's/^src="|"$//g' || true)"
  if [ -z "$bundle_path" ]; then
    echo "UI version: could not find the UI bundle in the HTML document." >&2
    return 1
  fi
  fetch "UI bundle" "${BASE_URL}${bundle_path}" "$bundle_body" || return 1
  # Vite bakes the build commit into the bundle as a string literal.
  if grep -Eq "[\`\"']${EXPECTED_COMMIT}[\`\"']" "$bundle_body"; then
    echo "UI version: running ${EXPECTED_COMMIT}."
    return 0
  fi

  local age
  if [ "$VERSION_GRACE" -gt 0 ] && age="$(promotion_age_seconds)" && [ "$age" -lt "$VERSION_GRACE" ]; then
    echo "WARN UI version: ${EXPECTED_COMMIT} was promoted ${age}s ago and is not live yet (grace ${VERSION_GRACE}s)." >&2
    return 0
  fi
  echo "UI version: expected the promoted commit ${EXPECTED_COMMIT} in ${bundle_path}, but the live UI is a different build. Production is not deploying promoted images." >&2
  return 1
}

check_once() {
  local root_body="$TEMP_DIR/root-body"
  local health_body="$TEMP_DIR/health-body"
  local oidc_body="$TEMP_DIR/oidc-body"
  : >"$root_body"
  : >"$health_body"
  : >"$oidc_body"

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

  fetch "OIDC discovery" "${BASE_URL}/realms/tertius/.well-known/openid-configuration" "$oidc_body" || return 1
  local expected_issuer actual_issuer
  expected_issuer="${BASE_URL}/realms/tertius"
  actual_issuer="$(sed -nE 's/.*"issuer"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$oidc_body" | head -n 1)"
  if [ "$actual_issuer" != "$expected_issuer" ]; then
    echo "OIDC discovery: expected issuer ${expected_issuer}, got ${actual_issuer:-missing}." >&2
    print_response_summary "OIDC discovery" "2xx" "$oidc_body" "" ""
    return 1
  fi

  if [ "$EXPECTED_COMMIT" = "skip" ]; then
    echo "PASS ${BASE_URL}: UI, public API, and OIDC discovery checks succeeded (version check skipped)."
    return 0
  fi
  check_version "$root_body" || return 1

  echo "PASS ${BASE_URL}: UI, public API, OIDC discovery, and UI version checks succeeded."
}

if [ -z "$EXPECTED_COMMIT" ]; then
  EXPECTED_COMMIT="$(promoted_ui_commit)"
  if [ -z "$EXPECTED_COMMIT" ]; then
    echo "No promoted UI image tag found in ${VALUES_FILE}; skipping the UI version check." >&2
    EXPECTED_COMMIT="skip"
  fi
elif [ "$EXPECTED_COMMIT" != "skip" ] && ! [[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "SMOKE_EXPECTED_COMMIT must be a hex commit SHA or \"skip\"." >&2
  exit 2
fi

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
