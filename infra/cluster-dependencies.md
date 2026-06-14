# Cluster Dependency Setup

This guide covers the cluster-side dependencies required before deploying Tertius to a k3s cluster. It is operator-focused: install the platform dependencies first, verify them with smoke tests, then deploy the Helm chart documented in `infra/charts/tertius/README.md`.

The current production-shaped target is:

- k3s single-node or small cluster.
- Helm 3.
- Flux for production GitOps.
- CloudNativePG operator.
- Keycloak operator.
- Cilium as the enforcing CNI for Kubernetes NetworkPolicy.
- gVisor `runsc` as the runtime for hardened compile jobs.

## Required Local Tools

Install these on the operator workstation or node where the setup scripts run:

- `kubectl`
- `helm`
- `jq`
- `sha512sum`
- `wget`
- `systemctl`
- `iptables-save` and `iptables-restore`
- `ip6tables-save` and `ip6tables-restore` if IPv6 is enabled

Use the node kubeconfig for root-run k3s scripts:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

From a normal user shell on this node, `/home/johnson/.kube/config` is also usable for read-only verification.

## Cluster Prerequisites

Install or verify:

- k3s server is running and node is Ready.
- A default StorageClass exists for CloudNativePG, Keycloak Postgres, Valkey, and NATS PVCs.
- Flux is bootstrapped for production deployments.
- CloudNativePG CRDs are installed, especially `clusters.postgresql.cnpg.io`.
- Keycloak operator CRDs are installed, especially `keycloaks.k8s.keycloak.org`.
- The cluster can pull the API and UI images.
- Production values and Secrets exist before Flux reconciles the Tertius HelmRelease.

Fast checks:

```bash
kubectl get nodes -o wide
kubectl get storageclass
kubectl get crd clusters.postgresql.cnpg.io keycloaks.k8s.keycloak.org
kubectl -n flux-system get pods
```

## Fresh k3s Node Bootstrap

For a new single-node k3s host, use the bootstrap wrapper:

```bash
sudo ./infra/scripts/bootstrap-k3s-node.sh
```

The default mode is a dry run. To apply the full setup on a host that already has k3s:

```bash
sudo -E APPLY_BOOTSTRAP=true ./infra/scripts/bootstrap-k3s-node.sh
```

To allow the script to install k3s when it is missing:

```bash
sudo -E APPLY_BOOTSTRAP=true INSTALL_K3S_IF_MISSING=true ./infra/scripts/bootstrap-k3s-node.sh
```

The bootstrap script:

- installs basic host packages when `apt-get` is available;
- optionally installs k3s with `flannel-backend=none` and `disable-network-policy=true`;
- configures existing k3s nodes for Cilium custom CNI mode;
- installs Cilium with the k3s CNI config and binary paths documented below;
- removes stale flannel state;
- installs gVisor;
- runs runc and gVisor deny-all egress smoke tests.

Use the lower-level scripts in the following sections when debugging one layer at a time.

## Network Policy Requirement

Compile jobs require enforced egress denial. A Kubernetes `NetworkPolicy` object is not enough unless the CNI enforces it.

Acceptance criteria:

- A normal `runc` pod selected by a deny-all egress policy logs `EGRESS_BLOCKED`.
- A `runtimeClassName: gvisor` pod selected by the same policy logs `EGRESS_BLOCKED`.
- Both pods receive Cilium-managed pod IPs, not stale flannel IPs.
- `cilium status --brief` returns `OK`.
- `cilium endpoint list` shows pod endpoints, not only the host endpoint.

Run the repo diagnostic:

```bash
sudo ./scripts/diagnose-k3s-networkpolicy.sh
```

Expected secure result:

```text
EGRESS_BLOCKED
EGRESS_BLOCKED
```

If either job logs `EGRESS_ALLOWED`, do not deploy compile jobs that rely on network isolation.

## Cilium on k3s

k3s ships with flannel and an embedded kube-router NetworkPolicy controller by default. On this node, the embedded controller did not enforce egress. The working setup was to disable flannel and k3s NetworkPolicy, then install Cilium with k3s-specific CNI paths.

Use a maintenance window. This changes pod networking and restarts workloads.

Before changing networking, back up:

- `/etc/rancher/k3s/config.yaml`
- `/etc/systemd/system/k3s.service`
- `/etc/systemd/system/k3s.service.env`
- `/var/lib/rancher/k3s/agent/etc/cni/net.d`
- current `kube-system` pod state
- current `NetworkPolicy` state

### Migration Script

Dry run:

```bash
sudo ./scripts/migrate-k3s-cilium.sh
```

Apply:

```bash
sudo -E APPLY_MIGRATION=true ./scripts/migrate-k3s-cilium.sh
```

The script backs up k3s state, sets persistent k3s custom CNI flags, installs Cilium, restarts workloads, and runs the NetworkPolicy smoke test.

### Required k3s Config

The k3s config must include:

```yaml
flannel-backend: none
disable-network-policy: true
```

Verify k3s accepted the flags:

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.annotations.k3s\.io/node-args}{"\n"}{end}'
```

Expected node args include:

```text
--flannel-backend none
--disable-network-policy true
```

### Required Cilium Helm Values for k3s

Do not use the default Cilium CNI paths on k3s. The defaults write to `/etc/cni/net.d` and `/opt/cni/bin`, but k3s on this node uses:

- CNI config: `/var/lib/rancher/k3s/agent/etc/cni/net.d`
- CNI binaries: `/var/lib/rancher/k3s/data/cni`

The working Cilium install command is:

```bash
KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm upgrade --install cilium \
  oci://quay.io/cilium/charts/cilium \
  --version 1.19.4 \
  --namespace kube-system \
  --set operator.replicas=1 \
  --set cni.confPath=/var/lib/rancher/k3s/agent/etc/cni/net.d \
  --set cni.binPath=/var/lib/rancher/k3s/data/cni
```

Verify:

```bash
kubectl -n kube-system rollout status daemonset/cilium --timeout=240s
kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium status --brief
kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium endpoint list
kubectl get pods -A -o wide
```

Healthy signs:

- `cilium status --brief` prints `OK`.
- New pods have `10.0.0.x` Cilium pod IPs in the current setup.
- Cilium endpoint list includes application pods.
- `ip -d link show type vxlan` shows `cilium_vxlan` and no active `flannel.1`.

### Flannel Leftovers

If pods still get old `10.42.x` addresses, or Cilium only shows the host endpoint, stale flannel state is still active.

Run:

```bash
sudo ./scripts/repair-cilium-after-flannel.sh
```

This stops k3s, removes stale flannel VXLAN/CNI runtime state, restarts k3s and Cilium, restarts workloads, then reruns the NetworkPolicy smoke test.

Manual checks:

```bash
ip -d link show type vxlan
find /var/lib/rancher/k3s/agent/etc/cni/net.d -maxdepth 1 -type f -print
kubectl -n kube-system get configmap cilium-config -o yaml | grep -E 'write-cni-conf|cni-bin|cni-conf|cni-'
```

## gVisor for Compile Jobs

Compile isolation requires k3s/containerd to know the `runsc` runtime and Kubernetes to expose it as a RuntimeClass.

Run:

```bash
sudo ./scripts/install-gvisor-k3s.sh
```

The script:

- Downloads official gVisor `runsc` and `containerd-shim-runsc-v1` release binaries.
- Verifies SHA-512 checksums.
- Installs binaries under `/usr/local/bin`.
- Adds a `runsc` runtime to the k3s containerd v3 template.
- Restarts k3s.
- Creates `RuntimeClass/gvisor`.
- Runs a hardened smoke Job.

Verify:

```bash
runsc --version
which containerd-shim-runsc-v1
kubectl get runtimeclass gvisor -o yaml
```

Expected RuntimeClass:

```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
```

Important: `containerd-shim-runsc-v1` must be the real shim binary. Do not symlink it to `runsc`; that fails with shim flags such as `-namespace`.

## Compile Job Security Baseline

Any future k3s compile Job should include at least:

```yaml
apiVersion: batch/v1
kind: Job
spec:
  activeDeadlineSeconds: 90
  ttlSecondsAfterFinished: 600
  backoffLimit: 0
  template:
    spec:
      runtimeClassName: gvisor
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
```

Pair the Job with a deny-all NetworkPolicy selecting the compile pod labels:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: compile-job-deny-all
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: compile-job
  policyTypes:
    - Ingress
    - Egress
```

The current Tertius chart NetworkPolicy behavior is ingress-only. If egress policies are added to app namespaces later, explicitly allow required traffic for DNS, NATS `4222`, Postgres, Valkey, Keycloak, and any intentionally required external endpoints. Do not copy those app egress allowances into compile-job namespaces.

## Anti-Patterns

Do not:

- Deploy compile jobs before proving both runc and gVisor deny-all egress smoke tests log `EGRESS_BLOCKED`.
- Trust NetworkPolicy objects without an enforcing CNI.
- Use Cilium default CNI paths on k3s.
- Leave `flannel.1` active after migrating to Cilium.
- Symlink `containerd-shim-runsc-v1` to `runsc`.
- Add `hostPath`, privileged containers, host networking, or service account tokens to compile Jobs.
- Use broad namespace-wide egress exceptions for compile workloads.

## Verification Matrix

| Check | Command | Pass Condition |
| --- | --- | --- |
| Node ready | `kubectl get nodes -o wide` | Node is `Ready` |
| Cilium healthy | `kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium status --brief` | Prints `OK` |
| Cilium endpoints | `kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium endpoint list` | Application pods appear |
| No flannel VXLAN | `ip -d link show type vxlan` | No active `flannel.1` |
| gVisor RuntimeClass | `kubectl get runtimeclass gvisor -o yaml` | `handler: runsc` |
| Egress blocked | `sudo ./scripts/diagnose-k3s-networkpolicy.sh` | runc and gVisor jobs log `EGRESS_BLOCKED` |
| Tertius chart render | `helm template tertius infra/charts/tertius --values infra/charts/tertius/values-local.yaml` | Renders without error |
| Tertius local smoke | `scripts/test-k3s-deployment.sh` | Smoke checks pass |

## Rollback Notes

Cilium migration rollback:

```bash
KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm uninstall cilium -n kube-system
sudo cp /root/<backup-dir>/etc/rancher/k3s/config.yaml /etc/rancher/k3s/config.yaml
sudo systemctl restart k3s
```

If the backup contains the previous k3s CNI config, restore it from:

```text
/root/<backup-dir>/var/lib/rancher/k3s/agent/etc/cni/net.d
```

gVisor rollback:

- Delete `RuntimeClass/gvisor` if no workloads use it.
- Remove the `runsc` runtime stanza from `/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl`.
- Restart k3s.

## Residual Operational Notes

After the Cilium migration on this node, NetworkPolicy enforcement passed for runc and gVisor smoke Jobs. `metrics-server` may need separate follow-up if it reports no Ready endpoint; verify with:

```bash
kubectl -n kube-system logs deploy/metrics-server --tail=100
kubectl get apiservice v1beta1.metrics.k8s.io -o yaml
kubectl top nodes
```

This does not affect compile-job NetworkPolicy acceptance, but it does affect `kubectl top`.

k3s CNI settings are host-level configuration, not Helm chart state. If production is GitOps-managed, document the host k3s config management path separately so Flux does not give a false sense that CNI state is fully represented in this repository.

## References

- k3s networking options: https://docs.k3s.io/networking/basic-network-options
- k3s NetworkPolicy controller: https://docs.k3s.io/networking/networking-services
- Cilium Helm installation: https://docs.cilium.io/en/stable/installation/k8s-install-helm/
- gVisor containerd quick start: https://gvisor.dev/docs/user_guide/containerd/quick_start/
- Kubernetes NetworkPolicy: https://kubernetes.io/docs/concepts/services-networking/network-policies/
