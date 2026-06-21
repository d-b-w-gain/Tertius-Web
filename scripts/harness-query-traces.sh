#!/usr/bin/env bash
set -Eeuo pipefail

TRACES_BASE_URL="${TRACES_BASE_URL:-http://localhost:10428}"
WINDOW_MINUTES=30
LIMIT=20
SERVICES=()
CROSS_SERVICES=()
REQUIRE_CROSS_SERVICE=false

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--service tertius-api] [--service tertius-ui] [--window-minutes 30]
  $(basename "$0") --require-cross-service --cross-service tertius-api --cross-service tertius-compile-job

Environment:
  TRACES_BASE_URL  VictoriaTraces base URL. Default: http://localhost:10428
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --base-url)
      TRACES_BASE_URL="${2:-}"
      shift 2
      ;;
    --service)
      SERVICES+=("${2:-}")
      shift 2
      ;;
    --cross-service)
      CROSS_SERVICES+=("${2:-}")
      shift 2
      ;;
    --window-minutes)
      WINDOW_MINUTES="${2:-}"
      shift 2
      ;;
    --limit)
      LIMIT="${2:-}"
      shift 2
      ;;
    --require-cross-service)
      REQUIRE_CROSS_SERVICE=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [ "${#SERVICES[@]}" -eq 0 ]; then
  SERVICES=(tertius-api tertius-compile-job)
fi

if [ "${#CROSS_SERVICES[@]}" -eq 0 ]; then
  CROSS_SERVICES=(tertius-api tertius-compile-job)
fi

for service in "${SERVICES[@]}" "${CROSS_SERVICES[@]}"; do
  if [ -z "$service" ]; then
    echo "Empty service name." >&2
    exit 2
  fi
done

case "$WINDOW_MINUTES" in
  ''|*[!0-9]*)
    echo "--window-minutes must be a positive integer." >&2
    exit 2
    ;;
esac

case "$LIMIT" in
  ''|*[!0-9]*)
    echo "--limit must be a positive integer." >&2
    exit 2
    ;;
esac

if [ "$WINDOW_MINUTES" -le 0 ] || [ "$LIMIT" -le 0 ]; then
  echo "--window-minutes and --limit must be positive." >&2
  exit 2
fi

TMP_DIR="${TMPDIR:-/tmp}/tertius-trace-queries.$$"
mkdir -p "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT

now_seconds="$(date -u +%s)"
end_micros=$((now_seconds * 1000000))
start_micros=$(((now_seconds - WINDOW_MINUTES * 60) * 1000000))

services_json="$TMP_DIR/services.json"
curl --fail --silent --show-error \
  "${TRACES_BASE_URL%/}/select/jaeger/api/services" >"$services_json"

python3 - "$services_json" "${SERVICES[@]}" <<'PY'
import json
import sys

path, wanted = sys.argv[1], sys.argv[2:]
with open(path, encoding="utf-8") as f:
    payload = json.load(f)
available = set(payload.get("data") or [])
missing = [service for service in wanted if service not in available]
if missing:
    print("Missing services: " + ", ".join(missing), file=sys.stderr)
    print("Available services: " + ", ".join(sorted(available)), file=sys.stderr)
    raise SystemExit(1)
print("Services found: " + ", ".join(wanted))
PY

trace_ids_file="$TMP_DIR/trace-ids.txt"
: >"$trace_ids_file"

for service in "${SERVICES[@]}"; do
  traces_json="$TMP_DIR/traces-${service}.json"
  curl --fail --silent --show-error --get \
    --data-urlencode "service=${service}" \
    --data-urlencode "start=${start_micros}" \
    --data-urlencode "end=${end_micros}" \
    --data-urlencode "limit=${LIMIT}" \
    "${TRACES_BASE_URL%/}/select/jaeger/api/traces" >"$traces_json"
  python3 - "$traces_json" "$service" "$trace_ids_file" <<'PY'
import json
import sys

path, service, trace_ids_path = sys.argv[1:4]
with open(path, encoding="utf-8") as f:
    payload = json.load(f)
traces = payload.get("data") or []
if not traces:
    print(f"No traces found for {service}", file=sys.stderr)
    raise SystemExit(1)
with open(trace_ids_path, "a", encoding="utf-8") as out:
    for trace in traces:
        trace_id = trace.get("traceID") or trace.get("traceId") or trace.get("trace_id")
        if trace_id:
            out.write(trace_id + "\n")
print(f"{service}: {len(traces)} trace(s)")
PY
done

if [ "$REQUIRE_CROSS_SERVICE" = true ]; then
  sort -u "$trace_ids_file" >"$TMP_DIR/trace-ids-unique.txt"
  found=false
  while IFS= read -r trace_id; do
    [ -n "$trace_id" ] || continue
    trace_json="$TMP_DIR/trace-${trace_id}.json"
    curl --fail --silent --show-error \
      "${TRACES_BASE_URL%/}/select/jaeger/api/traces/${trace_id}" >"$trace_json"
    if python3 - "$trace_json" "$trace_id" "${CROSS_SERVICES[@]}" <<'PY'
import json
import sys

path, trace_id, wanted = sys.argv[1], sys.argv[2], sys.argv[3:]
with open(path, encoding="utf-8") as f:
    payload = json.load(f)
traces = payload.get("data") or []
services = set()
for trace in traces:
    for process in (trace.get("processes") or {}).values():
        name = process.get("serviceName") or process.get("service_name")
        if name:
            services.add(name)
    for span in trace.get("spans") or []:
        process_id = span.get("processID") or span.get("processId")
        process = (trace.get("processes") or {}).get(process_id, {})
        name = process.get("serviceName") or process.get("service_name")
        if name:
            services.add(name)
missing = [service for service in wanted if service not in services]
if missing:
    raise SystemExit(1)
print(f"Cross-service trace found: {trace_id} ({', '.join(wanted)})")
PY
    then
      found=true
      break
    fi
  done <"$TMP_DIR/trace-ids-unique.txt"

  if [ "$found" != true ]; then
    echo "No trace contained all required services: ${CROSS_SERVICES[*]}" >&2
    exit 1
  fi
fi
