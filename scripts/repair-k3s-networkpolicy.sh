#!/usr/bin/env bash
set -euo pipefail

K3S_SERVICE="${K3S_SERVICE:-k3s}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
CLEAN_KUBE_ROUTER_RULES="${CLEAN_KUBE_ROUTER_RULES:-false}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root, for example: sudo $0" >&2
  exit 1
fi

section() {
  printf '\n== %s ==\n' "$1"
}

service_has_disabled_network_policy() {
  systemctl show "$K3S_SERVICE" -p ExecStart -p Environment 2>/dev/null | grep -q -- '--disable-network-policy'
}

remove_kube_router_rules() {
  local tool="$1"
  local restore_tool="$2"

  if ! command -v "$tool" >/dev/null 2>&1 || ! command -v "$restore_tool" >/dev/null 2>&1; then
    echo "Skipping ${tool}: ${tool} or ${restore_tool} is unavailable."
    return
  fi

  if "$tool" | grep -q 'KUBE-ROUTER'; then
    echo "Removing stale KUBE-ROUTER rules with ${tool} | ${restore_tool}."
    "$tool" | grep -v KUBE-ROUTER | "$restore_tool"
  else
    echo "No KUBE-ROUTER rules found in ${tool}."
  fi
}

section "preflight"
systemctl show "$K3S_SERVICE" -p ExecStart -p Environment

if service_has_disabled_network_policy; then
  echo "${K3S_SERVICE} is configured with --disable-network-policy." >&2
  echo "Remove that flag from the k3s service/config before this repair can enforce NetworkPolicy." >&2
  exit 1
fi

section "restart k3s"
systemctl restart "$K3S_SERVICE"
systemctl is-active --quiet "$K3S_SERVICE"

if [ "$CLEAN_KUBE_ROUTER_RULES" = "true" ]; then
  section "clean kube-router rules"
  remove_kube_router_rules iptables-save iptables-restore
  remove_kube_router_rules ip6tables-save ip6tables-restore

  section "restart k3s after rule cleanup"
  systemctl restart "$K3S_SERVICE"
  systemctl is-active --quiet "$K3S_SERVICE"
else
  section "skip rule cleanup"
  echo "Set CLEAN_KUBE_ROUTER_RULES=true to remove stale KUBE-ROUTER iptables/ip6tables rules."
fi

section "diagnose"
KUBECONFIG_PATH="$KUBECONFIG_PATH" "$SCRIPT_DIR/diagnose-k3s-networkpolicy.sh"
