#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-tertius}"
DEPLOYMENT="${DEPLOYMENT:-tertius-api}"
RELEASE_NAME="${RELEASE_NAME:-tertius}"
LLM_SECRET_NAME="${LLM_SECRET_NAME:-${RELEASE_NAME}-llm}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

warn() {
  printf 'WARN: %s\n' "$1"
}

ok() {
  printf 'OK: %s\n' "$1"
}

load_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0

  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ""|\#*) continue ;;
      export\ *) line="${line#export }" ;;
    esac

    case "$line" in
      LLM_BASE_URL=*|LLM_MODEL=*|LLM_API_KEY=*|LLM_FILE_EDIT_SYSTEM_PROMPT=*)
        key="${line%%=*}"
        value="${line#*=}"
        value="${value%$'\r'}"
        case "$value" in
          \"*\") value="${value#\"}"; value="${value%\"}" ;;
          \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        printf -v "$key" '%s' "$value"
        ;;
    esac
  done < "$file"
}

load_env_file "$ROOT_DIR/.env"
load_env_file "$ROOT_DIR/server/.env"

if [ -z "${LLM_BASE_URL:-}" ] && [ -z "${LLM_MODEL:-}" ] && [ -z "${LLM_API_KEY:-}" ] && [ -z "${LLM_FILE_EDIT_SYSTEM_PROMPT:-}" ]; then
  warn "No local LLM settings found in .env or server/.env; skipping k3s LLM sync."
  exit 0
fi

if ! kubectl -n "$NAMESPACE" get deployment "$DEPLOYMENT" >/dev/null 2>&1; then
  warn "Deployment ${NAMESPACE}/${DEPLOYMENT} was not found; skipping k3s LLM sync."
  exit 0
fi

set_env_args=()
[ -z "${LLM_BASE_URL:-}" ] || set_env_args+=("LLM_BASE_URL=${LLM_BASE_URL}")
[ -z "${LLM_MODEL:-}" ] || set_env_args+=("LLM_MODEL=${LLM_MODEL}")

if [ "${#set_env_args[@]}" -gt 0 ]; then
  kubectl -n "$NAMESPACE" set env "deployment/${DEPLOYMENT}" --containers=api "${set_env_args[@]}" >/dev/null
fi

secret_args=()
[ -z "${LLM_API_KEY:-}" ] || secret_args+=("--from-literal=LLM_API_KEY=${LLM_API_KEY}")
[ -z "${LLM_FILE_EDIT_SYSTEM_PROMPT:-}" ] || secret_args+=("--from-literal=LLM_FILE_EDIT_SYSTEM_PROMPT=${LLM_FILE_EDIT_SYSTEM_PROMPT}")

if [ "${#secret_args[@]}" -gt 0 ]; then
  kubectl -n "$NAMESPACE" create secret generic "$LLM_SECRET_NAME" \
    "${secret_args[@]}" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
fi

if [ "${#set_env_args[@]}" -gt 0 ] || [ "${#secret_args[@]}" -gt 0 ]; then
  kubectl -n "$NAMESPACE" rollout restart "deployment/${DEPLOYMENT}" >/dev/null
  kubectl -n "$NAMESPACE" rollout status "deployment/${DEPLOYMENT}" --timeout=180s
  ok "Synced local LLM settings into ${NAMESPACE}/${DEPLOYMENT}"
else
  warn "Local LLM settings were empty; no k3s LLM env changes applied."
fi
