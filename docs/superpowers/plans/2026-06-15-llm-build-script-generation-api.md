# LLM Build Script Generation API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated frontend-callable API that uses the OpenAI Python SDK against DeepSeek to generate an Intus build script, then publishes token usage and request metadata to NATS for billing.

**Architecture:** The existing FastAPI monolith remains the trusted boundary. The Intus API performs the LLM call synchronously after authenticated tenant scoping, DB-backed quota/rate checks, and project validation. A successful provider response must persist a tenant/user/project usage record and publish a billing usage event to a separate JetStream stream before returning the generated script. Helm stores non-secret LLM settings in the ConfigMap and the provider API key in an API-only dedicated Secret.

**Tech Stack:** Python, FastAPI, Pydantic, OpenAI Python SDK, NATS JetStream, Helm, pytest.

---

## Stream Coding Clarity Gate

### Problem

Intus needs a backend API that lets the frontend request a generated build script from an LLM without exposing the provider key to the browser. Because this is a paid endpoint, the API must enforce per-tenant/user limits before calling the provider and durably record token usage after provider success.

### Success Criteria

- The frontend can call `POST /api/intus/projects/{name}/build-script/generate` with bearer auth.
- The API uses `AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)` and model `settings.llm_model`.
- Defaults are `LLM_BASE_URL=https://api.deepseek.com` and `LLM_MODEL=deepseek-v4-flash`.
- `LLM_API_KEY` is read from an API-only dedicated Kubernetes Secret/env and never rendered into the ConfigMap, UI pod, or compile Job.
- The backend validates token issuer, signature, and audience/authorized-party before any LLM route work.
- Requests are rejected before the provider call when the tenant/user exceeds configured LLM request or token quota.
- Successful provider responses persist one DB usage record and publish one NATS billing event with token counts, prompt, tenant/user IDs, project ID, model, provider request ID if available, and supplied metadata.
- Billing persistence or publish failure returns a 503 instead of silently returning an unbilled paid result.
- Missing provider key returns a clear 503.
- Tests cover config, payload shape, NATS stream setup, LLM wrapper behavior, endpoint behavior, and Helm rendering.

### Non-Goals

- Do not implement external payment-provider aggregation or invoicing.
- Do not stream partial LLM responses.
- Do not allow the browser to choose `base_url`, model, or API key.
- Do not add LLM credentials to compile Job pods.
- Do not migrate the backend to Quarkus in this pass; this repo's active API is FastAPI/Python.

### Approach Options

1. **Recommended: synchronous LLM call, DB-backed usage guard, required billing event**
   - Lowest product latency and simplest frontend flow.
   - DB-backed request/token limits prevent avoidable provider spend before the LLM call.
   - Failure mode is explicit: provider failures fail the request; billing persistence/publish failures fail the request.

2. **Queued LLM generation through NATS**
   - Better for long-running generation and retries.
   - Requires a job table, polling endpoint, worker, and retry semantics that the request did not ask for.
   - Defer until generation latency or reliability requires it.

3. **Frontend calls DeepSeek directly**
   - Simple backend work, but exposes the provider key and loses trusted billing metadata.
   - Reject.

## File Structure

- Create `server/core/billing_messages.py`
  - Pydantic usage event contract, byte-size helper, deterministic billing message ID.
- Create `server/core/llm_usage.py`
  - DB-backed LLM request ledger, rate/quota checks, usage persistence, and paid-endpoint error helpers.
- Create `server/core/llm_client.py`
  - OpenAI-compatible provider wrapper, prompt construction, response parsing, token usage extraction, billing event publication helper.
- Modify `server/core/config.py`
  - Add LLM, quota/rate, and billing NATS settings.
- Modify `server/core/models.py` and add Alembic migration
  - Add LLM usage record table for rate limiting, quota checks, and durable paid usage evidence.
- Modify `server/core/nats_client.py`
  - Add billing stream reconciliation while preserving compile stream behavior.
- Modify `server/workflows/intus/intus_server.py`
  - Add request/response models and the authenticated generate endpoint.
- Modify `server/requirements.txt`
  - Add `openai`.
- Modify `server/.env.example`
  - Add local LLM and billing env names.
- Modify `docker-compose.yml`
  - Add API-only local LLM env names and assert compile runner does not receive provider config.
- Modify Helm files:
  - `infra/charts/tertius/values.yaml`
  - `infra/charts/tertius/values-local.yaml`
  - `infra/charts/tertius/templates/configmap.yaml`
  - `infra/charts/tertius/templates/secrets.yaml`
  - `infra/charts/tertius/templates/api.yaml`
  - `infra/charts/tertius/README.md`
- Modify tests:
  - `server/tests/test_config.py`
  - `server/tests/test_nats_client.py`
  - Create `server/tests/test_billing_messages.py`
  - Create `server/tests/test_llm_client.py`
  - Create `server/tests/test_build_script_generation.py`
  - `scripts/test-deployment-config.sh`

## API Contract

### Request

`POST /api/intus/projects/{name}/build-script/generate`

```json
{
  "prompt": "Generate a purlin bracket with 4 bolt holes",
  "active_file": "design.py",
  "current_code": "import build123d as bd\n",
  "metadata": {
    "source": "compiler_tab",
    "interaction_id": "ui-123"
  }
}
```

### Response

```json
{
  "success": true,
  "script": "import build123d as bd\n...",
  "model": "deepseek-v4-flash",
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 200,
    "total_tokens": 300
  }
}
```

### Error Responses

| Case | Status | Body |
|------|--------|------|
| Missing/invalid auth | 401 | Existing auth behavior |
| Invalid project or filename | 400 | `{"success": false, "error": "..."}` |
| Project not found | 404 | `{"success": false, "error": "Project not found"}` |
| Missing `LLM_API_KEY` | 503 | `{"success": false, "error": "LLM provider is not configured", "retryable": false}` |
| Quota/rate exceeded | 429 | `{"success": false, "error": "LLM usage limit exceeded", "retryable": true}` |
| Provider timeout/error | 503 | `{"success": false, "error": "LLM generation failed", "retryable": true}` |
| Billing persistence/publish failure | 503 | `{"success": false, "error": "LLM billing failed", "retryable": true}` |

## Billing Event Contract

Subject: `tertius.billing.usage.llm.tokens`

Stream: `TERTIUS_BILLING`

Message ID: `billing-usage:{event_id}`

```python
class LlmTokenUsageEvent(BaseModel):
    event_id: UUID
    tenant_id: UUID
    user_id: UUID
    project_id: UUID | None = None
    workflow: str
    operation: str
    provider: str
    model: str
    prompt: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    occurred_at: datetime
    provider_request_id: str | None = None
    metadata: dict[str, str] = {}
```

## Anti-Patterns

| Do Not | Do Instead | Why |
|--------|------------|-----|
| Put `LLM_API_KEY` in a ConfigMap | Store it in a dedicated LLM Secret and expose it only to the API container | Provider keys are sensitive |
| Add LLM env to compile Jobs | Keep LLM env on API only | Compile Jobs are intentionally NATS-only |
| Let browser choose `base_url` or model | Use server config | Prevent provider/key abuse and billing spoofing |
| Hide billing persistence or publish failures after provider success | Return `503` with `LLM billing failed` and log reconciliation context | Paid usage must not succeed silently without durable attribution |
| Publish billing usage without tenant/user IDs | Include `AuthContext` IDs | Billing needs trusted attribution |
| Mix billing events into `TERTIUS_COMPILE` | Use `TERTIUS_BILLING` | Separate consumers, limits, and lifecycle |
| Store generated script automatically | Return it to frontend for user review | Generation should not mutate project state without explicit save |
| Put `LLM_API_KEY` in the shared app Secret consumed by UI `envFrom` | Use a dedicated LLM Secret referenced only by the API container | The browser-facing UI pod must not receive provider credentials |
| Open a NATS connection for billing without a close path | Close and flush the connection in the endpoint after generation returns or fails | Avoid leaking one NATS connection per generation request |
| Use in-memory rate limiting for paid LLM usage | Use DB-backed request and token checks keyed by tenant/user | API replicas and restarts must not reset paid usage controls |
| Return generated scripts when billing persistence fails | Return a 503 and log enough context to reconcile provider usage | Paid usage must be durably attributable before the user receives the result |

## Tasks

### Task 0: JWT Issuer Enforcement

**Files:**
- Modify: `server/core/auth.py`
- Test: `server/tests/test_auth.py`

- [x] **Step 1: Add failing issuer validation test**

Add a test that signs a token with the trusted test key but sets `iss` to a different realm, then asserts `decode_keycloak_token()` raises `jwt.InvalidIssuerError`.

- [x] **Step 2: Verify test fails before implementation**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_auth.py -q
```

Expected before implementation: `test_decode_keycloak_token_rejects_wrong_issuer` fails because `verify_iss=False` accepts the token.

- [x] **Step 3: Enforce issuer validation**

In `server/core/auth.py`, pass `issuer=settings.keycloak_issuer.rstrip("/")` to both `jwt.decode()` calls and remove `verify_iss=False`.

- [x] **Step 4: Verify auth tests pass**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_auth.py -q
```

Expected after implementation: auth tests pass.

### Task 1: Configuration and Dependency

**Files:**
- Modify: `server/requirements.txt`
- Modify: `server/core/config.py`
- Modify: `server/.env.example`
- Test: `server/tests/test_config.py`

- [ ] **Step 1: Add failing config default test**

Add to `server/tests/test_config.py`:

```python
def test_settings_exposes_llm_and_billing_defaults(monkeypatch):
    for env_var in (
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_USER_RATE_LIMIT_PER_MINUTE",
        "LLM_TENANT_RATE_LIMIT_PER_MINUTE",
        "LLM_TENANT_DAILY_TOKEN_QUOTA",
        "LLM_USER_DAILY_TOKEN_QUOTA",
        "BILLING_STREAM_NAME",
        "BILLING_LLM_USAGE_SUBJECT",
        "BILLING_MAX_BYTES",
    ):
        monkeypatch.delenv(env_var, raising=False)

    settings = Settings()

    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-v4-flash"
    assert settings.llm_api_key == ""
    assert settings.llm_timeout_seconds == 60
    assert settings.llm_max_output_tokens == 2048
    assert settings.llm_user_rate_limit_per_minute == 10
    assert settings.llm_tenant_rate_limit_per_minute == 60
    assert settings.llm_tenant_daily_token_quota == 100000
    assert settings.llm_user_daily_token_quota == 25000
    assert settings.billing_stream_name == "TERTIUS_BILLING"
    assert settings.billing_llm_usage_subject == "tertius.billing.usage.llm.tokens"
    assert settings.billing_max_bytes == 262144
```

- [ ] **Step 2: Add failing env override test**

Add to `server/tests/test_config.py`:

```python
def test_settings_allows_llm_and_billing_overrides(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "1024")
    monkeypatch.setenv("LLM_USER_RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("LLM_TENANT_RATE_LIMIT_PER_MINUTE", "25")
    monkeypatch.setenv("LLM_TENANT_DAILY_TOKEN_QUOTA", "50000")
    monkeypatch.setenv("LLM_USER_DAILY_TOKEN_QUOTA", "10000")
    monkeypatch.setenv("BILLING_STREAM_NAME", "CUSTOM_BILLING")
    monkeypatch.setenv("BILLING_LLM_USAGE_SUBJECT", "custom.billing.llm")
    monkeypatch.setenv("BILLING_MAX_BYTES", "65536")

    settings = Settings()

    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-v4-flash"
    assert settings.llm_api_key == "secret-key"
    assert settings.llm_timeout_seconds == 30
    assert settings.llm_max_output_tokens == 1024
    assert settings.llm_user_rate_limit_per_minute == 5
    assert settings.llm_tenant_rate_limit_per_minute == 25
    assert settings.llm_tenant_daily_token_quota == 50000
    assert settings.llm_user_daily_token_quota == 10000
    assert settings.billing_stream_name == "CUSTOM_BILLING"
    assert settings.billing_llm_usage_subject == "custom.billing.llm"
    assert settings.billing_max_bytes == 65536
```

- [ ] **Step 3: Run config tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_config.py -q
```

Expected: fails because the new settings do not exist.

- [ ] **Step 4: Add settings fields**

Add to `Settings` in `server/core/config.py` after compile settings:

```python
    llm_base_url: str = Field(default="https://api.deepseek.com")
    llm_model: str = Field(default="deepseek-v4-flash")
    llm_api_key: str = Field(default="")
    llm_timeout_seconds: int = Field(default=60)
    llm_max_output_tokens: int = Field(default=2048)
    llm_user_rate_limit_per_minute: int = Field(default=10)
    llm_tenant_rate_limit_per_minute: int = Field(default=60)
    llm_tenant_daily_token_quota: int = Field(default=100000)
    llm_user_daily_token_quota: int = Field(default=25000)
    billing_stream_name: str = Field(default="TERTIUS_BILLING")
    billing_llm_usage_subject: str = Field(default="tertius.billing.usage.llm.tokens")
    billing_max_bytes: int = Field(default=256 * 1024)
```

- [ ] **Step 5: Add OpenAI SDK dependency**

Add to `server/requirements.txt`:

```text
openai
```

- [ ] **Step 6: Update env example**

Append to `server/.env.example`:

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=
LLM_TIMEOUT_SECONDS=60
LLM_MAX_OUTPUT_TOKENS=2048
LLM_USER_RATE_LIMIT_PER_MINUTE=10
LLM_TENANT_RATE_LIMIT_PER_MINUTE=60
LLM_TENANT_DAILY_TOKEN_QUOTA=100000
LLM_USER_DAILY_TOKEN_QUOTA=25000
BILLING_STREAM_NAME=TERTIUS_BILLING
BILLING_LLM_USAGE_SUBJECT=tertius.billing.usage.llm.tokens
BILLING_MAX_BYTES=262144
```

- [ ] **Step 7: Run config tests and verify pass**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_config.py -q
```

Expected: all config tests pass.

- [ ] **Step 8: Commit**

```bash
git add server/requirements.txt server/core/config.py server/.env.example server/tests/test_config.py
git commit -m "feat: add llm generation configuration"
```

### Task 2: Billing Message Contract

**Files:**
- Create: `server/core/billing_messages.py`
- Test: `server/tests/test_billing_messages.py`

- [ ] **Step 1: Write failing billing message tests**

Create `server/tests/test_billing_messages.py`:

```python
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
    serialized_billing_message_size,
)


def test_llm_token_usage_event_serializes_trusted_billing_fields():
    event = LlmTokenUsageEvent(
        event_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        project_id=uuid4(),
        workflow="intus",
        operation="build_script.generate",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt="make a bracket",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        provider_request_id="chatcmpl-123",
        metadata={"source": "compiler_tab"},
    )

    payload = event.model_dump_json()

    assert '"workflow":"intus"' in payload
    assert '"operation":"build_script.generate"' in payload
    assert '"prompt":"make a bracket"' in payload
    assert '"total_tokens":30' in payload
    assert billing_usage_message_id(event).startswith("billing-usage:")


def test_billing_message_size_limit_rejects_oversized_payload():
    event = LlmTokenUsageEvent(
        event_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        workflow="intus",
        operation="build_script.generate",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt="x" * 128,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert serialized_billing_message_size(event) > 0
    with pytest.raises(ValueError, match="billing event is"):
        assert_billing_message_size(event, 20)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_billing_messages.py -q
```

Expected: fails because `core.billing_messages` does not exist.

- [ ] **Step 3: Implement billing message contract**

Create `server/core/billing_messages.py`:

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LlmTokenUsageEvent(BaseModel):
    event_id: UUID
    tenant_id: UUID
    user_id: UUID
    project_id: UUID | None = None
    workflow: str
    operation: str
    provider: str
    model: str
    prompt: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    occurred_at: datetime
    provider_request_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def serialized_billing_message_size(message: BaseModel) -> int:
    return len(message.model_dump_json().encode("utf-8"))


def assert_billing_message_size(message: BaseModel, max_bytes: int) -> None:
    size = serialized_billing_message_size(message)
    if size > max_bytes:
        raise ValueError(f"billing event is {size} bytes, above {max_bytes} byte limit")


def billing_usage_message_id(event: LlmTokenUsageEvent) -> str:
    return f"billing-usage:{event.event_id}"
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_billing_messages.py -q
```

Expected: billing message tests pass.

- [ ] **Step 5: Commit**

```bash
git add server/core/billing_messages.py server/tests/test_billing_messages.py
git commit -m "feat: define llm billing usage event"
```

### Task 3: Billing NATS Stream Setup

**Files:**
- Modify: `server/core/nats_client.py`
- Test: `server/tests/test_nats_client.py`

- [ ] **Step 1: Add failing billing stream test**

Modify imports in `server/tests/test_nats_client.py`:

```python
from core.nats_client import NatsPublisher, ensure_billing_stream, ensure_compile_stream
```

Add:

```python
@pytest.mark.asyncio
async def test_ensure_billing_stream_creates_llm_usage_stream():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()

    result = await ensure_billing_stream(connection, settings)

    assert result is jetstream
    stream_config = jetstream.streams["TERTIUS_BILLING"]
    assert stream_config.subjects == ["tertius.billing.usage.llm.tokens"]
    assert stream_config.max_msg_size == 262144


@pytest.mark.asyncio
async def test_ensure_billing_stream_updates_existing_subjects_and_size():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    jetstream.streams["TERTIUS_BILLING"] = SimpleNamespace(
        config=SimpleNamespace(name="TERTIUS_BILLING", subjects=["old.subject"], max_msg_size=-1)
    )

    await ensure_billing_stream(connection, settings)

    stream_config = jetstream.streams["TERTIUS_BILLING"]
    assert stream_config.subjects == ["tertius.billing.usage.llm.tokens"]
    assert stream_config.max_msg_size == 262144
```

- [ ] **Step 2: Run NATS tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_nats_client.py -q
```

Expected: fails because `ensure_billing_stream` does not exist.

- [ ] **Step 3: Add billing stream reconciler**

Add to `server/core/nats_client.py`:

```python
async def ensure_billing_stream(nc, settings):
    from nats.js.api import StreamConfig
    from nats.js.errors import NotFoundError

    js = nc.jetstream()
    subjects = [settings.billing_llm_usage_subject]
    max_msg_size = settings.billing_max_bytes

    try:
        info = await js.stream_info(settings.billing_stream_name)
        current = info.config if hasattr(info, "config") else info
        current_subjects = list(getattr(current, "subjects", []) or [])
        current_max_msg_size = getattr(current, "max_msg_size", None)
        if sorted(current_subjects) != sorted(subjects) or current_max_msg_size != max_msg_size:
            await js.update_stream(
                StreamConfig(
                    name=settings.billing_stream_name,
                    subjects=subjects,
                    max_msg_size=max_msg_size,
                )
            )
    except NotFoundError:
        await js.add_stream(
            StreamConfig(
                name=settings.billing_stream_name,
                subjects=subjects,
                max_msg_size=max_msg_size,
            )
        )

    return js
```

- [ ] **Step 4: Run NATS tests and verify pass**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_nats_client.py -q
```

Expected: NATS tests pass.

- [ ] **Step 5: Commit**

```bash
git add server/core/nats_client.py server/tests/test_nats_client.py
git commit -m "feat: add billing usage nats stream"
```

### Task 3A: Paid LLM Usage Guard

**Files:**
- Modify: `server/core/models.py`
- Modify: `server/core/repositories.py`
- Create migration under `server/migrations/versions/`
- Create: `server/core/llm_usage.py`
- Test: `server/tests/test_llm_usage.py`
- Test: `server/tests/test_migrations.py`

- [ ] **Step 1: Add failing usage guard tests**

Create `server/tests/test_llm_usage.py` with tests for:

- recording one completed LLM usage row with `tenant_id`, `user_id`, `project_id`, provider/model, token counts, prompt, metadata, provider request ID, and event ID.
- rejecting when a user has `settings.llm_user_rate_limit_per_minute` recent LLM requests in the last minute.
- rejecting when a tenant has `settings.llm_tenant_rate_limit_per_minute` recent LLM requests in the last minute.
- rejecting when today's tenant token total is at or above `settings.llm_tenant_daily_token_quota`.
- rejecting when today's user token total is at or above `settings.llm_user_daily_token_quota`.
- never counting another tenant's rows against the authenticated tenant.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_usage.py -q
```

Expected: fails because the LLM usage model/helper does not exist.

- [ ] **Step 3: Add `LlmUsageRecord` model and migration**

Add a table with:

- `id` UUID primary key
- `event_id` UUID unique, indexed
- `tenant_id` UUID indexed
- `user_id` UUID indexed
- `project_id` UUID nullable/indexed with tenant/project foreign key
- `workflow`, `operation`, `provider`, `model`, `prompt`
- `prompt_tokens`, `completion_tokens`, `total_tokens`
- `provider_request_id`, `metadata_json`
- `status` string default `completed`
- `created_at` timestamp indexed

Keep the migration revision ID shorter than 32 characters.

- [ ] **Step 4: Add DB-backed guard helper**

Create `server/core/llm_usage.py`:

- `LlmUsageLimitExceeded` exception.
- `assert_llm_usage_allowed(db, settings, tenant_id, user_id, estimated_tokens)` that checks per-user and per-tenant minute request limits plus daily token quotas.
- `record_llm_usage(db, auth, project_id, request, result, provider_request_id, settings)` that persists the completed usage row and returns the generated `event_id`.

Use only database state for limit decisions; do not use process-local memory.

- [ ] **Step 5: Run usage and migration tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_usage.py server/tests/test_migrations.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/core/models.py server/core/repositories.py server/core/llm_usage.py server/migrations/versions server/tests/test_llm_usage.py server/tests/test_migrations.py
git commit -m "feat: add paid llm usage guard"
```

### Task 4: LLM Provider Wrapper

**Files:**
- Create: `server/core/llm_client.py`
- Test: `server/tests/test_llm_client.py`

- [ ] **Step 1: Write failing LLM wrapper tests**

Create `server/tests/test_llm_client.py`:

```python
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.auth_types import AuthContext
from core.config import Settings
from core.llm_client import (
    BuildScriptGenerationInput,
    LlmNotConfiguredError,
    LlmBillingError,
    generate_build_script,
)


class FakeChatCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="chatcmpl-test",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="```python\nimport build123d as bd\npart = bd.Box(1, 2, 3)\n```"
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22, total_tokens=33),
        )


class FakeOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


class FakePublisher:
    def __init__(self):
        self.published = []

    async def publish_json(self, subject, message, message_id=None):
        self.published.append((subject, message, message_id))


@pytest.mark.asyncio
async def test_generate_build_script_calls_openai_and_publishes_billing_event():
    client = FakeOpenAIClient()
    publisher = FakePublisher()
    settings = Settings(llm_api_key="secret")
    auth = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="kc-test",
        email="test@example.com",
    )
    project_id = uuid4()

    result = await generate_build_script(
        BuildScriptGenerationInput(
            prompt="make a bracket",
            active_file="design.py",
            current_code="import build123d as bd\n",
            metadata={"source": "compiler_tab"},
        ),
        settings=settings,
        auth=auth,
        project_id=project_id,
        openai_client=client,
        billing_publisher=publisher,
    )

    assert result.script == "import build123d as bd\npart = bd.Box(1, 2, 3)"
    assert result.usage.prompt_tokens == 11
    assert client.chat.completions.calls[0]["model"] == "deepseek-v4-flash"
    assert client.chat.completions.calls[0]["max_tokens"] == 2048
    assert publisher.published[0][0] == "tertius.billing.usage.llm.tokens"
    billing_event = publisher.published[0][1]
    assert billing_event.prompt == "make a bracket"
    assert billing_event.tenant_id == auth.tenant_id
    assert billing_event.user_id == auth.user_id
    assert billing_event.project_id == project_id
    assert billing_event.prompt_tokens == 11
    assert billing_event.completion_tokens == 22
    assert billing_event.total_tokens == 33
    assert billing_event.metadata == {"source": "compiler_tab"}
    assert publisher.published[0][2].startswith("billing-usage:")


@pytest.mark.asyncio
async def test_generate_build_script_raises_when_billing_publish_fails():
    class FailingPublisher(FakePublisher):
        async def publish_json(self, subject, message, message_id=None):
            raise RuntimeError("nats unavailable")

    with pytest.raises(LlmBillingError):
        await generate_build_script(
            BuildScriptGenerationInput(prompt="make a bracket"),
            settings=Settings(llm_api_key="secret"),
            auth=AuthContext(user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc-test"),
            project_id=uuid4(),
            openai_client=FakeOpenAIClient(),
            billing_publisher=FailingPublisher(),
        )


@pytest.mark.asyncio
async def test_generate_build_script_requires_configured_key():
    with pytest.raises(LlmNotConfiguredError):
        await generate_build_script(
            BuildScriptGenerationInput(prompt="make a bracket"),
            settings=Settings(llm_api_key=""),
            auth=AuthContext(user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc-test"),
            project_id=uuid4(),
            openai_client=FakeOpenAIClient(),
            billing_publisher=FakePublisher(),
        )
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py -q
```

Expected: fails because `core.llm_client` does not exist.

- [ ] **Step 3: Implement LLM wrapper**

Create `server/core/llm_client.py` with:

```python
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from core.auth_types import AuthContext
from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
)
from core.nats_client import NatsPublisher

logger = logging.getLogger(__name__)


class LlmNotConfiguredError(RuntimeError):
    pass


class LlmGenerationError(RuntimeError):
    pass


class LlmBillingError(RuntimeError):
    pass


class BuildScriptGenerationInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    active_file: str = Field(default="design.py")
    current_code: str = Field(default="", max_length=200000)
    metadata: dict[str, str] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class BuildScriptGenerationResult(BaseModel):
    success: bool = True
    script: str
    model: str
    usage: TokenUsage


def create_openai_client(settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
    )


def build_script_messages(request: BuildScriptGenerationInput) -> list[dict[str, str]]:
    system_prompt = (
        "You generate Python build scripts for Tertius Intus. "
        "Return only executable Python source code. "
        "Use build123d idioms when geometry is needed. "
        "Do not include markdown fences or explanation."
    )
    user_prompt = (
        f"Active file: {request.active_file}\n\n"
        f"Current code:\n{request.current_code}\n\n"
        f"Requested build script:\n{request.prompt}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def strip_markdown_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def extract_usage(response) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )


async def generate_build_script(
    request: BuildScriptGenerationInput,
    *,
    settings,
    auth: AuthContext,
    project_id: UUID | None,
    openai_client=None,
    billing_publisher: NatsPublisher | None = None,
) -> BuildScriptGenerationResult:
    if not settings.llm_api_key:
        raise LlmNotConfiguredError("LLM provider is not configured")

    client = openai_client or create_openai_client(settings)
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=build_script_messages(request),
        max_tokens=settings.llm_max_output_tokens,
    )
    content = response.choices[0].message.content or ""
    usage = extract_usage(response)
    result = BuildScriptGenerationResult(
        script=strip_markdown_code_fence(content),
        model=settings.llm_model,
        usage=usage,
    )

    if billing_publisher is not None:
        event = LlmTokenUsageEvent(
            event_id=uuid4(),
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            project_id=project_id,
            workflow="intus",
            operation="build_script.generate",
            provider="deepseek",
            model=settings.llm_model,
            prompt=request.prompt,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            occurred_at=datetime.now(timezone.utc),
            provider_request_id=getattr(response, "id", None),
            metadata=request.metadata,
        )
        try:
            assert_billing_message_size(event, settings.billing_max_bytes)
            await billing_publisher.publish_json(
                settings.billing_llm_usage_subject,
                event,
                message_id=billing_usage_message_id(event),
            )
        except Exception as exc:
            logger.exception("Failed to publish LLM billing usage event")
            raise LlmBillingError("LLM billing failed") from exc

    return result
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py -q
```

Expected: LLM wrapper tests pass.

- [ ] **Step 5: Commit**

```bash
git add server/core/llm_client.py server/tests/test_llm_client.py
git commit -m "feat: add llm build script generator"
```

### Task 5: Authenticated Intus API Endpoint

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Test: `server/tests/test_build_script_generation.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `server/tests/test_build_script_generation.py`:

```python
from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.llm_client import TokenUsage
from core.llm_usage import LlmUsageLimitExceeded
from core.models import AppUser, Project, Tenant, TenantMembership
from workflows.intus import intus_server
from workflows.intus.intus_server import app


def test_build_script_generation_requires_existing_project(authenticated_intus_client, monkeypatch):
    response = authenticated_intus_client.post(
        "/projects/missing_project/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 404
    assert response.json() == {"success": False, "error": "Project not found"}


def test_build_script_generation_does_not_cross_tenant(authenticated_intus_client, db_session):
    other_user = AppUser(keycloak_subject="kc-other", email="other@example.com")
    other_tenant = Tenant(name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    db_session.add(Project(tenant_id=other_tenant.id, name="other_project", created_by=other_user.id))
    db_session.commit()

    response = authenticated_intus_client.post(
        "/projects/other_project/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 404


def test_build_script_generation_returns_generated_script(authenticated_intus_client, seeded_tenant, monkeypatch):
    async def fake_generate_build_script(request, *, settings, auth, project_id, openai_client=None, billing_publisher=None):
        assert request.prompt == "make a bracket"
        assert request.active_file == "design.py"
        assert request.metadata == {"source": "compiler_tab"}
        assert auth.tenant_id == seeded_tenant.tenant_id
        assert project_id == seeded_tenant.project_id
        return SimpleNamespace(
            success=True,
            script="import build123d as bd\npart = bd.Box(1, 2, 3)",
            model="deepseek-v4-flash",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)

    async def fake_create_billing_publisher(settings):
        return None, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={
            "prompt": "make a bracket",
            "active_file": "design.py",
            "current_code": "import build123d as bd\n",
            "metadata": {"source": "compiler_tab"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "script": "import build123d as bd\npart = bd.Box(1, 2, 3)",
        "model": "deepseek-v4-flash",
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def test_build_script_generation_is_authenticated(db_session, seeded_tenant):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.post(
            "/projects/default_purlin/build-script/generate",
            json={"prompt": "make a bracket"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_build_script_generation_public_mounted_route(db_session, seeded_tenant, monkeypatch):
    from main import app as main_app

    async def fake_generate_build_script(request, *, settings, auth, project_id, openai_client=None, billing_publisher=None):
        return SimpleNamespace(
            success=True,
            script="import build123d as bd\npart = bd.Box(1, 2, 3)",
            model="deepseek-v4-flash",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)

    async def fake_create_billing_publisher(settings):
        return None, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    def override_db():
        yield db_session

    def override_auth():
        return AuthContext(
            user_id=seeded_tenant.user_id,
            tenant_id=seeded_tenant.tenant_id,
            keycloak_subject="kc-test",
            email="test@example.com",
        )

    intus_server.app.dependency_overrides[get_db] = override_db
    intus_server.app.dependency_overrides[get_auth_context] = override_auth
    try:
        response = TestClient(main_app).post(
            "/api/intus/projects/default_purlin/build-script/generate",
            json={"prompt": "make a bracket"},
        )
    finally:
        intus_server.app.dependency_overrides.clear()

    assert response.status_code == 200


def test_build_script_generation_rejects_invalid_active_file(authenticated_intus_client):
    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket", "active_file": "../design.py"},
    )

    assert response.status_code == 400
    assert response.json()["success"] is False


def test_build_script_generation_reports_missing_provider_key(authenticated_intus_client, monkeypatch):
    async def fake_generate_build_script(*args, **kwargs):
        from core.llm_client import LlmNotConfiguredError

        raise LlmNotConfiguredError("LLM provider is not configured")

    async def fake_create_billing_publisher(settings):
        return None, None

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "LLM provider is not configured",
        "retryable": False,
    }


def test_build_script_generation_returns_429_when_llm_limit_exceeded(authenticated_intus_client, monkeypatch):
    def fake_assert_llm_usage_allowed(*args, **kwargs):
        raise LlmUsageLimitExceeded("LLM usage limit exceeded")

    monkeypatch.setattr(intus_server, "assert_llm_usage_allowed", fake_assert_llm_usage_allowed)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 429
    assert response.json() == {
        "success": False,
        "error": "LLM usage limit exceeded",
        "retryable": True,
    }


def test_build_script_generation_reports_provider_failure(authenticated_intus_client, monkeypatch):
    async def fake_generate_build_script(*args, **kwargs):
        raise RuntimeError("provider timed out")

    async def fake_create_billing_publisher(settings):
        return None, None

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 503
    assert response.json()["retryable"] is True
```


- [ ] **Step 2: Run endpoint tests and verify failure**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_build_script_generation.py -q
```

Expected: fails because the endpoint does not exist.

- [ ] **Step 3: Add imports and helper**

In `server/workflows/intus/intus_server.py`, add imports:

```python
import logging

from core.llm_client import (
    BuildScriptGenerationInput,
    LlmBillingError,
    LlmGenerationError,
    LlmNotConfiguredError,
    generate_build_script,
)
from core.llm_usage import LlmUsageLimitExceeded, assert_llm_usage_allowed, record_llm_usage
```

Add near the existing `settings = get_settings()` pattern or after `publish_compile_command`:

```python
logger = logging.getLogger(__name__)


async def create_billing_publisher(settings):
    nc = None
    try:
        nc = await connect_nats(settings.nats_url)
        js = await ensure_billing_stream(nc, settings)
        return NatsPublisher(js), nc
    except Exception:
        if nc is not None:
            try:
                await nc.close()
            except Exception:
                logger.exception("Failed to close LLM billing NATS connection after setup failure")
        logger.exception("Failed to create LLM billing publisher")
        return None, None
```

Also import `ensure_billing_stream` from `core.nats_client`. The returned NATS connection must be closed by the endpoint after generation completes or fails.

- [ ] **Step 4: Add endpoint**

Add to `server/workflows/intus/intus_server.py` after `compile_project` or before compile status:

```python
@app.post("/projects/{name}/build-script/generate")
async def generate_project_build_script(
    name: str,
    req: BuildScriptGenerationInput,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    try:
        require_valid_python_filename(req.active_file)
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "Project not found"})

    settings = get_settings()
    billing_publisher = None
    billing_nc = None
    try:
        assert_llm_usage_allowed(
            db,
            settings,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            estimated_tokens=settings.llm_max_output_tokens,
        )
        billing_publisher, billing_nc = await create_billing_publisher(settings)
        result = await generate_build_script(
            req,
            settings=settings,
            auth=ctx,
            project_id=project.id,
            billing_publisher=billing_publisher,
        )
        record_llm_usage(
            db,
            auth=ctx,
            project_id=project.id,
            request=req,
            result=result,
            settings=settings,
        )
        db.commit()
    except LlmUsageLimitExceeded as exc:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"success": False, "error": str(exc), "retryable": True},
        )
    except LlmNotConfiguredError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": str(exc), "retryable": False},
        )
    except LlmBillingError:
        logger.exception("LLM billing failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM billing failed", "retryable": True},
        )
    except Exception:
        logger.exception("LLM build script generation failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM generation failed", "retryable": True},
        )
    finally:
        if billing_nc is not None:
            try:
                await billing_nc.flush()
            except Exception:
                logger.exception("Failed to flush LLM billing NATS connection")
            finally:
                try:
                    await billing_nc.close()
                except Exception:
                    logger.exception("Failed to close LLM billing NATS connection")

    return result.model_dump()
```

If `LlmGenerationError` is unused after implementation, remove that import.

- [ ] **Step 5: Run endpoint tests and verify pass**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_build_script_generation.py -q
```

Expected: endpoint tests pass.

- [ ] **Step 6: Run focused backend regression**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_build_script_generation.py server/tests/test_intus_endpoints.py server/tests/test_llm_client.py server/tests/test_billing_messages.py server/tests/test_nats_client.py server/tests/test_config.py -q
```

Expected: all focused tests pass.

- [ ] **Step 7: Commit**

```bash
git add server/workflows/intus/intus_server.py server/tests/test_build_script_generation.py
git commit -m "feat: expose intus build script generation endpoint"
```

### Task 6: Helm ConfigMap and Secret Wiring

**Files:**
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `infra/charts/tertius/templates/secrets.yaml`
- Modify: `infra/charts/tertius/templates/api.yaml`
- Modify: `scripts/test-deployment-config.sh`

- [ ] **Step 1: Add failing deployment checks**

In `scripts/test-deployment-config.sh`, add a render helper:

```bash
render_app_secret_created() {
  helm template "$RELEASE_NAME" "$CHART_DIR" \
    --set app.llmSecret.create=true \
    --set-string app.llmSecret.apiKey=deepseek-test-key
}
```

After render variables, add:

```bash
app_secret_rendered="$(render_app_secret_created)"
api_with_llm_secret="$(extract_render_doc "$app_secret_rendered" 'app.kubernetes.io/component: api')"
ui_with_llm_secret="$(extract_render_doc "$app_secret_rendered" 'app.kubernetes.io/component: ui')"
```

Add assertions after ConfigMap checks:

```bash
if ! printf '%s\n' "$rendered" | rg -q 'LLM_BASE_URL: "https://api.deepseek.com"' || ! printf '%s\n' "$rendered" | rg -q 'LLM_MODEL: "deepseek-v4-flash"'; then
  echo "ConfigMap must render DeepSeek LLM base URL and model." >&2
  exit 1
fi

if ! printf '%s\n' "$rendered" | rg -q 'LLM_USER_RATE_LIMIT_PER_MINUTE: "10"' || ! printf '%s\n' "$rendered" | rg -q 'LLM_TENANT_DAILY_TOKEN_QUOTA: "100000"'; then
  echo "ConfigMap must render paid LLM rate and quota settings." >&2
  exit 1
fi

if printf '%s\n' "$rendered" | rg -q 'LLM_API_KEY'; then
  echo "ConfigMap must not render LLM_API_KEY." >&2
  exit 1
fi

if ! printf '%s\n' "$app_secret_rendered" | rg -q 'kind: Secret' || ! printf '%s\n' "$app_secret_rendered" | rg -q 'LLM_API_KEY: "deepseek-test-key"'; then
  echo "Dedicated LLM Secret must render LLM_API_KEY when app.llmSecret.create=true." >&2
  exit 1
fi

if ! printf '%s\n' "$api_with_llm_secret" | rg -q 'name: LLM_API_KEY' || ! printf '%s\n' "$api_with_llm_secret" | rg -q 'key: LLM_API_KEY'; then
  echo "API Deployment must reference LLM_API_KEY from the dedicated LLM Secret." >&2
  exit 1
fi

if printf '%s\n' "$ui_with_llm_secret" | rg -q 'LLM_API_KEY|llm'; then
  echo "UI Deployment must not receive or reference LLM provider credentials." >&2
  exit 1
fi
```

Extend the compile ScaledJob leakage check:

```bash
if printf '%s\n' "$scaled_job" | rg -q 'envFrom:|secretRef:|APP_DB_PASSWORD|APP_DB_OWNER|APP_DB_HOST|APP_DB_NAME|DATABASE_URL|LLM_API_KEY|LLM_BASE_URL|LLM_MODEL'; then
  echo "Compile ScaledJob must not receive app secrets, database environment, or LLM provider configuration." >&2
  exit 1
fi
```

- [ ] **Step 2: Run deployment config test and verify failure**

Run:

```bash
rtk scripts/test-deployment-config.sh
```

Expected: fails because Helm does not render the new LLM values.

- [ ] **Step 3: Add values**

Add under `app.config` in `infra/charts/tertius/values.yaml` and `values-local.yaml`:

```yaml
    llmBaseUrl: https://api.deepseek.com
    llmModel: deepseek-v4-flash
    llmTimeoutSeconds: 60
    llmMaxOutputTokens: 2048
    llmUserRateLimitPerMinute: 10
    llmTenantRateLimitPerMinute: 60
    llmTenantDailyTokenQuota: 100000
    llmUserDailyTokenQuota: 25000
    billingStreamName: TERTIUS_BILLING
    billingLlmUsageSubject: tertius.billing.usage.llm.tokens
    billingMaxBytes: 262144
```

Add under `app`:

```yaml
  llmSecretName: ""
  llmSecret:
    create: false
    apiKey: ""
```

- [ ] **Step 4: Render ConfigMap env**

Add to `infra/charts/tertius/templates/configmap.yaml`:

```yaml
  LLM_BASE_URL: {{ .Values.app.config.llmBaseUrl | quote }}
  LLM_MODEL: {{ .Values.app.config.llmModel | quote }}
  LLM_TIMEOUT_SECONDS: {{ .Values.app.config.llmTimeoutSeconds | quote }}
  LLM_MAX_OUTPUT_TOKENS: {{ .Values.app.config.llmMaxOutputTokens | quote }}
  LLM_USER_RATE_LIMIT_PER_MINUTE: {{ .Values.app.config.llmUserRateLimitPerMinute | quote }}
  LLM_TENANT_RATE_LIMIT_PER_MINUTE: {{ .Values.app.config.llmTenantRateLimitPerMinute | quote }}
  LLM_TENANT_DAILY_TOKEN_QUOTA: {{ .Values.app.config.llmTenantDailyTokenQuota | quote }}
  LLM_USER_DAILY_TOKEN_QUOTA: {{ .Values.app.config.llmUserDailyTokenQuota | quote }}
  BILLING_STREAM_NAME: {{ .Values.app.config.billingStreamName | quote }}
  BILLING_LLM_USAGE_SUBJECT: {{ .Values.app.config.billingLlmUsageSubject | quote }}
  BILLING_MAX_BYTES: {{ printf "%d" (.Values.app.config.billingMaxBytes | int64) | quote }}
```

- [ ] **Step 5: Render Secret env**

Add a separate optional Secret to `infra/charts/tertius/templates/secrets.yaml`; do not add `LLM_API_KEY` to the existing shared app Secret because the UI currently consumes that Secret through `envFrom`.

```yaml
{{- if .Values.app.llmSecret.create }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ default (printf "%s-llm" (include "tertius.fullname" .)) .Values.app.llmSecretName | quote }}
  labels:
    {{- include "tertius.labels" . | nindent 4 }}
type: Opaque
stringData:
  LLM_API_KEY: {{ .Values.app.llmSecret.apiKey | quote }}
{{- end }}
```

Add an API-only secret reference to `infra/charts/tertius/templates/api.yaml` under the existing API container `env:` list:

```yaml
            - name: LLM_API_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ default (printf "%s-llm" (include "tertius.fullname" .)) .Values.app.llmSecretName | quote }}
                  key: LLM_API_KEY
                  optional: true
```

- [ ] **Step 6: Run deployment config test and Helm lint**

Run:

```bash
rtk scripts/test-deployment-config.sh
rtk helm lint infra/charts/tertius
```

Expected: deployment config test passes and Helm lint reports `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 7: Commit**

```bash
git add infra/charts/tertius/values.yaml infra/charts/tertius/values-local.yaml infra/charts/tertius/templates/configmap.yaml infra/charts/tertius/templates/secrets.yaml infra/charts/tertius/templates/api.yaml scripts/test-deployment-config.sh
git commit -m "feat: wire llm provider config through helm"
```

### Task 7: Local Compose Wiring

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add API-only local env**

Add LLM and billing env vars to the API/backend service only:

```yaml
      LLM_BASE_URL: ${LLM_BASE_URL:-https://api.deepseek.com}
      LLM_MODEL: ${LLM_MODEL:-deepseek-v4-flash}
      LLM_API_KEY: ${LLM_API_KEY:-}
      LLM_TIMEOUT_SECONDS: ${LLM_TIMEOUT_SECONDS:-60}
      LLM_MAX_OUTPUT_TOKENS: ${LLM_MAX_OUTPUT_TOKENS:-2048}
      LLM_USER_RATE_LIMIT_PER_MINUTE: ${LLM_USER_RATE_LIMIT_PER_MINUTE:-10}
      LLM_TENANT_RATE_LIMIT_PER_MINUTE: ${LLM_TENANT_RATE_LIMIT_PER_MINUTE:-60}
      LLM_TENANT_DAILY_TOKEN_QUOTA: ${LLM_TENANT_DAILY_TOKEN_QUOTA:-100000}
      LLM_USER_DAILY_TOKEN_QUOTA: ${LLM_USER_DAILY_TOKEN_QUOTA:-25000}
      BILLING_STREAM_NAME: ${BILLING_STREAM_NAME:-TERTIUS_BILLING}
      BILLING_LLM_USAGE_SUBJECT: ${BILLING_LLM_USAGE_SUBJECT:-tertius.billing.usage.llm.tokens}
      BILLING_MAX_BYTES: ${BILLING_MAX_BYTES:-262144}
```

Do not add these env vars to `compile-job-runner`.

- [ ] **Step 2: Verify compose config**

Run:

```bash
rtk docker compose config
```

Expected: backend/API service includes the LLM env vars; `compile-job-runner` does not include `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, or billing env vars.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: wire llm config into local api compose"
```

### Task 8: Documentation

**Files:**
- Modify: `infra/charts/tertius/README.md`

- [ ] **Step 1: Add chart configuration docs**

Add a section to `infra/charts/tertius/README.md`:

```markdown
## LLM Build Script Generation

The API can call an OpenAI-compatible LLM provider to generate Intus build scripts.

Non-secret provider settings are rendered into the app ConfigMap:

- `app.config.llmBaseUrl` -> `LLM_BASE_URL`, default `https://api.deepseek.com`
- `app.config.llmModel` -> `LLM_MODEL`, default `deepseek-v4-flash`
- `app.config.llmTimeoutSeconds` -> `LLM_TIMEOUT_SECONDS`
- `app.config.llmMaxOutputTokens` -> `LLM_MAX_OUTPUT_TOKENS`
- `app.config.llmUserRateLimitPerMinute` -> `LLM_USER_RATE_LIMIT_PER_MINUTE`
- `app.config.llmTenantRateLimitPerMinute` -> `LLM_TENANT_RATE_LIMIT_PER_MINUTE`
- `app.config.llmTenantDailyTokenQuota` -> `LLM_TENANT_DAILY_TOKEN_QUOTA`
- `app.config.llmUserDailyTokenQuota` -> `LLM_USER_DAILY_TOKEN_QUOTA`
- `app.config.billingStreamName` -> `BILLING_STREAM_NAME`
- `app.config.billingLlmUsageSubject` -> `BILLING_LLM_USAGE_SUBJECT`
- `app.config.billingMaxBytes` -> `BILLING_MAX_BYTES`

The provider API key is secret material:

- `app.llmSecret.apiKey` -> `LLM_API_KEY` when `app.llmSecret.create=true`
- `app.llmSecretName` selects an externally managed dedicated LLM Secret when production manages the key out of chart values.
- Do not put `LLM_API_KEY` in the shared app Secret selected by `app.secretName`, because the UI Deployment consumes that shared Secret through `envFrom`.

Only the API Deployment receives `LLM_API_KEY`. UI and Compile Jobs do not receive the LLM configuration or key.
```

- [ ] **Step 2: Commit**

```bash
git add infra/charts/tertius/README.md
git commit -m "docs: document llm provider configuration"
```

### Task 9: Final Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run focused backend tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_auth.py server/tests/test_build_script_generation.py server/tests/test_llm_client.py server/tests/test_llm_usage.py server/tests/test_billing_messages.py server/tests/test_nats_client.py server/tests/test_config.py -q
```

Expected: all pass.

- [ ] **Step 2: Run deployment checks**

```bash
rtk scripts/test-deployment-config.sh
rtk helm lint infra/charts/tertius
rtk docker compose config
```

Expected: script exits 0, Helm lint passes, and compose config shows LLM env vars only on the API/backend service.

- [ ] **Step 3: Run broader affected backend tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_auth.py server/tests/test_keycloak_integration.py server/tests/test_intus_endpoints.py server/tests/test_compile_flow.py server/tests/test_compile_job.py server/tests/test_compile_result_consumer.py server/tests/test_nats_client.py server/tests/test_config.py -q
```

Expected: all pass.

- [ ] **Step 4: Inspect git diff**

```bash
rtk git diff --stat
rtk git status --short
```

Expected: only the planned files are modified.

## Open Questions Before Implementation

- The plan assumes Intus owns this endpoint because the generated artifact is a build script. If the product owner wants this under Artus instead, reuse `server/core/llm_client.py` and move only the route/tests to `server/workflows/artus/artus_server.py`.
- The plan returns generated code for user review and does not save it. If auto-save is required, add an explicit frontend confirmation flow and reuse `ProjectRepository.stage_code_update`.
- Production uses externally managed Helm values and secrets. Before enabling this in production, create or update the dedicated LLM Secret referenced by `app.llmSecretName`; otherwise the endpoint correctly returns the planned missing-provider-key 503.

## Source Notes

- The OpenAI Python library supports synchronous and asynchronous clients powered by `httpx`, and official migration guidance recommends instantiated clients in application code.
- OpenAI-compatible custom provider routing should use an instantiated client with `base_url`, `api_key`, and async calls from `AsyncOpenAI`.
