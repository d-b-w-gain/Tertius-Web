# k3s Harness Lifecycle Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make disposable k3s harness releases self-identifying, automatically recoverable, and completely removable without risking Flux-managed production resources.

**Architecture:** A release-scoped ConfigMap records the lease and expiry. Existing Helm labels and Flux checks remain the deletion boundary. A host-side janitor performs Helm-aware cleanup, and an optional systemd user timer runs it every 15 minutes.

**Tech Stack:** Bash, Helm, kubectl, jq, systemd user units, GitHub Actions, existing shell test harnesses.

---

### Task 1: Lifecycle cleanup regression harness

**Files:**
- Create: `scripts/test-k3s-harness-lifecycle.sh`
- Modify: `scripts/test-deployment-config.sh`
- Modify: `docs/harness/quality-gates.md`

- [x] **Step 1: Write failing mock-based cleanup tests**

Create a temporary `kubectl`/`helm` command log, source the deployment script
with `TEST_K3S_DEPLOYMENT_LIB_ONLY=true`, and assert these exact behaviors:

```bash
run_cleanup
assert_log 'helm uninstall disposable -n tertius --ignore-not-found'
assert_log 'kubectl delete secret disposable-app -n tertius --ignore-not-found=true'
assert_log 'kubectl delete configmap disposable-harness-lifecycle -n tertius --ignore-not-found=true'
assert_absence_check
```

Add separate cases for `--retain-data`, `--retain-auth`, unconditional Flux and
production refusal, lease UUID mismatch, exact legacy adoption, idempotency,
and remaining-resource failure.

- [x] **Step 2: Run the test and verify RED**

Run: `rtk bash scripts/test-k3s-harness-lifecycle.sh`

Expected: FAIL because complete cleanup, explicit retention flags, and absence
verification do not exist.

- [x] **Step 3: Register the new focused gate**

Add the test to `scripts/test-deployment-config.sh` and document its command in
`docs/harness/quality-gates.md`.

- [x] **Step 4: Commit the red tests**

```bash
rtk git add scripts/test-k3s-harness-lifecycle.sh scripts/test-deployment-config.sh docs/harness/quality-gates.md
rtk git commit -m "test: define k3s harness lifecycle cleanup"
```

### Task 2: Complete cleanup, lease creation, and automatic rollback

**Files:**
- Modify: `scripts/test-k3s-deployment.sh`
- Modify: `scripts/harness-k3s.sh`
- Modify: `scripts/test-k3s-harness-lifecycle.sh`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/templates/_helpers.tpl`
- Modify: data-bearing templates under `infra/charts/tertius/templates/`

- [x] **Step 1: Implement exact flags and validated lifecycle state**

Add `--retain-data` and `--retain-auth`, retain `--delete-data` as a compatibility
alias, and validate `HARNESS_TTL_SECONDS` in `[900, 86400]`. Apply the marker:

```yaml
metadata:
  name: ${RELEASE_NAME}-harness-lifecycle
  labels:
    tertius.io/harness-managed: "true"
    app.kubernetes.io/instance: ${RELEASE_NAME}
  annotations:
    tertius.io/lease-id: ${lease_id}
    tertius.io/release-name: ${RELEASE_NAME}
    tertius.io/expires-at: ${expires_at}
    tertius.io/cleanup-policy: delete
```

Pass the lease ID through a dedicated chart value and render it on CNPG Cluster
CRs and every chart-created PVC from their first API write. Refuse `up` when a
Helm release already exists without a valid marker.

- [x] **Step 2: Implement full cleanup and absence verification**

Full cleanup must require matching lease UUIDs; unconditionally refuse
production and any Flux target found by effective target namespace/release;
uninstall Helm; delete exact non-retained Clusters, PVCs, `${APP_SECRET_NAME}`,
and lifecycle marker; wait for every kind enumerated by the spec plus captured
operator descendants; and fail if anything non-retained remains. Retention
preserves a tombstone marker containing retained names and UIDs.

- [x] **Step 3: Implement explicit legacy adoption**

Add `harness-k3s.sh adopt <namespace>/<release>`. Require the exact confirmation
text, refuse production/Flux, create a lease UUID, and annotate the external
Secret plus existing release data. There is no generic force-delete mode.

- [x] **Step 4: Add guarded failure/signal cleanup**

After marker creation, `ERR`, `INT`, and `TERM` capture Helm/scoped diagnostics,
then call full cleanup once unless `HARNESS_RETAIN_ON_FAILURE=true`. Preserve
the original exit code and keep the existing local-file/process cleanup.

- [x] **Step 5: Make wrapper cleanup default-complete**

`harness-k3s.sh down` performs full cleanup. Accept `--retain-data` and
`--retain-auth`; keep `delete-data` as a compatibility alias. Source the saved
status for cleanup only when explicit namespace/release environment overrides
are absent, then re-run the Flux guard against the resolved target.

- [x] **Step 6: Run GREEN tests**

Run:

```bash
rtk bash scripts/test-k3s-harness-lifecycle.sh
rtk bash scripts/test-k3s-wffc-wait.sh
```

Expected: all cases pass.

- [x] **Step 7: Commit**

```bash
rtk git add scripts/test-k3s-deployment.sh scripts/harness-k3s.sh scripts/test-k3s-harness-lifecycle.sh
rtk git commit -m "feat: enforce disposable k3s harness lifecycles"
```

### Task 3: Expiry janitor and systemd schedule

**Files:**
- Create: `scripts/cleanup-expired-k3s-harness.sh`
- Create: `scripts/install-k3s-harness-cleanup-timer.sh`
- Create: `scripts/test-k3s-harness-janitor.sh`
- Modify: `scripts/test-deployment-config.sh`

- [x] **Step 1: Write failing janitor and installer tests**

Use mocked JSON inventory and an injected `NOW_EPOCH`. Cover future, exactly
expired, malformed, mismatched, production, Flux-managed, cleanup failure, and
continuation to later markers. Under a temporary HOME, assert installer output:

```ini
[Timer]
OnBootSec=5m
OnUnitActiveSec=15m
Persistent=true
```

- [x] **Step 2: Run and verify RED**

Run: `rtk bash scripts/test-k3s-harness-janitor.sh`

Expected: FAIL because both scripts are missing.

- [x] **Step 3: Implement fail-closed janitor**

List only `tertius.io/harness-managed=true` ConfigMaps, validate every metadata
field and lease UUID, parse RFC 3339 timestamps, skip retention tombstones,
refuse production/Flux, and invoke exact full cleanup. Continue across
candidates and return nonzero if any malformed marker or cleanup failure occurs.
Pass the observed marker UID, resourceVersion, lease UUID, expiry, and decision
time so cleanup can reject renewal or replacement races before mutation.

- [x] **Step 4: Implement timer install/uninstall**

Write user units atomically under `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`,
pin the repository and kubeconfig paths, run `systemctl --user daemon-reload`,
and enable/disable `tertius-k3s-harness-cleanup.timer`.

- [x] **Step 5: Run GREEN tests and commit**

```bash
rtk bash scripts/test-k3s-harness-janitor.sh
rtk git add scripts/cleanup-expired-k3s-harness.sh scripts/install-k3s-harness-cleanup-timer.sh scripts/test-k3s-harness-janitor.sh scripts/test-deployment-config.sh
rtk git commit -m "feat: add scheduled k3s harness janitor"
```

### Task 4: Failure-safe diagnostics and port forwards

**Files:**
- Modify: `scripts/diagnose-k3s-networkpolicy.sh`
- Modify: `scripts/install-gvisor-k3s.sh`
- Modify: `scripts/harness-k3s.sh`
- Modify: `scripts/test-k3s-deployment.sh`
- Create: `scripts/test-k3s-harness-process-cleanup.sh`

- [x] **Step 1: Write failing subprocess tests**

Mock `kubectl`, force failure after namespace creation, and assert one namespace
delete on EXIT/INT/TERM only when its ownership UUID matches; pre-existing and
mismatched namespaces must never be deleted. Mock port-forward startup to assert
per-target atomic PID/start-token/command recording before readiness, no signal
to a recycled PID, child termination on timeout, and cleanup of earlier children
after partial startup.

- [x] **Step 2: Run and verify RED**

Run: `rtk bash scripts/test-k3s-harness-process-cleanup.sh`

Expected: FAIL on current tail-only diagnostic cleanup and subshell PID loss.

- [x] **Step 3: Implement diagnostic ownership traps**

Refuse a pre-existing namespace. After creating a UUID-labelled namespace,
install cleanup immediately. Disable the trap during cleanup, re-check UUID,
preserve the original exit status, and honor the explicit keep flag.

- [x] **Step 4: Implement parent-owned port-forward tracking**

Replace command-substitution callers with an output-variable argument. Store
state per context/namespace/release, record PID plus `/proc` start token and
exact command atomically before readiness polling, verify identity before kill,
terminate/reap on failure, and install the wrapper trap before the first start.

- [x] **Step 5: Run GREEN tests and commit**

```bash
rtk bash scripts/test-k3s-harness-process-cleanup.sh
rtk git add scripts/diagnose-k3s-networkpolicy.sh scripts/install-gvisor-k3s.sh scripts/harness-k3s.sh scripts/test-k3s-deployment.sh scripts/test-k3s-harness-process-cleanup.sh
rtk git commit -m "fix: clean interrupted k3s harness resources"
```

### Task 5: CI teardown and operator documentation

**Files:**
- Modify: `.github/workflows/chart-tests.yml`
- Modify: `scripts/test-deployment-config.sh`
- Modify: `docs/harness/local-harness.md`
- Modify: `infra/deploy/README.md`

- [x] **Step 1: Add failing workflow contract assertions**

Require a distinct cleanup step with `if: ${{ always() }}`, forbid `|| true`,
and include new lifecycle/janitor/diagnostic files in workflow path filters.

- [x] **Step 2: Run and verify RED**

Run: `rtk bash scripts/test-deployment-config.sh`

Expected: FAIL because cleanup is embedded and ignored.

- [x] **Step 3: Split CI deployment and cleanup**

Run deploy normally. Add a later always-step that runs full cleanup and absence
verification without suppressing failure. Hosted runner disposal remains a
fallback, not the asserted cleanup mechanism.

- [x] **Step 4: Document exact lifecycle commands**

Document TTL defaults, status-file target resolution, explicit retention,
janitor dry-run/execute behavior, timer installation, Flux refusal, and full
cleanup semantics. Link to the implementation spec rather than duplicate its
error/test matrices.

- [x] **Step 5: Run GREEN checks and commit**

```bash
rtk bash scripts/test-deployment-config.sh
rtk git add .github/workflows/chart-tests.yml scripts/test-deployment-config.sh docs/harness/local-harness.md infra/deploy/README.md
rtk git commit -m "ci: guarantee k3s harness teardown"
```

### Task 6: Verification and authorized live cleanup

**Files:**
- Modify: `docs/superpowers/plans/2026-08-14-k3s-harness-lifecycle-cleanup.md`

- [x] **Step 1: Run shell and configuration quality gates**

```bash
rtk bash -n scripts/test-k3s-deployment.sh scripts/harness-k3s.sh scripts/cleanup-expired-k3s-harness.sh scripts/install-k3s-harness-cleanup-timer.sh scripts/diagnose-k3s-networkpolicy.sh scripts/install-gvisor-k3s.sh
rtk bash scripts/test-k3s-harness-lifecycle.sh
rtk bash scripts/test-k3s-harness-janitor.sh
rtk bash scripts/test-k3s-harness-process-cleanup.sh
rtk bash scripts/test-deployment-config.sh
rtk bash scripts/check-runtime-parity.sh
```

- [x] **Step 2: Install and verify the user timer**

Install with the canonical checkout path and `/home/johnson/.kube/config`, then
verify the timer is enabled and list its next activation without exposing
credentials.

- [x] **Step 3: Clean the two authorized targets**

Run full cleanup for exactly:

```text
tertius/tertius-live-flow-smoke
tertius/tertius-3mf-task15
```

Do not target `tertius/tertius`.

- [x] **Step 4: Verify live absence and production health**

Assert both Helm releases, exact app Secrets, lifecycle markers, CNPG clusters,
PVCs, labelled workloads, ScaledJobs, and Jobs are absent. Assert Flux
`HelmRelease/tertius` remains Ready and its API/UI workloads remain Available.

- [x] **Step 5: Mark plan checkboxes and commit verification evidence**

```bash
rtk git add docs/superpowers/plans/2026-08-14-k3s-harness-lifecycle-cleanup.md
rtk git commit -m "docs: record k3s lifecycle cleanup verification"
```

Verified 2026-08-14: the user timer is enabled and active on its 15-minute
schedule; both authorized legacy Helm releases, lifecycle markers, exact
external Secrets, data resources, and release-scoped objects are absent.
Flux `HelmRelease/tertius` remains Ready, and `tertius-api` plus `tertius-ui`
each have all requested replicas Available.

## Plan self-review

- Spec coverage: every requirement in the design maps to Tasks 1-6.
- Placeholder scan: no TBD/TODO or unspecified implementation step remains.
- Type/interface consistency: release marker, flags, timer name, TTL bounds, and
  cleanup commands match the design document exactly.
