# Lean 3MF Re-review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every remaining second-review finding on stacked PRs #355–#358 and push verified, correctly based replacement heads.

**Architecture:** PostgreSQL remains the durable source store, while NATS Object Store remains the temporary digest-addressed compile-sidecar transport. The API and injected compile runtime enforce the same identity-build-only 3MF subset through shared fixtures, the API uses bounded streaming XML preflight, and UI import persistence is separated from activation state.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, nats.py JetStream Object Store, defusedxml, pytest, React, TypeScript, Vitest, Helm, shell configuration tests.

---

### Task 1: Remove unrelated #355 history while preserving the approved spec

**Files:**
- Keep: `docs/superpowers/specs/2026-08-16-lean-3mf-rereview-fixes-design.md`
- Remove from branch diff: unrelated edits in `scripts/test-k3s-deployment.sh` and `scripts/test-k3s-harness-lifecycle.sh`

- [x] **Step 1: Record the old stack heads**

Run:

```bash
rtk git rev-parse codex/3mf-lean-sidecars codex/3mf-lean-loader codex/3mf-lean-api codex/3mf-lean-ui
```

Expected: the original four heads are recorded for later `rebase --onto` operations.

- [x] **Step 2: Rebase the spec commit around the unrelated cleanup commit**

Run:

```bash
rtk git rebase --onto 1e4d7f5 67e6bc4 codex/3mf-lean-sidecars
```

Expected: the sidecar branch contains its three sidecar commits followed by the design spec, but no `67e6bc4` cleanup commit.

- [x] **Step 3: Verify branch scope**

Run:

```bash
rtk git diff --stat origin/master..codex/3mf-lean-sidecars
rtk git diff origin/master..codex/3mf-lean-sidecars -- scripts/test-k3s-deployment.sh scripts/test-k3s-harness-lifecycle.sh
```

Expected: the second command is empty and the sidecar files remain in scope.

### Task 2: Define result-driven sidecar outage behavior on #355

**Files:**
- Modify: `server/tests/test_compile_job.py`
- Modify: `server/workflows/intus/compile_job.py`
- Modify: `server/tests/test_config.py`
- Modify: `docs/configuration-and-secrets.md`

- [x] **Step 1: Write failing sidecar transport tests**

Add tests that import `ObjectStoreUnavailableError` and prove:

```python
class UnavailableObjectStore:
    async def get(self, requested_ref):
        raise ObjectStoreUnavailableError("object store operation failed")

def fail_if_called(*args, **kwargs):
    raise AssertionError("sandbox must not run")

# After handle_compile_request_message(...):
assert msg.acked is True
assert msg.naked is False
result = publisher.published[0][1]
assert result.status == "failed"
assert result.error_code == "binary_asset_unavailable"
assert result.retryable is True
```

Add a result-publication-failure variant asserting `msg.naked is True` and
`msg.acked is False`. Retain the existing integrity test and explicitly assert
`invalid_binary_asset` plus `retryable is False`.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_compile_job.py -k 'sidecar or integrity' -q
```

Expected: the unavailable-store test fails because the exception currently reaches NAK without a result.

- [x] **Step 3: Implement the transport-specific result**

Import `ObjectStoreUnavailableError`. Catch it separately from integrity and
missing-object failures:

```python
except ObjectStoreUnavailableError as exc:
    result = _failed_result(
        command,
        now_utc(),
        error=str(exc),
        error_code="binary_asset_unavailable",
        user_message="Compile input storage is temporarily unavailable. Try again.",
        retryable=True,
    )
except (ObjectIntegrityError, ObjectNotFoundError) as exc:
    result = _failed_result(
        command,
        now_utc(),
        error=str(exc),
        error_code="invalid_binary_asset",
        user_message="Compile input failed its integrity check.",
        retryable=False,
    )
```

Represent an Object Store open failure with an object whose `get()` raises the
captured `ObjectStoreUnavailableError`, then pass it to
`handle_compile_request_message()` so normal publish-and-ACK semantics apply.

- [x] **Step 4: Add the `run_once()` open-failure regression**

Mock a valid asset command, make `open_compile_sidecar_store()` raise, and let
the real handler run with a fake publisher. Assert a retryable
`binary_asset_unavailable` result is published and the command ACKs.

- [x] **Step 5: Document and test intentional `MaxDeliver=1`**

Add an operator note that sidecar transport outages produce retryable terminal
results and ACK the command, so `compileMaxDeliver: 1` is intentional rather
than a NAK retry policy. Add shell assertions matching both the value and the
documentation text.

- [x] **Step 6: Verify GREEN and commit**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_compile_job.py -q
rtk git diff --check
```

Expected: all commands exit 0.

Commit only the four listed files with message:

```text
fix: report transient compile sidecar outages
```

### Task 3: Reconcile existing Object Store configuration on #355

**Files:**
- Modify: `server/tests/test_object_store.py`
- Modify: `server/core/object_store.py`

- [x] **Step 1: Write failing reconciliation tests**

Create a fake existing store whose `status()` returns a full `stream_info.config`
containing Object Store subjects, storage, discard policy, rollup headers, and
the existing `max_age`/`max_bytes`. Add four tests:

```python
assert jetstream.update_calls == []  # matching values
assert updated.max_bytes == settings.compile_sidecar_max_bytes  # stale capacity
assert updated.max_age == timedelta(seconds=settings.compile_sidecar_ttl_seconds)  # stale TTL
assert updated.subjects == original.subjects
assert updated.allow_rollup_hdrs == original.allow_rollup_hdrs
```

Keep the create-race regression.

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_object_store.py -k 'open_store' -q
```

Expected: stale configuration tests fail because existing stores return without an update.

- [x] **Step 3: Implement full-config reconciliation**

After obtaining an existing or race-recovered store, call `await store.status()`.
Use `status.stream_info.config` as the update object. Compare its normalized
`max_age` and `max_bytes` to settings. When either differs, update only those
attributes on a copied full configuration and call:

```python
await jetstream.update_stream(config=updated_config)
```

Do not create a new `StreamConfig`, alter subjects, or recreate the bucket.
Map status/update transport failures to `ObjectStoreUnavailableError`.

- [x] **Step 4: Verify GREEN and commit**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_object_store.py server/tests/test_compile_job.py -q
rtk git diff --check
```

Expected: all tests pass, including create-race coverage.

Commit the two listed files with message:

```text
fix: reconcile compile sidecar bucket limits
```

### Task 4: Restack #356 and complete the common graph fixture matrix

**Files:**
- Modify: `server/tests/fixtures/three_mf.py`
- Modify: `server/tests/test_tertius_imports_runtime.py`

- [x] **Step 1: Restack the loader branch**

Run `rebase --onto` using the recorded old #355 head and the new
`codex/3mf-lean-sidecars` head:

```bash
rtk git switch codex/3mf-lean-loader
rtk git rebase --onto codex/3mf-lean-sidecars 67e6bc4
```

Expected: the two loader commits follow the updated sidecar branch.

- [x] **Step 2: Extend fixtures and write failing runtime cases**

Extend `make_3mf()` with explicit `include_build` and `include_mesh_objects`
controls. Export a named case matrix such as:

```python
UNSUPPORTED_BUILD_GRAPH_CASES = [
    pytest.param({"build_transform": IDENTITY_TRANSLATED}, id="transform"),
    pytest.param({"build_object_ids": [1, 1]}, id="repeated-build-object"),
    pytest.param({"component_object_ids": [1, 2], "build_object_ids": [3]}, id="components"),
    pytest.param({"build_object_ids": [1]}, id="mesh-subset"),
    pytest.param({"build_object_ids": [99]}, id="missing-object"),
    pytest.param({"include_non_mesh_object": True, "build_object_ids": [3]}, id="non-mesh-object"),
    pytest.param({"include_build": False}, id="missing-build"),
    pytest.param({"include_mesh_objects": False}, id="no-mesh-objects"),
]
```

Use plain `(id, options)` data rather than pytest-specific parameters if that
keeps the fixture module reusable by both suites.

- [x] **Step 3: Verify RED then retain the minimal runtime guard**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_tertius_imports_runtime.py -k 'build_graph or returns_two' -q
```

Expected: missing-build/no-mesh fixture coverage initially exposes any generator or message mismatch. Adjust only the injected guard and fixture generator needed for stable unsupported-graph rejection.

- [x] **Step 4: Verify GREEN and commit**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_tertius_imports_runtime.py -q
rtk git diff --check
```

Commit the changed fixture/test/runtime files with message:

```text
test: complete supported 3mf graph matrix
```

### Task 5: Restack #357 and implement bounded streaming HTTP preflight

**Files:**
- Modify: `server/core/three_mf_archive.py`
- Modify: `server/tests/test_three_mf_archive.py`
- Modify: `server/tests/test_lean_3mf_import_api.py`
- Reuse: `server/tests/fixtures/three_mf.py`

- [x] **Step 1: Restack the API branch**

Run:

```bash
rtk git switch codex/3mf-lean-api
rtk git rebase --onto codex/3mf-lean-loader 3c44915
```

Expected: the three API commits follow the updated loader branch.

- [x] **Step 2: Add failing supported-subset preflight tests**

Parameterize the same common matrix through `validate_3mf_archive_bytes()`.
Assert valid one- and two-mesh identity builds pass, while every unsupported
case raises:

```python
with pytest.raises(Unsupported3mfBuildGraphError, match="unsupported 3MF build graph"):
    validate_3mf_archive_bytes(content)
```

- [x] **Step 3: Add failing streaming resource-limit tests**

Cover a reader that returns more than the configured XML limit, a document over
the depth limit, large irrelevant metadata/vertex content, malformed XML, and
DTD/entity payloads. Instrument element clearing or the retained semantic state
so the large irrelevant document test proves the parser does not construct a
full retained DOM.

- [x] **Step 4: Run validator tests and verify RED**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_three_mf_archive.py -q
```

Expected: unsupported graphs are accepted and resource-bound tests fail against `fromstring()`.

- [x] **Step 5: Implement the capped streaming parser**

Add:

```python
class Unsupported3mfBuildGraphError(Invalid3mfArchiveError):
    pass

def _unsupported() -> Unsupported3mfBuildGraphError:
    return Unsupported3mfBuildGraphError(
        "The file uses an unsupported 3MF build graph."
    )
```

Use a `_CappedReader` around `archive.open(info)` and
`DefusedElementTree.iterparse(..., events=("start", "end"))`. Track current
depth, reject beyond `MAX_3MF_XML_DEPTH`, and clear elements after processing.
For relationships, retain only the unique internal 3D model target. For the
model, retain only object IDs, whether each object has a direct mesh or
components, and each direct build item object ID/transform. Apply the exact
set-equality and uniqueness rules from the injected loader.

- [x] **Step 6: Add endpoint rollback regression**

POST one unsupported fixture through the authenticated endpoint and assert
HTTP 400. Query both `Project` and `Artifact` by tenant/name and assert no rows
were created. Ensure the endpoint preserves the stable unsupported-graph error
body rather than replacing it with a generic 500.

- [x] **Step 7: Verify GREEN and commit**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_three_mf_archive.py server/tests/test_lean_3mf_import_api.py server/tests/test_tertius_imports_runtime.py -q
rtk git diff --check
```

Commit the validator, endpoint test, and common fixtures with message:

```text
fix: reject unsupported 3mf imports during preflight
```

Local validator/runtime/unit-endpoint coverage is green. The DB-backed rollback
case remains scheduled for the final suite because Docker is unavailable in the
current host environment; CI provides the PostgreSQL testcontainer.

### Task 6: Encode the 32 GiB PostgreSQL source-capacity contract on #357

**Files:**
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `server/tests/test_project_assets.py`
- Modify: `docs/configuration-and-secrets.md`

- [x] **Step 1: Add a failing deployment contract assertion**

Parse both values files and assert the application PostgreSQL storage size is
`32Gi`, while leaving the Keycloak database size unchanged. Also assert the
operator documentation connects the 128 MiB per-project durable source limit
to PostgreSQL sizing overhead.

- [x] **Step 2: Verify RED**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_project_assets.py::test_postgres_defaults_cover_durable_3mf_source_contract -q
```

Expected: failure reports the current 2 GiB application database default.

- [x] **Step 3: Update values and documentation**

Set only:

```yaml
postgres:
  storage:
    size: 32Gi
```

in default and local values. Document durable originals, project-count
planning, normal database data, indexes, WAL, temporary space, and backups.

- [x] **Step 4: Verify GREEN and commit**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_project_assets.py::test_postgres_defaults_cover_durable_3mf_source_contract -q
rtk bash scripts/check-runtime-parity.sh
rtk helm lint infra/charts/tertius
rtk git diff --check
```

Commit the four listed files with message:

```text
docs: align postgres capacity with 3mf sources
```

The focused capacity contract, runtime parity, and Helm lint are green. The
broader deployment script remains scheduled for CI because its unrelated
pseudo-TTY fixture is not portable to this macOS host.

### Task 7: Restack #358 and make activation failure recoverable

**Files:**
- Modify: `ui/src/workflows/shared/ui/ProjectSelector.test.tsx`
- Modify: `ui/src/workflows/shared/ui/ProjectSelector.tsx`
- Remove from branch diff: `ui/src/workflows/site/SiteWorkbench.test.tsx`

- [x] **Step 1: Restack #358 without the unrelated PDF commit**

The published UI branch is checked out in a pre-existing external worktree, so
leave that checkout untouched. Create a temporary local implementation branch
at the last relevant UI commit and restack it onto the updated API branch:

```bash
rtk git switch -c codex/3mf-lean-ui-rereview ffcbbe5
rtk git rebase --onto codex/3mf-lean-api 6c10864 codex/3mf-lean-ui-rereview
```

Expected: only `e372b46` and `ffcbbe5` are replayed. The unrelated `484176f`
commit and `SiteWorkbench.test.tsx` diff are absent. The temporary branch will
be pushed explicitly to the existing remote PR branch in Task 8.

- [x] **Step 2: Replace the old failure test with a failing recovery test**

Set `listProjects` to return `['default']` initially and
`['default', 'falcon9']` after import. Make `activateProject` reject once and
then resolve. Assert after the failed activation:

```typescript
expect(storage.import3mf).toHaveBeenCalledTimes(1)
expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
expect(screen.getByRole('option', { name: 'falcon9' })).toBeInTheDocument()
expect(selector).toHaveValue('default')
expect(listener).not.toHaveBeenCalled()
```

Select `falcon9`, then assert activation succeeds, import remains called once,
the selector changes, and the event fires exactly once.

- [x] **Step 3: Run the test and verify RED**

Run:

```bash
rtk npm --prefix ui test -- ProjectSelector.test.tsx
```

Expected: the dialog remains open and the imported option is unavailable.

- [x] **Step 4: Implement persistence-first UI state**

After `import3mf()` resolves, call and await a project-list refresh that does
not auto-activate. Close and reset the import dialog before attempting
`selectProject(result.project)`. Keep `selectProject()` as the only path that
updates `activeProject` and broadcasts the event. Do not retain a code path that
can call `import3mf()` again for the same completed dialog submission.

- [x] **Step 5: Verify GREEN and commit**

Run:

```bash
rtk npm --prefix ui test -- ProjectSelector.test.tsx
rtk npm --prefix ui run typecheck
rtk git diff --check
```

Confirm `git diff codex/3mf-lean-api..HEAD -- ui/src/workflows/site/SiteWorkbench.test.tsx`
is empty. Commit the selector files with message:

```text
fix: recover imported project after activation failure
```

### Task 8: Verify, push, and update the four existing PRs

**Files:**
- Update checkboxes in this plan as each task lands.
- Do not create a new PR.

- [ ] **Step 1: Run branch-appropriate inherited suites**

On the top stack branch run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_object_store.py server/tests/test_compile_job.py server/tests/test_tertius_imports_runtime.py server/tests/test_three_mf_archive.py server/tests/test_lean_3mf_import_api.py -q
rtk env UV_CACHE_DIR=.uv-cache uv run mypy
rtk npm --prefix ui test -- ProjectSelector.test.tsx
rtk npm --prefix ui run typecheck
rtk bash scripts/test-deployment-config.sh
rtk bash scripts/check-runtime-parity.sh
```

Expected: every command exits 0.

- [ ] **Step 2: Run the broader quality gates**

Run:

```bash
rtk env UV_CACHE_DIR=.uv-cache uv run pytest
rtk npm --prefix ui test
rtk npm --prefix ui run build
```

Expected: all suites and build complete with zero failures.

- [ ] **Step 3: Attempt canonical live-flow validation**

Use the isolated local-values k3s smoke release and run:

```bash
rtk scripts/harness-k3s.sh live-flow
```

Expected: authenticated identity one-part and multi-part imports activate and
compile GLB successfully. If runtime, auth, provider credentials, or port
forwarding are unavailable, capture the exact failing command and error rather
than substituting compile-only validation.

- [ ] **Step 4: Confirm stack topology and scope**

Run:

```bash
rtk git merge-base --is-ancestor origin/master codex/3mf-lean-sidecars
rtk git merge-base --is-ancestor codex/3mf-lean-sidecars codex/3mf-lean-loader
rtk git merge-base --is-ancestor codex/3mf-lean-loader codex/3mf-lean-api
rtk git merge-base --is-ancestor codex/3mf-lean-api codex/3mf-lean-ui-rereview
rtk git diff --check origin/master..codex/3mf-lean-ui-rereview
```

Expected: every command exits 0 and unrelated files are absent from PR-specific diffs.

- [ ] **Step 5: Push the four replacement heads safely**

Because restacking rewrites published branch history, use explicit leases tied
to the recorded old heads:

```bash
rtk git push --force-with-lease=codex/3mf-lean-sidecars:67e6bc454b3667a349f65695ca4ed32f34fd0d9d origin codex/3mf-lean-sidecars
rtk git push --force-with-lease=codex/3mf-lean-loader:3c44915d994e1dcc1b5ef1deb3dde21a9bfa5607 origin codex/3mf-lean-loader
rtk git push --force-with-lease=codex/3mf-lean-api:6c108640e104f75cffdc839126e174f874471658 origin codex/3mf-lean-api
rtk git push --force-with-lease=codex/3mf-lean-ui:484176f5d07dbb778f6d4c1f76ff7df2d78c8edb origin codex/3mf-lean-ui-rereview:codex/3mf-lean-ui
```

Expected: each existing PR updates exactly once; no new PR is created.

- [ ] **Step 6: Confirm GitHub bases, heads, mergeability, and checks**

Inspect PRs #355–#358 and report for each: commits added, final head SHA,
focused tests, broader/CI state, fixed findings, and intentional deferrals.
Confirm the bases remain `master`, `codex/3mf-lean-sidecars`,
`codex/3mf-lean-loader`, and `codex/3mf-lean-api` respectively. Do not merge or
squash.
