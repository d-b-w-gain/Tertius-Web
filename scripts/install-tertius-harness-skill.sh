#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${ROOT_DIR}/tools/codex/skills/tertius-harness"
CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
DEST_DIR="${CODEX_HOME}/skills/tertius-harness"

if [ ! -f "${SOURCE_DIR}/SKILL.md" ]; then
  echo "Missing source skill: ${SOURCE_DIR}/SKILL.md" >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST_DIR")"
rm -rf "$DEST_DIR"
cp -R "$SOURCE_DIR" "$DEST_DIR"

echo "Installed Tertius harness skill to ${DEST_DIR}"
