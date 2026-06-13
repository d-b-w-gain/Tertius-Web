# Async Compile Worker Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the NATS-backed async compile worker safe under duplicate delivery, worker crashes, API publish failures, and project edits during queued execution.

**Architecture:** Keep the current FastAPI + Postgres + NATS JetStream model. Add a Postgres-backed atomic job claim/lease, per-job source-file snapshots, stale queued-job republishing, and claim-token guarded terminal writes. NATS delivery remains at-least-once, while Postgres decides which worker owns a lease and prevents stale workers from overwriting newer progress.

**Tech Stack:** Python, FastAPI, SQLAlchemy 2, Alembic, Postgres, NATS JetStream, pytest.

---

## Scope

This plan fixes the worker model that currently has two live `tertius-api-compile-worker` replicas consuming the durable `compile-workers` pull consumer.

In scope:
- Atomic claim and lease for compile jobs.
- Idempotent duplicate/redelivered command handling.
- Recovery for jobs committed to Postgres but not published to NATS.
- Per-job file snapshot so queued jobs compile the source that was submitted.
- Tests for the failure modes found during evaluation.
- Reconciliation of existing JetStream consumer settings.

Out of scope:
- Public NATS routing.
- NATS authentication.
- Replacing NATS with a different queue.
- Changing the UI compile polling contract.
- Auto-retrying user-code compile failures. The DB `retryable` flag remains a UI hint unless a later feature adds explicit retry API behavior.

## Implementation Files

- Modify: `server/core/models.py`
  - Add claim/lease columns to `CompileJob`.
  - Add `CompileJobFile` for immutable source snapshots.
- Create: `server/migrations/versions/0004_compile_job_claims_and_snapshots.py`
  - Add claim/lease columns and snapshot table.
- Modify: `server/core/repositories.py`
  - Add atomic claim, stale queued lookup, non-committing code staging, snapshot creation, snapshot read, and claim-token guarded finish helpers.
- Modify: `server/core/compile_messages.py`
  - Keep `CompileCommand` compatible; no new required message fields.
- Modify: `server/workflows/intus/intus_server.py`
  - Stage the submitted source, create the queued job, and snapshot files in one transaction.
  - Publish after commit. If publish fails after commit, leave the job queued so stale queued recovery can republish it.
- Modify: `server/workflows/intus/compile_executor.py`
  - Execute from job snapshot and finish only if the claim token is still current.
- Modify: `server/workflows/intus/compile_worker.py`
  - Claim before executing.
  - Ack duplicate terminal/in-flight messages.
  - Periodically republish stale queued jobs.
- Modify: `server/core/nats_client.py`
  - Reconcile existing durable consumer settings when they differ from configuration.
- Modify tests:
  - `server/tests/test_repositories.py`
  - `server/tests/test_compile_flow.py`
  - `server/tests/test_compile_worker.py`
  - `server/tests/test_nats_client.py`
  - `server/tests/test_migrations.py`

## Anti-Patterns

| Do not | Do instead | Why |
|---|---|---|
| Do not execute a job only because command identity matches. | Execute only after an atomic claim succeeds. | JetStream can redeliver and multiple workers are live. |
| Do not hold a DB transaction open while running the compile sandbox. | Use a lease token and re-check before persisting output. | Long external work must not hold row locks. |
| Do not compile mutable project rows for a queued job. | Compile `compile_job_files` snapshot rows. | Earlier queued jobs must not drift after later edits. |
| Do not treat `retryable=True` as broker retry. | Keep it as UI retry metadata and document broker retry boundaries in tests. | Current UI polls DB; result events are not the source of truth. |
| Do not fail or requeue fresh queued jobs immediately. | Only republish queued jobs older than a short age threshold. | Fresh API publishes may still be in flight. |
| Do not delete and recreate the JetStream stream in production. | Reconcile durable consumer config in place where NATS supports update. | Stream deletion can drop operational history. |
| Do not mark a job failed merely because a worker lost its claim. | Roll back stale output and ack without publishing a result event. | Another worker may already own or have completed the job. |
| Do not commit the source save before creating the queued job snapshot. | Use a non-committing repository helper for compile enqueue. | The saved code, job row, and snapshot must represent one submitted compile request. |

## Error Handling Matrix

| Scenario | Expected state | NATS action | User-visible result |
|---|---|---|---|
| Duplicate command for already `succeeded` job | Leave job unchanged | Ack | Polling still shows success/artifact |
| Duplicate command for already `failed` job | Leave job unchanged | Ack | Polling still shows failure |
| Duplicate command while job is `running` with unexpired lease | Leave job unchanged | Ack | First worker owns the lease |
| Redelivery after worker crash and expired lease | New worker claims same job | Ack after execution | Job eventually finishes |
| API dies after DB commit before publish | Job remains `queued`; stale queued scanner republishes command | Worker later acks | Job eventually runs |
| API publish call returns an error after DB commit | Job remains `queued`; stale queued scanner republishes command | Worker later acks | Request may return 503, but polling eventually shows the recovered job result |
| Compile sandbox returns failure | Mark failed, `retryable=True` | Ack | UI can offer manual retry |
| Worker cannot publish result event after DB finish | Nack/redelivery | Duplicate delivery sees terminal job and acks | DB polling remains correct |
| Stale worker finishes sandbox after losing claim | Roll back stale output and leave job unchanged | Ack without result event | Polling follows the current owner or terminal job state |
| Existing consumer has stale config | Update consumer config | No message loss | Runtime matches chart/config |

## Task 1: Add DB Schema for Claim Leases and Source Snapshots

**Files:**
- Modify: `server/core/models.py`
- Create: `server/migrations/versions/0004_compile_job_claims_and_snapshots.py`
- Modify: `server/tests/test_migrations.py`

- [ ] **Step 1: Write migration test expectations**

Add assertions to `server/tests/test_migrations.py` inside the existing schema test:

```python
    compile_job_columns = {
        column["name"]: column for column in inspector.get_columns("compile_jobs")
    }
    assert "claim_token" in compile_job_columns
    assert "claimed_at" in compile_job_columns
    assert "lease_expires_at" in compile_job_columns
    assert "attempt_count" in compile_job_columns

    assert "compile_job_files" in table_names
    snapshot_columns = {
        column["name"]: column for column in inspector.get_columns("compile_job_files")
    }
    assert {"id", "compile_job_id", "tenant_id", "project_id", "filename", "content", "created_at"} <= set(snapshot_columns)
```

- [ ] **Step 2: Run migration test and verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_migrations.py -q
```

Expected: FAIL because the claim columns and `compile_job_files` table do not exist.

- [ ] **Step 3: Update SQLAlchemy models**

In `server/core/models.py`, add these fields to `CompileJob` after `retryable`:

```python
    claim_token: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
```

Add this model after `CompileJob`:

```python
class CompileJobFile(Base):
    __tablename__ = "compile_job_files"
    __table_args__ = (
        UniqueConstraint("compile_job_id", "filename", name="uq_compile_job_file_name"),
        ForeignKeyConstraint(
            ["compile_job_id", "project_id", "tenant_id"],
            ["compile_jobs.id", "compile_jobs.project_id", "compile_jobs.tenant_id"],
            name="fk_compile_job_files_compile_job_project_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    compile_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
```

- [ ] **Step 4: Create Alembic migration**

Create `server/migrations/versions/0004_compile_job_claims_and_snapshots.py`:

```python
"""compile job claims and snapshots

Revision ID: 0004_compile_job_claims_and_snapshots
Revises: 0003_compile_job_error_fields
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_compile_job_claims_and_snapshots"
down_revision = "0003_compile_job_error_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compile_jobs", sa.Column("claim_token", sa.Uuid(), nullable=True))
    op.add_column("compile_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compile_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compile_jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("compile_jobs", "attempt_count", server_default=None)
    op.create_index("ix_compile_jobs_lease_expires_at", "compile_jobs", ["lease_expires_at"])

    op.create_table(
        "compile_job_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("compile_job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["compile_job_id", "project_id", "tenant_id"],
            ["compile_jobs.id", "compile_jobs.project_id", "compile_jobs.tenant_id"],
            name="fk_compile_job_files_compile_job_project_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("compile_job_id", "filename", name="uq_compile_job_file_name"),
    )
    op.create_index("ix_compile_job_files_compile_job_id", "compile_job_files", ["compile_job_id"])
    op.create_index("ix_compile_job_files_tenant_id", "compile_job_files", ["tenant_id"])
    op.create_index("ix_compile_job_files_project_id", "compile_job_files", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_compile_job_files_project_id", table_name="compile_job_files")
    op.drop_index("ix_compile_job_files_tenant_id", table_name="compile_job_files")
    op.drop_index("ix_compile_job_files_compile_job_id", table_name="compile_job_files")
    op.drop_table("compile_job_files")
    op.drop_index("ix_compile_jobs_lease_expires_at", table_name="compile_jobs")
    op.drop_column("compile_jobs", "attempt_count")
    op.drop_column("compile_jobs", "lease_expires_at")
    op.drop_column("compile_jobs", "claimed_at")
    op.drop_column("compile_jobs", "claim_token")
```

- [ ] **Step 5: Run migration test**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_migrations.py -q
```

Expected: PASS.

## Task 2: Add Atomic Claim, Snapshot, and Stale Queued Repository APIs

**Files:**
- Modify: `server/core/repositories.py`
- Modify: `server/tests/test_repositories.py`

- [ ] **Step 1: Write repository tests**

Add tests to `server/tests/test_repositories.py`:

```python
def test_compile_repository_claims_queued_job_once(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")
    db_session.commit()

    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )

    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    assert claimed is not None
    first_token = claimed.claim_token
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert claimed.lease_expires_at is not None
    db_session.commit()

    duplicate = repo.claim_job_for_command(command, lease_seconds=660)
    assert duplicate is None

    persisted = db_session.get(CompileJob, job.id)
    assert persisted.claim_token == first_token
    assert persisted.attempt_count == 1


def test_compile_repository_reclaims_expired_running_job(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")
    db_session.commit()
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )
    first = repo.claim_job_for_command(command, lease_seconds=1)
    first.lease_expires_at = now_utc() - timedelta(seconds=1)
    first_token = first.claim_token
    db_session.commit()

    second = repo.claim_job_for_command(command, lease_seconds=660)
    assert second is not None
    assert second.claim_token != first_token
    assert second.attempt_count == 2


def test_compile_repository_finishes_only_current_claim(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")
    db_session.commit()
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )
    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    stale_token = uuid4()
    db_session.commit()

    assert repo.finish_job_if_claim_current(job.id, stale_token, "failed", error_code="stale_claim") is None
    persisted = db_session.get(CompileJob, job.id)
    assert persisted.status == "running"
    assert persisted.claim_token == claimed.claim_token

    finished = repo.finish_job_if_claim_current(job.id, claimed.claim_token, "succeeded")
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.lease_expires_at is None


def test_compile_repository_snapshots_job_files(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    project_repo = ProjectRepository(db_session, seeded_tenant.tenant_id)
    project_repo.save_code("default_purlin", "design.py", "shape = 'snapshot'\n", seeded_tenant.user_id, "snapshot")
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")

    files = project_repo.files_for_runtime("default_purlin")
    repo.snapshot_job_files(job, files)
    project_repo.save_code("default_purlin", "design.py", "shape = 'later'\n", seeded_tenant.user_id, "later")
    db_session.commit()

    snapshot = repo.files_for_job(job.id)
    assert snapshot["design.py"] == "shape = 'snapshot'\n"
```

Add imports:

```python
from datetime import timedelta
from uuid import uuid4
from core.models import CompileJob
from core.time import now_utc
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py -q
```

Expected: FAIL because repository methods and `CompileJobFile` do not exist yet.

- [ ] **Step 3: Implement repository methods**

In `server/core/repositories.py`, import the new model and helpers:

```python
import uuid
from datetime import timedelta
from sqlalchemy import or_, update
from core.models import CompileJobFile
```

Add these methods to `CompileRepository`:

```python
    def claim_job_for_command(self, command: CompileCommand, lease_seconds: int) -> CompileJob | None:
        now = now_utc()
        claim_token = uuid.uuid4()
        stmt = (
            update(CompileJob)
            .where(
                CompileJob.id == command.job_id,
                CompileJob.tenant_id == command.tenant_id,
                CompileJob.project_id == command.project_id,
                CompileJob.requested_by == command.requested_by,
                CompileJob.export_format == command.export_format,
                or_(
                    CompileJob.status == "queued",
                    (CompileJob.status == "running") & (CompileJob.lease_expires_at < now),
                ),
            )
            .values(
                status="running",
                error=None,
                error_code=None,
                user_message=None,
                retryable=False,
                finished_at=None,
                claim_token=claim_token,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempt_count=CompileJob.attempt_count + 1,
            )
            .returning(CompileJob.id)
        )
        claimed_id = self.db.scalar(stmt)
        if claimed_id is None:
            return None
        return self.db.get(CompileJob, claimed_id)

    def snapshot_job_files(self, job: CompileJob, files: dict[str, str]) -> None:
        for filename, content in files.items():
            self.db.add(
                CompileJobFile(
                    compile_job_id=job.id,
                    tenant_id=job.tenant_id,
                    project_id=job.project_id,
                    filename=filename,
                    content=content,
                )
            )

    def files_for_job(self, job_id: UUID) -> dict[str, str]:
        rows = self.db.scalars(
            select(CompileJobFile).where(
                CompileJobFile.compile_job_id == job_id,
                CompileJobFile.tenant_id == self.tenant_id,
            )
        ).all()
        return {row.filename: row.content for row in rows}

    def stale_queued_jobs(self, older_than_seconds: int, limit: int = 50) -> list[CompileJob]:
        cutoff = now_utc() - timedelta(seconds=older_than_seconds)
        return list(
            self.db.scalars(
                select(CompileJob)
                .where(
                    CompileJob.tenant_id == self.tenant_id,
                    CompileJob.status == "queued",
                    CompileJob.created_at < cutoff,
                )
                .order_by(CompileJob.created_at)
                .limit(limit)
            )
        )

    def finish_job_if_claim_current(
        self,
        job_id: UUID,
        claim_token: UUID,
        status: str,
        error: str | None = None,
        error_code: str | None = None,
        user_message: str | None = None,
        retryable: bool = False,
    ) -> CompileJob | None:
        stmt = (
            update(CompileJob)
            .where(
                CompileJob.id == job_id,
                CompileJob.tenant_id == self.tenant_id,
                CompileJob.status == "running",
                CompileJob.claim_token == claim_token,
            )
            .values(
                status=status,
                error=error,
                error_code=error_code,
                user_message=user_message,
                retryable=retryable,
                finished_at=now_utc(),
                lease_expires_at=None,
            )
            .returning(CompileJob.id)
        )
        finished_id = self.db.scalar(stmt)
        if finished_id is None:
            return None
        return self.db.get(CompileJob, finished_id)
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py -q
```

Expected: PASS.

## Task 3: Snapshot Files When Compile Is Queued

**Files:**
- Modify: `server/core/repositories.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/tests/test_compile_flow.py`

- [ ] **Step 1: Extend enqueue test for snapshots**

In `test_compile_enqueues_job_and_returns_immediately`, after fetching the job:

```python
    snapshot_rows = db_session.scalars(
        select(CompileJobFile).where(CompileJobFile.compile_job_id == job.id)
    ).all()
    snapshot = {row.filename: row.content for row in snapshot_rows}
    assert snapshot["design.py"] == "shape = 'queued'\n"
```

Change `test_compile_marks_job_failed_when_enqueue_fails` because publish failures happen after the queued job transaction commits. The committed job must remain queued for stale recovery:

```python
def test_compile_leaves_job_queued_when_publish_fails_after_commit(authenticated_intus_client, db_session, monkeypatch):
    async def fake_publish_compile(command):
        raise RuntimeError("nats down")

    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish_compile)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "shape = 'queued'\n", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["job_id"]
    assert body["user_message"] == "Compile queued but could not be published immediately. It will be retried."
    assert body["retryable"] is True

    job = db_session.get(CompileJob, body["job_id"])
    assert job.status == "queued"
    assert job.error_code is None
    assert job.retryable is False
```

Add import:

```python
from core.models import CompileJobFile
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest \
  server/tests/test_compile_flow.py::test_compile_enqueues_job_and_returns_immediately \
  server/tests/test_compile_flow.py::test_compile_leaves_job_queued_when_publish_fails_after_commit \
  -q
```

Expected: FAIL because no snapshot rows are created and publish failure marks the committed job failed.

- [ ] **Step 3: Add a non-committing source staging helper**

In `ProjectRepository`, split the current `save_code()` implementation so compile enqueue can save submitted code without committing before the compile job and snapshot exist:

```python
    def stage_code_update(self, project_name: str, filename: str, content: str, user_id: UUID, message: str) -> bool:
        filename = require_valid_python_filename(filename)
        project = self.get_project(project_name)
        if project is None:
            return False

        file = self.db.scalar(
            select(ProjectFile).where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.filename == filename,
            )
        )
        if file is None:
            file = ProjectFile(tenant_id=self.tenant_id, project_id=project.id, filename=filename, content=content)
            self.db.add(file)
        else:
            file.content = content
            file.updated_at = now_utc()

        project.updated_at = now_utc()
        self.db.flush()
        self._snapshot(project, user_id, message)
        return True

    def save_code(self, project_name: str, filename: str, content: str, user_id: UUID, message: str) -> bool:
        saved = self.stage_code_update(project_name, filename, content, user_id, message)
        if saved:
            self.db.commit()
        return saved
```

- [ ] **Step 4: Create snapshot before commit**

In `compile_project()`, replace the `repo.save_code(...)` call with:

```python
        saved = repo.stage_code_update(
            name,
            filename,
            req.code,
            ctx.user_id,
            f"Compile update ({filename}) via Intus",
        )
```

In `compile_project()` in `server/workflows/intus/intus_server.py`, after `job = compile_repo.start_job(...)` and before `db.commit()`, add:

```python
        files = repo.files_for_runtime(name)
        if files is None:
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        compile_repo.snapshot_job_files(job, files)
```

This must happen after `repo.stage_code_update(...)` so the submitted file content is included in the snapshot. Do not call `repo.save_code(...)` in this route; it commits too early for the compile enqueue transaction.

- [ ] **Step 5: Leave committed publish failures queued**

In `compile_project()`, track whether the queued-job transaction has committed:

```python
    committed = False
```

Set it immediately after the commit:

```python
        db.commit()
        committed = True
```

In the exception handler, before marking a persisted job failed, add:

```python
            if committed:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "success": False,
                        "job_id": str(job_id),
                        "error": str(exc),
                        "short": "Compile command publish failed",
                        "user_message": "Compile queued but could not be published immediately. It will be retried.",
                        "retryable": True,
                    },
                )
```

Only the pre-commit path should mark the job failed with `enqueue_failed`. Once the job transaction commits, stale queued recovery owns the publish gap.

- [ ] **Step 6: Run compile flow tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_flow.py -q
```

Expected: PASS.

## Task 4: Execute Only Claimed Jobs and Compile Snapshots

**Files:**
- Modify: `server/workflows/intus/compile_executor.py`
- Modify: `server/tests/test_compile_worker.py`

- [ ] **Step 1: Update executor success test to use snapshots and claim token**

In `test_execute_compile_job_records_artifact_and_success`, claim the job and snapshot files before calling the executor:

```python
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    repo.snapshot_job_files(job, {"design.py": "shape = 'snapshot'\n"})
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )
    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    db_session.commit()

    event = execute_compile_job(
        db_session,
        job.id,
        claim_token=claimed.claim_token,
        timeout_seconds=600,
        artifact_retention_limit=10,
    )
```

Change the fake sandbox assertion to prove it receives snapshot content:

```python
    def fake_run_compile_sandbox(project_dir, export_format, timeout_seconds):
        assert (project_dir / "design.py").read_text() == "shape = 'snapshot'\n"
        return SimpleNamespace(success=True, output_path=output_path, error=None, stderr=None)
```

- [ ] **Step 2: Add stale-token test**

Add this test:

```python
def test_execute_compile_job_rolls_back_output_when_claim_is_lost(db_session, seeded_tenant, monkeypatch, tmp_path):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    repo.snapshot_job_files(job, {"design.py": "shape = 'snapshot'\n"})
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )
    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    lost_token = claimed.claim_token
    claimed.claim_token = uuid4()
    db_session.commit()

    output_path = tmp_path / "model.glb"
    output_path.write_bytes(b"stale")
    monkeypatch.setattr(
        "workflows.intus.compile_executor.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(success=True, output_path=output_path, error=None, stderr=None),
    )

    event = execute_compile_job(
        db_session,
        job.id,
        claim_token=lost_token,
        timeout_seconds=600,
        artifact_retention_limit=10,
    )

    assert event is None
    persisted = db_session.get(CompileJob, job.id)
    assert persisted.status == "running"
    assert persisted.claim_token != lost_token
    assert db_session.scalar(select(Artifact).where(Artifact.compile_job_id == job.id)) is None
```

Add import:

```python
from uuid import uuid4
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_worker.py -q
```

Expected: FAIL because `execute_compile_job` does not accept `claim_token` and reads mutable project files.

- [ ] **Step 4: Update executor signature and file source**

Change `execute_compile_job` signature in `server/workflows/intus/compile_executor.py`:

```python
def execute_compile_job(
    db: Session,
    job_id: UUID,
    claim_token: UUID,
    timeout_seconds: int,
    artifact_retention_limit: int,
) -> CompileResultEvent | None:
```

Remove `project_repo = ProjectRepository(...)` and replace the runtime file load:

```python
        files = compile_repo.files_for_job(job.id)
        if not files:
            raise RuntimeError("Compile job snapshot is empty")
```

When handling a sandbox failure, replace `compile_repo.finish_job(...)` with `compile_repo.finish_job_if_claim_current(...)`. If it returns `None`, roll back and return `None`:

```python
                finished = compile_repo.finish_job_if_claim_current(
                    job.id,
                    claim_token,
                    "failed",
                    error=error,
                    error_code=_error_code(error),
                    user_message=_user_message(error),
                    retryable=True,
                )
                if finished is None:
                    db.rollback()
                    return None
                db.commit()
                return _event(
                    finished,
                    "failed",
                    error_code=finished.error_code,
                    user_message=finished.user_message,
                    error=error,
                    retryable=finished.retryable,
                )
```

After reading successful output bytes, record the artifact and then finish through the same guarded helper before committing:

```python
        artifact = compile_repo.record_artifact(job.project_id, job.id, job.export_format, output_bytes)
        finished = compile_repo.finish_job_if_claim_current(job.id, claim_token, "succeeded")
        if finished is None:
            db.rollback()
            return None
        pruned = compile_repo.prunable_artifacts(
            job.project_id,
            job.export_format,
            max(1, artifact_retention_limit),
        )
        compile_repo.delete_artifacts(pruned)
        db.commit()
        return _event(finished, "succeeded", artifact_id=artifact.id)
```

The rollback is required: if the lease was lost after the sandbox completed, the artifact insert and any stale status changes must disappear together.

- [ ] **Step 5: Run worker tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_worker.py -q
```

Expected: PASS.

## Task 5: Claim in Worker and Make Duplicate Delivery Idempotent

**Files:**
- Modify: `server/workflows/intus/compile_worker.py`
- Modify: `server/tests/test_compile_worker.py`

- [ ] **Step 1: Add duplicate terminal and running tests**

Add tests to `server/tests/test_compile_worker.py`:

```python
def test_worker_acks_duplicate_terminal_job_without_reexecuting(db_session, seeded_tenant, monkeypatch):
    from workflows.intus.compile_worker import handle_compile_message

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()
    called = False

    def fake_execute(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)
    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), worker_settings()))

    assert msg.acked is True
    assert called is False


def test_worker_acks_duplicate_running_job_with_active_lease_without_reexecuting(db_session, seeded_tenant, monkeypatch):
    from workflows.intus.compile_worker import handle_compile_message

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="glb",
        claim_token=uuid4(),
        claimed_at=now_utc(),
        lease_expires_at=now_utc() + timedelta(seconds=600),
        attempt_count=1,
    )
    db_session.add(job)
    db_session.commit()
    called = False

    def fake_execute(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)
    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), worker_settings()))

    assert msg.acked is True
    assert called is False


def test_worker_acks_without_publish_when_executor_loses_claim(db_session, seeded_tenant, monkeypatch):
    from workflows.intus.compile_worker import handle_compile_message

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()
    published = []

    def fake_execute(*args, **kwargs):
        return None

    class FakePublisher:
        async def publish_json(self, subject, event):
            published.append((subject, event))

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)
    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), worker_settings()))

    assert msg.acked is True
    assert published == []
```

Create helper in the test file:

```python
def worker_settings():
    return SimpleNamespace(
        compile_timeout_seconds=600,
        compile_ack_wait_seconds=660,
        artifact_retention_limit=10,
        compile_succeeded_subject="tertius.compile.succeeded",
        compile_failed_subject="tertius.compile.failed",
    )
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_worker.py -q
```

Expected: FAIL because the worker still executes matching commands without claiming.

- [ ] **Step 3: Update worker handler**

In `handle_compile_message`, after the identity mismatch block and before calling `execute_compile_job`, replace the direct executor call with:

```python
    claimed = repo.claim_job_for_command(command, lease_seconds=settings.compile_ack_wait_seconds)
    if claimed is None:
        await msg.ack()
        return
    db.commit()

    event = execute_compile_job(
        db,
        claimed.id,
        claim_token=claimed.claim_token,
        timeout_seconds=settings.compile_timeout_seconds,
        artifact_retention_limit=settings.artifact_retention_limit,
    )
    if event is None:
        await msg.ack()
        return
```

Keep the existing success/failure result publish and `await msg.ack()` after execution.

- [ ] **Step 4: Run worker tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_worker.py -q
```

Expected: PASS.

## Task 6: Recover Stale Queued Jobs

**Files:**
- Modify: `server/workflows/intus/compile_worker.py`
- Modify: `server/tests/test_compile_worker.py`

- [ ] **Step 1: Add stale queued republish test**

Add this test:

```python
def test_worker_republishes_stale_queued_jobs(db_session, seeded_tenant):
    from workflows.intus.compile_worker import republish_stale_queued_jobs

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="glb",
    )
    db_session.add(job)
    db_session.flush()
    job.created_at = now_utc() - timedelta(minutes=5)
    db_session.commit()

    published = []

    class FakePublisher:
        async def publish_json(self, subject, command):
            published.append((subject, command))

    settings = SimpleNamespace(compile_request_subject="tertius.compile.request")
    asyncio.run(republish_stale_queued_jobs(db_session, FakePublisher(), settings, older_than_seconds=60))

    assert len(published) == 1
    assert published[0][0] == "tertius.compile.request"
    assert published[0][1].job_id == job.id
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_worker.py::test_worker_republishes_stale_queued_jobs -q
```

Expected: FAIL because `republish_stale_queued_jobs` does not exist.

- [ ] **Step 3: Implement stale queued republisher**

Add to `server/workflows/intus/compile_worker.py`:

```python
async def republish_stale_queued_jobs(db, publisher: NatsPublisher, settings, older_than_seconds: int = 60) -> int:
    tenant_ids = db.scalars(select(CompileJob.tenant_id).where(CompileJob.status == "queued").distinct()).all()
    count = 0
    for tenant_id in tenant_ids:
        repo = CompileRepository(db, tenant_id)
        for job in repo.stale_queued_jobs(older_than_seconds=older_than_seconds):
            command = CompileCommand(
                job_id=job.id,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                requested_by=job.requested_by,
                export_format=job.export_format,
                created_at=job.created_at,
            )
            await publisher.publish_json(settings.compile_request_subject, command)
            count += 1
    return count
```

Add imports:

```python
from sqlalchemy import select
from core.models import CompileJob
```

In `run_worker`, track the last recovery pass:

```python
        last_recovery = 0.0
```

Inside the `while True` loop before `subscription.fetch(...)`:

```python
            now = asyncio.get_running_loop().time()
            if now - last_recovery >= 60:
                with SessionLocal() as db:
                    try:
                        republished = await republish_stale_queued_jobs(db, publisher, settings, older_than_seconds=60)
                        if republished:
                            logger.warning("Republished %s stale queued compile jobs", republished)
                    except Exception:
                        logger.exception("Compile worker stale queued recovery failed")
                last_recovery = now
```

- [ ] **Step 4: Run worker tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_worker.py -q
```

Expected: PASS.

## Task 7: Reconcile Existing JetStream Consumer Settings

**Files:**
- Modify: `server/core/nats_client.py`
- Modify: `server/tests/test_nats_client.py`

- [ ] **Step 1: Add consumer update test**

Extend the fake JetStream in `server/tests/test_nats_client.py` with an `update_consumer` method that records updates:

```python
    async def update_consumer(self, stream_name, config):
        self.consumers[(stream_name, config.durable_name)] = config
        self.updated_consumers.append((stream_name, config))
```

Initialize:

```python
        self.updated_consumers = []
```

Add test:

```python
@pytest.mark.asyncio
async def test_ensure_compile_stream_updates_existing_consumer_config():
    settings = SimpleNamespace(
        compile_stream_name="TERTIUS_COMPILE",
        compile_worker_queue="compile-workers",
        compile_request_subject="tertius.compile.request",
        compile_succeeded_subject="tertius.compile.succeeded",
        compile_failed_subject="tertius.compile.failed",
        compile_ack_wait_seconds=660,
        compile_max_deliver=3,
    )
    jetstream = FakeJetStream()
    await ensure_compile_stream(FakeNats(jetstream), settings)

    settings.compile_ack_wait_seconds = 900
    await ensure_compile_stream(FakeNats(jetstream), settings)

    assert jetstream.updated_consumers
    updated = jetstream.updated_consumers[-1][1]
    assert updated.ack_wait == 900
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_nats_client.py -q
```

Expected: FAIL because existing consumer config is not updated.

- [ ] **Step 3: Implement reconciliation**

In `ensure_compile_stream`, build the desired `ConsumerConfig` once:

```python
    desired_consumer = ConsumerConfig(
        durable_name=settings.compile_worker_queue,
        filter_subject=settings.compile_request_subject,
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=settings.compile_ack_wait_seconds,
        max_deliver=settings.compile_max_deliver,
    )
```

Replace the existing `add_consumer` block with:

```python
    try:
        info = await js.consumer_info(settings.compile_stream_name, settings.compile_worker_queue)
        current = info.config
        if (
            current.filter_subject != desired_consumer.filter_subject
            or current.ack_wait != desired_consumer.ack_wait
            or current.max_deliver != desired_consumer.max_deliver
            or current.ack_policy != desired_consumer.ack_policy
        ):
            await js.update_consumer(settings.compile_stream_name, desired_consumer)
    except NotFoundError:
        await js.add_consumer(settings.compile_stream_name, desired_consumer)
```

- [ ] **Step 4: Run NATS tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_nats_client.py -q
```

Expected: PASS.

## Task 8: Focused Integration Verification

**Files:**
- No source edits.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest \
  server/tests/test_compile_flow.py \
  server/tests/test_compile_worker.py \
  server/tests/test_repositories.py \
  server/tests/test_nats_client.py \
  server/tests/test_migrations.py
```

Expected: PASS.

- [ ] **Step 2: Run deployment render gate**

Run:

```bash
rtk scripts/test-deployment-config.sh
```

Expected: PASS, including existing checks for NATS and compile-worker rendering.

- [ ] **Step 3: Check formatting and whitespace**

Run:

```bash
rtk git diff --check
```

Expected: no output.

## Task 9: Local k3s Smoke Verification

**Files:**
- No source edits.

- [ ] **Step 1: Run the k3s deployment smoke harness**

Run:

```bash
KUBECONFIG=/home/johnson/.kube/config rtk scripts/test-k3s-deployment.sh
```

Expected: the harness waits for Postgres, Valkey, NATS, Keycloak, API, UI, and compile worker readiness, then completes smoke checks.

- [ ] **Step 2: Inspect worker and queue state after smoke**

Run:

```bash
KUBECONFIG=/home/johnson/.kube/config rtk kubectl get pods -n tertius -l app.kubernetes.io/component=compile-worker
KUBECONFIG=/home/johnson/.kube/config rtk kubectl logs -n tertius deploy/tertius-api-compile-worker --all-containers=true --since=10m --tail=200 --prefix=true
KUBECONFIG=/home/johnson/.kube/config rtk kubectl run tertius-nats-check-$(date +%s) -n tertius --restart=Never --rm -i --image=natsio/nats-box:0.19.7 --command -- nats consumer info TERTIUS_COMPILE compile-workers --server nats://tertius-nats:4222
```

Expected:
- Compile worker pods are Ready with zero new restarts.
- Logs show subscription and no repeated handler failures.
- NATS consumer has `Outstanding Acks: 0`, `Unprocessed Messages: 0`, and no unexpected redelivery growth.

## Self-Review

- Spec coverage:
  - Duplicate delivery and two-worker claim race: Task 2 and Task 5.
  - Worker crash/redelivery after claim: Task 2 lease reclaim.
  - API crash after DB commit before publish: Task 6 stale queued republish.
  - Mutable source drift: Task 1 snapshot table, Task 3 snapshot creation, Task 4 executor snapshot use.
  - Retry semantics clarity: Error handling matrix and Task 5 ack behavior.
  - JetStream config drift: Task 7.
- Placeholder scan:
  - No placeholder markers or undefined later-work steps are present.
- Type consistency:
  - `claim_token` is a UUID on `CompileJob`.
  - `claim_job_for_command()` returns `CompileJob | None`.
  - `execute_compile_job()` accepts `claim_token: UUID` and returns `CompileResultEvent | None`; `None` means the worker lost the claim and must not publish a result event.
