#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-install}"
UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
SERVICE_NAME=tertius-k3s-harness-cleanup.service
TIMER_NAME=tertius-k3s-harness-cleanup.timer

case "$ACTION" in
  uninstall)
    uninstall_status=0
    if [ -e "${UNIT_DIR}/${SERVICE_NAME}" ] || [ -e "${UNIT_DIR}/${TIMER_NAME}" ]; then
      if ! systemctl --user disable --now "$TIMER_NAME"; then
        echo "Failed to disable ${TIMER_NAME}; removing unit files anyway." >&2
        uninstall_status=1
      fi
    fi
    rm -f "${UNIT_DIR}/${SERVICE_NAME}" "${UNIT_DIR}/${TIMER_NAME}"
    if ! systemctl --user daemon-reload; then
      uninstall_status=1
    fi
    exit "$uninstall_status"
    ;;
  install) ;;
  *) echo "Usage: $(basename "$0") [install|uninstall]" >&2; exit 2 ;;
esac

ROOT_DIR="${REPOSITORY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
KUBE_CONFIG="${KUBECONFIG:-${HOME}/.kube/config}"
case "$ROOT_DIR" in /*) ;; *) echo "REPOSITORY_ROOT must be absolute." >&2; exit 2 ;; esac
case "$KUBE_CONFIG" in /*) ;; *) echo "KUBECONFIG must be absolute." >&2; exit 2 ;; esac
case "$ROOT_DIR$KUBE_CONFIG" in *$'\n'*|*$'\r'*) echo "Repository and kubeconfig paths cannot contain newlines." >&2; exit 2 ;; esac
[ -x "${ROOT_DIR}/scripts/cleanup-expired-k3s-harness.sh" ] || {
  echo "Janitor is not executable under repository root: $ROOT_DIR" >&2
  exit 1
}

mkdir -p "$UNIT_DIR"
service_tmp=$(mktemp "${UNIT_DIR}/.${SERVICE_NAME}.XXXXXX")
timer_tmp=$(mktemp "${UNIT_DIR}/.${TIMER_NAME}.XXXXXX")
cleanup_tmp() { rm -f "$service_tmp" "$timer_tmp"; }
trap cleanup_tmp EXIT

systemd_quote() {
  quoted=$1
  quoted=${quoted//\\/\\\\}
  quoted=${quoted//\"/\\\"}
  quoted=${quoted//%/%%}
  printf '"%s"' "$quoted"
}
{
  printf '%s\n' '[Unit]' 'Description=Remove expired Tertius k3s harness releases' ''
  printf '%s\n' '[Service]' 'Type=oneshot'
  printf 'Environment=%s\n' "$(systemd_quote "KUBECONFIG=${KUBE_CONFIG}")"
  printf 'ExecStart=%s\n' "$(systemd_quote "${ROOT_DIR}/scripts/cleanup-expired-k3s-harness.sh")"
} >"$service_tmp"
cat >"$timer_tmp" <<'EOF'
[Unit]
Description=Run Tertius k3s harness cleanup every 15 minutes

[Timer]
OnBootSec=5m
OnUnitActiveSec=15m
Persistent=true

[Install]
WantedBy=timers.target
EOF
mv "$service_tmp" "${UNIT_DIR}/${SERVICE_NAME}"
mv "$timer_tmp" "${UNIT_DIR}/${TIMER_NAME}"
systemctl --user daemon-reload
systemctl --user enable --now "$TIMER_NAME"
echo "Installed and enabled ${TIMER_NAME}."
