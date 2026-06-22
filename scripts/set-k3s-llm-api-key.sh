#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-tertius}"
DEPLOYMENT="${DEPLOYMENT:-tertius-api}"
SECRET_NAME="${SECRET_NAME:-tertius-llm}"
SECRET_KEY="${SECRET_KEY:-LLM_API_KEY}"
RESTART_API="${RESTART_API:-true}"

usage() {
  cat <<'EOF'
Usage:
  scripts/set-k3s-llm-api-key.sh
  LLM_API_KEY=... scripts/set-k3s-llm-api-key.sh
  scripts/set-k3s-llm-api-key.sh --key '...'
  scripts/set-k3s-llm-api-key.sh --key-file /path/to/key

Patches only LLM_API_KEY on Secret/tertius-llm and preserves existing
LLM_FILE_EDIT_SYSTEM_PROMPT and any other Secret keys.

Environment overrides:
  NAMESPACE=tertius
  DEPLOYMENT=tertius-api
  SECRET_NAME=tertius-llm
  RESTART_API=true|false
EOF
}

key="${LLM_API_KEY:-}"
key_file=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --key)
      [ "$#" -ge 2 ] || { printf 'ERROR: --key requires a value.\n' >&2; exit 2; }
      key="$2"
      shift 2
      ;;
    --key-file)
      [ "$#" -ge 2 ] || { printf 'ERROR: --key-file requires a path.\n' >&2; exit 2; }
      key_file="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "$key_file" ]; then
  [ -f "$key_file" ] || { printf 'ERROR: key file not found: %s\n' "$key_file" >&2; exit 2; }
  key="$(tr -d '\r\n' < "$key_file")"
fi

if [ -z "$key" ]; then
  printf 'Enter LLM API key: ' >&2
  stty -echo
  IFS= read -r key
  stty echo
  printf '\n' >&2
fi

[ -n "$key" ] || { printf 'ERROR: LLM API key is empty.\n' >&2; exit 2; }

if ! kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" >/dev/null 2>&1; then
  printf 'ERROR: Secret %s/%s does not exist. Create it with LLM_FILE_EDIT_SYSTEM_PROMPT first, then rerun this script.\n' "$NAMESPACE" "$SECRET_NAME" >&2
  exit 1
fi

if ! kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" -o jsonpath='{.data.LLM_FILE_EDIT_SYSTEM_PROMPT}' | grep -q .; then
  printf 'ERROR: Secret %s/%s does not contain LLM_FILE_EDIT_SYSTEM_PROMPT; refusing to replace the Secret.\n' "$NAMESPACE" "$SECRET_NAME" >&2
  exit 1
fi

encoded_key="$(printf '%s' "$key" | base64 -w0)"
patch="$(printf '{"data":{"%s":"%s"}}' "$SECRET_KEY" "$encoded_key")"
kubectl -n "$NAMESPACE" patch secret "$SECRET_NAME" --type merge -p "$patch" >/dev/null
printf 'OK: patched %s on Secret %s/%s without changing LLM_FILE_EDIT_SYSTEM_PROMPT.\n' "$SECRET_KEY" "$NAMESPACE" "$SECRET_NAME"

if [ "$RESTART_API" = "true" ]; then
  kubectl -n "$NAMESPACE" rollout restart "deployment/${DEPLOYMENT}" >/dev/null
  kubectl -n "$NAMESPACE" rollout status "deployment/${DEPLOYMENT}" --timeout=180s
fi
