# Billing/Usage Tracking for Compile Jobs

## Context

Currently the codebase has zero billing or cost-tracking for compile jobs. The `CompileJob` model captures `created_at`, `finished_at`, `export_format`, `status`, and `attempt_count`; `Artifact` captures `byte_size`. Worker timing data (`worker_started_at`, `worker_finished_at`) arrives on `CompileResultPayload` from the worker. None of this is used for cost calculation or usage aggregation. This plan adds per-job cost tracking, per-tenant usage aggregation, and admin-facing usage dashboard endpoints (no external billing integration).

## Approach

- **Cost capture point**: `apply_compile_result()` in `compile_result_consumer.py`, after the job is finalized
- **Data model**: New `compile_usage_records` table — one row per completed job with computed cost. Aggregation via SQL `GROUP BY` over this table.
- **Rate card**: Configurable via env vars (pydantic-settings), not DB-stored. Rates are snapshotted onto each usage record so historical data stays accurate.
- **Auth**: New `require_tenant_owner` dependency — only tenant owners can see usage data

---

## Implementation Steps

### 1. Rate Card Configuration

**File: `server/core/config.py`** — add to `Settings` class:
- `billing_rate_cents_per_hour: int` (default 100 = $1.00/hr base rate)
- `billing_format_multiplier_stl: float` (default 1.0)
- `billing_format_multiplier_step: float` (default 1.5)
- `billing_format_multiplier_gltf: float` (default 2.0)
- `billing_format_multiplier_glb: float` (default 2.0)

**Files: `infra/charts/tertius/templates/configmap.yaml`, `infra/charts/tertius/values.yaml`** — add corresponding env vars

### 2. Database Model

**File: `server/core/models.py`** — add `CompileUsageRecord` model after `CompileJob`:
- `id`, `tenant_id` (indexed), `project_id`, `compile_job_id`
- Composite FK on `(compile_job_id, project_id, tenant_id)` → `compile_jobs(id, project_id, tenant_id)` with CASCADE. This matches the `Artifact` and `CompileJobFile` FK pattern — `compile_jobs` uses a composite unique constraint, so single-column FK won't work.
- `requested_by`, `export_format`, `status`
- `compute_duration_seconds` (float) — derived as `(result.worker_finished_at - result.worker_started_at).total_seconds()` from the `CompileResultPayload`
- `artifact_byte_size` (int, 0 for failed)
- `cost_cents` (int)
- `base_rate_cents_per_hour`, `format_multiplier` (rate card snapshot)
- `created_at`

**File: `server/migrations/versions/0005_compile_usage_records.py`** — new Alembic migration creating the table with indexes on `tenant_id`, `project_id`, `compile_job_id`, and composite `(tenant_id, created_at)`. Include `upgrade()` and `downgrade()` functions matching the existing migration pattern.

### 3. Cost Calculation Logic

**File: `server/core/billing.py`** (new) — pure functions:
- `compute_cost_cents(duration_seconds, export_format, settings)` — `(duration_seconds / 3600) × base_rate × format_multiplier`, rounded to nearest int. Sub-cent costs round to zero; no minimum per-job charge.
- `get_format_multiplier(export_format, settings)` — lookup helper
- Timing data comes from `CompileResultPayload.worker_started_at` and `worker_finished_at` (not from the `CompileJob` model), passed explicitly to the cost function

### 4. Repository Methods

**File: `server/core/repositories.py`**:
- Add `CompileRepository.record_usage()` — inserts a `CompileUsageRecord`
- Add new `UsageRepository` class with methods:
  - `total_summary()` — aggregate counts/cost/duration/bytes
  - `daily_breakdown(days=30)` — `GROUP BY date_trunc('day', created_at)`
  - `monthly_breakdown(months=12)` — `GROUP BY date_trunc('month', created_at)`
  - `project_breakdown()` — `GROUP BY project_id`
  - `format_breakdown()` — `GROUP BY export_format` (cost-per-format is core to billing visibility)
  - `recent_jobs(limit=50)` — latest usage records, join with `AppUser` to surface `requested_by` username

### 5. Hook Into Result Processing

**File: `server/workflows/intus/compile_result_consumer.py`**:
- Add `_record_usage_if_applicable(db, result, job, settings)` helper that:
  - Reads `worker_started_at` and `worker_finished_at` from the `CompileResultPayload` (not from CompileJob — these fields live on the NATS message only)
  - Computes duration = `(worker_finished_at - worker_started_at).total_seconds()`, clamps negative to 0
  - Calls `compute_cost_cents()` and `usage_repo.record_usage()`
- Call it in both success and failure paths within `apply_compile_result()`, after `repo.finish_job()`
- Same DB transaction as the job finish — no partial writes

### 6. API Endpoints

**File: `server/core/auth.py`** — add `require_tenant_owner` dependency. Since `AuthContext` only carries `user_id`/`tenant_id` (not role), this must query `TenantMembership` for the role: `db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == ctx.tenant_id, TenantMembership.user_id == ctx.user_id))`. Returns 403 if missing or role != `"owner"`.

**File: `server/workflows/intus/usage_server.py`** (new) — `APIRouter(prefix="/usage")` (the Intus app is mounted at `/api/intus`, so the full paths will be `/api/intus/usage/...`):
- `GET /summary` — total jobs, cost, compute time, artifact bytes
- `GET /daily?days=30` — daily aggregated breakdown
- `GET /monthly?months=12` — monthly aggregated breakdown
- `GET /by-project` — per-project breakdown
- `GET /by-format` — per-format breakdown
- `GET /recent?limit=50` — recent usage records
- All endpoints use `require_tenant_owner` dependency

**File: `server/workflows/intus/intus_server.py`** — include the usage router via `app.include_router(usage_router)`. This is an `APIRouter`, not a separate FastAPI app, so it uses `include_router` not `app.mount()`. Keeping it under the intus app keeps routing consistent since usage data is intus-specific.

### 7. Pydantic Response Schemas

**File: `server/core/usage_messages.py`** (new):
- `UsageSummaryResponse`, `DailyUsageItem`, `UsageRecordResponse` — typed API output

### 8. Frontend Usage Dashboard

**File: `ui/src/workflows/intus/ui/UsageTab.tsx`** (new):
- Summary cards: total jobs, total cost, total compute hours
- Daily usage chart (recharts bar/line chart, last 30 days)
- Format breakdown chart (pie or bar by export_format)
- Recent jobs table: date, format, status, duration, artifact size, cost
- Uses existing `apiFetch` pattern from `CompilerTab.tsx`

**File: `ui/src/workflows/intus/IntusWindow.tsx`** — add sub-tab toggle between "Compiler" and "Usage" tabs within the Intus window. Check `/api/intus/usage/summary` for 200 vs 403; hide the Usage sub-tab if 403. The Usage tab is a sub-tab inside Intus, not a top-level tab in `App.tsx`, since usage data is intus-specific.

### 9. Testing

- **`server/tests/test_billing.py`** — unit tests for `compute_cost_cents` (various formats, durations, edge cases)
- **`server/tests/test_compile_result_consumer.py`** — add tests verifying `CompileUsageRecord` creation on success/failure
- **`server/tests/test_usage_endpoints.py`** — API integration tests (populate records, query endpoints, assert shapes)
- **`ui/src/workflows/intus/ui/UsageTab.test.tsx`** — render states: loading, empty, populated, 403 hidden-tab

### 10. Helm Config

- **`infra/charts/tertius/templates/configmap.yaml`** — add billing env vars
- **`infra/charts/tertius/values.yaml`** — add production defaults

---

## Build Order

1. Rate card config (`config.py` + Helm)
2. Model + migration
3. `billing.py` cost functions
4. Repository methods
5. Hook into `apply_compile_result()`
6. Pydantic schemas
7. API endpoints + auth dependency + include router in `intus_server.py`
8. Frontend `UsageTab` + sub-tab toggle in `IntusWindow.tsx`
9. Tests (incrementally alongside each phase)

## Edge Cases Handled

- **Missing timing data**: `worker_started_at` and `worker_finished_at` are required `datetime` fields on `CompileResultPayload` (Pydantic guarantees they are non-None after validation), so this is a defensive guard that shouldn't trigger in practice. Skip usage record if either is None (log warning).
- **Zero/negative duration**: clamped to 0
- **Sub-cent cost**: rounded to 0 (no minimum per-job charge)
- **Stale-running timeout**: `fail_stale_running_jobs()` in `compile_result_consumer.py:178` finishes jobs whose leases expire before a worker returns a result. These jobs never receive a `CompileResultPayload` and thus have no timing data — intentionally excluded from usage records (no cost to compute).
- **Double-completion**: prevented by existing idempotency check in `apply_compile_result` (rolls back if job already terminal)
- **Rate card changes**: snapshot on each record keeps history accurate
- **Non-owner access**: 403 → frontend hides the Usage sub-tab

## Verification

1. Run `rtk pytest server/tests/test_billing.py -v` — cost calculation unit tests pass
2. Run `rtk pytest server/tests/test_compile_result_consumer.py -v` — usage record creation verified
3. Run `rtk pytest server/tests/test_usage_endpoints.py -v` — API responses validated
4. Trigger a compile job, verify `compile_usage_records` table gets a row
5. Hit `/api/intus/usage/summary` as tenant owner → 200 with correct data
6. Hit `/api/intus/usage/summary` as non-owner → 403
7. Open Usage tab in UI → see summary cards, chart, recent jobs table
