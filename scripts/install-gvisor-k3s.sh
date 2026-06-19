#!/usr/bin/env bash
set -euo pipefail

RUNTIME_CLASS_NAME="${RUNTIME_CLASS_NAME:-gvisor}"
RUNTIME_HANDLER="${RUNTIME_HANDLER:-runsc}"
K3S_SERVICE="${K3S_SERVICE:-k3s}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
CONTAINERD_DIR="${CONTAINERD_DIR:-/var/lib/rancher/k3s/agent/etc/containerd}"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-tertius-gvisor-smoke}"
SMOKE_JOB="${SMOKE_JOB:-gvisor-smoke}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root, for example: sudo $0" >&2
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required before running this script." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required before running this script." >&2
  exit 1
fi

install_gvisor_binaries() {
  local arch
  arch="$(uname -m)"

  case "$arch" in
    x86_64|aarch64|arm64) ;;
    *)
      echo "Unsupported architecture for gVisor release binaries: ${arch}" >&2
      exit 1
      ;;
  esac

  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y ca-certificates wget
  fi

  local url="https://storage.googleapis.com/gvisor/releases/release/latest/${arch}"
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  (
    cd "$tmp"
    wget "${url}/runsc" "${url}/runsc.sha512" \
      "${url}/containerd-shim-runsc-v1" "${url}/containerd-shim-runsc-v1.sha512"
    sha512sum -c runsc.sha512 -c containerd-shim-runsc-v1.sha512
    chmod a+rx runsc containerd-shim-runsc-v1
    mv runsc containerd-shim-runsc-v1 /usr/local/bin/
  )
}

ensure_gvisor_binaries() {
  if ! command -v runsc >/dev/null 2>&1; then
    echo "runsc is still not on PATH after install." >&2
    exit 1
  fi

  if ! command -v containerd-shim-runsc-v1 >/dev/null 2>&1; then
    echo "containerd-shim-runsc-v1 is still not on PATH after shim setup." >&2
    exit 1
  fi

  if [ "$(readlink -f "$(command -v runsc)")" = "$(readlink -f "$(command -v containerd-shim-runsc-v1)")" ]; then
    echo "containerd-shim-runsc-v1 must be the real shim binary, not a symlink to runsc." >&2
    exit 1
  fi
}

ensure_containerd_template() {
  mkdir -p "$CONTAINERD_DIR"

  local generated_config="$CONTAINERD_DIR/config.toml"
  local template="$CONTAINERD_DIR/config-v3.toml.tmpl"

  if [ ! -f "$template" ]; then
    if [ ! -f "$generated_config" ]; then
      echo "Cannot find $template or $generated_config." >&2
      exit 1
    fi
    cp "$generated_config" "$template"
  fi

  if grep -q "\\[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.${RUNTIME_HANDLER}\\]" "$template"; then
    return
  fi

  cp "$template" "${template}.bak.$(date +%Y%m%d%H%M%S)"

  cat >>"$template" <<EOF

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.${RUNTIME_HANDLER}]
  runtime_type = "io.containerd.runsc.v1"
EOF
}

restart_k3s() {
  systemctl restart "$K3S_SERVICE"
  systemctl is-active --quiet "$K3S_SERVICE"
}

apply_runtime_class() {
  KUBECONFIG="$KUBECONFIG_PATH" kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: ${RUNTIME_CLASS_NAME}
handler: ${RUNTIME_HANDLER}
EOF
}

run_smoke_test() {
  KUBECONFIG="$KUBECONFIG_PATH" kubectl delete namespace "$SMOKE_NAMESPACE" --ignore-not-found=true --wait=true

  KUBECONFIG="$KUBECONFIG_PATH" kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${SMOKE_NAMESPACE}
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: compile-job-deny-all
  namespace: ${SMOKE_NAMESPACE}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: compile-job
  policyTypes:
    - Ingress
    - Egress
---
apiVersion: batch/v1
kind: Job
metadata:
  name: ${SMOKE_JOB}
  namespace: ${SMOKE_NAMESPACE}
  labels:
    app.kubernetes.io/name: tertius
    app.kubernetes.io/component: compile-job
spec:
  activeDeadlineSeconds: 90
  ttlSecondsAfterFinished: 600
  backoffLimit: 0
  template:
    metadata:
      labels:
        app.kubernetes.io/name: tertius
        app.kubernetes.io/component: compile-job
    spec:
      runtimeClassName: ${RUNTIME_CLASS_NAME}
      restartPolicy: Never
      automountServiceAccountToken: false
      enableServiceLinks: false
      hostNetwork: false
      hostPID: false
      hostIPC: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: compile
          image: busybox:1.36
          imagePullPolicy: IfNotPresent
          command:
            - sh
            - -c
            - |
              id
              test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token
              wget -T 3 -O- http://example.com >/tmp/egress 2>&1 && exit 42 || true
              echo ok > /output/result.txt
              cat /output/result.txt
          resources:
            requests:
              cpu: 100m
              memory: 64Mi
              ephemeral-storage: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
              ephemeral-storage: 512Mi
          securityContext:
            privileged: false
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: scratch
              mountPath: /tmp
            - name: output
              mountPath: /output
      volumes:
        - name: scratch
          emptyDir:
            sizeLimit: 128Mi
        - name: output
          emptyDir:
            sizeLimit: 16Mi
EOF

  local deadline=$((SECONDS + 120))
  local complete=""
  local failed=""

  while [ "$SECONDS" -lt "$deadline" ]; do
    complete="$(KUBECONFIG="$KUBECONFIG_PATH" kubectl -n "$SMOKE_NAMESPACE" get "job/${SMOKE_JOB}" -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null || true)"
    failed="$(KUBECONFIG="$KUBECONFIG_PATH" kubectl -n "$SMOKE_NAMESPACE" get "job/${SMOKE_JOB}" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null || true)"

    if [ "$complete" = "True" ]; then
      KUBECONFIG="$KUBECONFIG_PATH" kubectl -n "$SMOKE_NAMESPACE" logs "job/${SMOKE_JOB}"
      return
    fi

    if [ "$failed" = "True" ]; then
      echo "gVisor smoke job failed. Events:" >&2
      KUBECONFIG="$KUBECONFIG_PATH" kubectl -n "$SMOKE_NAMESPACE" get events --sort-by=.lastTimestamp >&2 || true
      echo "gVisor smoke job logs:" >&2
      KUBECONFIG="$KUBECONFIG_PATH" kubectl -n "$SMOKE_NAMESPACE" logs "job/${SMOKE_JOB}" --all-containers=true >&2 || true
      echo "If the pod exited 42, gVisor started but NetworkPolicy egress was not enforced." >&2
      echo "Run scripts/diagnose-k3s-networkpolicy.sh with sudo to compare runc and gVisor egress enforcement." >&2
      return 1
    fi

    sleep 2
  done

  echo "Timed out waiting for gVisor smoke job to complete or fail. Current state:" >&2
  KUBECONFIG="$KUBECONFIG_PATH" kubectl -n "$SMOKE_NAMESPACE" get all -o wide >&2 || true
  KUBECONFIG="$KUBECONFIG_PATH" kubectl -n "$SMOKE_NAMESPACE" get events --sort-by=.lastTimestamp >&2 || true
  return 1
}

cleanup_smoke_test() {
  KUBECONFIG="$KUBECONFIG_PATH" kubectl delete namespace "$SMOKE_NAMESPACE" --ignore-not-found=true --wait=true
}

install_gvisor_binaries
ensure_gvisor_binaries
ensure_containerd_template
restart_k3s
apply_runtime_class
run_smoke_test
cleanup_smoke_test

echo "gVisor installed and verified for k3s RuntimeClass ${RUNTIME_CLASS_NAME}."
