#!/usr/bin/env bash
set -euo pipefail

APPLY_BOOTSTRAP="${APPLY_BOOTSTRAP:-false}"
INSTALL_K3S_IF_MISSING="${INSTALL_K3S_IF_MISSING:-false}"
ALLOW_MULTI_NODE="${ALLOW_MULTI_NODE:-false}"
K3S_SERVICE="${K3S_SERVICE:-k3s}"
K3S_CONFIG="${K3S_CONFIG:-/etc/rancher/k3s/config.yaml}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
HELM_BIN="${HELM_BIN:-}"
BACKUP_DIR="${BACKUP_DIR:-/root/tertius-k3s-bootstrap-$(date +%Y%m%d%H%M%S)}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root, for example: sudo $0" >&2
  exit 1
fi

section() {
  printf '\n== %s ==\n' "$1"
}

run() {
  echo "+ $*"
  if [ "$APPLY_BOOTSTRAP" = "true" ]; then
    "$@"
  fi
}

run_shell() {
  echo "+ $*"
  if [ "$APPLY_BOOTSTRAP" = "true" ]; then
    bash -lc "$*"
  fi
}

run_kubectl() {
  KUBECONFIG="$KUBECONFIG_PATH" kubectl "$@"
}

require_script() {
  local path="$1"
  if [ ! -x "$path" ]; then
    echo "Required script is missing or not executable: ${path}" >&2
    exit 1
  fi
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Required command is missing: ${name}" >&2
    exit 1
  fi
}

find_helm() {
  if [ -n "$HELM_BIN" ]; then
    return
  fi

  if command -v helm >/dev/null 2>&1; then
    HELM_BIN="$(command -v helm)"
    return
  fi

  if [ -x /home/johnson/.local/bin/helm ]; then
    HELM_BIN="/home/johnson/.local/bin/helm"
    return
  fi

  echo "helm is required before Cilium install. Install Helm or set HELM_BIN=/path/to/helm." >&2
  exit 1
}

backup_path() {
  local path="$1"
  if [ -e "$path" ]; then
    mkdir -p "$BACKUP_DIR$(dirname "$path")"
    cp -a "$path" "$BACKUP_DIR$path"
  fi
}

ensure_config_flag() {
  local key="$1"
  local value="$2"

  mkdir -p "$(dirname "$K3S_CONFIG")"
  touch "$K3S_CONFIG"

  if grep -Eq "^[[:space:]]*${key}:" "$K3S_CONFIG"; then
    sed -i -E "s|^[[:space:]]*${key}:.*|${key}: ${value}|" "$K3S_CONFIG"
  else
    printf '%s: %s\n' "$key" "$value" >>"$K3S_CONFIG"
  fi
}

preflight() {
  require_command systemctl
  require_command kubectl
  require_command wget
  require_command sha512sum
  require_command ip

  if ! command -v iptables-save >/dev/null 2>&1; then
    echo "Warning: iptables-save is missing; kube-router cleanup diagnostics will be limited." >&2
  fi
}

install_host_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    run apt-get update
    run apt-get install -y ca-certificates curl wget jq iptables
  else
    echo "No apt-get found; ensure ca-certificates, curl, wget, jq, and iptables tools are installed."
  fi
}

snapshot_state() {
  if [ "$APPLY_BOOTSTRAP" != "true" ]; then
    echo "Would back up host state under ${BACKUP_DIR}."
    return
  fi

  mkdir -p "$BACKUP_DIR"
  backup_path /etc/rancher/k3s/config.yaml
  backup_path /etc/systemd/system/k3s.service
  backup_path /etc/systemd/system/k3s.service.env
  backup_path /var/lib/rancher/k3s/agent/etc/cni/net.d
  backup_path /var/lib/rancher/k3s/agent/etc/containerd

  KUBECONFIG="$KUBECONFIG_PATH" kubectl get nodes -o wide >"$BACKUP_DIR/nodes.before.txt" 2>/dev/null || true
  KUBECONFIG="$KUBECONFIG_PATH" kubectl -n kube-system get daemonset,deploy,pod -o wide >"$BACKUP_DIR/kube-system.before.txt" 2>/dev/null || true
  KUBECONFIG="$KUBECONFIG_PATH" kubectl get networkpolicy -A >"$BACKUP_DIR/networkpolicy.before.txt" 2>/dev/null || true
  ip -d link show type vxlan >"$BACKUP_DIR/vxlan-links.before.txt" 2>/dev/null || true
}

install_k3s_if_needed() {
  if systemctl list-unit-files "$K3S_SERVICE.service" >/dev/null 2>&1 || command -v k3s >/dev/null 2>&1; then
    echo "k3s appears to be installed."
    return
  fi

  if [ "$INSTALL_K3S_IF_MISSING" != "true" ]; then
    echo "k3s is missing. Re-run with INSTALL_K3S_IF_MISSING=true to install it." >&2
    exit 1
  fi

  run_shell 'curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --flannel-backend=none --disable-network-policy=true" sh -'
}

print_existing_state() {
  echo "k3s service:"
  systemctl show "$K3S_SERVICE" -p ExecStart -p Environment 2>/dev/null || true

  echo "nodes:"
  KUBECONFIG="$KUBECONFIG_PATH" kubectl get nodes -o wide 2>/dev/null || true

  echo "runtime classes:"
  KUBECONFIG="$KUBECONFIG_PATH" kubectl get runtimeclass 2>/dev/null || true

  echo "CNI config files:"
  find /var/lib/rancher/k3s/agent/etc/cni/net.d /etc/cni/net.d -maxdepth 1 -type f -print 2>/dev/null || true

  echo "VXLAN links:"
  ip -d link show type vxlan 2>/dev/null || true

  echo "gVisor binaries:"
  command -v runsc || true
  command -v containerd-shim-runsc-v1 || true
}

guard_single_node() {
  local node_count
  node_count="$(KUBECONFIG="$KUBECONFIG_PATH" kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"

  if [ -z "$node_count" ] || [ "$node_count" = "0" ]; then
    echo "No Kubernetes nodes are visible yet."
    return
  fi

  if [ "$node_count" != "1" ] && [ "$ALLOW_MULTI_NODE" != "true" ]; then
    echo "Refusing to bootstrap a ${node_count}-node cluster without ALLOW_MULTI_NODE=true." >&2
    exit 1
  fi
}

configure_k3s_for_cilium() {
  if [ "$APPLY_BOOTSTRAP" = "true" ]; then
    ensure_config_flag flannel-backend none
    ensure_config_flag disable-network-policy true
  else
    echo "Would set ${K3S_CONFIG}:"
    echo "  flannel-backend: none"
    echo "  disable-network-policy: true"
  fi

  run systemctl restart "$K3S_SERVICE"
  if [ "$APPLY_BOOTSTRAP" = "true" ]; then
    systemctl is-active --quiet "$K3S_SERVICE"
    run_kubectl wait --for=condition=Ready node --all --timeout=180s
  fi
}

remove_kube_router_rules() {
  if command -v iptables-save >/dev/null 2>&1 && command -v iptables-restore >/dev/null 2>&1; then
    run_shell "iptables-save | grep -v KUBE-ROUTER | iptables-restore"
  fi

  if command -v ip6tables-save >/dev/null 2>&1 && command -v ip6tables-restore >/dev/null 2>&1; then
    run_shell "ip6tables-save | grep -v KUBE-ROUTER | ip6tables-restore"
  fi
}

remove_flannel_leftovers() {
  run_shell "find /var/lib/rancher/k3s/agent/etc/cni/net.d -maxdepth 1 -type f \\( -name '*flannel*' -o -name '10-flannel.conflist' -o -name '10-flannel.conf' \\) -print -exec mv {} {}.disabled-by-cilium-migration \\; 2>/dev/null || true"
  run_shell "ip link show flannel.1 >/dev/null 2>&1 && ip link delete flannel.1 || true"
  run_shell "ip link show cilium_vxlan >/dev/null 2>&1 && ip link delete cilium_vxlan || true"
  run_shell "rm -rf /run/flannel /var/lib/cni/networks/cbr0 /var/lib/cni/networks/flannel.1 /var/lib/cni/networks/cilium"
}

install_cilium() {
  find_helm

  run_shell "KUBECONFIG=${KUBECONFIG_PATH} ${HELM_BIN} upgrade --install cilium oci://quay.io/cilium/charts/cilium --version 1.19.4 --namespace kube-system --set operator.replicas=1 --set cni.confPath=/var/lib/rancher/k3s/agent/etc/cni/net.d --set cni.binPath=/var/lib/rancher/k3s/data/cni"

  if [ "$APPLY_BOOTSTRAP" = "true" ]; then
    run_kubectl -n kube-system rollout status daemonset/cilium --timeout=240s
    run_kubectl -n kube-system rollout status deployment/cilium-operator --timeout=240s
  fi
}

verify_cilium_pod_endpoints() {
  if [ "$APPLY_BOOTSTRAP" != "true" ]; then
    echo "Would verify Cilium endpoint list contains pod endpoints."
    return
  fi

  run_kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium status --brief
  run_kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium endpoint list | tee "$BACKUP_DIR/cilium-endpoints.after.txt"
}

main() {
  require_script "$REPO_ROOT/scripts/install-gvisor-k3s.sh"
  require_script "$REPO_ROOT/scripts/diagnose-k3s-networkpolicy.sh"
  require_script "$REPO_ROOT/scripts/repair-cilium-after-flannel.sh"
  preflight

  section "mode"
  if [ "$APPLY_BOOTSTRAP" = "true" ]; then
    echo "Applying fresh k3s node bootstrap."
  else
    echo "Dry run only. Re-run with: sudo -E APPLY_BOOTSTRAP=true $0"
    echo "If k3s is not installed, add: INSTALL_K3S_IF_MISSING=true"
  fi
  echo "Backup directory: ${BACKUP_DIR}"

  section "host packages"
  install_host_packages

  section "existing state"
  print_existing_state

  section "backup"
  snapshot_state

  section "k3s"
  install_k3s_if_needed
  guard_single_node
  configure_k3s_for_cilium

  section "cleanup default networking"
  remove_kube_router_rules
  remove_flannel_leftovers

  section "cilium"
  install_cilium
  verify_cilium_pod_endpoints

  section "gvisor"
  run "$REPO_ROOT/scripts/install-gvisor-k3s.sh"

  section "network policy acceptance"
  run "$REPO_ROOT/scripts/diagnose-k3s-networkpolicy.sh"

  section "done"
  echo "Pass condition: node Ready, Cilium OK, RuntimeClass/gvisor exists, and runc/gVisor smoke jobs log EGRESS_BLOCKED."
  echo "Backup directory: ${BACKUP_DIR}"
}

main "$@"
