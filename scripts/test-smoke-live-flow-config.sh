#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${ROOT_DIR}/scripts/smoke-live-flow.sh"

bash -n "$SCRIPT"

help_output=$("$SCRIPT" --help)
grep -q 'LIVE_FLOW_AI_PROMPT' <<<"$help_output"
grep -q 'LIVE_FLOW_EXPECTED_AI_OUTCOME' <<<"$help_output"
grep -q 'LIVE_FLOW_VERIFY_CONVERSATION' <<<"$help_output"

grep -q 'AI_PROMPT="${LIVE_FLOW_AI_PROMPT:-' "$SCRIPT"
grep -q 'EXPECTED_AI_OUTCOME="${LIVE_FLOW_EXPECTED_AI_OUTCOME:-}"' "$SCRIPT"
grep -Fq 'ROOT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)' "$SCRIPT"
grep -Fq 'LIVE_FLOW_VERIFY_CONVERSATION="${LIVE_FLOW_VERIFY_CONVERSATION:-false}"' "$SCRIPT"
grep -Fq 'write_json "$request" llm_edit "$metadata" "${LIVE_FLOW_MODEL_ID:-}" "$prompt"' "$SCRIPT"
grep -Fq '[ "$outcome" != "$EXPECTED_AI_OUTCOME" ]' "$SCRIPT"
grep -Fq 'LIVE_FLOW_USER_CANARY=${LIVE_FLOW_USER_CANARY:-TERTIUS_CONTEXT_USER_CANARY_' "$SCRIPT"
grep -Fq 'LIVE_FLOW_ASSISTANT_CANARY=${LIVE_FLOW_ASSISTANT_CANARY:-TERTIUS_CONTEXT_ASSISTANT_CANARY_' "$SCRIPT"
grep -Fq "second_prompt='In design.py, add a Python comment containing the codeword from my previous user request; do not change geometry.'" "$SCRIPT"
grep -Fq 'live-flow-sensitive-canaries.env' "$SCRIPT"
grep -Fq 'set +x' "$SCRIPT"
grep -Fq 'SENSITIVE_OUTPUT=true' "$SCRIPT"
grep -Fq 'if [ "$SENSITIVE_OUTPUT" = true ]; then' "$SCRIPT"
grep -Fq 'response body suppressed during conversation verification' "$SCRIPT"
grep -Fq 'umask 077' "$SCRIPT"
grep -Fq 'chmod 600 "$canary_file"' "$SCRIPT"
grep -Fq "printf 'LIVE_FLOW_USER_CANARY=%q\\nLIVE_FLOW_ASSISTANT_CANARY=%q\\n'" "$SCRIPT"
grep -Fq -- '-e "$LIVE_FLOW_USER_CANARY" -e "$LIVE_FLOW_ASSISTANT_CANARY" >/dev/null; then' "$SCRIPT"
grep -Fq 'rg -F -e "$LIVE_FLOW_ASSISTANT_CANARY" >/dev/null; then' "$SCRIPT"

invalid_output=$(LIVE_FLOW_EXPECTED_AI_OUTCOME=invalid "$SCRIPT" http://127.0.0.1 2>&1) && {
  echo "invalid expected outcome should fail" >&2
  exit 1
}
grep -q 'must be changed or no_changes' <<<"$invalid_output"

invalid_context_output=$(LIVE_FLOW_VERIFY_CONVERSATION=invalid "$SCRIPT" http://127.0.0.1 2>&1) && {
  echo 'invalid conversation verification flag should fail' >&2
  exit 1
}
grep -q 'LIVE_FLOW_VERIFY_CONVERSATION must be true or false' <<<"$invalid_context_output"

compile_only_context_output=$(LIVE_FLOW_VERIFY_CONVERSATION=true "$SCRIPT" --compile-only http://127.0.0.1 2>&1) && {
  echo 'compile-only conversation verification should fail' >&2
  exit 1
}
grep -q 'LIVE_FLOW_VERIFY_CONVERSATION cannot be combined with --compile-only' <<<"$compile_only_context_output"

echo "smoke live-flow configuration tests passed"
