#!/usr/bin/env bash
set -euo pipefail

K3S_SERVICE="${K3S_SERVICE:-k3s}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
K3S_CONFIG="${K3S_CONFIG:-/etc/rancher/k3s/config.yaml}"
CILIUM_VERSION="${CILIUM_VERSION:-1.19.4}"
CILIUM_NAMESPACE="${CILIUM_NAMESPACE:-kube-system}"
CILIUM_CNI_CONF_PATH="${CILIUM_CNI_CONF_PATH:-/var/lib/rancher/k3s/agent/etc/cni/net.d}"
CILIUM_CNI_BIN_PATH="${CILIUM_CNI_BIN_PATH:-/var/lib/rancher/k3s/data/cni}"
HELM_BIN="${HELM_BIN:-}"
BACKUP_DIR="${BACKUP_DIR:-/root/k3s-cilium-migration-$(date +%Y%m%d%H%M%S)}"
APPLY_MIGRATION="${APPLY_MIGRATION:-false}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root, for example: sudo $0" >&2
  exit 1
fi

section() {
  printf '\n== %s ==\n' "$1"
}

run_kubectl() {
  KUBECONFIG="$KUBECONFIG_PATH" kubectl "$@"
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

  echo "helm is required. Set HELM_BIN=/path/to/helm if sudo cannot find it." >&2
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

  touch "$K3S_CONFIG"

  if grep -Eq "^[[:space:]]*${key}:" "$K3S_CONFIG"; then
    sed -i -E "s|^[[:space:]]*${key}:.*|${key}: ${value}|" "$K3S_CONFIG"
  else
    printf '%s: %s\n' "$key" "$value" >>"$K3S_CONFIG"
  fi
}

clean_kube_router_rules() {
  if command -v iptables-save >/dev/null 2>&1 && command -v iptables-restore >/dev/null 2>&1; then
    iptables-save | grep -v KUBE-ROUTER | iptables-restore
  fi

  if command -v ip6tables-save >/dev/null 2>&1 && command -v ip6tables-restore >/dev/null 2>&1; then
    ip6tables-save | grep -v KUBE-ROUTER | ip6tables-restore
  fi
}

clean_flannel_leftovers() {
  local cni_dir="/var/lib/rancher/k3s/agent/etc/cni/net.d"

  if [ -d "$cni_dir" ]; then
    find "$cni_dir" -maxdepth 1 -type f \( \
      -name '*flannel*' -o \
      -name '10-flannel.conflist' -o \
      -name '10-flannel.conf' \
    \) -print | while read -r file; do
      echo "Moving stale flannel CNI config: ${file}"
      mv "$file" "${file}.disabled-by-cilium-migration"
    done
  fi

  if ip link show flannel.1 >/dev/null 2>&1; then
    ip link delete flannel.1 || true
  fi

  if ip link show cilium_vxlan >/dev/null 2>&1; then
    ip link delete cilium_vxlan || true
  fi
}

snapshot_state() {
  mkdir -p "$BACKUP_DIR"
  backup_path /etc/systemd/system/k3s.service
  backup_path /etc/systemd/system/k3s.service.env
  backup_path "$K3S_CONFIG"
  backup_path /var/lib/rancher/k3s/agent/etc/cni/net.d

  run_kubectl get nodes -o wide >"$BACKUP_DIR/nodes.before.txt" || true
  run_kubectl -n kube-system get daemonset,deploy,pod -o wide >"$BACKUP_DIR/kube-system.before.txt" || true
  run_kubectl get networkpolicy -A >"$BACKUP_DIR/networkpolicy.before.txt" || true
}

print_plan() {
  cat <<EOF
This will migrate single-node k3s networking from flannel/kube-router-netpol to Cilium.

Actions:
  1. Back up k3s config, service files, and current CNI config under:
     ${BACKUP_DIR}
  2. Set these persistent k3s config values:
     flannel-backend: none
     disable-network-policy: true
  3. Restart k3s.
  4. Remove stale KUBE-ROUTER iptables/ip6tables rules.
  5. Move stale flannel CNI config aside and remove stale VXLAN links.
  6. Install/upgrade Cilium ${CILIUM_VERSION} in ${CILIUM_NAMESPACE}.
  7. Restart CoreDNS, Traefik, and Tertius workloads so they attach through Cilium.
  8. Run the runc/gVisor deny-all egress smoke test.

Rollback sketch:
  1. helm uninstall cilium -n ${CILIUM_NAMESPACE}
  2. restore ${BACKUP_DIR}/etc/rancher/k3s/config.yaml to ${K3S_CONFIG}
  3. restore ${BACKUP_DIR}/var/lib/rancher/k3s/agent/etc/cni/net.d if needed
  4. systemctl restart ${K3S_SERVICE}

This is disruptive. Run with APPLY_MIGRATION=true to execute.
EOF
}

install_cilium() {
  KUBECONFIG="$KUBECONFIG_PATH" "$HELM_BIN" upgrade --install cilium oci://quay.io/cilium/charts/cilium \
    --version "$CILIUM_VERSION" \
    --namespace "$CILIUM_NAMESPACE" \
    --set operator.replicas=1 \
    --set "cni.confPath=${CILIUM_CNI_CONF_PATH}" \
    --set "cni.binPath=${CILIUM_CNI_BIN_PATH}"
}

restart_workloads() {
  run_kubectl -n kube-system rollout restart deployment/coredns || true
  run_kubectl -n kube-system rollout restart deployment/traefik || true
  run_kubectl -n tertius rollout restart deployment || true
  run_kubectl -n tertius rollout restart statefulset || true
}

wait_for_cluster() {
  run_kubectl wait --for=condition=Ready node --all --timeout=180s
  run_kubectl -n "$CILIUM_NAMESPACE" rollout status daemonset/cilium --timeout=240s
  run_kubectl -n "$CILIUM_NAMESPACE" rollout status deployment/cilium-operator --timeout=240s
  run_kubectl -n kube-system rollout status deployment/coredns --timeout=180s || true
  run_kubectl -n kube-system rollout status deployment/traefik --timeout=180s || true
}

find_helm
section "plan"
print_plan

if [ "$APPLY_MIGRATION" != "true" ]; then
  echo
  echo "Dry run only. Re-run with: sudo -E APPLY_MIGRATION=true $0"
  exit 0
fi

section "backup"
snapshot_state
echo "Backed up current state to ${BACKUP_DIR}"

section "write k3s custom CNI config"
mkdir -p "$(dirname "$K3S_CONFIG")"
ensure_config_flag flannel-backend none
ensure_config_flag disable-network-policy true
cat "$K3S_CONFIG"

section "restart k3s"
systemctl restart "$K3S_SERVICE"
systemctl is-active --quiet "$K3S_SERVICE"
run_kubectl wait --for=condition=Ready node --all --timeout=180s

section "clean stale kube-router rules"
clean_kube_router_rules

section "clean stale flannel state"
clean_flannel_leftovers

section "install cilium"
install_cilium
wait_for_cluster

section "restart workloads onto cilium"
restart_workloads
wait_for_cluster

section "verify network policy"
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/diagnose-k3s-networkpolicy.sh"
