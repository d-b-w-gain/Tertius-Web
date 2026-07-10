# CI-Owned Image Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PAT-backed Flux image promotion with a GitHub App-driven CI promotion PR and read-only Flux reconciliation.

**Architecture:** `Build Images` serializes build and promotion for the current `master` SHA, publishes unique run-attempt tags, updates a fixed promotion branch, waits for the named chart check, and merges the exact checked head. Flux only reads the public repository; image reflection and automation resources are removed.

**Tech Stack:** GitHub Actions, GitHub App installation tokens, Python 3 standard library, Helm, Flux CD, Kustomize, Bash.

---

### Task 1: Lock The New Contract With A Failing Gate

**Files:**
- Modify: `scripts/test-deployment-config.sh`

- [x] Replace the Flux setter, ImageUpdateAutomation, PAT workflow, and write-secret assertions with negative assertions for those resources and positive assertions for App-token promotion in `images.yml`.
- [x] Add a temporary-copy exercise that invokes `scripts/promote_images.py` with `master-999-1-abcdef0` and verifies both marked tag values change.
- [x] Run `rtk ./scripts/test-deployment-config.sh` and verify it fails because the old automation manifests and PAT architecture still exist.

### Task 2: Add The Deterministic Tag Updater

**Files:**
- Create: `scripts/promote_images.py`
- Modify: `infra/charts/tertius/values.yaml`

- [ ] Implement `master-[0-9]+-[0-9]+-[a-f0-9]{7}` validation and exact single replacement for `$imagepromoter` markers `tertius-api` and `tertius-ui`.
- [ ] Make writes atomic with a sibling temporary file and preserve all unrelated text.
- [ ] Replace the four Flux image-policy comments with two CI image-promoter tag markers; repository values remain unmarked.
- [ ] Run the configuration gate and verify the updater tests pass while the architecture assertions still fail on old Flux resources.

### Task 3: Move Promotion Into Build Images

**Files:**
- Modify: `.github/workflows/images.yml`
- Delete: `.github/workflows/flux-image-update-pr.yml`

- [ ] Add workflow concurrency `ci-owned-image-promotion` with `cancel-in-progress: true` and remove the `[skip ci]` deployment bypass.
- [ ] Verify live `master` equals `GITHUB_SHA` before image publication.
- [ ] Change the published immutable tag to `master-${GITHUB_RUN_NUMBER}-${GITHUB_RUN_ATTEMPT}-${short_sha}` and expose it as a job output.
- [ ] Add a `promote` job after both image pushes that mints a v3 App token from `IMAGE_PROMOTION_APP_CLIENT_ID` and `IMAGE_PROMOTION_APP_PRIVATE_KEY`.
- [ ] Check out the source SHA without persisted `GITHUB_TOKEN`, call `scripts/promote_images.py`, force-update `image-promotion` with a lease, and create or reuse its PR.
- [ ] Poll `Chart render/config checks` for the exact PR head; fail on timeout or non-success.
- [ ] Recheck live `master`, mint a fresh App token, and merge using `--match-head-commit` and branch deletion.
- [ ] Remove the obsolete asynchronous PAT workflow.

### Task 4: Make Flux Read-Only

**Files:**
- Modify: `infra/clusters/production/kustomization.yaml`
- Modify: `infra/clusters/production/flux-system/gitrepository.yaml`
- Delete: `infra/clusters/production/flux-system/image-repositories.yaml`
- Delete: `infra/clusters/production/flux-system/image-policies.yaml`
- Delete: `infra/clusters/production/flux-system/image-update-automation.yaml`

- [ ] Remove all three image automation resources from the production Kustomization.
- [ ] Remove `secretRef: tertius-web-write` from the public GitRepository.
- [ ] Delete the unused image reflection and automation manifests.
- [ ] Run `kubectl kustomize infra/clusters/production` and verify there are no image toolkit resources or Secret references.

### Task 5: Keep Generated Promotion Checks Focused

**Files:**
- Modify: `.github/workflows/chart-tests.yml`
- Modify: `.github/workflows/tests.yml`
- Modify: `.github/workflows/integration.yml`

- [ ] Remove the obsolete `flux-image-updates` push trigger and workflow path.
- [ ] Skip heavy tests and k3s smoke only when the exact generated head branch is `image-promotion`; keep `Chart render/config checks` running on its PR.
- [ ] Add deployment-gate assertions for these exact branch rules.

### Task 6: Replace PAT Operations With App Operations

**Files:**
- Modify: `infra/deploy/README.md`

- [ ] Replace PAT regeneration instructions with repository-scoped GitHub App creation, permissions, installation, variable, private-key secret, and key-rotation instructions.
- [ ] Document that the App must not bypass `Protect Master`.
- [ ] Document the ordered cleanup of `tertius-web-write`, Flux image resources, and the obsolete remote branch only after read-only reconciliation is Ready.
- [ ] Update production operations to describe CI-owned PR promotion and read-only Flux deployment.

### Task 7: Verify The Complete Change

**Files:**
- Modify: `docs/superpowers/plans/2026-07-10-ci-owned-image-promotion.md`

- [ ] Run `rtk ./scripts/test-deployment-config.sh`; expect `Runtime parity check passed.`
- [ ] Run `rtk helm lint infra/charts/tertius`; expect zero failed charts.
- [ ] Run `rtk helm template tertius infra/charts/tertius`; verify successful rendering.
- [ ] Run `rtk kubectl kustomize infra/clusters/production`; verify successful rendering with no image toolkit resources.
- [ ] Parse all changed workflow YAML with the repository Python environment and run `git diff --check`.
- [ ] Request a code review, address findings, and mark every completed plan checkbox.
