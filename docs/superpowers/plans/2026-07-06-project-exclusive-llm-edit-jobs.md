# Project-Exclusive LLM Edit Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a second LLM file edit job from being accepted for a project while that project already has a queued or running LLM edit job.

**Architecture:** Add a query-based admission check to the existing async LLM edit submit endpoint. The backend first reconciles stale queued/running jobs for the project, then queries `llm_edit_jobs` for any remaining active job in that same tenant/project; if one exists, the POST returns `409 Conflict` with the active job id and does not enqueue or dispatch a new job. This deliberately does not add mutexes, advisory locks, row locks, long-running DB transactions, or a compile-job lease.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Postgres, pytest, React/Vite TypeScript, Vitest.

---

## Document Type

Implementation plan.

## Current State

- [server/core/models.py](/home/johnson/code/Tertius-Web/server/core/models.py:246) defines `LlmEditJob` with `tenant_id`, `project_id`, `status`, `created_at`, `finished_at`, and `attempt_count`.
- [server/core/repositories.py](/home/johnson/code/Tertius-Web/server/core/repositories.py:724) defines `LlmEditRepository.start_job(...)`, `mark_job_dispatched(...)`, `finish_job(...)`, and query-based stale reconciliation.
- [server/workflows/intus/intus_server.py](/home/johnson/code/Tertius-Web/server/workflows/intus/intus_server.py:1080) is the LLM edit job submit gate: `POST /projects/{name}/files/llm-edit/jobs`.
- [server/workflows/intus/intus_server.py](/home/johnson/code/Tertius-Web/server/workflows/intus/intus_server.py:1175) already reconciles stale project jobs before returning history.
- [server/workflows/intus/intus_server.py](/home/johnson/code/Tertius-Web/server/workflows/intus/intus_server.py:1211) already reconciles a stale single job before returning job status.
- Existing active statuses are `queued` and `running`.
- Existing terminal statuses are `succeeded` and `failed`.
- Existing file-version protection in `ProjectRepository.stage_file_updates(...)` remains unchanged. This plan is only for project-level LLM edit admission.

## Required Behavior

| Case | Expected behavior |
|---|---|
| No active LLM edit job exists for the project | Existing submit behavior remains: validate request, insert `queued` job, commit, dispatch background task, return `202`. |
| A non-stale `queued` or `running` job exists for the same tenant/project | Return `409 Conflict`; include `success=false`, `error_code="llm_edit_in_progress"`, `active_job_id`, and `active_job_status`; do not insert another `LlmEditJob`; do not schedule a background task; do not call the provider. |
| A stale `queued` or `running` job exists for the same tenant/project | Reconcile it to `failed` using the existing stale timeout, then allow the new job to be accepted if no other active job remains. |
| A `succeeded` or `failed` job exists | Ignore it for exclusivity. |
| An active job exists in another project or tenant | Ignore it for this project. |

## Non-Goals

- Do not add a Python mutex, process-global lock, advisory lock, `SELECT FOR UPDATE`, table lock, or row lock for project exclusivity.
- Do not hold a DB transaction open while the LLM provider call runs.
- Do not reuse compile-job `claim_token` or `lease_expires_at`; this is an LLM edit admission check, not compile worker claiming.
- Do not change the existing final file-version conflict behavior.
- Do not add high-cardinality job ids to metrics or logs.

## Important Limit

This implements query-based admission control. It prevents ordinary duplicate submits and stale active-job blockage, but a plain "check then insert" query is not a strict distributed lock under two simultaneous POST transactions. If strict serializable exclusivity is required later, add a database constraint or serializable transaction design in a separate plan. That stronger design is intentionally out of scope because this plan follows the explicit requirement to use a query and avoid mutexes or row locks.

## Files

- Modify: `server/core/models.py`
  - Add a composite index for the active-job lookup.
- Create: `server/migrations/versions/0010_llm_edit_project_status_index.py`
  - Add/drop the composite index.
- Modify: `server/core/repositories.py`
  - Add `LLM_EDIT_ACTIVE_STATUSES`.
  - Add `LlmEditRepository.get_active_job_for_project(...)`.
  - Reuse the active-status constant in stale reconciliation.
- Modify: `server/workflows/intus/intus_server.py`
  - Reconcile stale project jobs before admission.
  - Return a bounded `409` response when another active LLM edit job exists.
- Modify: `server/tests/test_repositories.py`
  - Add repository tests for active-job lookup and tenant/project scoping.
- Modify: `server/tests/test_llm_file_edit.py`
  - Add endpoint tests for conflict, stale reconciliation, and no duplicate enqueue.
- Modify: `server/tests/test_migrations.py`
  - Assert the composite index exists after Alembic upgrade.
- Modify: `ui/src/workflows/shared/projectStorage.test.ts`
  - Assert a `409` active-job response surfaces the server message.

## Anti-Patterns

| Don't | Do Instead | Why |
|---|---|---|
| Add a process mutex around LLM generation | Query `llm_edit_jobs` before enqueue | Multiple API pods/processes would not share a Python mutex. |
| Add `SELECT FOR UPDATE` or hold row locks during generation | Use a normal `SELECT` admission check and short transactions | The LLM call can outlive HTTP and DB lock budgets. |
| Check compile jobs for exclusivity | Check `LlmEditJob.status in ("queued", "running")` | The exclusive resource is project LLM edit generation, not compile. |
| Let stale active jobs block forever | Reconcile stale project jobs before the active-job query | Existing worker-lost semantics already handle stale queued/running jobs. |
| Hide the active job id from the authenticated caller | Return `active_job_id` and `active_job_status` in the `409` body | The caller already owns project job ids and can poll/resume. |
| Add job ids to metric labels or logs | Keep the conflict response bounded and user-scoped | Telemetry safety forbids high-cardinality identifiers in metrics/logs. |

## Error Handling Matrix

| Condition | HTTP status | Response body | Retry semantics |
|---|---:|---|---|
| Active non-stale LLM edit job exists | `409` | `{"success": false, "error": "An AI edit is already running for this project. Wait for it to finish before starting another.", "error_code": "llm_edit_in_progress", "retryable": true, "active_job_id": "llm-job-id", "active_job_status": "queued"}` | Retry after active job reaches `succeeded` or `failed`. |
| Stale active jobs were reconciled and no active job remains | `202` | Existing queued-job body | Caller polls new job normally. |
| Project not found | `404` | Existing `Project not found` shape | No change. |
| File pointer invalid and no active job exists | `400` or `404` or `409` | Existing validation response | No change. |
| Enqueue fails unexpectedly | `503` | Existing retryable enqueue failure | No change. |

## Test Case Specifications

| ID | File | Behavior |
|---|---|---|
| UT-001 | `server/tests/test_repositories.py` | `get_active_job_for_project(...)` returns the oldest queued/running job for the tenant/project. |
| UT-002 | `server/tests/test_repositories.py` | `get_active_job_for_project(...)` ignores `succeeded`/`failed` jobs, other projects, and other tenants. |
| UT-003 | `server/tests/test_migrations.py` | Alembic head creates `ix_llm_edit_jobs_project_status_created` on `tenant_id`, `project_id`, `status`, `created_at`. |
| IT-001 | `server/tests/test_llm_file_edit.py` | POST returns `409` when a running job exists for the same project and does not enqueue another job. |
| IT-002 | `server/tests/test_llm_file_edit.py` | POST reconciles a stale running job first, then accepts a new job. |
| IT-003 | `server/tests/test_llm_file_edit.py` | Active job in another tenant/project does not block submit. |
| UI-001 | `ui/src/workflows/shared/projectStorage.test.ts` | `applyLlmFileEditJob(...)` surfaces the server's active-job conflict message. |

## Task 1: Add The Active-Job Lookup Index

**Files:**
- Modify: `server/core/models.py`
- Create: `server/migrations/versions/0010_llm_edit_project_status_index.py`
- Modify: `server/tests/test_migrations.py`

- [ ] **Step 1: Add the failing migration test assertion**

In `server/tests/test_migrations.py`, inside `test_alembic_upgrade_creates_multitenant_schema(...)`, add this assertion after the `compile_job_columns` assertions:

```python
    llm_edit_indexes = {
        index["name"]: index for index in inspector.get_indexes("llm_edit_jobs")
    }
    assert llm_edit_indexes["ix_llm_edit_jobs_project_status_created"]["column_names"] == [
        "tenant_id",
        "project_id",
        "status",
        "created_at",
    ]
```

- [ ] **Step 2: Run the focused migration test and verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_migrations.py::test_alembic_upgrade_creates_multitenant_schema -q
```

Expected: FAIL with `KeyError: 'ix_llm_edit_jobs_project_status_created'`.

- [ ] **Step 3: Add the SQLAlchemy model index**

In `server/core/models.py`, update `LlmEditJob.__table_args__` to include the new composite index:

```python
    __table_args__ = (
        UniqueConstraint("id", "project_id", "tenant_id", name="uq_llm_edit_jobs_id_project_tenant"),
        ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"], ondelete="CASCADE"),
        Index("ix_llm_edit_jobs_created_at", "tenant_id", "created_at"),
        Index("ix_llm_edit_jobs_project_status_created", "tenant_id", "project_id", "status", "created_at"),
    )
```

- [ ] **Step 4: Create the Alembic migration**

Create `server/migrations/versions/0010_llm_edit_project_status_index.py`:

```python
"""add llm edit project status lookup index

Revision ID: 0010_llm_edit_project_status_index
Revises: 0009_compile_llm_origin
Create Date: 2026-07-06
"""

from alembic import op


revision = "0010_llm_edit_project_status_index"
down_revision = "0009_compile_llm_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_llm_edit_jobs_project_status_created",
        "llm_edit_jobs",
        ["tenant_id", "project_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_edit_jobs_project_status_created", table_name="llm_edit_jobs")
```

- [ ] **Step 5: Run migration tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_migrations.py -q
```

Expected: PASS.

## Task 2: Add The Repository Active-Job Query

**Files:**
- Modify: `server/core/repositories.py`
- Modify: `server/tests/test_repositories.py`

- [ ] **Step 1: Write repository tests**

In `server/tests/test_repositories.py`, add `LlmEditJob` to the `from core.models import (...)` import list.

Add these tests near the existing `LlmEditRepository` tests:

```python
def test_llm_edit_repository_get_active_job_for_project_returns_oldest_queued_or_running(db_session):
    seeded = seed_two_tenants(db_session)
    repo = LlmEditRepository(db_session, seeded["tenant_a"])
    base_time = datetime(2026, 7, 6, tzinfo=timezone.utc)

    succeeded = repo.start_job(
        seeded["project_a"],
        seeded["user_a"],
        {"prompt": "done", "files": []},
        status="succeeded",
    )
    queued = repo.start_job(
        seeded["project_a"],
        seeded["user_a"],
        {"prompt": "queued", "files": []},
        status="queued",
    )
    running = repo.start_job(
        seeded["project_a"],
        seeded["user_a"],
        {"prompt": "running", "files": []},
        status="running",
    )
    succeeded.created_at = base_time
    queued.created_at = base_time + timedelta(seconds=1)
    running.created_at = base_time + timedelta(seconds=2)
    db_session.commit()

    active = repo.get_active_job_for_project(seeded["project_a"])

    assert active is not None
    assert active.id == queued.id
    assert active.id != succeeded.id


def test_llm_edit_repository_get_active_job_for_project_is_tenant_and_project_scoped(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = LlmEditRepository(db_session, seeded["tenant_a"])
    repo_b = LlmEditRepository(db_session, seeded["tenant_b"])

    failed = repo_a.start_job(
        seeded["project_a"],
        seeded["user_a"],
        {"prompt": "failed", "files": []},
        status="failed",
    )
    other_tenant_running = repo_b.start_job(
        seeded["project_b"],
        seeded["user_b"],
        {"prompt": "other tenant", "files": []},
        status="running",
    )
    db_session.commit()

    active = repo_a.get_active_job_for_project(seeded["project_a"])

    assert active is None
    assert db_session.get(LlmEditJob, failed.id) is not None
    assert db_session.get(LlmEditJob, other_tenant_running.id) is not None
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest \
  server/tests/test_repositories.py::test_llm_edit_repository_get_active_job_for_project_returns_oldest_queued_or_running \
  server/tests/test_repositories.py::test_llm_edit_repository_get_active_job_for_project_is_tenant_and_project_scoped \
  -q
```

Expected: FAIL because `LlmEditRepository.get_active_job_for_project` does not exist.

- [ ] **Step 3: Add the repository constant and query method**

In `server/core/repositories.py`, add this near the existing LLM edit constants:

```python
LLM_EDIT_ACTIVE_STATUSES = ("queued", "running")
```

Inside `class LlmEditRepository`, add this method after `list_jobs_for_project(...)`:

```python
    def get_active_job_for_project(self, project_id: UUID) -> LlmEditJob | None:
        return self.db.scalar(
            select(LlmEditJob)
            .where(
                LlmEditJob.tenant_id == self.tenant_id,
                LlmEditJob.project_id == project_id,
                LlmEditJob.status.in_(LLM_EDIT_ACTIVE_STATUSES),
            )
            .order_by(LlmEditJob.created_at.asc(), LlmEditJob.id.asc())
            .limit(1)
        )
```

In `reconcile_stale_job(...)` and `reconcile_stale_jobs_for_project(...)`, replace:

```python
                LlmEditJob.status.in_(["queued", "running"]),
```

with:

```python
                LlmEditJob.status.in_(LLM_EDIT_ACTIVE_STATUSES),
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py -q
```

Expected: PASS.

## Task 3: Reject A New Job When A Project Has An Active LLM Edit

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/tests/test_llm_file_edit.py`

- [ ] **Step 1: Write the active-job conflict endpoint test**

Add this test near `test_llm_file_edit_job_completes_and_status_returns_result(...)`:

```python
def test_llm_file_edit_job_rejects_when_project_has_active_job(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    active_job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        request_payload={"prompt": "already running", "files": [file_pointer(design)]},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(active_job)
    db_session.commit()

    async def fake_generate_file_edits(*args, **kwargs):
        raise AssertionError("provider should not be called when an active job exists")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
            "metadata": {"source": "generate_design_window"},
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "error": "An AI edit is already running for this project. Wait for it to finish before starting another.",
        "error_code": "llm_edit_in_progress",
        "retryable": True,
        "active_job_id": str(active_job.id),
        "active_job_status": "running",
    }
    assert db_session.scalar(
        select(func.count()).select_from(LlmEditJob).where(
            LlmEditJob.tenant_id == seeded_tenant.tenant_id,
            LlmEditJob.project_id == seeded_tenant.project_id,
        )
    ) == 1
```

- [ ] **Step 2: Write the stale-active recovery endpoint test**

Add this test near the active-job conflict test:

```python
def test_llm_file_edit_job_reconciles_stale_active_job_before_admission(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    monkeypatch.setattr(
        intus_server,
        "get_settings",
        lambda: make_llm_settings(llm_api_key="test-key", llm_timeout_seconds=1),
    )
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    stale_job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        request_payload={"prompt": "lost worker", "files": [file_pointer(design)]},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db_session.add(stale_job)
    db_session.commit()

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design.id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )
    monkeypatch.setattr(
        intus_server,
        "generate_file_edits",
        make_fake_generate_file_edits(return_value=fake_result),
    )

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(), None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
        },
    )

    assert response.status_code == 202
    new_job_id = UUID(response.json()["job_id"])
    assert new_job_id != stale_job.id

    db_session.expire_all()
    assert db_session.get(LlmEditJob, stale_job.id).status == "failed"
    assert db_session.get(LlmEditJob, stale_job.id).error_code == "worker_lost"
    assert db_session.get(LlmEditJob, new_job_id).status == "succeeded"
```

- [ ] **Step 3: Run the focused failing endpoint tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest \
  server/tests/test_llm_file_edit.py::test_llm_file_edit_job_rejects_when_project_has_active_job \
  server/tests/test_llm_file_edit.py::test_llm_file_edit_job_reconciles_stale_active_job_before_admission \
  -q
```

Expected: first test FAILS because the endpoint enqueues another job; second test FAILS because admission does not reconcile before checking active state.

- [ ] **Step 4: Add the endpoint admission guard**

In `server/workflows/intus/intus_server.py`, inside `start_llm_file_edit_job(...)`, after confirming `project is not None` and before file pointer validation, add:

```python
        settings = get_settings()
        llm_edit_repo.reconcile_stale_jobs_for_project(
            project.id,
            older_than_seconds=_llm_edit_stale_after_seconds(settings),
        )
        active_job = llm_edit_repo.get_active_job_for_project(project.id)
        if active_job is not None:
            db.commit()
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "success": False,
                    "error": "An AI edit is already running for this project. Wait for it to finish before starting another.",
                    "error_code": "llm_edit_in_progress",
                    "retryable": True,
                    "active_job_id": str(active_job.id),
                    "active_job_status": active_job.status,
                },
            )
```

Do not add locks or move this check into `_run_llm_file_edit_job(...)`; it belongs at the submit admission boundary.

- [ ] **Step 5: Run focused endpoint tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest \
  server/tests/test_llm_file_edit.py::test_llm_file_edit_job_rejects_when_project_has_active_job \
  server/tests/test_llm_file_edit.py::test_llm_file_edit_job_reconciles_stale_active_job_before_admission \
  -q
```

Expected: PASS.

## Task 4: Preserve Frontend Error Surfacing

**Files:**
- Modify: `ui/src/workflows/shared/projectStorage.test.ts`

- [ ] **Step 1: Add the storage conflict test**

Add this test near the existing LLM edit job storage tests:

```ts
  it('surfaces active LLM edit job conflicts from submit', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({
      success: false,
      error: 'An AI edit is already running for this project. Wait for it to finish before starting another.',
      error_code: 'llm_edit_in_progress',
      retryable: true,
      active_job_id: 'llm-job-running',
      active_job_status: 'running',
    }), { status: 409 }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(
      storage.applyLlmFileEditJob('demo', {
        prompt: 'make a bracket',
        files: [{ id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' }],
      }),
    ).rejects.toThrow('An AI edit is already running for this project. Wait for it to finish before starting another.')
  })
```

- [ ] **Step 2: Run the focused UI test**

Run:

```bash
rtk npm --prefix ui test -- projectStorage.test.ts
```

Expected: PASS. The current storage helper should already surface the server `error` field; no production TypeScript change is required unless this test fails.

## Task 5: Full Verification

**Files:**
- All files changed by Tasks 1-4.

- [ ] **Step 1: Run backend focused suites**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest \
  server/tests/test_repositories.py \
  server/tests/test_llm_file_edit.py \
  server/tests/test_migrations.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend focused suite**

Run:

```bash
rtk npm --prefix ui test -- projectStorage.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run the AI edit live-flow gate if implementing this behavior**

Because this changes AI edit behavior, run a full authenticated live-flow before finalizing implementation:

```bash
scripts/harness-k3s.sh live-flow
```

Expected: authenticated frontend-origin compile and AI edit flow passes. `LIVE_FLOW_COMPILE_ONLY=true` is not acceptable for this change because the admission guard directly touches AI edit submit behavior.

If local runtime, auth, provider credentials, or port-forwarding are unavailable, report the exact blocker and include the focused backend/frontend tests that passed.

## Clarity Gate Self-Assessment

| Check | Result |
|---|---|
| Actionable | Pass: every task names exact files, functions, tests, and commands. |
| Current | Pass: plan is based on current `LlmEditJob`, `LlmEditRepository`, and `start_llm_file_edit_job(...)` flow. |
| Single Source | Pass: active statuses are centralized as `LLM_EDIT_ACTIVE_STATUSES`. |
| Decision, Not Wish | Pass: response code, response body, index shape, and method name are fixed. |
| Prompt-Ready | Pass: snippets are ready for an implementation agent. |
| No Future State | Pass: future strict-locking alternatives are explicitly out of scope. |
| No Fluff | Pass: content is implementation-only. |
| Type Identified | Pass: implementation plan. |
| Anti-patterns Placed | Pass: anti-patterns live in this implementation document. |
| Test Cases Placed | Pass: test cases live in this implementation document. |
| Error Handling Placed | Pass: error handling matrix is included here. |
| Deep Links Present | Pass: current-state references include exact local paths. |
| No Duplicates | Pass: existing docs are referenced, not copied. |

**AI Coder Understandability Score:** 9/10. The only intentional ambiguity is the accepted race window inherent to the required plain-query admission approach.
