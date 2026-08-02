# Generate Design Model Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Generate Design switch among configured GPT-5.6 Sol, Luna, and Terra models while enforcing a server-owned 300,000-character source-context budget.

**Architecture:** Helm and Compose provide one ordered `PI_AGENT_MODELS_JSON` catalog to the API and Pi worker. Shared Python validation resolves the configured default and validates per-job selections; the selected model is persisted and retained through dispatch, progress, results, repair, and retry. The browser renders the API catalog and no longer sends a context tier; the server applies one fixed product constant bounded by the existing operational cap.

**Tech Stack:** React 19, TypeScript, Vite/Vitest, FastAPI, Pydantic v2, SQLAlchemy/Postgres, NATS JetStream, Pi RPC 0.80.6, Helm, Docker Compose, Bash harness tests

**Design:** [`../specs/2026-08-01-generate-design-model-switching-design.md`](../specs/2026-08-01-generate-design-model-switching-design.md)

---

## File Structure

| File | Responsibility |
|---|---|
| `server/core/pi_agent_models.py` | Strict bounded model-catalog parsing and default-membership validation. |
| `server/core/config.py` | Catalog environment setting and construction-time validation. |
| `server/core/llm_file_edit.py` | Fixed 300,000-character product context and request schema without tiers. |
| `server/workflows/intus/usage_server.py` | Configured model discovery. |
| `server/workflows/intus/intus_server.py` | Selected-model validation, persistence, dispatch, metrics, and fixed context use. |
| `server/workflows/intus/pi_agent_job.py` | Worker allowlist validation. |
| `server/workflows/intus/pi_agent_result_consumer.py` | Catalog-aware queued-job republication. |
| `ui/src/workflows/shared/projectStorage.ts` | Browser edit request without `context_tier`. |
| `ui/src/workflows/generate/GenerateDesignWindow.tsx` | Accessible configured-model selector and selected-model submission. |
| Helm, Compose, scripts, `.env`, and configuration docs | One catalog across canonical and adapter runtimes. |

## Task 1: Add the Shared Pi Model Catalog Contract

**Files:**
- Create: `server/core/pi_agent_models.py`
- Create: `server/tests/test_pi_agent_models.py`
- Modify: `server/core/config.py`
- Modify: `server/tests/test_config.py`
- Modify: `server/.env.example` (catalog/obsolete label only; context cap remains Task 4)

- [x] **Step 1: Write failing catalog parser and Settings tests**

Create `server/tests/test_pi_agent_models.py` with tests equivalent to:

```python
import json

import pytest
from pydantic import ValidationError

from core.config import Settings
from core.pi_agent_models import DEFAULT_PI_AGENT_MODELS_JSON, parse_pi_agent_models


def test_default_catalog_is_ordered_sol_luna_terra():
    assert [(item.id, item.label) for item in parse_pi_agent_models(DEFAULT_PI_AGENT_MODELS_JSON)] == [
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "{}",
        "[]",
        json.dumps([{"id": "gpt-5.6-sol", "label": "Sol"}] * 2),
        json.dumps([{"id": "unsafe/model", "label": "Unsafe"}]),
        json.dumps([{"id": f"model-{index}", "label": str(index)} for index in range(21)]),
    ],
)
def test_catalog_rejects_malformed_or_unbounded_values_without_echoing_input(raw):
    with pytest.raises(ValueError) as exc_info:
        parse_pi_agent_models(raw)
    assert "PI_AGENT_MODELS_JSON" in str(exc_info.value)
    assert raw not in str(exc_info.value)


def test_settings_rejects_default_model_outside_catalog():
    with pytest.raises(ValidationError, match="PI_AGENT_MODEL must reference"):
        Settings(
            pi_agent_model="gpt-5.6-terra",
            pi_agent_models_json='[{"id":"gpt-5.6-sol","label":"GPT-5.6 Sol"}]',
        )


@pytest.mark.parametrize(
    "raw,default_model",
    [
        ("SENSITIVE_MALFORMED_CATALOG", "gpt-5.6-sol"),
        ('[{"id":"private-sentinel","label":"Private Sentinel"}]', "gpt-5.6-sol"),
    ],
)
def test_settings_catalog_errors_hide_raw_configuration(raw, default_model):
    with pytest.raises(ValidationError) as exc_info:
        Settings(pi_agent_model=default_model, pi_agent_models_json=raw)
    assert raw not in str(exc_info.value)
```

Update `server/tests/test_config.py` so the default test expects `pi_agent_models_json` and `settings.pi_agent_models`, no longer expects `pi_agent_model_label`, and an override supplies a catalog containing its custom default.

Update `server/.env.example` in the same TDD slice: remove `PI_AGENT_MODEL_LABEL` and add the compact default `PI_AGENT_MODELS_JSON`. Leave `LLM_FILE_EDIT_MAX_CONTEXT_CHARS` unchanged until Task 4.

- [x] **Step 2: Run the catalog tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/tertius-model-switch-uv-cache rtk uv run pytest server/tests/test_pi_agent_models.py server/tests/test_config.py -q
```

Expected: FAIL because `core.pi_agent_models`, `pi_agent_models_json`, and `pi_agent_models` do not exist and `pi_agent_model_label` still exists.

- [x] **Step 3: Implement strict shared catalog parsing**

Create `server/core/pi_agent_models.py` with:

```python
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PI_AGENT_MODELS = 20
MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DEFAULT_PI_AGENT_MODELS_JSON = json.dumps(
    [
        {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"},
        {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna"},
        {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
    ],
    separators=(",", ":"),
)


class PiAgentModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=80)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if MODEL_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("model id has an invalid format")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("model label must not be blank")
        return stripped


def parse_pi_agent_models(raw: str) -> tuple[PiAgentModelOption, ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("PI_AGENT_MODELS_JSON must be valid JSON") from exc
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_PI_AGENT_MODELS:
        raise ValueError("PI_AGENT_MODELS_JSON must contain 1 to 20 models")
    try:
        models = tuple(PiAgentModelOption.model_validate(item) for item in payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("PI_AGENT_MODELS_JSON contains an invalid model entry") from exc
    ids = [model.id for model in models]
    if len(ids) != len(set(ids)):
        raise ValueError("PI_AGENT_MODELS_JSON contains duplicate model ids")
    return models


def validate_default_pi_agent_model(
    raw: str,
    default_model: str,
) -> tuple[PiAgentModelOption, ...]:
    models = parse_pi_agent_models(raw)
    if default_model not in {model.id for model in models}:
        raise ValueError("PI_AGENT_MODEL must reference a model in PI_AGENT_MODELS_JSON")
    return models
```

In `server/core/config.py`, add `hide_input_in_errors=True` to `settings_config()` so Pydantic does not echo raw environment values, import `DEFAULT_PI_AGENT_MODELS_JSON`, `PiAgentModelOption`, and `validate_default_pi_agent_model`; replace `pi_agent_model_label` with `pi_agent_models_json`, validate the default from the existing `model_validator`, and expose:

```python
@property
def pi_agent_models(self) -> tuple[PiAgentModelOption, ...]:
    return validate_default_pi_agent_model(self.pi_agent_models_json, self.pi_agent_model)
```

Keep the intermediate application state coherent with a derived read-only `pi_agent_model_label` property that resolves the configured default's label from `pi_agent_models`. It is not a Pydantic model field or environment setting, so `PI_AGENT_MODEL_LABEL` remains removed; test both the default and a custom catalog/default label. Task 2 removes the endpoint's reliance on this compatibility accessor when it returns the full catalog.

- [x] **Step 4: Run catalog/config tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass with bounded validation messages.

- [x] **Step 5: Commit Task 1**

```bash
rtk git add server/core/pi_agent_models.py server/core/config.py server/tests/test_pi_agent_models.py server/tests/test_config.py server/.env.example
rtk git commit -m "feat: add configurable Pi model catalog"
```

Task 1 evidence (2026-08-02): RED failed with the expected missing module and derived-property errors. Commit `4ee1f8c5` passes 28 focused tests plus targeted Ruff and diff checks. Independent specification and code-quality reviews found no open issues.

## Task 2: Dispatch Selected Models and Fix Context at 300,000 Characters

**Files:**
- Modify: `server/core/llm_file_edit.py`
- Modify: `server/workflows/intus/usage_server.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/tests/test_llm_file_edit_domain.py`
- Modify: `server/tests/test_llm_usage.py`
- Modify: `server/tests/test_llm_file_edit.py`

- [x] **Step 1: Write failing models endpoint, selection, rejection, and context tests**

Update `test_llm_models_endpoint_reflects_pi_agent_availability` to expect all three configured records in order. Add parametrized API submission coverage:

```python
@pytest.mark.parametrize("model_id", ["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra"])
def test_submit_persists_and_dispatches_selected_catalog_model(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    model_id,
):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    commands = []

    async def publish(_settings, command):
        commands.append(command)

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Change length",
            "files": [file_pointer(design)],
            "model_id": model_id,
        },
    )
    assert response.status_code == 202
    job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
    assert job.request_payload["dispatched_model"] == model_id
    assert commands[0].model == model_id
```

Retain the unknown-model test and assert no job/publish occurs. Replace tier tests in `server/tests/test_llm_file_edit_domain.py` with:

```python
def test_file_edit_context_budget_is_fixed_at_300000_characters():
    assert LLM_FILE_EDIT_CONTEXT_CHARS == 300_000


def test_file_edit_request_has_no_context_tier_contract():
    assert "context_tier" not in LlmFileEditInput.model_fields
```

Add a submission test that monkeypatches `select_domain_context_files`, sends legacy `context_tier`, and verifies `max_chars == min(settings.llm_file_edit_max_context_chars, 300_000)`.

Add history serialization coverage for a queued/failed job submitted with omitted or blank `model_id`: `_llm_edit_job_model` must prefer validated result provenance, then persisted `dispatched_model`, and use raw `model_id` only as a legacy fallback. Assert the current job reports `gpt-5.6-sol` before any result exists and retain a legacy fallback test.

- [x] **Step 2: Run the focused API/domain tests and verify RED**

```bash
UV_CACHE_DIR=/tmp/tertius-model-switch-uv-cache rtk uv run pytest server/tests/test_llm_file_edit_domain.py server/tests/test_llm_usage.py server/tests/test_llm_file_edit.py -q
```

Expected: FAIL because the endpoint returns one model, selected models are rejected/overwritten, and tiers still control context.

- [x] **Step 3: Remove the tier contract and resolve the requested catalog model**

In `server/core/llm_file_edit.py`, replace the tier type/function with:

```python
LLM_FILE_EDIT_CONTEXT_CHARS = 300_000
```

Remove `context_tier` from `LlmFileEditInput`.

In `usage_server.llm_models`, return `settings.pi_agent_models` mapped to the existing response shape and `settings.pi_agent_model` as default.

In `start_llm_file_edit_job`, resolve once:

```python
allowed_model_ids = {model.id for model in settings.pi_agent_models}
selected_model = req.model_id or settings.pi_agent_model
if selected_model not in allowed_model_ids:
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": "unsupported_model"},
    )
```

Use `selected_model` for `dispatched_model`, `PiAgentCommand.model`, and the queued metric. Select files with:

```python
max_chars=min(settings.llm_file_edit_max_context_chars, LLM_FILE_EDIT_CONTEXT_CHARS)
```

- [x] **Step 4: Run API/domain tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

- [x] **Step 5: Commit Task 2**

```bash
rtk git add server/core/llm_file_edit.py server/workflows/intus/usage_server.py server/workflows/intus/intus_server.py server/tests/test_llm_file_edit_domain.py server/tests/test_llm_usage.py server/tests/test_llm_file_edit.py
rtk git commit -m "feat: dispatch selected Generate Design models"
```

Task 2 evidence (2026-08-02): RED exposed six endpoint/selection/context failures and three provenance-history failures. Amended commit `355b65d` passes all 39 focused API/domain tests plus targeted Ruff and diff checks. Independent specification and code-quality reviews found no open issues.

## Task 3: Validate Catalog Models in Workers and Queued Reconciliation

**Files:**
- Modify: `server/workflows/intus/pi_agent_job.py`
- Modify: `server/workflows/intus/pi_agent_result_consumer.py`
- Modify: `server/tests/test_pi_agent_job.py`
- Modify: `server/tests/test_pi_agent_result_consumer.py`

- [x] **Step 1: Write failing worker and reconciliation tests**

Add worker tests proving Luna and Terra commands are accepted when present in `settings.pi_agent_models`, while an unknown command is terminated before `run_pi_agent`.

Update `worker_settings()` and every `SimpleNamespace` passed to the worker or queued reconciler so the test double exposes `pi_agent_models=parse_pi_agent_models(DEFAULT_PI_AGENT_MODELS_JSON)`. Add the catalog imports once per test module; do not add production fallbacks for incomplete test doubles.

Add reconciler tests equivalent to:

```python
@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-terra"])
@pytest.mark.asyncio
async def test_queued_reconciliation_republishes_catalog_non_default_model(
    db_session,
    seeded_tenant,
    model,
):
    file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    job = _job(db_session, seeded_tenant, file, _result(seeded_tenant, file))
    payload = dict(job.request_payload)
    payload["dispatch_attempted_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    ).isoformat()
    payload["dispatch_created_at"] = datetime.now(timezone.utc).isoformat()
    payload["dispatched_thinking"] = "medium"
    payload["dispatched_model"] = model
    job.request_payload = payload
    flag_modified(job, "request_payload")
    db_session.commit()
    publisher = Publisher()
    settings = SimpleNamespace(
        pi_agent_request_subject="request",
        pi_agent_request_max_bytes=524288,
        pi_agent_provider="openai-codex",
        pi_agent_model="gpt-5.6-sol",
        pi_agent_models=parse_pi_agent_models(DEFAULT_PI_AGENT_MODELS_JSON),
        pi_agent_thinking="medium",
    )
    assert await republish_queued_pi_agent_jobs(
        db_session, publisher, settings, backoff_seconds=0
    ) == 1
    assert publisher.calls[0][0][1].model == model


@pytest.mark.asyncio
async def test_queued_reconciliation_fails_closed_when_model_left_catalog(
    db_session,
    seeded_tenant,
):
    file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    job = _job(db_session, seeded_tenant, file, _result(seeded_tenant, file))
    payload = dict(job.request_payload)
    payload["dispatch_attempted_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    ).isoformat()
    payload["dispatch_created_at"] = datetime.now(timezone.utc).isoformat()
    payload["dispatched_thinking"] = "medium"
    payload["dispatched_model"] = "retired-model"
    job.request_payload = payload
    flag_modified(job, "request_payload")
    db_session.commit()
    settings = SimpleNamespace(
        pi_agent_request_subject="request",
        pi_agent_request_max_bytes=524288,
        pi_agent_provider="openai-codex",
        pi_agent_model="gpt-5.6-sol",
        pi_agent_models=parse_pi_agent_models(DEFAULT_PI_AGENT_MODELS_JSON),
        pi_agent_thinking="medium",
    )
    assert await republish_queued_pi_agent_jobs(
        db_session, Publisher(), settings, backoff_seconds=0
    ) == 0
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "dispatch_config_error"
```

- [x] **Step 2: Run focused worker/reconciler tests and verify RED**

```bash
UV_CACHE_DIR=/tmp/tertius-model-switch-uv-cache rtk uv run pytest server/tests/test_pi_agent_job.py server/tests/test_pi_agent_result_consumer.py -q
```

Expected: non-default configured models are rejected by equality checks.

- [x] **Step 3: Replace fixed-model equality with catalog membership**

In the worker:

```python
allowed_models = {model.id for model in settings.pi_agent_models}
if command.model not in allowed_models or command.thinking != settings.pi_agent_thinking:
    logger.warning("Rejected Pi agent command with unsupported runtime selection")
    await msg.term()
    return
```

In queued reconciliation, validate `payload["dispatched_model"]` against the same catalog instead of comparing it with `settings.pi_agent_model`. Do not change result/progress comparisons against persisted dispatch provenance.

- [x] **Step 4: Run focused worker/reconciler tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

- [x] **Step 5: Commit Task 3**

```bash
rtk git add server/workflows/intus/pi_agent_job.py server/workflows/intus/pi_agent_result_consumer.py server/tests/test_pi_agent_job.py server/tests/test_pi_agent_result_consumer.py
rtk git commit -m "feat: validate Pi jobs against model catalog"
```

Task 3 evidence (2026-08-02): RED produced four expected non-default catalog failures with 97 tests already passing. Commit `e69c6bb` passes all 101 focused worker/reconciler tests plus targeted Ruff and diff checks. Independent specification and code-quality reviews found no open issues.

## Task 4: Wire the Catalog Through Helm and Compose

**Files:**
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `infra/charts/tertius/templates/pi-agent-worker.yaml`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.parity.yml`
- Modify: `server/.env.example`
- Modify: `scripts/test-deployment-config.sh`
- Modify: `scripts/check-runtime-parity.sh`
- Modify: `docs/configuration-and-secrets.md`
- Modify: `docs/harness/runtime-parity.md`

- [x] **Step 1: Add failing deployment and parity assertions**

Extend `scripts/test-deployment-config.sh` to require the `PI_AGENT_MODELS_JSON` ConfigMap key and each ordered ID/label pair (`gpt-5.6-sol`/`GPT-5.6 Sol`, then Luna, then Terra) in the rendered value without depending on Helm's YAML quote style. Require the worker to use `configMapKeyRef` with `key: PI_AGENT_MODELS_JSON`, require `PI_AGENT_MODEL: "gpt-5.6-sol"`, and reject `PI_AGENT_MODEL_LABEL`.

Extend `scripts/check-runtime-parity.sh` to verify API and worker catalog presence in Helm, Compose dev, and Compose parity and to reject the obsolete label variable.

- [x] **Step 2: Run deployment/parity checks and verify RED**

```bash
rtk bash scripts/test-deployment-config.sh
rtk bash scripts/check-runtime-parity.sh
```

Expected: FAIL because the catalog is not rendered and the obsolete label setting remains.

- [x] **Step 3: Add one ordered values catalog and render/inject it everywhere**

In `values.yaml` replace `piAgentModelLabel` with:

```yaml
piAgentModels:
  - id: gpt-5.6-sol
    label: GPT-5.6 Sol
  - id: gpt-5.6-luna
    label: GPT-5.6 Luna
  - id: gpt-5.6-terra
    label: GPT-5.6 Terra
```

Render the ConfigMap with:

```yaml
PI_AGENT_MODELS_JSON: {{ .Values.app.config.piAgentModels | toJson | quote }}
```

Make the Pi worker consume that exact ConfigMap key rather than rendering a second value:

```yaml
- name: PI_AGENT_MODELS_JSON
  valueFrom:
    configMapKeyRef:
      name: {{ include "tertius.configName" . }}
      key: PI_AGENT_MODELS_JSON
```

Replace `PI_AGENT_MODEL_LABEL` in Compose dev/parity API environments and add the identical catalog to their Pi worker environments. The catalog value is already present in `server/.env.example` from Task 1; raise that file's existing `LLM_FILE_EDIT_MAX_CONTEXT_CHARS=80000` safety cap to `2000000`, matching the runtime default so the fixed 300,000-character product budget is effective in direct local runs.

Document that catalog entries are non-secret deployment configuration, Sol is the default, selection is persisted per job, and end users cannot configure the fixed 300,000-character source budget.

- [x] **Step 4: Run deployment/parity checks and verify GREEN**

Run the Step 2 commands. Expected: both exit 0.

- [x] **Step 5: Commit Task 4**

```bash
rtk git add infra/charts/tertius/values.yaml infra/charts/tertius/templates/configmap.yaml infra/charts/tertius/templates/pi-agent-worker.yaml docker-compose.yml docker-compose.parity.yml server/.env.example scripts/test-deployment-config.sh scripts/check-runtime-parity.sh docs/configuration-and-secrets.md docs/harness/runtime-parity.md
rtk git commit -m "feat: configure Generate Design model catalog"
```

Task 4 evidence (2026-08-02): RED produced six intentional static-contract failures across the missing Helm catalog/key reference and obsolete label settings. Commit `b9bc24e` passes deployment configuration, runtime-parity, shell syntax, and diff checks. Independent specification and code-quality reviews found no open issues.

## Task 5: Add the Generate Design Model Selector and Remove Context Controls

**Files:**
- Modify: `ui/src/workflows/shared/projectStorage.ts`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`

- [ ] **Step 1: Write failing model-switching and fixed-context component tests**

Change the default fixture to all three models. Replace the fixed-label test with:

```tsx
it('selects the configured default model and allows switching models', async () => {
  render(<GenerateDesignWindow />)
  await screen.findByText('Latest model viewer')
  openGenerateDesignConversation()

  const selector = await screen.findByRole('combobox', { name: 'AI model' })
  expect(selector).toHaveValue('gpt-5.6-sol')
  expect(within(selector).getAllByRole('option')).toHaveLength(3)
  fireEvent.change(selector, { target: { value: 'gpt-5.6-terra' } })
  expect(selector).toHaveValue('gpt-5.6-terra')
  expect(screen.queryByRole('combobox', { name: 'AI context size' })).not.toBeInTheDocument()
})
```

Update the generation test to select Terra and assert:

```tsx
expect(storage.applyLlmFileEditJob).toHaveBeenCalledWith('project_a', expect.objectContaining({
  model_id: 'gpt-5.6-terra',
}))
expect(storage.applyLlmFileEditJob.mock.calls[0][1]).not.toHaveProperty('context_tier')
```

Extend the compile-repair test to submit with Terra, change the selector to Luna before the first compile reaches its repairable failure, and verify the repair request still uses Terra and omits `context_tier`. This proves the repair uses the submitted job's model snapshot rather than live selector state.

Add disabled-catalog coverage proving no enabled option means Generate remains disabled. Add a success-then-refresh-failure regression (and an empty refreshed response variant) that first loads a valid catalog, reruns the model-loading effect, and verifies the prior catalog and `selectedModelId` are cleared so a stale model cannot remain selectable.

- [ ] **Step 2: Run Generate Design tests and verify RED**

```bash
rtk npm --prefix ui test -- src/workflows/generate/GenerateDesignWindow.test.tsx
```

Expected: FAIL because the model is static and the context selector/request remain.

- [ ] **Step 3: Implement the selector and remove client context policy**

Remove `LlmEditContextTier` from `projectStorage.ts` and `context_tier` from the request type. In `GenerateDesignWindow.tsx`, remove the tier import, choices, state, callback dependencies, request fields, and context settings row.

At normal submission, snapshot `selectedModel.id` into a local `submittedModelId`, store it in the existing `ChatMessage.model` field on the pending assistant message, and use that local value in the initial request. During automatic repair, use `currentMessage.model`; if it is absent, fail the repair with a bounded “Original AI model is unavailable” message instead of reading the current selector. Hydrated assistant messages already populate `model` from persisted job history.

Replace the static model row with:

```tsx
<div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3 text-xs">
  <label htmlFor="generate-design-model" className="font-semibold text-slate-200">
    AI model
  </label>
  <select
    id="generate-design-model"
    aria-label="AI model"
    value={selectedModel?.id || ''}
    onChange={event => setSelectedModelId(event.currentTarget.value)}
    className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-cyan-500 disabled:cursor-not-allowed disabled:text-slate-500"
  >
    {llmModels.map(model => (
      <option key={model.id} value={model.id} disabled={!model.enabled}>
        {model.label}
      </option>
    ))}
  </select>
</div>
```

Model loading must retain a current enabled selection, otherwise choose the enabled default, otherwise the first enabled model, otherwise `''`. Define `selectedModel` only from the selected ID; do not fall back to a disabled first entry. Use `selectedModel?.enabled` in Generate enablement. On an empty response or request failure, clear both `llmModels` and `selectedModelId` before showing the existing bounded error; never retain a previously loaded catalog after discovery fails.

- [ ] **Step 4: Run Generate Design tests and verify GREEN**

Run the Step 2 command. Expected: all Generate Design tests pass.

- [ ] **Step 5: Run frontend type, lint, and build gates**

```bash
rtk npm --prefix ui run typecheck
rtk npm --prefix ui run lint
rtk npm --prefix ui run build
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit Task 5**

```bash
rtk git add ui/src/workflows/shared/projectStorage.ts ui/src/workflows/generate/GenerateDesignWindow.tsx ui/src/workflows/generate/GenerateDesignWindow.test.tsx
rtk git commit -m "feat: switch Generate Design models"
```

## Task 6: Integrate, Review, and Validate the Full AI Edit Flow

**Files:**
- Modify: `docs/superpowers/plans/2026-08-02-generate-design-model-switching.md` (checkboxes and evidence)

- [ ] **Step 1: Run the full focused regression set**

```bash
UV_CACHE_DIR=/tmp/tertius-model-switch-uv-cache rtk uv run pytest server/tests/test_pi_agent_models.py server/tests/test_config.py server/tests/test_llm_file_edit_domain.py server/tests/test_llm_usage.py server/tests/test_llm_file_edit.py server/tests/test_pi_agent_job.py server/tests/test_pi_agent_result_consumer.py -q
rtk npm --prefix ui test -- src/workflows/generate/GenerateDesignWindow.test.tsx src/workflows/shared/projectStorage.test.ts
rtk bash scripts/test-deployment-config.sh
rtk bash scripts/check-runtime-parity.sh
```

Expected: all feature-focused tests pass. Record the known untouched-master `server/pi` U-022 baseline separately if the complete Pi package test is run.

- [ ] **Step 2: Run repository quality gates**

```bash
UV_CACHE_DIR=/tmp/tertius-model-switch-uv-cache rtk uv run ruff check server
UV_CACHE_DIR=/tmp/tertius-model-switch-uv-cache rtk uv run pytest server/tests -q
rtk npm --prefix ui test
rtk npm --prefix ui run typecheck
rtk npm --prefix ui run lint
rtk npm --prefix ui run build
```

Expected: all gates pass, except any reproduced and explicitly documented untouched-master baseline failure.

- [ ] **Step 3: Request specification and code-quality review**

Dispatch a code-review subagent with the approved design, this plan, base SHA `5035949`, and current head. Fix every Critical and Important finding, then rerun affected tests.

- [ ] **Step 4: Run the isolated k3s authenticated live-flow**

Use the local-values smoke release described in `docs/harness/local-harness.md` and run:

```bash
KUBECONFIG=/home/johnson/.kube/config NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke KEDA_ENABLED=true rtk scripts/harness-k3s.sh up
KUBECONFIG=/home/johnson/.kube/config NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke rtk scripts/harness-k3s.sh live-flow
```

Do not set `LIVE_FLOW_COMPILE_ONLY=true`. In the browser, verify Sol/Luna/Terra are present, the context selector is absent, select a non-default model, submit a real edit, observe terminal completion and compile artifact, and inspect console/network failures. Verify the persisted/result model matches the selection without exposing prompts, source, or credentials.

- [ ] **Step 5: Update plan evidence and commit verification notes**

Mark completed checkboxes and append exact commands/results plus any blocker. Then:

```bash
rtk git add docs/superpowers/plans/2026-08-02-generate-design-model-switching.md
rtk git commit -m "docs: record model switching verification"
```

- [ ] **Step 6: Verify final repository state**

```bash
rtk git status --short --branch
rtk git log --oneline --decorate -8
rtk git diff 5035949...HEAD --check
```

Expected: clean feature worktree, intentional commits only, and no whitespace errors.
