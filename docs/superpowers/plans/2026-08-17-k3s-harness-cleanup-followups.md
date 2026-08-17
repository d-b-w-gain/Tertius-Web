# k3s Harness Cleanup Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make external Secret lease ownership atomic and make retained harness data safely removable by a later explicit full cleanup.

**Architecture:** Keep the existing lifecycle marker and fail-closed cleanup boundary. Render the application Secret as JSON, add its lease annotation locally, and apply it in one server-side write. For retained tombstones, compare every surviving data root and captured operator descendant with the tombstone's exact kind/name/UID records before allowing a manual compare-and-swap claim.

**Tech Stack:** Bash, kubectl, Helm, jq, mock-based shell contract tests, k3s harness scripts.

---

## File responsibilities

- `scripts/test-k3s-deployment.sh`: create the leased Secret atomically and validate retained tombstones before cleanup mutation.
- `scripts/test-k3s-harness-lifecycle.sh`: model Secret input manifests and retained marker metadata; prove success and refusal behavior.
- `docs/harness/local-harness.md`: state how explicitly retained data is later removed.
- `docs/superpowers/specs/2026-08-17-k3s-harness-cleanup-followups-design.md`: approved safety and behavior contract.

### Task 1: Apply the application Secret with lease ownership atomically

**Files:**
- Modify: `scripts/test-k3s-harness-lifecycle.sh`
- Modify: `scripts/test-k3s-deployment.sh:951-962`

- [x] **Step 1: Extend the kubectl mock to expose the first applied Secret manifest**

Teach the mock `create secret generic "$APP_SECRET_NAME" ... -o json` path to emit a minimal Secret JSON document and teach `apply -f -` to save stdin in `${COMMAND_LOG}.stdin`. Do not log test Secret values.

- [x] **Step 2: Add the failing atomic-ownership test**

Invoke `ensure_app_secret` through the library-only entry point with a known lease and dummy values. Assert:

```bash
jq -e --arg lease '11111111-1111-4111-8111-111111111111' \
  '.metadata.annotations["tertius.io/lease-id"] == $lease' \
  "${COMMAND_LOG}.stdin"
assert_not_log 'kubectl annotate secret test-release-app' \
  'application Secret ownership must not require a second server-side write'
```

- [x] **Step 3: Run the test and verify RED**

Run: `bash scripts/test-k3s-harness-lifecycle.sh`

Expected: FAIL because the applied Secret manifest lacks `tertius.io/lease-id` and the existing function performs a later `kubectl annotate`.

- [x] **Step 4: Implement the single-write Secret pipeline**

Change `ensure_app_secret` to produce JSON locally, add the annotation with jq, and apply the resulting manifest:

```bash
kubectl -n "$NAMESPACE" create secret generic "$APP_SECRET_NAME" \
  --from-literal=DATABASE_URL="$APP_DATABASE_URL" \
  --from-literal=VALKEY_URL="$APP_VALKEY_URL" \
  --from-literal=OIDC_CLIENT_SECRET="$APP_OIDC_CLIENT_SECRET" \
  --from-literal=AUTH_SESSION_SECRET="$APP_AUTH_SESSION_SECRET" \
  --dry-run=client -o json |
  jq --arg lease "$LIFECYCLE_LEASE_ID" \
    '.metadata.annotations = ((.metadata.annotations // {}) + {"tertius.io/lease-id": $lease})' |
  kubectl apply -f -
```

Remove the separate server-side `kubectl annotate secret` call while preserving the redacted diagnostic line.

- [x] **Step 5: Run the lifecycle test and verify GREEN**

Run: `bash scripts/test-k3s-harness-lifecycle.sh`

Expected: `k3s harness lifecycle contract tests passed`.

### Task 2: Safely clean retained lifecycle tombstones

**Files:**
- Modify: `scripts/test-k3s-harness-lifecycle.sh`
- Modify: `scripts/test-k3s-deployment.sh:1750-1795`
- Modify: `scripts/test-k3s-deployment.sh:1940-2000`

- [x] **Step 1: Model retained-object metadata in the lifecycle marker mock**

Add a marker state file whose default is an empty string and expose it as the `tertius.io/retained-objects` annotation. Add helpers that create the post-`--retain-data` state: no Helm release or Secret, marker policy `retain`, and exact records for the surviving Cluster and PVC UIDs.

- [x] **Step 2: Add a failing valid-tombstone cleanup test**

Start from the retained-data state and run plain cleanup. Assert that the marker is claimed from `retain` to `cleaning`, each surviving Cluster/PVC is deleted with preconditions, and the marker is deleted.

- [x] **Step 3: Run the lifecycle test and verify RED**

Run: `bash scripts/test-k3s-harness-lifecycle.sh`

Expected: FAIL with `Lifecycle marker is not eligible for cleanup claiming.`

- [x] **Step 4: Add failing replacement and unexpected-data refusal tests**

Add cases where:

```text
Cluster/test-release-postgres@replacement-cluster-uid
PersistentVolumeClaim/test-release-extra@extra-uid
```

appear in live inventory but not as exact tombstone records. Each case must return nonzero and log no marker patch, Helm uninstall, annotation, or raw delete.

- [x] **Step 5: Add a failing missing-recorded-object idempotency test**

Remove one object recorded by the tombstone before plain cleanup. The test must expect cleanup to succeed and remove the remaining exact objects and marker.

- [x] **Step 6: Implement retained-tombstone validation**

Add a helper that parses the marker annotation as comma-separated `kind/name@uid` records, rejects malformed or duplicate records, resolves every recorded object independently by kind and name, builds exact records for current Cluster roots, PVCs, and discovered operator descendants, and requires every current record to appear in the tombstone. Only a confirmed missing exact lookup is treated as absent; surviving roots without release labels and descendants whose recorded root is missing still require matching UIDs. Existing lease checks remain mandatory for Cluster/PVC roots.

In `claim_cleanup_marker`, accept `retain` only when no janitor expected-snapshot variables are present and the retained-tombstone validation succeeds. Keep `delete|cleaning` behavior unchanged. Use the existing UID/resourceVersion policy test in the claim patch so the transition to `cleaning` remains atomic.

- [x] **Step 7: Run lifecycle and janitor tests and verify GREEN**

Run:

```bash
bash scripts/test-k3s-harness-lifecycle.sh
bash scripts/test-k3s-harness-janitor.sh
```

Expected: both contract suites pass, and the janitor still reports retained tombstones as skipped.

### Task 3: Document and verify the complete cleanup contract

**Files:**
- Modify: `docs/harness/local-harness.md:93-110`
- Modify: `docs/superpowers/plans/2026-08-17-k3s-harness-cleanup-followups.md` (check completed steps)

- [x] **Step 1: Document later removal of retained data**

State that `down --retain-data` and `down --retain-auth` create an identity-bearing tombstone and that a later plain `down` removes the exact retained objects after validating their recorded UIDs.

- [x] **Step 2: Run focused cleanup gates**

Run:

```bash
bash scripts/test-k3s-harness-lifecycle.sh
bash scripts/test-k3s-harness-janitor.sh
bash scripts/test-k3s-harness-process-cleanup.sh
```

Expected: all three suites pass.

- [x] **Step 3: Run broad deployment gates**

Run:

```bash
bash scripts/check-runtime-parity.sh
PATH="$(pwd)/.venv/bin:$PATH" UV_CACHE_DIR=/tmp/tertius-uv-cache UV_NO_SYNC=1 \
  bash scripts/test-deployment-config.sh
bash -n scripts/test-k3s-deployment.sh scripts/test-k3s-harness-lifecycle.sh
git diff --check
```

If the worktree has no `.venv`, use the main checkout's existing `.venv/bin` at `/home/johnson/code/Tertius-Web/.venv/bin`. Expected: every command exits zero.

- [ ] **Step 4: Request independent code review**

Give the reviewer the approved design, base SHA, head SHA, diff, and verification evidence. Resolve all critical and important findings before publishing.

- [ ] **Step 5: Commit and publish**

Stage only the specification, plan, two scripts, and harness documentation. Commit with a scoped fix message, push `codex/fix-k3s-cleanup-followups`, create one non-draft PR against `master`, and confirm the PR URL and initial check rollup.
