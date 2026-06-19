#!/usr/bin/env bash
set -euo pipefail

KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
K3S_SERVICE="${K3S_SERVICE:-k3s}"
NAMESPACE="${NAMESPACE:-tertius-netpol-diagnose-$(date +%H%M%S)}"
KEEP_NAMESPACE="${KEEP_NAMESPACE:-false}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root, for example: sudo $0" >&2
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required." >&2
  exit 1
fi

run_kubectl() {
  KUBECONFIG="$KUBECONFIG_PATH" kubectl "$@"
}

section() {
  printf '\n== %s ==\n' "$1"
}

job_condition() {
  local job="$1"
  local condition="$2"
  run_kubectl -n "$NAMESPACE" get "job/${job}" \
    -o "jsonpath={.status.conditions[?(@.type==\"${condition}\")].status}" 2>/dev/null || true
}

wait_job_terminal() {
  local job="$1"
  local deadline=$((SECONDS + 75))
  local complete=""
  local failed=""

  while [ "$SECONDS" -lt "$deadline" ]; do
    complete="$(job_condition "$job" Complete)"
    failed="$(job_condition "$job" Failed)"

    if [ "$complete" = "True" ] || [ "$failed" = "True" ]; then
      return 0
    fi

    sleep 2
  done

  return 1
}

apply_job() {
  local name="$1"
  local runtime_class="$2"
  local runtime_class_line=""

  if [ -n "$runtime_class" ]; then
    runtime_class_line="      runtimeClassName: ${runtime_class}"
  fi

  run_kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${name}
  namespace: ${NAMESPACE}
spec:
  activeDeadlineSeconds: 45
  ttlSecondsAfterFinished: 300
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: netpol-diagnose
        job: ${name}
    spec:
${runtime_class_line}
      restartPolicy: Never
      automountServiceAccountToken: false
      enableServiceLinks: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: check
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              id
              wget -T 3 -O- http://example.com >/tmp/egress 2>&1 && { echo EGRESS_ALLOWED; exit 42; } || { echo EGRESS_BLOCKED; exit 0; }
          resources:
            requests:
              cpu: 50m
              memory: 32Mi
            limits:
              cpu: 100m
              memory: 64Mi
          securityContext:
            privileged: false
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 16Mi
EOF
}

section "k3s service"
systemctl show "$K3S_SERVICE" -p ExecStart -p Environment || true

section "node and runtime classes"
run_kubectl get nodes -o wide
run_kubectl get runtimeclass || true
run_kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" annotations: "}{.metadata.annotations.k3s\.io/node-args}{" flannel="}{.metadata.annotations.flannel\.alpha\.coreos\.com/backend-type}{" podCIDR="}{.spec.podCIDR}{"\n"}{end}' || true

section "kube-system network components"
run_kubectl -n kube-system get daemonset,deploy,pod -o wide || true

section "k3s journal network-policy lines"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -u "$K3S_SERVICE" -b --no-pager 2>/dev/null | grep -Ei 'network.?policy|kube-router|netpol|flannel' || true
else
  echo "journalctl not available"
fi

section "host firewall network-policy chains"
if command -v iptables-save >/dev/null 2>&1; then
  iptables-save | grep -E 'KUBE-ROUTER|KUBE-NWPLCY|KUBE-POD-FW|netpol' || true
else
  echo "iptables-save not available"
fi

if command -v ip6tables-save >/dev/null 2>&1; then
  ip6tables-save | grep -E 'KUBE-ROUTER|KUBE-NWPLCY|KUBE-POD-FW|netpol' || true
fi

if command -v nft >/dev/null 2>&1; then
  nft list ruleset 2>/dev/null | grep -Ei 'KUBE-ROUTER|KUBE-NWPLCY|KUBE-POD-FW|netpol|network.?policy' || true
fi

section "apply deny-all egress smoke"
run_kubectl delete namespace "$NAMESPACE" --ignore-not-found=true --wait=true --timeout=90s
run_kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAMESPACE}
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-egress
  namespace: ${NAMESPACE}
spec:
  podSelector:
    matchLabels:
      app: netpol-diagnose
  policyTypes:
    - Ingress
    - Egress
EOF

apply_job "runc-egress-deny" ""

if run_kubectl get runtimeclass gvisor >/dev/null 2>&1; then
  apply_job "gvisor-egress-deny" "gvisor"
else
  echo "RuntimeClass/gvisor is absent; skipping gVisor comparison job."
fi

wait_job_terminal "runc-egress-deny" || true
if run_kubectl -n "$NAMESPACE" get job gvisor-egress-deny >/dev/null 2>&1; then
  wait_job_terminal "gvisor-egress-deny" || true
fi

section "smoke results"
run_kubectl -n "$NAMESPACE" get networkpolicy,jobs,pods -o wide || true
run_kubectl -n "$NAMESPACE" logs job/runc-egress-deny --all-containers=true --tail=80 || true
if run_kubectl -n "$NAMESPACE" get job gvisor-egress-deny >/dev/null 2>&1; then
  run_kubectl -n "$NAMESPACE" logs job/gvisor-egress-deny --all-containers=true --tail=80 || true
fi
run_kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp || true

section "interpretation"
echo "Expected secure result: each job logs EGRESS_BLOCKED and exits successfully."
echo "If runc logs EGRESS_ALLOWED, NetworkPolicy egress is not being enforced for ordinary pods on this node."
echo "If runc is blocked but gVisor is allowed, investigate runtime/CNI integration before using gVisor for compile isolation."

if [ "$KEEP_NAMESPACE" != "true" ]; then
  section "cleanup"
  run_kubectl delete namespace "$NAMESPACE" --ignore-not-found=true --wait=true --timeout=90s
else
  echo "Keeping namespace ${NAMESPACE} because KEEP_NAMESPACE=true."
fi
