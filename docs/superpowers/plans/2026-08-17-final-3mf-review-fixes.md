# Final 3MF Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align local NATS capacity with the 8 GiB compile sidecar store and prevent users from closing the 3MF import dialog while its request is still active.

**Architecture:** Keep the existing lean 3MF pipeline unchanged. Enforce the storage relationship with a focused deployment-values test in the #355 layer, then implement the UI interaction as a one-attribute state binding in #358 and restack the two unchanged middle PRs between them.

**Tech Stack:** Python, pytest, PyYAML, Helm, React, TypeScript, Vitest, GitHub Actions

---

## File Map

- `server/tests/test_config.py`: owns the explicit default/local Helm storage-capacity contract.
- `infra/charts/tertius/values-local.yaml`: owns local NATS JetStream PVC sizing.
- `ui/src/workflows/shared/ui/ProjectSelector.test.tsx`: owns pending-import interaction regression coverage.
- `ui/src/workflows/shared/ui/ProjectSelector.tsx`: binds Cancel availability to `importPending`.
- `docs/superpowers/specs/2026-08-17-final-3mf-review-fixes-design.md`: approved scope and acceptance criteria.
- `docs/superpowers/plans/2026-08-17-final-3mf-review-fixes.md`: execution checklist and verification record.

### Task 1: Lock the NATS storage contract in #355

**Files:**
- Modify: `server/tests/test_config.py`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `docs/superpowers/plans/2026-08-17-final-3mf-review-fixes.md`

- [x] **Step 1: Add the failing deployment-values test**

Append this focused test near the existing compile configuration tests:

```python
@pytest.mark.parametrize(
    "values_name",
    ["values.yaml", "values-local.yaml"],
)
def test_nats_storage_has_headroom_for_compile_sidecars(values_name: str):
    root = Path(__file__).parents[2]
    chart_dir = root / "infra/charts/tertius"
    defaults = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((chart_dir / values_name).read_text(encoding="utf-8"))

    sidecar_max_bytes = defaults["app"]["config"]["compileSidecarMaxBytes"]
    nats_pvc_size = values["nats"]["config"]["jetstream"]["fileStore"]["pvc"]["size"]

    assert sidecar_max_bytes == 8 * 1024**3
    assert nats_pvc_size == "10Gi"
    assert int(nats_pvc_size.removesuffix("Gi")) * 1024**3 >= sidecar_max_bytes
```

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
rtk .venv/bin/pytest server/tests/test_config.py::test_nats_storage_has_headroom_for_compile_sidecars -q
```

Expected: one parameter case passes for `values.yaml`; the local case fails because it reads `1Gi` instead of `10Gi`.

- [x] **Step 3: Raise the local NATS PVC to 10 GiB**

Change only the NATS JetStream PVC entry in `infra/charts/tertius/values-local.yaml`:

```yaml
nats:
  config:
    jetstream:
      fileStore:
        pvc:
          size: 10Gi
          storageClassName: local-path
```

Do not alter the unrelated 1 GiB observability or Valkey volumes.

- [x] **Step 4: Verify GREEN and deployment rendering**

Run:

```bash
rtk .venv/bin/pytest server/tests/test_config.py::test_nats_storage_has_headroom_for_compile_sidecars -q
rtk helm lint infra/charts/tertius
rtk env UV_CACHE_DIR=.uv-cache UV_NO_BUILD_ISOLATION=1 PATH=/opt/homebrew/bin:/Users/johnsonyuen/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin bash scripts/test-deployment-config.sh
rtk git diff --check
```

Expected: the focused test reports two passes and Helm lint succeeds. On this macOS host the broader deployment script may stop at its known `I-019` pseudo-TTY portability fixture; record that exact blocker after confirming all preceding storage/render checks pass.

- [x] **Step 5: Commit the #355 fix**

```bash
rtk git add server/tests/test_config.py infra/charts/tertius/values-local.yaml docs/superpowers/plans/2026-08-17-final-3mf-review-fixes.md
rtk git commit -m "fix: align local nats storage with sidecars"
```

### Task 2: Restack #356 through #358

**Files:**
- No source edits.
- Rewrite only the descendant branch ancestry.

- [ ] **Step 1: Record the updated #355 head**

```bash
rtk git rev-parse codex/3mf-lean-sidecars
```

Expected: a new SHA containing the spec, plan, and storage fix.

- [ ] **Step 2: Replay #356 onto the updated #355 head**

```bash
rtk git rebase --onto codex/3mf-lean-sidecars fcdf42a77c89ea3af18666acc858fb0ec219895d codex/3mf-lean-loader
```

Expected: the three #356 commits replay without conflicts.

- [ ] **Step 3: Replay #357 onto the updated #356 head**

Before rebasing, retain `84864e2f0b4fca533335cf882bc29e0e0e4cc694` as the old API tip and `2c17997f44880aaace9cc237801fdaa2f7a4a25a` as its old loader boundary. Run:

```bash
rtk git rebase --onto codex/3mf-lean-loader 2c17997f44880aaace9cc237801fdaa2f7a4a25a codex/3mf-lean-api
```

Expected: the five #357 commits replay without conflicts.

- [ ] **Step 4: Replay the temporary #358 branch onto the updated #357 head**

```bash
rtk git rebase --onto codex/3mf-lean-api 84864e2f0b4fca533335cf882bc29e0e0e4cc694 codex/3mf-lean-ui-rereview
```

Expected: the three existing #358 commits replay without the unrelated PDF commit.

### Task 3: Disable Cancel during an active import in #358

**Files:**
- Modify: `ui/src/workflows/shared/ui/ProjectSelector.test.tsx`
- Modify: `ui/src/workflows/shared/ui/ProjectSelector.tsx`
- Modify: `docs/superpowers/plans/2026-08-17-final-3mf-review-fixes.md`

- [ ] **Step 1: Add a controllable pending-import regression**

Add this test after the normal successful import case:

```typescript
it('disables Cancel while import is pending and completes normally', async () => {
  let resolveImport!: (result: { success: boolean; project: string }) => void
  storage.import3mf.mockImplementation(() => new Promise((resolve) => {
    resolveImport = resolve
  }))
  storage.listProjects
    .mockResolvedValueOnce(['default'])
    .mockResolvedValueOnce(['default', 'falcon9'])
  render(<ProjectSelector />)

  fireEvent.click(await screen.findByRole('button', { name: 'Import 3MF' }))
  const file = new File(['3mf'], 'falcon9.3mf')
  fireEvent.change(screen.getByLabelText('3MF file'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: 'Import project' }))

  await waitFor(() => expect(storage.import3mf).toHaveBeenCalledWith(file, 'falcon9'))
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()

  resolveImport({ success: true, project: 'falcon9' })

  await waitFor(() => expect(storage.activateProject).toHaveBeenCalledWith('falcon9'))
  expect(storage.activateProject).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run the test and verify RED**

```bash
rtk npm --prefix ui test -- ProjectSelector.test.tsx
```

Expected: the new test fails because the visible Cancel button is enabled while `import3mf` remains pending.

- [ ] **Step 3: Bind Cancel availability to the existing state**

Replace the Cancel button with:

```tsx
<button
  type="button"
  disabled={importPending}
  onClick={() => setIsImporting(false)}
  className="px-2 py-1 text-xs text-slate-300 disabled:opacity-50"
>
  Cancel
</button>
```

- [ ] **Step 4: Verify GREEN and inherited recovery behavior**

```bash
rtk npm --prefix ui test -- ProjectSelector.test.tsx projectStorage.test.ts
rtk npm --prefix ui run typecheck
rtk npm --prefix ui run build
rtk git diff --check
```

Expected: all focused tests pass, TypeScript reports no errors, and the production build completes. The existing activation-failure recovery test remains green.

- [ ] **Step 5: Commit the #358 fix**

```bash
rtk git add ui/src/workflows/shared/ui/ProjectSelector.test.tsx ui/src/workflows/shared/ui/ProjectSelector.tsx docs/superpowers/plans/2026-08-17-final-3mf-review-fixes.md
rtk git commit -m "fix: disable cancel during 3mf import"
```

### Task 4: Verify the complete stack

**Files:**
- No intended source edits.

- [ ] **Step 1: Run inherited backend and deployment checks**

```bash
rtk .venv/bin/pytest server/tests/test_config.py server/tests/test_object_store.py server/tests/test_compile_job.py server/tests/test_tertius_imports_runtime.py server/tests/test_three_mf_archive.py server/tests/test_project_assets.py -q
rtk .venv/bin/mypy
rtk .venv/bin/ruff check server/core/object_store.py server/core/three_mf_archive.py server/tests/test_config.py server/tests/test_object_store.py server/tests/test_compile_job.py server/tests/test_tertius_imports_runtime.py server/tests/test_three_mf_archive.py server/tests/test_project_assets.py
rtk helm lint infra/charts/tertius
rtk env PATH=/opt/homebrew/bin:/Users/johnsonyuen/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin bash scripts/check-runtime-parity.sh
```

Expected: focused tests, mypy, scoped Ruff, and Helm lint pass. Runtime parity either passes or explicitly skips Docker-only checks when Docker is unavailable.

- [ ] **Step 2: Run frontend checks**

```bash
rtk npm --prefix ui test -- ProjectSelector.test.tsx projectStorage.test.ts
rtk npm --prefix ui run typecheck
rtk npm --prefix ui run build
```

Expected: focused tests, typecheck, and build all pass.

- [ ] **Step 3: Attempt full live-flow**

```bash
rtk scripts/harness-k3s.sh live-flow
```

Expected: full authenticated live-flow passes when the isolated k3s release and compile worker exist. If unavailable, report the exact missing runtime prerequisite; do not substitute compile-only validation.

- [ ] **Step 4: Verify topology, scope, and cleanliness**

```bash
rtk git merge-base --is-ancestor origin/master codex/3mf-lean-sidecars
rtk git merge-base --is-ancestor codex/3mf-lean-sidecars codex/3mf-lean-loader
rtk git merge-base --is-ancestor codex/3mf-lean-loader codex/3mf-lean-api
rtk git merge-base --is-ancestor codex/3mf-lean-api codex/3mf-lean-ui-rereview
rtk git diff --check origin/master..codex/3mf-lean-ui-rereview
rtk git status --short
```

Expected: all ancestry and whitespace checks pass and the worktree is clean.

### Task 5: Update the four existing PRs safely

**Files:**
- No source edits.
- Do not create, merge, or squash a PR.

- [ ] **Step 1: Verify remote leases and PR bases**

Confirm the remote heads still equal:

```text
codex/3mf-lean-sidecars = fcdf42a77c89ea3af18666acc858fb0ec219895d
codex/3mf-lean-loader   = 2c17997f44880aaace9cc237801fdaa2f7a4a25a
codex/3mf-lean-api      = 84864e2f0b4fca533335cf882bc29e0e0e4cc694
codex/3mf-lean-ui       = 93520d95c34f057b8e1e9bb62fe775a22136e88c
```

Also confirm PR bases remain `master`, `codex/3mf-lean-sidecars`, `codex/3mf-lean-loader`, and `codex/3mf-lean-api`.

- [ ] **Step 2: Push with exact force-with-lease guards**

```bash
rtk git push --force-with-lease=codex/3mf-lean-sidecars:fcdf42a77c89ea3af18666acc858fb0ec219895d origin codex/3mf-lean-sidecars
rtk git push --force-with-lease=codex/3mf-lean-loader:2c17997f44880aaace9cc237801fdaa2f7a4a25a origin codex/3mf-lean-loader
rtk git push --force-with-lease=codex/3mf-lean-api:84864e2f0b4fca533335cf882bc29e0e0e4cc694 origin codex/3mf-lean-api
rtk git push --force-with-lease=codex/3mf-lean-ui:93520d95c34f057b8e1e9bb62fe775a22136e88c origin codex/3mf-lean-ui-rereview:codex/3mf-lean-ui
```

Expected: all four existing PR branches update; an unexpected remote movement rejects the corresponding push.

- [ ] **Step 3: Monitor GitHub Actions and report**

Wait for PR #355 through #358 checks. Report each final head, commits added, focused verification, deployment/CI status, and storage values. State `READY TO MERGE` only when every required acceptance item and all current checks pass.
