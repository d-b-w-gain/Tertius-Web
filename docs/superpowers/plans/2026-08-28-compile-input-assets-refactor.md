# Compile Input Assets Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Document type:** Implementation

**Goal:** Extract the PR #357 compile-time binary asset lifecycle into a small, format-neutral abstraction while preserving the lean 3MF import, sandbox loader, UI, and AI/Pi boundaries.

**Architecture:** A new `core.compile_inputs` module owns the supported artifact-kind-to-logical-filename definitions plus project-to-job snapshotting and job-only materialization. `CompileRepository` provides tenant-scoped format-neutral list/delete operations, while normal compile and stale republish retain responsibility for transactions, job state, publication, and user-facing errors.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Pydantic compile messages, pytest, uv, Ruff, mypy.

---

## Scope Decisions

| Decision | Implementation consequence |
|---|---|
| Keep one production definition: `source_3mf -> source.3mf` | No speculative STEP, OBJ, STL, plugin, worker, BREP, or manifest support is added. |
| Inject a bytes-to-object-reference callback into materialization | Both compile paths share asset construction without moving settings, NATS, HTTP, or transactions into `core`. |
| Treat durable input kinds as the expected-kind inventory during materialization | A deleted job snapshot is detectable without a schema migration. Durable bytes are never used for republish. This is valid because the current import API atomically creates inputs and exposes no add/remove input API. |
| Keep the current `CompileBinaryAsset` wire allowlist and one-asset limit | The helper/repository APIs iterate over definitions, but product support remains exactly one 3MF input until a future feature intentionally expands the wire contract. |
| Rename source-specific repository APIs and cleanup | Compile lifecycle code no longer repeats `source_3mf`; cleanup still targets only configured input kinds and never deletes output artifacts. |

## File Map

| File | Responsibility |
|---|---|
| `server/core/compile_inputs.py` | Compile input definitions, missing-input error, snapshotting, and materialization. |
| `server/core/repositories.py` | Tenant-scoped project/job input queries and ephemeral job-input cleanup. |
| `server/workflows/intus/intus_server.py` | Call shared snapshot/materialization during initial compile; preserve transaction/publication behavior. |
| `server/workflows/intus/compile_result_consumer.py` | Materialize only pinned job inputs on republish and map missing input to `missing_snapshot`. |
| `server/tests/test_compile_inputs.py` | Focused iterable-definition, snapshot, immutability, missing-input, and no-input tests. |
| `server/tests/test_compile_flow.py` | Route-level Python-only `assets == []` regression test. |
| `server/tests/test_lean_3mf_import_api.py` | Imported compile snapshot and uploaded-byte coverage. |
| `server/tests/test_compile_result_consumer.py` | A-vs-B republish and deleted-snapshot no-fallback coverage. |
| `server/tests/test_repositories.py` | Format-neutral cleanup and tenant-isolation coverage. |

## Anti-Patterns (Do Not)

| Do not | Do instead | Why |
|---|---|---|
| Read durable project bytes during stale republish | Upload only the job-level artifact bytes | Republish must reproduce the original job snapshot. |
| Treat every job artifact as a compile input | Filter by centralized supported input kinds | Output artifacts share the same table and must survive input cleanup. |
| Duplicate `source_3mf` branches in workflow modules | Add definitions and behavior in `core.compile_inputs` | Prevents format-specific orchestration growth. |
| Add a worker, manifest, plugin system, BREP, or new formats | Keep one lean synchronous snapshot/materialization abstraction | Those changes are outside the approved handoff. |
| Move commits, NATS, settings, or HTTP errors into the core helper | Inject only the object-store callback | Preserves current transaction and retry semantics. |
| Fall back to current project bytes when a job snapshot is missing | Raise `MissingCompileInputError` and fail stale republish | Silent substitution breaks immutability. |
| Refactor AI/Pi-agent or UI code | Leave those layers unchanged | Raw binary compile inputs remain outside AI context. |

## Test Case Specifications

| ID | Scope | Setup | Expected result |
|---|---|---|---|
| CI-001 | Python-only route | Compile a project with no durable input artifact | Published command has `assets == []`. |
| CI-002 | Initial 3MF compile | Durable 3MF A exists | Job snapshot contains A and uploader receives A as `source.3mf`. |
| CI-003 | Immutable republish | Job snapshot A, durable project artifact B | Uploader receives A, never B. |
| CI-004 | Deleted snapshot | Text snapshot and durable 3MF exist; job input row does not | Job becomes failed with `missing_snapshot`; no upload/publish; durable row remains. |
| CI-005 | Corrupt durable input | Durable input row has `content=None` | Initial snapshot raises before upload. |
| CI-006 | Multiple definitions | Pass two test-only input definitions and two durable rows | Both rows are snapshotted by the same loop. |
| CI-007 | Tenant isolation | Two tenants own project/job input rows | Each repository sees only its tenant's inputs. |
| CI-008 | Terminal cleanup | Target input, durable input, other-job input, and output artifact exist | Only the target job input is deleted. |

## Error Handling Matrix

| Failure | Detection | Behavior | Persistence |
|---|---|---|---|
| Initial durable input content missing | Snapshot helper finds `content is None` | Raise deterministic `MissingCompileInputError` through existing compile failure handling | Pre-commit transaction rolls back code update, job, and snapshots. |
| Job input row missing/corrupt on republish | Expected durable kind has no usable job row | Mark job failed with `missing_snapshot`; do not upload or publish | Terminal cleanup removes only remaining ephemeral input rows; durable input remains. |
| Initial object-store write fails | Injected uploader raises | Preserve current HTTP 503 behavior | Pre-commit transaction rolls back. |
| Republish object-store or NATS write fails | Injected uploader/publisher raises | Preserve current consumer retry behavior | Job stays queued with its snapshots. |
| Compile command exceeds configured size | Existing message-size assertion raises | Preserve `source_bundle_too_large` terminal failure | Ephemeral job inputs are deleted; durable input remains. |

### Task 1: Add compile-input behavior tests

**Files:**
- Create: `server/tests/test_compile_inputs.py`
- Modify: `server/tests/test_compile_flow.py`
- Modify: `server/tests/test_compile_result_consumer.py`
- Modify: `server/tests/test_repositories.py`

- [x] **Step 1: Write failing tests for the desired public API and no-fallback behavior**

Use the wished-for API:

```python
from core.compile_inputs import (
    COMPILE_INPUT_KINDS,
    CompileInputKind,
    MissingCompileInputError,
    materialize_job_binary_assets,
    snapshot_project_compile_inputs,
)

snapshots = snapshot_project_compile_inputs(repo, project_id, job.id)
assets = await materialize_job_binary_assets(
    repo, project_id, job.id, fake_store
)
```

Cover CI-001 through CI-008. The deleted-snapshot consumer test must include a valid `CompileJobFile`, a durable `source_3mf`, no job input row, and assertions that neither uploader nor publisher ran.

- [x] **Step 2: Run the new focused tests and verify RED**

Run:

```bash
uv run pytest server/tests/test_compile_inputs.py server/tests/test_compile_flow.py server/tests/test_compile_result_consumer.py server/tests/test_repositories.py -q
```

Expected: failure because `core.compile_inputs` and the format-neutral repository APIs do not exist, plus the current stale republish incorrectly publishes without a binary snapshot.

### Task 2: Add the narrow compile-input module and repository APIs

**Files:**
- Create: `server/core/compile_inputs.py`
- Modify: `server/core/repositories.py`

- [x] **Step 1: Implement the centralized definitions and helpers**

Use this interface:

```python
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import UUID

from core.compile_messages import CompileBinaryAsset
from core.models import Artifact
from core.object_store import ObjectRef


@dataclass(frozen=True)
class CompileInputKind:
    artifact_kind: str
    logical_filename: str


COMPILE_INPUT_KINDS = (CompileInputKind("source_3mf", "source.3mf"),)
COMPILE_INPUT_ARTIFACT_KINDS = tuple(
    definition.artifact_kind for definition in COMPILE_INPUT_KINDS
)


class MissingCompileInputError(RuntimeError):
    pass


class CompileInputRepository(Protocol):
    def project_input_artifacts(
        self, project_id: UUID, artifact_kinds: tuple[str, ...]
    ) -> list[Artifact]:
        raise NotImplementedError

    def project_input_kinds(
        self, project_id: UUID, artifact_kinds: tuple[str, ...]
    ) -> set[str]:
        raise NotImplementedError

    def job_input_artifacts(
        self, job_id: UUID, artifact_kinds: tuple[str, ...]
    ) -> list[Artifact]:
        raise NotImplementedError

    def record_artifact(
        self,
        project_id: UUID,
        job_id: UUID | None,
        kind: str,
        content: bytes,
        *,
        content_type: str | None = None,
    ) -> Artifact:
        raise NotImplementedError


def _artifact_kinds(
    definitions: tuple[CompileInputKind, ...],
) -> tuple[str, ...]:
    return tuple(definition.artifact_kind for definition in definitions)


def snapshot_project_compile_inputs(
    repo: CompileInputRepository,
    project_id: UUID,
    job_id: UUID,
    *,
    definitions: tuple[CompileInputKind, ...] = COMPILE_INPUT_KINDS,
) -> list[Artifact]:
    kinds = _artifact_kinds(definitions)
    project_inputs = {
        artifact.kind: artifact
        for artifact in repo.project_input_artifacts(project_id, kinds)
    }
    snapshots = []
    for definition in definitions:
        source = project_inputs.get(definition.artifact_kind)
        if source is None:
            continue
        if source.content is None:
            raise MissingCompileInputError(
                f"Project compile input {definition.logical_filename} content is missing"
            )
        snapshots.append(
            repo.record_artifact(
                project_id,
                job_id,
                definition.artifact_kind,
                source.content,
                content_type=source.content_type,
            )
        )
    return snapshots


async def materialize_job_binary_assets(
    repo: CompileInputRepository,
    project_id: UUID,
    job_id: UUID,
    store: Callable[[bytes], Awaitable[ObjectRef]],
    *,
    definitions: tuple[CompileInputKind, ...] = COMPILE_INPUT_KINDS,
) -> list[CompileBinaryAsset]:
    kinds = _artifact_kinds(definitions)
    expected_kinds = repo.project_input_kinds(project_id, kinds)
    job_inputs = {
        artifact.kind: artifact
        for artifact in repo.job_input_artifacts(job_id, kinds)
    }
    assets = []
    for definition in definitions:
        if definition.artifact_kind not in expected_kinds:
            continue
        snapshot = job_inputs.get(definition.artifact_kind)
        if snapshot is None or snapshot.content is None:
            raise MissingCompileInputError(
                f"Compile input snapshot {definition.logical_filename} is missing"
            )
        assets.append(
            CompileBinaryAsset(
                logical_filename=cast(Literal["source.3mf"], definition.logical_filename),
                object_ref=await store(snapshot.content),
            )
        )
    return assets
```

The snapshot helper reads durable project inputs, rejects missing content, and records job-level copies. Materialization compares expected project kinds with job kinds, rejects absent/empty snapshots, and passes only job bytes to `store`.

- [x] **Step 2: Replace the repository methods**

Provide tenant-scoped methods:

```python
def project_input_artifacts(
    self, project_id: UUID, artifact_kinds: Collection[str] = COMPILE_INPUT_ARTIFACT_KINDS
) -> list[Artifact]:
    normalized = tuple(kind.lower() for kind in artifact_kinds)
    if not normalized:
        return []
    return list(
        self.db.scalars(
            select(Artifact)
            .where(
                Artifact.tenant_id == self.tenant_id,
                Artifact.project_id == project_id,
                Artifact.compile_job_id.is_(None),
                Artifact.kind.in_(normalized),
            )
            .order_by(Artifact.created_at, Artifact.id)
        ).all()
    )

def project_input_kinds(
    self, project_id: UUID, artifact_kinds: Collection[str] = COMPILE_INPUT_ARTIFACT_KINDS
) -> set[str]:
    normalized = tuple(kind.lower() for kind in artifact_kinds)
    if not normalized:
        return set()
    return set(
        self.db.scalars(
            select(Artifact.kind)
            .where(
                Artifact.tenant_id == self.tenant_id,
                Artifact.project_id == project_id,
                Artifact.compile_job_id.is_(None),
                Artifact.kind.in_(normalized),
            )
            .distinct()
        ).all()
    )

def job_input_artifacts(
    self, job_id: UUID, artifact_kinds: Collection[str] = COMPILE_INPUT_ARTIFACT_KINDS
) -> list[Artifact]:
    normalized = tuple(kind.lower() for kind in artifact_kinds)
    if not normalized:
        return []
    return list(
        self.db.scalars(
            select(Artifact)
            .where(
                Artifact.tenant_id == self.tenant_id,
                Artifact.compile_job_id == job_id,
                Artifact.kind.in_(normalized),
            )
            .order_by(Artifact.created_at, Artifact.id)
        ).all()
    )

def delete_job_input_artifacts(
    self, job_id: UUID, artifact_kinds: Collection[str] = COMPILE_INPUT_ARTIFACT_KINDS
) -> None:
    normalized = tuple(kind.lower() for kind in artifact_kinds)
    if not normalized:
        return
    self.db.execute(
        delete(Artifact).where(
            Artifact.tenant_id == self.tenant_id,
            Artifact.compile_job_id == job_id,
            Artifact.kind.in_(normalized),
        )
    )
    self.db.flush()
```

Update all terminal cleanup call sites to use `delete_job_input_artifacts`. Keep the kind filter; do not delete all artifacts for the job.

- [x] **Step 3: Run helper/repository tests and verify GREEN**

Run:

```bash
uv run pytest server/tests/test_compile_inputs.py server/tests/test_repositories.py -q
```

Expected: all selected tests pass.

### Task 3: Integrate initial compile and stale republish

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/workflows/intus/compile_result_consumer.py`
- Modify: `server/tests/test_lean_3mf_import_api.py`
- Modify: `server/tests/test_compile_result_consumer.py`

- [x] **Step 1: Replace initial compile's inline 3MF block**

After text file snapshotting, call:

```python
snapshot_project_compile_inputs(compile_repo, project_id, job.id)
assets = await materialize_job_binary_assets(
    compile_repo, project_id, job.id, store_compile_sidecar
)
```

Keep this before the existing transaction commit so object-store failures retain rollback behavior.

- [x] **Step 2: Replace republish's inline 3MF block**

Use the same materializer with a settings-bound callback:

```python
async def store_snapshot(content: bytes):
    return await put_compile_sidecar(content, settings)

try:
    assets = await materialize_job_binary_assets(
        compile_repo, job.project_id, job.id, store_snapshot
    )
except MissingCompileInputError as exc:
    compile_repo.finish_job(
        job,
        "failed",
        error=str(exc),
        error_code="missing_snapshot",
        user_message="Compile failed because a submitted binary input snapshot is missing. Try again.",
        retryable=False,
    )
    db.commit()
    continue
```

Do not catch uploader errors; preserve existing republish retry behavior.

- [x] **Step 3: Run route/consumer tests and verify GREEN**

Run:

```bash
uv run pytest server/tests/test_compile_flow.py server/tests/test_lean_3mf_import_api.py server/tests/test_compile_result_consumer.py -q
```

Expected: all selected tests pass, including A-vs-B and deleted-snapshot no-fallback.

### Task 4: Verify the complete focused change

- [ ] **Step 1: Run the handoff's focused backend suite**

```bash
uv run pytest server/tests/test_compile_inputs.py server/tests/test_compile_flow.py server/tests/test_lean_3mf_import_api.py server/tests/test_compile_result_consumer.py server/tests/test_repositories.py server/tests/test_object_store_connection.py server/tests/test_tertius_imports_runtime.py server/tests/test_three_mf_archive.py
```

- [ ] **Step 2: Run static and repository checks**

```bash
uv run mypy
uv run ruff check server
git diff --check
```

- [ ] **Step 3: Run runtime parity checks**

```bash
bash scripts/check-runtime-parity.sh
helm lint infra/charts/tertius
```

- [ ] **Step 4: Confirm scope boundaries in the final diff**

The diff must not modify `pi_agent_rpc.py`, AI edit models/routing, provider abstractions, the 3MF sandbox loader contract, or UI source. If PR #357's head changes, report that PR #358 still targets its predecessor and requires restacking; do not rewrite PR branches without explicit authorization.

## References

| Topic | Location |
|---|---|
| Approved handoff | [`docs/CODEX_HANDOFF_3MF_COMPILE_INPUT_REFACTOR.md`](../../CODEX_HANDOFF_3MF_COMPILE_INPUT_REFACTOR.md#target-design) |
| Artifact persistence | [`server/core/repositories.py`](../../../server/core/repositories.py) |
| Initial compile orchestration | [`server/workflows/intus/intus_server.py`](../../../server/workflows/intus/intus_server.py) |
| Stale republish | [`server/workflows/intus/compile_result_consumer.py`](../../../server/workflows/intus/compile_result_consumer.py) |
| Compile message contract | [`server/core/compile_messages.py`](../../../server/core/compile_messages.py) |
| Harness quality gates | [`docs/harness/quality-gates.md`](../../harness/quality-gates.md) |

## Clarity Gate Self-Assessment

| Criterion | Score | Evidence |
|---|---:|---|
| Actionability | 10/10 | Exact files, APIs, failure behavior, and commands are specified. |
| Specificity | 9/10 | Deleted-row detection and transaction boundaries are explicit; implementation may choose equivalent local naming only. |
| Consistency | 10/10 | This plan points to the handoff and centralizes new implementation decisions. |
| Structure | 10/10 | Scope, files, tests, errors, tasks, and references are separated. |
| Disambiguation | 10/10 | Seven anti-patterns and eight concrete tests cover boundaries and edge cases. |
| Reference clarity | 10/10 | All references use exact repository paths. |

**Weighted AI coder understandability:** 9.8/10. All 13 Stream Coding foundation and document-architecture checks pass.
