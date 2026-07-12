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
grep -Fq 'canary_tmp=$(mktemp "${canary_dir}/live-flow-sensitive-canaries.XXXXXX")' "$SCRIPT"
grep -Fq 'TEMP_FILES="${TEMP_FILES} ${canary_tmp}"' "$SCRIPT"
grep -Fq 'chmod 600 "$canary_tmp"' "$SCRIPT"
grep -Fq 'mv -fT -- "$canary_tmp" "$canary_file"' "$SCRIPT"
grep -Fq "printf 'LIVE_FLOW_USER_CANARY=%q\\nLIVE_FLOW_ASSISTANT_CANARY=%q\\n'" "$SCRIPT"
grep -Fq -- '-e "$LIVE_FLOW_USER_CANARY" -e "$LIVE_FLOW_ASSISTANT_CANARY" >/dev/null; then' "$SCRIPT"
grep -Fq 'rg -F -e "$LIVE_FLOW_ASSISTANT_CANARY" >/dev/null; then' "$SCRIPT"

python3 - "$SCRIPT" <<'PY'
from pathlib import Path
import sys

script = Path(sys.argv[1]).read_text(encoding="utf-8")
block = script.split('canary_dir="${ROOT_DIR}/.tmp/harness"', 1)[1].split(
    'first_prompt=', 1
)[0]
operations = (
    'canary_tmp=$(mktemp "${canary_dir}/live-flow-sensitive-canaries.XXXXXX")',
    'TEMP_FILES="${TEMP_FILES} ${canary_tmp}"',
    'chmod 600 "$canary_tmp"',
    "printf 'LIVE_FLOW_USER_CANARY=%q\\nLIVE_FLOW_ASSISTANT_CANARY=%q\\n'",
    'mv -fT -- "$canary_tmp" "$canary_file"',
)
assert [block.index(operation) for operation in operations] == sorted(
    block.index(operation) for operation in operations
)
assert '> "$canary_file"' not in block
PY

probe_root=$(mktemp -d)
trap 'rm -rf "$probe_root"' EXIT
probe_target_dir="${probe_root}/symlink-target"
probe_canary_file="${probe_root}/live-flow-sensitive-canaries.env"
mkdir -p "$probe_target_dir"
ln -s "$probe_target_dir" "$probe_canary_file"
umask 077
probe_tmp=$(mktemp "${probe_root}/live-flow-sensitive-canaries.XXXXXX")
chmod 600 "$probe_tmp"
printf 'LIVE_FLOW_USER_CANARY=%q\nLIVE_FLOW_ASSISTANT_CANARY=%q\n' \
  probe-user probe-assistant > "$probe_tmp"
mv -fT -- "$probe_tmp" "$probe_canary_file"
test -f "$probe_canary_file"
test ! -L "$probe_canary_file"
test "$(stat -c '%a' "$probe_canary_file")" = 600
test -z "$(find "$probe_target_dir" -mindepth 1 -maxdepth 1 -print -quit)"

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
