# Codex edit instructions for PR #176

Repository: `d-b-w-gain/Tertius-Web`  
PR: `#176` / branch `feature/llm-file-edit-endpoint`  
Review target: multi-file authenticated AI edit endpoint and related UI/storage changes.

## Summary

Please make a small follow-up commit that addresses the review findings below. Keep the PR focused on the AI file edit feature; do not rewrite unrelated code.

## 1. Restore unrelated deleted plan documents

The PR deletes several existing files under `docs/superpowers/plans/` while adding the new LLM file edit plan. Those deletions appear unrelated to the feature and should not ship unless they were explicitly intended.

Restore these deleted files from `master` / the PR base:

- `docs/superpowers/plans/2026-06-13-async-compile-worker-hardening.md`
- `docs/superpowers/plans/2026-06-13-compile-preflight-hang-fix.md`
- `docs/superpowers/plans/2026-06-14-keda-compile-jobs-no-db-worker.md`
- `docs/superpowers/plans/2026-06-15-build123d-colours-viewer.md`
- `docs/superpowers/plans/2026-06-15-compile-billing-usage-tracking.md`
- `docs/superpowers/plans/2026-06-15-llm-build-script-generation-api.md`

Implementation hint:

```bash
git checkout master -- docs/superpowers/plans/2026-06-13-async-compile-worker-hardening.md \
  docs/superpowers/plans/2026-06-13-compile-preflight-hang-fix.md \
  docs/superpowers/plans/2026-06-14-keda-compile-jobs-no-db-worker.md \
  docs/superpowers/plans/2026-06-15-build123d-colours-viewer.md \
  docs/superpowers/plans/2026-06-15-compile-billing-usage-tracking.md \
  docs/superpowers/plans/2026-06-15-llm-build-script-generation-api.md
```

If the branch name differs locally, use the PR base ref instead of `master`.

## 2. Make AI file persistence atomically version-guarded

`POST /projects/{name}/files/llm-edit` checks file `updated_at` before the provider call and again before calling `ProjectRepository.stage_file_updates`, but the repository method still performs a normal read/check and then mutates ORM rows. A concurrent save that lands after the final check but before flush/commit can still be overwritten.

Fix this in `server/core/repositories.py` so `stage_file_updates(...)` does not do a separate unguarded check-then-write.

Preferred implementation options:

### Option A: row locks

- Add a helper such as `files_by_ids(..., for_update: bool = False)` or a private locked loader.
- In `stage_file_updates`, load the target `ProjectFile` rows with `SELECT ... FOR UPDATE` when `expected_updated_at` is supplied.
- Re-check every expected version while holding the row locks.
- Only then mutate contents, update `updated_at`, flush, and create the snapshot.

### Option B: conditional updates

- For every returned edit, issue an `UPDATE project_files SET content = ..., updated_at = ... WHERE tenant_id = ... AND project_id = ... AND id = ... AND updated_at = ...`.
- Verify every update affected exactly one row.
- If any row count is zero, roll back/raise `FileVersionConflictError` and do not create a snapshot.
- Reload changed rows after the guarded updates so the endpoint response still has filenames and new `updated_at` values.

Whichever option you choose, preserve existing behavior:

- Return `None` for missing project/file.
- Raise `FileVersionConflictError("Files changed while AI edit was running")` for stale versions.
- Raise `ValueError("LLM returned no file changes")` when no file content actually changes.
- Create exactly one `SourceSnapshot` for a successful multi-file edit.
- Do not create a snapshot or `LlmUsageRecord` on conflict.

Add or update tests:

- Keep the existing stale-version tests.
- Add a regression test that covers a version change at persist time, not only a stale pointer detected before calling `stage_file_updates`. A two-session test is ideal if the test harness supports it; otherwise add a focused repository-level test/hook that proves the final write is guarded and no snapshot is created on late conflict.
- Run at least:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py server/tests/test_llm_file_edit.py -q
```

## 3. Reset/refetch the file status baseline after AI edits in the UI

`CompilerTab.applyAiEdit` directly calls `setActiveFile(...)` and `setCode(...)` after a successful AI edit. Unlike `switchFile(...)`, it does not reset `mtimeRef.current`. If the AI edit switches the editor to another changed file, the polling baseline can still belong to the previous file, so future external-change polling can miss or misclassify changes.

Fix `ui/src/workflows/intus/ui/CompilerTab.tsx`:

- When applying an AI edit changes or switches the active file, reset `mtimeRef.current = 0` before setting the new active file/code.
- If switching to a different changed file, prefer routing through `switchFile(activeChanged.filename, { saveCurrent: false })` so the same baseline reset and canonical server reload path is used. Avoid creating an extra save/snapshot while switching to the AI-edited file.
- If staying on the current active file, setting `code` from the server response is fine, but still reset the polling baseline so the next status poll establishes a fresh mtime baseline instead of treating the AI edit as a stale external change.

Add/update UI tests:

- Add a test where an AI edit changes a non-active file and the UI switches to it; assert the next poll establishes a baseline for that file instead of using the old file's mtime.
- Keep existing tests that verify AI edit request construction, 20-file limit, and switching to changed files.
- Run at least:

```bash
cd ui && npx vitest run ui/src/workflows/intus/ui/CompilerTab.compile.test.tsx ui/src/workflows/shared/projectStorage.test.ts
cd ui && npx tsc -b && npx eslint .
```

## Final verification

After making the above changes, run the narrower tests first, then the full checks if time allows:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py server/tests/test_llm_client.py server/tests/test_llm_file_edit.py -q
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests -q
UV_CACHE_DIR=.uv-cache rtk uv run mypy
cd ui && npx vitest run
cd ui && npx tsc -b && npx eslint .
git diff --check
```

## Expected end state

- The feature branch no longer deletes unrelated historical plan docs.
- AI file edits cannot overwrite a concurrent user save that happens during the final persistence window.
- The UI's file-status polling baseline is reset after AI edits, especially when the AI edit switches to a different changed file.
- Existing behavior and error responses remain compatible with current tests.
