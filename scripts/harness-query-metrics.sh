#!/usr/bin/env bash
set -Eeuo pipefail

METRICS_BASE_URL="${METRICS_BASE_URL:-http://localhost:8428}"
QUERY_FILE=""
QUERY_NAME=""

usage() {
  cat <<EOF
Usage:
  $(basename "$0") '<promql>'
  $(basename "$0") --file docs/harness/queries/api.promql [--name query_name]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --file)
      QUERY_FILE="${2:-}"
      shift 2
      ;;
    --name)
      QUERY_NAME="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

run_query() {
  name=$1
  query=$2
  echo "== ${name} =="
  curl --fail --silent --show-error --get \
    --data-urlencode "query=${query}" \
    "${METRICS_BASE_URL%/}/api/v1/query"
  echo
}

if [ -n "$QUERY_FILE" ]; then
  [ -f "$QUERY_FILE" ] || {
    echo "Missing query file: ${QUERY_FILE}" >&2
    exit 1
  }
  python3 - "$QUERY_FILE" "$QUERY_NAME" >"${TMPDIR:-/tmp}/tertius-metric-queries.$$" <<'PY'
import sys

path, wanted = sys.argv[1], sys.argv[2]
queries = []
seen = set()
pending = None
with open(path, encoding="utf-8") as f:
    for lineno, raw in enumerate(f, 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# name:"):
            name = line.split(":", 1)[1].strip()
            if not name:
                raise SystemExit(f"{path}:{lineno}: empty query name")
            if name in seen:
                raise SystemExit(f"{path}:{lineno}: duplicate query name {name}")
            seen.add(name)
            pending = name
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            raise SystemExit(f"{path}:{lineno}: expression without # name")
        queries.append((pending, line))
        pending = None
if pending is not None:
    raise SystemExit(f"{path}: # name: {pending} has no expression")
if wanted:
    queries = [item for item in queries if item[0] == wanted]
    if not queries:
        raise SystemExit(f"{path}: no query named {wanted}")
for name, query in queries:
    print(f"{name}\t{query}")
PY
  while IFS="$(printf '\t')" read -r name query; do
    [ -n "$name" ] || continue
    run_query "$name" "$query"
  done <"${TMPDIR:-/tmp}/tertius-metric-queries.$$"
  rm -f "${TMPDIR:-/tmp}/tertius-metric-queries.$$"
  exit 0
fi

[ "$#" -eq 1 ] || {
  usage >&2
  exit 2
}

run_query adhoc "$1"
