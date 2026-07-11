#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${ROOT_DIR}/scripts/smoke-live-flow.sh"

bash -n "$SCRIPT"

help_output=$("$SCRIPT" --help)
grep -q 'LIVE_FLOW_AI_PROMPT' <<<"$help_output"
grep -q 'LIVE_FLOW_EXPECTED_AI_OUTCOME' <<<"$help_output"

grep -q 'AI_PROMPT="${LIVE_FLOW_AI_PROMPT:-' "$SCRIPT"
grep -q 'EXPECTED_AI_OUTCOME="${LIVE_FLOW_EXPECTED_AI_OUTCOME:-}"' "$SCRIPT"
grep -q 'write_json "$request" llm_edit "$metadata" "${LIVE_FLOW_MODEL_ID:-}" "$AI_PROMPT"' "$SCRIPT"
grep -Fq '[ "$outcome" != "$EXPECTED_AI_OUTCOME" ]' "$SCRIPT"

invalid_output=$(LIVE_FLOW_EXPECTED_AI_OUTCOME=invalid "$SCRIPT" http://127.0.0.1 2>&1) && {
  echo "invalid expected outcome should fail" >&2
  exit 1
}
grep -q 'must be changed or no_changes' <<<"$invalid_output"

echo "smoke live-flow configuration tests passed"
