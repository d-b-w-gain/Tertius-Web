#!/usr/bin/env bash
set -euo pipefail

K3S_SERVICE="${K3S_SERVICE:-k3s}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
CNI_DIR="${CNI_DIR:-/var/lib/rancher/k3s/agent/etc/cni/net.d}"
BACKUP_DIR="${BACKUP_DIR:-/root/k3s-cilium-flannel-cleanup-$(date +%Y%m%d%H%M%S)}"

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

backup_path() {
  local path="$1"
  if [ -e "$path" ]; then
    mkdir -p "$BACKUP_DIR$(dirname "$path")"
    cp -a "$path" "$BACKUP_DIR$path"
  fi
}

move_flannel_cni_configs() {
  if [ ! -d "$CNI_DIR" ]; then
    echo "CNI directory does not exist: ${CNI_DIR}"
    return
  fi

  mkdir -p "$BACKUP_DIR$CNI_DIR"

  find "$CNI_DIR" -maxdepth 1 -type f \( \
    -name '*flannel*' -o \
    -name '10-flannel.conflist' -o \
    -name '10-flannel.conf' \
  \) -print | while read -r file; do
    echo "Moving stale flannel CNI config: ${file}"
    cp -a "$file" "$BACKUP_DIR$file"
    mv "$file" "${file}.disabled-by-cilium-migration"
  done
}

delete_link_if_present() {
  local link="$1"
  if ip link show "$link" >/dev/null 2>&1; then
    echo "Deleting stale link: ${link}"
    ip link delete "$link" || true
  else
    echo "No ${link} link present."
  fi
}

delete_path_if_present() {
  local path="$1"
  if [ -e "$path" ]; then
    echo "Removing stale path: ${path}"
    rm -rf "$path"
  else
    echo "No ${path} present."
  fi
}

assert_no_flannel_link() {
  if ip link show flannel.1 >/dev/null 2>&1; then
    echo "flannel.1 is still present after cleanup; refusing to continue with a mixed datapath." >&2
    ip -d link show flannel.1 >&2 || true
    exit 1
  fi
}

assert_cilium_has_pod_endpoints() {
  local attempts=30
  local endpoint_count=""

  while [ "$attempts" -gt 0 ]; do
    endpoint_count="$(run_kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium endpoint list 2>/dev/null | awk 'NR > 2 && $1 ~ /^[0-9]+$/ { count++ } END { print count + 0 }')"
    if [ "$endpoint_count" -gt 1 ]; then
      return
    fi

    sleep 2
    attempts=$((attempts - 1))
  done

  echo "Cilium still does not show pod endpoints; new pods are probably not using Cilium CNI." >&2
  run_kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium endpoint list >&2 || true
  run_kubectl get pods -A -o wide >&2 || true
  exit 1
}

restart_workloads() {
  run_kubectl -n kube-system rollout restart deployment/coredns || true
  run_kubectl -n kube-system rollout restart deployment/local-path-provisioner || true
  run_kubectl -n kube-system rollout restart deployment/metrics-server || true
  run_kubectl -n kube-system rollout restart deployment/traefik || true
  run_kubectl -n tertius rollout restart deployment || true
  run_kubectl -n tertius rollout restart statefulset || true
}

wait_for_cluster() {
  run_kubectl wait --for=condition=Ready node --all --timeout=180s
  run_kubectl -n kube-system rollout status daemonset/cilium --timeout=240s
  run_kubectl -n kube-system rollout status deployment/cilium-operator --timeout=240s
  run_kubectl -n kube-system rollout status deployment/coredns --timeout=180s || true
  run_kubectl -n kube-system rollout status deployment/traefik --timeout=180s || true
}

section "backup"
mkdir -p "$BACKUP_DIR"
backup_path /etc/rancher/k3s/config.yaml
backup_path "$CNI_DIR"
ip -d link show type vxlan >"$BACKUP_DIR/vxlan-links.before.txt" 2>/dev/null || true
run_kubectl -n kube-system get configmap cilium-config -o yaml >"$BACKUP_DIR/cilium-config.before.yaml" || true
echo "Backed up state to ${BACKUP_DIR}"

section "confirm k3s custom CNI mode"
if ! run_kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.annotations.k3s\.io/node-args}{"\n"}{end}' | grep -q -- '--flannel-backend.*none'; then
  echo "k3s node args do not show --flannel-backend none. Run migrate-k3s-cilium.sh first." >&2
  exit 1
fi

section "remove stale flannel CNI config"
move_flannel_cni_configs
find "$CNI_DIR" -maxdepth 1 -type f -print 2>/dev/null || true
find /etc/cni/net.d "$CNI_DIR" -maxdepth 1 -type f -print -exec sed -n '1,120p' {} \; 2>/dev/null || true

section "stop k3s for host datapath cleanup"
systemctl stop "$K3S_SERVICE"

section "remove stale vxlan links and CNI runtime state"
delete_link_if_present flannel.1
delete_link_if_present cilium_vxlan
delete_path_if_present /run/flannel
delete_path_if_present /var/lib/cni/networks/cbr0
delete_path_if_present /var/lib/cni/networks/flannel.1
delete_path_if_present /var/lib/cni/networks/cilium
assert_no_flannel_link

section "start k3s and cilium"
systemctl start "$K3S_SERVICE"
systemctl is-active --quiet "$K3S_SERVICE"
run_kubectl -n kube-system rollout restart daemonset/cilium
wait_for_cluster
assert_no_flannel_link

section "restart workloads onto current CNI"
restart_workloads
wait_for_cluster
assert_cilium_has_pod_endpoints

section "current cilium status"
run_kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium status --brief || true
run_kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium endpoint list || true
ip -d link show type vxlan 2>/dev/null || true
find /etc/cni/net.d "$CNI_DIR" -maxdepth 1 -type f -print -exec sed -n '1,120p' {} \; 2>/dev/null || true

section "verify network policy"
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/diagnose-k3s-networkpolicy.sh"
