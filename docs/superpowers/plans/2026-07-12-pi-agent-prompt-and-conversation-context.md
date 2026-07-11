# Pi Agent Prompt and Conversation Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Document type:** Implementation plan

**Goal:** Give each one-shot Pi edit job the same immutable Tertius system policy and a bounded, structured, database-backed conversation context containing prior user requests and assistant outcomes.

**Architecture:** Tertius remains the source of truth for files and conversation history. Both the API and Pi worker load one checked-in append-prompt file from the common Python image, while the API records its SHA-256 and sends only the hash in a version-2 command. Each new job deterministically advances a bounded conversation context from terminal `LlmEditJob` JSON, and the ephemeral worker validates the prompt hash, renders that context, runs Pi with `--no-session`, and returns a bounded assistant summary.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy/Postgres JSON, NATS JetStream, Pi RPC 0.80.6, Docker, Helm/KEDA, pytest, Bash harness scripts

---

## 1. Decisions

| Concern | Decision | Implementation implication |
|---|---|---|
| System prompt ownership | One checked-in `server/core/pi_agent_system_prompt.md` file | The prompt is copied into both API and worker images and is not configurable through environment variables, Helm values, NATS, or the OAuth PVC. |
| Pi prompt semantics | Append to Pi's default coding-agent prompt | Continue using `--append-system-prompt`, but pass the existing file path instead of its contents. |
| Prompt drift | API records and commands carry the prompt SHA-256 | A v2 worker refuses to call Pi when its local hash differs, returning bounded `worker_config_mismatch` without logging either hash or prompt content. |
| Conversation authority | Existing `llm_edit_jobs` rows | No migration; `request_payload` stores the exact dispatched context and `result_payload`/terminal fields provide assistant outcomes. |
| Conversation shape | Rolling deterministic summary plus five recent structured turns | Do not replay an unbounded transcript or Pi tool traces. |
| Summary generation | Application-generated, no extra LLM call | Fold completed turns into bounded lines; never recursively embed a prior dispatched context. |
| Worker lifecycle | One Pi process for one edit job | Preserve `--no-session`, KEDA `ScaledJob`, current file hydration, retries, and result consumption. |
| Wire rollout | API emits command schema v2; worker accepts v1 and v2 | Commands retained in the 24-hour JetStream window remain consumable during rollout. |
| UI | Existing history endpoint and Generate Design conversation | No frontend or API response contract changes. |

## 2. Scope

### In scope

- Immutable shared system-prompt file with fail-closed loading and exact-byte SHA-256.
- Prompt text removed from process argv, environment variables, Helm values, and deployment manifests.
- Structured prior turns with user request, status, outcome, assistant summary, error code, and changed filenames.
- Deterministic rolling context built from tenant/project-scoped terminal jobs.
- Bounded assistant text extraction from Pi RPC `message_end` events.
- Exact retry reconstruction from persisted dispatch metadata.
- v1 command compatibility for retained JetStream messages.
- Unit, integration, container, Helm/Compose parity, telemetry-safety, and authenticated two-turn live-flow validation.

### Explicit non-goals

- Persisting or resuming Pi JSONL sessions.
- Long-lived per-conversation Pi processes or sticky worker routing.
- Storing prompts, assistant messages, source, or session data on the OAuth PVC.
- A database migration or new conversation table.
- Model-generated conversation compaction.
- Sending historical file contents, snapshot IDs, snapshot hashes, tool calls, or tool results as conversation history.
- Rewriting `docs/superpowers/plans/2026-07-11-pi-coding-agent-openai-subscription.md`; it remains the historical integration plan.

## 3. File Map

### Create

| File | Responsibility |
|---|---|
| `server/core/pi_agent_system_prompt.md` | Exact immutable text appended to Pi's default prompt. |
| `server/core/pi_agent_prompt.py` | Fail-closed prompt loading, exact-byte hash, shared user-prompt rendering, and preflight estimation inputs. |
| `server/core/pi_agent_conversation.py` | Convert completed jobs to safe turns, advance/compact context, bootstrap legacy history, and render structured history. |
| `server/tests/test_pi_agent_prompt.py` | Prompt loader, hash, rendering, and prompt-estimation tests. |
| `server/tests/test_pi_agent_conversation.py` | Context validation, rollover, legacy bootstrap, failure labeling, and content-exclusion tests. |

### Modify

| File | Responsibility |
|---|---|
| `server/core/pi_agent_messages.py` | Add strict conversation models and backward-compatible v1/v2 command validation. |
| `server/core/llm_file_edit.py` | Remove the second system-prompt owner and estimate the exact shared prompt/user framing plus authoritative source budget. |
| `server/core/config.py` | Remove the inline `pi_agent_system_prompt` setting. |
| `server/core/repositories.py` | Replace prompt-only history lookup with bounded terminal-job retrieval under tenant/project scope. |
| `server/core/pi_agent_rpc.py` | Pass a prompt file path, extract bounded assistant text, and keep sensitive content out of logs. |
| `server/workflows/intus/intus_server.py` | Build/persist v2 conversation context and prompt hash, and estimate the exact dispatch framing. |
| `server/workflows/intus/pi_agent_job.py` | Validate the prompt hash, adapt v1 history, render v2 history, and preserve summaries for all success outcomes. |
| `server/workflows/intus/pi_agent_result_consumer.py` | Reconstruct v1/v2 commands exactly during queued-job republish. |
| `server/tests/test_config.py` | Lock removal of the legacy prompt environment setting. |
| `server/tests/test_repositories.py` | Verify tenant/project/status/order bounds for terminal history. |
| `server/tests/test_pi_agent_messages.py` | Verify strict conversation fields, schema compatibility, and prompt-free NATS JSON. |
| `server/tests/test_llm_file_edit_domain.py` | Replace obsolete direct-provider prompt tests with exact shared estimation tests. |
| `server/tests/test_llm_file_edit.py` | Verify persisted and published v2 context/hash identity. |
| `server/tests/test_pi_agent_job.py` | Verify hash enforcement, structured rendering, prompt-path use, and no-change summaries. |
| `server/tests/test_pi_agent_rpc.py` | Verify path-only argv and assistant text extraction. |
| `server/tests/test_pi_agent_result_consumer.py` | Verify exact v1/v2 retry reconstruction. |
| `server/tests/test_pi_agent_pipeline_e2e.py` | Verify a second job receives the first job's structured outcome and refreshed files. |
| `server/tests/test_pi_agent_telemetry_safety.py` | Treat all new history/summary fields as sensitive. |
| `server/tests/test_pi_agent_image_config.py` | Verify identical non-writable prompt artifacts in both image targets. |
| `server/.env.example` | Remove `PI_AGENT_SYSTEM_PROMPT`. |
| `Dockerfile.api` | Set the common prompt artifact to mode `0444`. |
| `infra/charts/tertius/values.yaml` | Remove `piAgent.systemPrompt`. |
| `infra/charts/tertius/templates/pi-agent-worker.yaml` | Remove `PI_AGENT_SYSTEM_PROMPT` injection. |
| `infra/charts/tertius/README.md` | Document immutable prompt ownership and rebuild behavior. |
| `.github/workflows/tests.yml` | Inspect the prompt artifact in both built image targets. |
| `scripts/test-deployment-config.sh` | Assert prompt env/config removal and image-only ownership. |
| `scripts/check-runtime-parity.sh` | Assert the same prompt/session/context contract across Helm and Compose. |
| `scripts/smoke-live-flow.sh` | Add an opt-in two-turn conversation canary. |
| `scripts/test-smoke-live-flow-config.sh` | Test the canary configuration and prompt separation. |
| `docs/configuration-and-secrets.md` | Distinguish application policy from secrets and OAuth state. |
| `docs/harness/runtime-parity.md` | Record immutable prompt and stateless DB-rehydrated context parity. |
| `docs/harness/browser-validation.md` | Document the context-dependent follow-up journey. |
| `docs/harness/queries/traces.md` | Explicitly prohibit prompt/history/summary telemetry. |

## 4. Fixed Contracts

### Prompt artifact

- Canonical source and runtime path: `/app/server/core/pi_agent_system_prompt.md`.
- Maximum raw size: 32,768 bytes.
- Encoding: UTF-8 with no NUL byte.
- Image owner/mode: `root:root`, `0444`.
- Hash: lowercase SHA-256 of the exact raw bytes Pi reads.
- The prompt is non-secret but must never be logged, added to telemetry, passed through NATS, or stored on the OAuth PVC.

### Conversation bounds

| Field | Bound |
|---|---:|
| Recent structured turns | 5 |
| Rolling summary | 8,000 characters |
| User request per turn | 12,000 characters |
| Assistant summary per turn | 2,000 characters |
| Changed filenames per turn | 20 |
| Changed filename | 512 characters |
| Rendered historical context | 12,000 estimated tokens using UTF-8 bytes divided by four |
| Legacy bootstrap query | 200 terminal jobs |
| Whole command | Existing 524,288-byte serializer gate |

Quota preflight adds a fixed `PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS = 8_192` for Pi's built-in base prompt and tool schemas, which are not duplicated into Tertius source. It then counts the checked-in append prompt, rendered conversation/current request, request metadata, and authoritative source characters exactly once before adding the configured output-token reserve.

### Command compatibility

Replace the existing `schema_version` and `prior_prompts` declarations, add the two v2 fields, and add this validator while retaining the existing identity, file, timestamp, and filename/hash validators:

```python
schema_version: Literal[1, 2]
prior_prompts: list[str] = Field(default_factory=list, max_length=5)
conversation: PiAgentConversationContext | None = None
system_prompt_sha256: str | None = Field(
    default=None,
    pattern=r"^[0-9a-f]{64}$",
)

@model_validator(mode="after")
def versioned_context(self):
    if self.schema_version == 1:
        if self.conversation is not None or self.system_prompt_sha256 is not None:
            raise ValueError("v1 commands cannot contain v2 prompt context")
    elif self.conversation is None or self.system_prompt_sha256 is None:
        raise ValueError("v2 commands require conversation and prompt hash")
    elif self.prior_prompts:
        raise ValueError("v2 commands cannot contain legacy prior prompts")
    return self
```

- The API and queued-job republisher emit v2 for new jobs.
- The worker accepts v1 until the retained request stream has exceeded its 24-hour maximum age after deployment.
- v1 renders `prior_prompts` as a separately delimited JSON list of legacy user requests; it never fabricates statuses/outcomes and is never rewritten in NATS.
- `PiAgentResult.schema_version` remains `1`.

## 5. Anti-Patterns

| Do not | Do instead | Why |
|---|---|---|
| Put the prompt in `PI_AGENT_SYSTEM_PROMPT`, Helm values, or a Secret | Load the checked-in common image file | Prevent split ownership and multiline config drift. |
| Put `APPEND_SYSTEM.md` under `/var/lib/pi-agent` | Keep it under `/app/server/core` | The Pi agent directory is the mutable shared OAuth PVC. |
| Pass prompt text as a CLI argument | Pass the validated existing file path | Prompt bytes must not appear in process inspection. |
| Send the prompt text/path in `PiAgentCommand` | Send only its SHA-256 | NATS is not the prompt distribution mechanism. |
| Replay all chat or Pi tool history | Send bounded structured outcomes plus a rolling summary | Prevent unbounded cost, stale tool state, and source leakage. |
| Treat prior user requests as active instructions | Delimit them as historical context and make the current request authoritative | Old requests can conflict with the current request. |
| Include historical source or snapshot identifiers | Hydrate only current authoritative files | Persisted project files already own current state. |
| Recompute conversation context during a retry | Reuse `request_payload["dispatched_conversation"]` exactly | A retry must execute the same logical command. |
| Use another model call to compact history | Use deterministic application compaction | Avoid hidden billing and nondeterministic retry state. |
| Log hashes to diagnose mismatches | Emit only bounded error code `worker_config_mismatch` | Hashes are audit data, not telemetry dimensions. |
| Replace Pi's entire system prompt | Append the Tertius policy | Pi's base coding/tool instructions remain required. |

## 6. Error Handling Matrix

| Failure | Detection | API/worker behavior | User-visible result | Retryable |
|---|---|---|---|---|
| Prompt file missing, non-file, unreadable, blank, invalid UTF-8, NUL-containing, or over 32 KiB | `load_pi_agent_prompt()` before estimate/spawn | Fail before provider execution; log only a fixed diagnostic | `AI editing is not configured` at API or `worker_config_error` from worker | No |
| API/worker prompt hash mismatch | Compare v2 command hash with worker snapshot | Do not hydrate/call Pi; publish bounded failure | `AI worker configuration changed; retry after deployment completes.` | Yes |
| Malformed persisted conversation JSON | Strict Pydantic validation | Ignore the malformed cached context and bootstrap from at most 200 terminal jobs | Normal request if bootstrap succeeds | No |
| Invalid terminal job payload | Missing/non-string prompt or malformed result | Skip only that job during legacy bootstrap | Remaining valid history is used | No |
| Oversized conversation field | Pydantic bounds or deterministic compactor | Fold oldest turn, clip summary entries, or reject malformed external command | Generic invalid-command failure | No |
| v1 command retained in JetStream | `schema_version == 1` | Render `prior_prompts` as bounded legacy user-request JSON and execute with the current prompt file | Normal legacy execution | No |
| v2 command missing context/hash | Pydantic model validator | Terminate invalid NATS message without provider execution | No new job result; existing invalid-message telemetry | No |
| Pi produces no final assistant text | No assistant `message_end` text | Persist deterministic fallback based on outcome/error | `Updated files.`, `No files changed.`, or bounded error message | No |
| Assistant text contains prompt/source | It is model output, not trusted metadata | Persist bounded result content but never log/label it | Existing conversation UI may display it | No |
| Queued retry lacks v2 persisted fields | Republisher checks `dispatched_command_schema_version` | Reconstruct legacy v1 only when legacy fields are present; otherwise fail job with `dispatch_config_error` | `AI edit could not be safely retried.` | No |

## 7. Test Case Specifications

### Unit tests

| ID | Component | Input | Expected result |
|---|---|---|---|
| U-01 | Prompt loader | Checked-in UTF-8 prompt | Exact content/path/raw-byte hash returned. |
| U-02 | Prompt loader | Missing, blank, NUL, invalid UTF-8, 32,769-byte files | Fixed `PiAgentPromptError`; no content in exception. |
| U-03 | Command model | Valid v1 and v2 payloads | Both parse; cross-version fields fail. |
| U-04 | Turn model | Success/failure/outcome combinations | Only consistent combinations parse. |
| U-05 | Context advance | Six complete turns | Oldest folds into rolling summary; newest five remain oldest-first. |
| U-06 | Summary/token bound | Repeated long completed turns | Summary stays at or below 8,000 characters, rendered history stays at or below 12,000 estimated tokens, and the current request remains intact outside history compaction. |
| U-07 | Job conversion | Succeeded changed/no-change and failed jobs | Safe summaries/status/error code/filenames only; no file content, raw error, IDs, or hashes. |
| U-08 | Legacy bootstrap | Mixed tenants/projects and malformed payloads | Only valid requested tenant/project terminal jobs contribute. |
| U-09 | Prompt renderer | Summary, recent turns, current request | Historical JSON and current request are separately delimited; current files declared authoritative. |
| U-10 | Estimator | Exact prompt/user framing/source characters | Deterministic ceiling division includes content exactly once plus the fixed 8,192-token Pi base/tool reserve. |
| U-11 | RPC parser | Assistant `message_end` with text/thinking/tool calls | Only final text is captured and clipped to 2,000 characters. |
| U-12 | RPC argv | Prompt containing a unique sentinel | Argv contains only path; sentinel absent from argv and env. |

### Integration tests

| ID | Flow | Setup | Verification |
|---|---|---|---|
| I-01 | API dispatch | Prior success and failure jobs | Persisted context exactly equals v2 command context; hash matches checked-in prompt; command has no prompt bytes/path. |
| I-02 | Worker execution | v2 command with matching and mismatched hashes | Matching invokes Pi; mismatch does not invoke Pi and returns retryable bounded failure. |
| I-03 | Result persistence | Changed and no-change Pi results | Both store the bounded assistant summary for the next turn. |
| I-04 | Queued republish | One legacy v1 and one v2 queued job | Each reconstructs its exact original schema/context without querying newer conversation state. |
| I-05 | Two-job pipeline | First result changes files and has summary | Second command contains first structured outcome and refreshed current file content only. |
| I-06 | Image contract | Build `api` and `pi-agent` targets | Prompt file SHA and mode match; API still lacks `/app/server/pi` and Pi executable. |
| I-07 | Helm/Compose parity | Render canonical runtime configurations | No prompt env/value/mount; auth PVC remains credential-only; both use common image artifact. |

### End-to-end tests

| ID | Flow | Pass condition |
|---|---|---|
| E-01 | Authenticated k3s full live flow | Compile, first AI edit, second context-dependent AI edit, and post-edit compile succeed. |
| E-02 | Context canary | Second request writes a codeword present only in the first request's history, proving DB reconstruction rather than Pi session reuse. |
| E-03 | Telemetry inspection | System/user/history/assistant sentinels are absent from pod logs, metric labels, and trace attributes. |

## 8. Implementation Tasks

### Task 1: Add the immutable prompt artifact and loader

**Files:**
- Create: `server/core/pi_agent_system_prompt.md`
- Create: `server/core/pi_agent_prompt.py`
- Create: `server/tests/test_pi_agent_prompt.py`
- Modify: `server/core/llm_file_edit.py`
- Modify: `server/tests/test_llm_file_edit_domain.py`
- Modify: `Dockerfile.api`

- [x] **Step 1: Write failing prompt-loader and exact-estimate tests**

Create `server/tests/test_pi_agent_prompt.py` with these concrete tests (retain the exact checked-in text assertion in the first test so accidental prompt edits require an intentional test change):

```python
from hashlib import sha256

import pytest

from core.pi_agent_prompt import (
    PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS,
    PI_AGENT_PROMPT_PATH,
    PiAgentPromptError,
    estimate_pi_agent_usage,
    load_pi_agent_prompt,
)


def test_checked_in_pi_prompt_loads_exact_bytes_and_hash():
    load_pi_agent_prompt.cache_clear()
    snapshot = load_pi_agent_prompt()
    raw = snapshot.path.read_bytes()
    assert snapshot.path == PI_AGENT_PROMPT_PATH.resolve()
    assert snapshot.content == raw.decode("utf-8")
    assert snapshot.sha256 == sha256(raw).hexdigest()
    assert len(raw) <= 32_768
    assert snapshot.content.startswith("Tertius file-edit policy:\n")


@pytest.mark.parametrize(
    "raw",
    [b"", b"  \n", b"bad\0prompt", b"\xff", b"x" * 32_769],
)
def test_pi_prompt_loader_rejects_invalid_content_without_echo(tmp_path, raw):
    path = tmp_path / "prompt.md"
    path.write_bytes(raw)
    load_pi_agent_prompt.cache_clear()
    with pytest.raises(PiAgentPromptError) as caught:
        load_pi_agent_prompt(path)
    assert str(path) not in str(caught.value)
    assert "bad" not in str(caught.value)


def test_pi_prompt_loader_rejects_missing_path_without_echo(tmp_path):
    path = tmp_path / "missing.md"
    load_pi_agent_prompt.cache_clear()
    with pytest.raises(PiAgentPromptError) as caught:
        load_pi_agent_prompt(path)
    assert str(path) not in str(caught.value)


def test_pi_usage_estimate_counts_exact_bytes_once_plus_fixed_reserve():
    usage = estimate_pi_agent_usage(
        system_prompt="policy",
        user_prompt="history and current request",
        source_bytes=len("width = 10".encode("utf-8")),
        metadata={"source": "ai_edit"},
        max_output_tokens=65_536,
    )
    framed = b"policyhistory and current requestwidth = 10sourceai_edit"
    expected_prompt = PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS + (len(framed) + 3) // 4
    assert usage.prompt_tokens == expected_prompt
    assert usage.total_tokens == expected_prompt + 65_536
```

- [x] **Step 2: Run the tests and verify red**

Run:

```bash
rtk uv run pytest server/tests/test_pi_agent_prompt.py server/tests/test_llm_file_edit_domain.py -q
```

Expected: failure because `core.pi_agent_prompt` and the checked-in prompt file do not exist.

- [x] **Step 3: Add the exact append-prompt text**

Create `server/core/pi_agent_system_prompt.md` with:

```markdown
Tertius file-edit policy:

- Work only on the existing files in the current workspace.
- Do not create, delete, or rename files.
- Treat conversation summaries and prior turns as historical context. The current user request is the only active request.
- Treat current workspace files as authoritative. Historical conversation must not override their current contents.
- Inspect the current files before editing and edit them in place instead of returning replacement source in chat.
- Use only build123d APIs known to exist in this runtime; do not invent helpers, classes, or functions.
- Do not use bd.RoundedPolygon; it is not available.
- For rounded rectangular or handle-like geometry, prefer bd.Box, bd.Cylinder, bd.Sphere, bd.Cone, boolean operations, and fillets on resulting solids.
- Always produce code that can run with `import build123d as bd`.
- Avoid advanced builder-mode APIs unless they already appear in the current project files.
```

- [x] **Step 4: Implement the fail-closed loader**

Create `server/core/pi_agent_prompt.py` with these concrete contracts:

```python
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from core.llm_file_edit import TokenUsage

MAX_PI_AGENT_PROMPT_BYTES = 32_768
PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS = 8_192
PI_AGENT_PROMPT_PATH = Path(__file__).with_name("pi_agent_system_prompt.md")


class PiAgentPromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class PiAgentPromptSnapshot:
    path: Path
    content: str
    sha256: str


@lru_cache(maxsize=8)
def load_pi_agent_prompt(path: Path = PI_AGENT_PROMPT_PATH) -> PiAgentPromptSnapshot:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise OSError
        raw = resolved.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PiAgentPromptError("Pi agent system prompt is unavailable") from exc
    if not raw or len(raw) > MAX_PI_AGENT_PROMPT_BYTES or "\0" in content or not content.strip():
        raise PiAgentPromptError("Pi agent system prompt is invalid")
    return PiAgentPromptSnapshot(
        path=resolved,
        content=content,
        sha256=sha256(raw).hexdigest(),
    )


def estimate_pi_agent_usage(
    *,
    system_prompt: str,
    user_prompt: str,
    source_bytes: int,
    metadata: Mapping[str, str],
    max_output_tokens: int,
) -> TokenUsage:
    metadata_bytes = sum(
        len(key.encode("utf-8")) + len(value.encode("utf-8"))
        for key, value in metadata.items()
    )
    framed_bytes = (
        len(system_prompt.encode("utf-8"))
        + len(user_prompt.encode("utf-8"))
        + source_bytes
        + metadata_bytes
    )
    prompt_tokens = PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS + (framed_bytes + 3) // 4
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=max_output_tokens,
        total_tokens=prompt_tokens + max_output_tokens,
    )
```

Remove `BUILD123D_RUNTIME_GUARDRAILS`, `FileEditPromptContents`, `file_edit_system_content()`, `file_edit_prompt_contents()`, `estimate_file_edit_usage()`, and `estimate_file_edit_tokens()` from `llm_file_edit.py`. Keep `TokenUsage`, file selection, request validation, and result models. Update callers to use `estimate_pi_agent_usage()`; do not retain a second estimator.

- [x] **Step 5: Lock the image mode and run green tests**

After the common `server/core` copy in `Dockerfile.api`, add:

```dockerfile
RUN chmod 0444 /app/server/core/pi_agent_system_prompt.md
```

Run:

```bash
rtk uv run pytest server/tests/test_pi_agent_prompt.py server/tests/test_llm_file_edit_domain.py -q
rtk uv run ruff check server/core/pi_agent_prompt.py server/tests/test_pi_agent_prompt.py
```

Expected: all selected tests and Ruff pass.

- [x] **Step 6: Commit**

```bash
rtk git add Dockerfile.api server/core/pi_agent_system_prompt.md server/core/pi_agent_prompt.py server/core/llm_file_edit.py server/tests/test_pi_agent_prompt.py server/tests/test_llm_file_edit_domain.py
rtk git commit -m "feat: add immutable Pi agent policy"
```

### Task 2: Define and compact structured conversation context

**Files:**
- Create: `server/core/pi_agent_conversation.py`
- Create: `server/tests/test_pi_agent_conversation.py`
- Modify: `server/core/pi_agent_messages.py`
- Modify: `server/tests/test_pi_agent_messages.py`

- [x] **Step 1: Write failing strict-model and rollover tests**

Create `server/tests/test_pi_agent_conversation.py` with the following core cases; keep command-version field-matrix tests in `test_pi_agent_messages.py` next to the existing command round-trip test:

```python
from types import SimpleNamespace

from core.pi_agent_conversation import (
    MAX_RENDERED_CONTEXT_TOKENS,
    advance_conversation_context,
    conversation_turn_from_job,
    estimated_context_tokens,
    next_conversation_context,
    render_conversation_context,
    render_legacy_prior_prompts,
)
from core.pi_agent_messages import PiAgentConversationContext, PiAgentConversationTurn


def successful_turn(index: int, *, request_size: int = 10):
    return PiAgentConversationTurn(
        user_request=f"request-{index}-" + "u" * request_size,
        status="succeeded",
        outcome="changed",
        assistant_summary=f"changed-{index}",
        changed_files=["design.py"],
    )


def test_context_rolls_oldest_turns_and_obeys_token_budget():
    context = PiAgentConversationContext()
    for index in range(12):
        context = advance_conversation_context(
            context,
            successful_turn(index, request_size=11_500),
        )
    assert len(context.recent_turns) <= 5
    assert context.recent_turns[-1].user_request.startswith("request-11-")
    assert len(context.rolling_summary) <= 8_000
    assert estimated_context_tokens(context) <= MAX_RENDERED_CONTEXT_TOKENS


def test_failed_job_uses_user_message_and_excludes_internal_payload():
    job = SimpleNamespace(
        status="failed",
        request_payload={
            "prompt": "try the change",
            "dispatched_conversation": {"rolling_summary": "secret recursion"},
        },
        result_payload={
            "files": [{"filename": "design.py", "content": "SOURCE_SENTINEL"}],
            "snapshot": {"id": "SNAPSHOT_SENTINEL"},
        },
        user_message="Provider was unavailable",
        error="RAW_INTERNAL_SENTINEL",
        error_code="provider_error",
    )
    turn = conversation_turn_from_job(job)
    assert turn.status == "failed"
    assert turn.error_code == "provider_error"
    assert turn.assistant_summary == "Provider was unavailable"
    serialized = turn.model_dump_json()
    assert "SOURCE_SENTINEL" not in serialized
    assert "SNAPSHOT_SENTINEL" not in serialized
    assert "RAW_INTERNAL_SENTINEL" not in serialized
    assert "secret recursion" not in serialized


def test_latest_persisted_context_advances_only_the_latest_job():
    persisted = PiAgentConversationContext(recent_turns=[successful_turn(1)])
    latest = SimpleNamespace(
        status="succeeded",
        request_payload={
            "prompt": "latest request",
            "dispatched_conversation": persisted.model_dump(mode="json"),
        },
        result_payload={"outcome": "no_changes", "message": "No update needed", "files": []},
        user_message=None,
        error=None,
        error_code=None,
    )
    context = next_conversation_context([latest])
    assert [turn.user_request for turn in context.recent_turns] == [
        persisted.recent_turns[0].user_request,
        "latest request",
    ]


def test_renderer_keeps_current_request_outside_historical_json():
    context = PiAgentConversationContext(recent_turns=[successful_turn(1)])
    rendered = render_conversation_context(context, "CURRENT_REQUEST_SENTINEL")
    history, current = rendered.split("Current user request:\n", maxsplit=1)
    assert "request-1" in history
    assert "CURRENT_REQUEST_SENTINEL" not in history
    assert current == "CURRENT_REQUEST_SENTINEL"


def test_legacy_renderer_labels_unknown_outcomes_without_fabricating_turns():
    rendered = render_legacy_prior_prompts(
        ["LEGACY_REQUEST_SENTINEL"],
        "CURRENT_REQUEST_SENTINEL",
    )
    history, current = rendered.split("Current user request:\n", maxsplit=1)
    assert "LEGACY_REQUEST_SENTINEL" in history
    assert "outcome" not in history
    assert "succeeded" not in history
    assert "CURRENT_REQUEST_SENTINEL" not in history
    assert current == "CURRENT_REQUEST_SENTINEL"
```

In `test_pi_agent_messages.py`, parameterize `(schema_version, prior_prompts, conversation, system_prompt_sha256, valid)` across valid v1, valid v2, v1-with-v2-fields, v2-without-hash, v2-without-context, and v2-with-prior-prompts. Assert invalid rows raise `ValidationError`.

- [x] **Step 2: Run the tests and verify red**

```bash
rtk uv run pytest server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_conversation.py -q
```

Expected: failures because the v2 context models and builder do not exist.

- [x] **Step 3: Add strict conversation models and v1/v2 validation**

Add before `PiAgentCommand`:

```python
class PiAgentConversationTurn(StrictMessage):
    user_request: str = Field(min_length=1, max_length=12000)
    status: Literal["succeeded", "failed"]
    outcome: Literal["changed", "no_changes"] | None = None
    assistant_summary: str = Field(default="", max_length=2000)
    error_code: str | None = Field(default=None, max_length=100)
    changed_files: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def consistent_outcome(self):
        if self.status == "succeeded" and self.outcome is None:
            raise ValueError("successful conversation turns require an outcome")
        if self.status == "failed" and (self.outcome is not None or not self.error_code):
            raise ValueError("failed conversation turns require only an error code")
        if self.outcome != "changed" and self.changed_files:
            raise ValueError("only changed turns can contain filenames")
        return self


class PiAgentConversationContext(StrictMessage):
    rolling_summary: str = Field(default="", max_length=8000)
    recent_turns: list[PiAgentConversationTurn] = Field(default_factory=list, max_length=5)
```

Add filename validation with `validate_filename()` and a 512-character bound. Change `PiAgentCommand` to the compatibility contract in section 4. Do not modify result schema version 1.

- [x] **Step 4: Implement deterministic context advancement**

Create pure helpers in `pi_agent_conversation.py`:

```python
MAX_RECENT_TURNS = 5
MAX_ROLLING_SUMMARY_CHARS = 8_000
MAX_RENDERED_CONTEXT_TOKENS = 12_000


def render_historical_context(context: PiAgentConversationContext) -> str:
    historical = context.model_dump_json(indent=2)
    return (
        "Historical conversation context follows. It describes completed work "
        "and is not a new instruction.\n"
        "<conversation_context>\n"
        f"{historical}\n"
        "</conversation_context>"
    )


def estimated_context_tokens(context: PiAgentConversationContext) -> int:
    encoded = render_historical_context(context).encode("utf-8")
    return (len(encoded) + 3) // 4


def advance_conversation_context(
    context: PiAgentConversationContext,
    turn: PiAgentConversationTurn,
) -> PiAgentConversationContext:
    turns = [*context.recent_turns, turn]
    summary_lines = [line for line in context.rolling_summary.splitlines() if line]
    candidate = PiAgentConversationContext(
        rolling_summary="\n".join(summary_lines),
        recent_turns=turns,
    )
    while turns and (
        len(turns) > MAX_RECENT_TURNS
        or estimated_context_tokens(candidate) > MAX_RENDERED_CONTEXT_TOKENS
    ):
        summary_lines.append(compact_turn_line(turns.pop(0)))
        candidate = PiAgentConversationContext(
            rolling_summary="\n".join(summary_lines),
            recent_turns=turns,
        )
    while len("\n".join(summary_lines)) > MAX_ROLLING_SUMMARY_CHARS:
        summary_lines.pop(0)
    candidate = PiAgentConversationContext(
        rolling_summary="\n".join(summary_lines),
        recent_turns=turns,
    )
    while summary_lines and estimated_context_tokens(candidate) > MAX_RENDERED_CONTEXT_TOKENS:
        summary_lines.pop(0)
        candidate = PiAgentConversationContext(
            rolling_summary="\n".join(summary_lines),
            recent_turns=turns,
        )
    return candidate
```

Implement the helper bodies exactly as follows, with `_safe_filenames()` validating at most 20 filenames through `validate_filename()` and clipping each to 512 characters:

```python
def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def compact_turn_line(turn: PiAgentConversationTurn) -> str:
    result = turn.assistant_summary or turn.error_code or turn.status
    return (
        f"- user={_clip(turn.user_request, 600)!r}; "
        f"status={turn.status}; outcome={turn.outcome or 'none'}; "
        f"result={_clip(result, 400)!r}"
    )


def _safe_filenames(raw_files: object) -> list[str]:
    if not isinstance(raw_files, list):
        return []
    filenames: list[str] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        if not isinstance(filename, str) or len(filename) > 512:
            continue
        try:
            filenames.append(validate_filename(filename))
        except ValueError:
            continue
        if len(filenames) == 20:
            break
    return filenames


def conversation_turn_from_job(job) -> PiAgentConversationTurn | None:
    request_payload = job.request_payload if isinstance(job.request_payload, dict) else {}
    user_request = request_payload.get("prompt")
    if not isinstance(user_request, str) or not user_request.strip():
        return None
    if job.status == "failed":
        error_code = job.error_code if isinstance(job.error_code, str) and job.error_code else "unknown_failure"
        user_message = job.user_message if isinstance(job.user_message, str) else "Previous request failed."
        return PiAgentConversationTurn(
            user_request=user_request,
            status="failed",
            assistant_summary=_clip(user_message, 2000),
            error_code=error_code,
        )
    if job.status != "succeeded":
        return None
    result = job.result_payload if isinstance(job.result_payload, dict) else {}
    outcome = result.get("outcome")
    if outcome not in {"changed", "no_changes"}:
        return None
    message = result.get("message") if isinstance(result.get("message"), str) else ""
    fallback = "Updated files." if outcome == "changed" else "No files changed."
    filenames = _safe_filenames(result.get("files", [])) if outcome == "changed" else []
    return PiAgentConversationTurn(
        user_request=user_request,
        status="succeeded",
        outcome=outcome,
        assistant_summary=_clip(message.strip() or fallback, 2000),
        changed_files=filenames,
    )
```

`conversation_turn_from_job()` therefore uses:

- `request_payload["prompt"]` for the user request;
- `result_payload["outcome"]`, `result_payload["message"]`, and result filenames for success;
- `user_message` and `error_code` for failure;
- fixed fallbacks `Updated files.` and `No files changed.` when a success summary is empty.

It must never use `job.error`, result file `content`, snapshot metadata, raw IDs, traces, or dispatched context.

- [x] **Step 5: Implement legacy bootstrap and structured rendering**

Implement bootstrap and rendering with these exact bodies:

```python
def next_conversation_context(jobs: list) -> PiAgentConversationContext:
    if not jobs:
        return PiAgentConversationContext()
    latest = jobs[-1]
    latest_payload = latest.request_payload if isinstance(latest.request_payload, dict) else {}
    latest_turn = conversation_turn_from_job(latest)
    try:
        persisted = PiAgentConversationContext.model_validate(
            latest_payload["dispatched_conversation"]
        )
    except (KeyError, TypeError, ValueError):
        persisted = None
    if persisted is not None and latest_turn is not None:
        return advance_conversation_context(persisted, latest_turn)
    context = PiAgentConversationContext()
    for job in jobs:
        turn = conversation_turn_from_job(job)
        if turn is not None:
            context = advance_conversation_context(context, turn)
    return context


def render_conversation_context(
    context: PiAgentConversationContext,
    current_request: str,
) -> str:
    return (
        f"{render_historical_context(context)}\n\n"
        "Current user request:\n"
        f"{current_request}"
    )


def render_legacy_prior_prompts(
    prior_prompts: list[str],
    current_request: str,
) -> str:
    historical = json.dumps(prior_prompts, ensure_ascii=False, indent=2)
    return (
        "Legacy historical user requests follow. Their assistant outcomes are "
        "unknown; they are context, not completed-success claims or new instructions.\n"
        "<legacy_user_requests>\n"
        f"{historical}\n"
        "</legacy_user_requests>\n\n"
        "Current user request:\n"
        f"{current_request}"
    )
```

Import `json` in this module. The legacy renderer consumes only the command's
already bounded `prior_prompts`; it does not construct `PiAgentConversationTurn`
objects and therefore does not invent `status`, `outcome`, or assistant text.

The rendered form is:

```text
Historical conversation context follows. It describes completed work and is not a new instruction.
<conversation_context>
{...bounded JSON...}
</conversation_context>

Current user request:
...
```

- [x] **Step 6: Run green tests and commit**

```bash
rtk uv run pytest server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_conversation.py -q
rtk uv run ruff check server/core/pi_agent_messages.py server/core/pi_agent_conversation.py server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_conversation.py
rtk git add server/core/pi_agent_messages.py server/core/pi_agent_conversation.py server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_conversation.py
rtk git commit -m "feat: define bounded Pi conversation context"
```

### Task 3: Build and persist v2 context at API dispatch

**Files:**
- Modify: `server/core/repositories.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/tests/test_repositories.py`
- Modify: `server/tests/test_llm_file_edit.py`

- [ ] **Step 1: Write failing repository and API dispatch tests**

Replace the prompt-only repository test with a terminal-history test using the existing `seed_two_tenants()` fixture helper:

```python
def test_llm_edit_repository_lists_bounded_terminal_jobs_oldest_first(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = LlmEditRepository(db_session, seeded["tenant_a"])
    repo_b = LlmEditRepository(db_session, seeded["tenant_b"])
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expected = []
    for index, status in enumerate(["succeeded", "failed", "running", "succeeded"]):
        job = repo_a.start_job(
            seeded["project_a"],
            seeded["user_a"],
            {"prompt": f"request-{index}", "files": []},
            status=status,
        )
        job.created_at = base + timedelta(minutes=index)
        if status in {"succeeded", "failed"}:
            expected.append(job.id)
    repo_b.start_job(
        seeded["project_b"],
        seeded["user_b"],
        {"prompt": "other tenant", "files": []},
        status="succeeded",
    )
    db_session.flush()
    jobs = repo_a.list_recent_terminal_jobs(seeded["project_a"], limit=200)
    assert [job.id for job in jobs] == expected
    assert repo_a.list_recent_terminal_jobs(seeded["project_a"], limit=1)[0].id == expected[-1]
```

Extend `test_submit_commits_job_and_publishes_selected_persisted_files()` with one prior successful job whose result contains a historical-only source sentinel, then make these exact assertions after dispatch:

```python
snapshot = load_pi_agent_prompt()
prior = LlmEditJob(
    tenant_id=seeded_tenant.tenant_id,
    project_id=seeded_tenant.project_id,
    requested_by=seeded_tenant.user_id,
    status="succeeded",
    request_payload={"prompt": "Earlier request", "files": []},
    result_payload={
        "outcome": "changed",
        "message": "Adjusted the bracket",
        "files": [
            {
                "filename": "historical.py",
                "content": "HISTORICAL_SOURCE_SENTINEL",
                "changed": True,
            }
        ],
    },
)
db_session.add(prior)
db_session.commit()

command = commands[0]
job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
assert command.schema_version == 2
assert command.conversation.model_dump(mode="json") == job.request_payload["dispatched_conversation"]
assert command.system_prompt_sha256 == snapshot.sha256
assert job.request_payload["dispatched_system_prompt_sha256"] == snapshot.sha256
serialized = command.model_dump_json()
assert snapshot.content not in serialized
assert str(snapshot.path) not in serialized
assert "HISTORICAL_SOURCE_SENTINEL" not in command.conversation.model_dump_json()
assert command.conversation.recent_turns[-1].assistant_summary == "Adjusted the bracket"
```

Keep the endpoint submission/capture setup already present at `server/tests/test_llm_file_edit.py:31-43`; insert the prior job before the POST and the assertions after the existing identity/file assertions.

- [ ] **Step 2: Run the tests and verify red**

```bash
rtk uv run pytest server/tests/test_repositories.py server/tests/test_llm_file_edit.py -q
```

Expected: failures because dispatch still uses `list_recent_prompts()` and schema v1.

- [ ] **Step 3: Replace prompt lookup with terminal-job retrieval**

Implement:

```python
def list_recent_terminal_jobs(
    self,
    project_id: UUID,
    *,
    limit: int = 200,
) -> list[LlmEditJob]:
    normalized_limit = max(1, min(limit, 200))
    jobs = list(
        self.db.scalars(
            select(LlmEditJob)
            .where(
                LlmEditJob.tenant_id == self.tenant_id,
                LlmEditJob.project_id == project_id,
                LlmEditJob.status.in_(["succeeded", "failed"]),
            )
            .order_by(desc(LlmEditJob.created_at), desc(LlmEditJob.id))
            .limit(normalized_limit)
        )
    )
    return list(reversed(jobs))
```

Delete `list_recent_prompts()` after all callers/tests move.

- [ ] **Step 4: Build one exact dispatch context under the existing project lock**

In `start_llm_file_edit_job()`:

1. Load `prompt_snapshot = load_pi_agent_prompt()` before quota estimation.
2. Query terminal jobs after the project row is locked.
3. Build `conversation = next_conversation_context(history_jobs)`.
4. Build the exact user prompt using the same shared renderer the worker calls.
5. Estimate the append prompt + rendered user prompt + selected current source characters + request metadata exactly once, then add the fixed 8,192-token Pi base/tool reserve.
6. Persist:

```python
"dispatched_command_schema_version": 2,
"dispatched_conversation": conversation.model_dump(mode="json"),
"dispatched_system_prompt_sha256": prompt_snapshot.sha256,
```

7. Publish `PiAgentCommand(schema_version=2, conversation=conversation, system_prompt_sha256=prompt_snapshot.sha256, ...)` with `prior_prompts=[]`.

Catch `PiAgentPromptError` separately and return HTTP 503 with fixed body:

```json
{"success": false, "error": "AI editing is not configured", "retryable": false}
```

Do not log the exception object because its cause can contain a filesystem path.

- [ ] **Step 5: Run green tests and commit**

```bash
rtk uv run pytest server/tests/test_repositories.py server/tests/test_llm_file_edit.py server/tests/test_pi_agent_prompt.py -q
rtk git add server/core/repositories.py server/workflows/intus/intus_server.py server/tests/test_repositories.py server/tests/test_llm_file_edit.py
rtk git commit -m "feat: dispatch structured Pi conversation context"
```

### Task 4: Use the prompt file and capture assistant outcomes in the worker

**Files:**
- Modify: `server/core/pi_agent_rpc.py`
- Modify: `server/workflows/intus/pi_agent_job.py`
- Modify: `server/tests/test_pi_agent_rpc.py`
- Modify: `server/tests/test_pi_agent_job.py`

- [ ] **Step 1: Write failing RPC and worker tests**

Add these concrete tests beside the existing RPC and worker contract tests:

```python
def test_pi_argv_uses_prompt_path_without_prompt_bytes(tmp_path):
    path = tmp_path / "APPEND_SYSTEM.md"
    path.write_text("PROMPT_ARGV_SENTINEL", encoding="utf-8")
    argv = build_pi_argv(
        "pi",
        provider="openai-codex",
        model="gpt-5.5",
        thinking="high",
        system_prompt_path=path,
        extension_path="/opt/tertius-pi/workspace-guard.ts",
    )
    index = argv.index("--append-system-prompt")
    assert argv[index + 1] == str(path)
    assert "PROMPT_ARGV_SENTINEL" not in argv


@pytest.mark.asyncio
async def test_rpc_captures_only_bounded_final_assistant_text(fake_pi, tmp_path):
    path = tmp_path / "APPEND_SYSTEM.md"
    path.write_text("policy", encoding="utf-8")
    result = await run_pi_agent(
        "prompt",
        **settings(fake_pi, "assistant-summary"),
        system_prompt_path=path,
    )
    assert result.assistant_summary == "first block second block"[:2000]


@pytest.mark.asyncio
async def test_worker_rejects_v2_prompt_hash_mismatch_without_calling_pi(
    monkeypatch, tmp_path
):
    import workflows.intus.pi_agent_job as job

    request = command([source("design.py", "x = 1")]).model_copy(
        update={
            "schema_version": 2,
            "prior_prompts": [],
            "conversation": PiAgentConversationContext(),
            "system_prompt_sha256": "0" * 64,
        }
    )
    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(
        job,
        "run_pi_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Pi must not run on prompt mismatch")
        ),
    )
    result = await job.execute_pi_agent_command(request, worker_settings())
    assert result.status == "failed"
    assert result.error_code == "worker_config_mismatch"
    assert result.retryable is True
    assert not (tmp_path / "workspace").exists()
```

Add this exact branch to the fake RPC script after its existing scenarios:

```python
elif scenario == "assistant-summary":
    events = [
        {
            "type": "message_end",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "USER_SENTINEL"}],
            },
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "call-1",
            "toolName": "read",
            "result": {
                "content": [{"type": "text", "text": "TOOL_SENTINEL"}],
                "details": {},
            },
            "isError": False,
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "THINKING_SENTINEL" * 210},
                    {"type": "text", "text": "first block "},
                    {"type": "text", "text": "second block"},
                ],
                "stopReason": "stop",
            },
        },
    ]
```

Add the missing-path test exactly as follows:

```python
@pytest.mark.asyncio
async def test_rpc_rejects_missing_prompt_path_before_spawn(
    monkeypatch, fake_pi, tmp_path
):
    async def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent(
            "prompt",
            **settings(fake_pi),
            system_prompt_path=tmp_path / "missing.md",
        )
    assert caught.value.code == "worker_config_error"
```

Retain the existing v1 worker test and replace its prior-prompt assertions with:

```python
legacy, current = captured["prompt"].split("Current user request:\n", maxsplit=1)
assert "<legacy_user_requests>" in legacy
assert '"First request"' in legacy
assert '"Second request"' in legacy
assert "outcome" not in legacy
assert "succeeded" not in legacy
assert current.startswith("Current request")
```

Change the main v2 worker test to use `load_pi_agent_prompt().sha256` and assert no-change `rpc.assistant_summary` survives in `PiAgentResult`.

- [ ] **Step 2: Run the tests and verify red**

```bash
rtk uv run pytest server/tests/test_pi_agent_rpc.py server/tests/test_pi_agent_job.py -q
```

Expected: failures because prompt text is still in argv and assistant summaries are empty.

- [ ] **Step 3: Change RPC to path-only append prompt**

Rename `system_prompt` parameters to `system_prompt_path`. Before spawn, require the path to be an existing readable regular file. Build argv with:

```python
[
    executable,
    "--mode",
    "rpc",
    "--no-session",
    "--provider",
    provider,
    "--model",
    model,
    "--thinking",
    thinking,
    "--tools",
    "read,edit,write,grep,find,ls",
    "--no-extensions",
    "--extension",
    extension_path,
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
    "--no-approve",
    "--append-system-prompt",
    str(system_prompt_path),
]
```

Never read or log prompt text in `pi_agent_rpc.py`; the shared loader owns validation/content/hash and Pi owns reading the validated file.

- [ ] **Step 4: Extract the final bounded assistant text**

Extend normalized events with a private assistant event:

```python
def _assistant_text(message: object) -> str | None:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    text = "".join(
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    return text[:2000] or None
```

On assistant `message_end`, queue `{"type": "_assistant_message", "text": text}` without logging it. Track the latest value until `agent_settled` and return it in `PiAgentRpcResult.assistant_summary`.

- [ ] **Step 5: Validate v2 prompt identity and render structured history**

At the start of `execute_pi_agent_command()`:

```python
snapshot = load_pi_agent_prompt()
if command.schema_version == 2 and command.system_prompt_sha256 != snapshot.sha256:
    return _failure(
        command,
        started_at,
        "worker_config_mismatch",
        "AI worker configuration changed; retry after deployment completes.",
        True,
        execution_id=execution_id,
    )
```

Catch `PiAgentPromptError` separately and return non-retryable `worker_config_error` with `Pi agent policy is unavailable.`. Do not include the path, underlying exception, content, or hash in the result or logs.

For v2, call `render_conversation_context(command.conversation, command.prompt)`.
For v1, call `render_legacy_prior_prompts(command.prior_prompts, command.prompt)`
directly; do not create `PiAgentConversationTurn` objects for legacy prompts.
Pass the selected rendered history/current-request block plus current
filenames/active filename through the shared user-prompt renderer, then pass
`snapshot.path` to RPC. Set `assistant_summary=rpc.assistant_summary` for both
`changed` and `no_changes`.

- [ ] **Step 6: Run green tests and commit**

```bash
rtk uv run pytest server/tests/test_pi_agent_rpc.py server/tests/test_pi_agent_job.py -q
rtk uv run ruff check server/core/pi_agent_rpc.py server/workflows/intus/pi_agent_job.py
rtk git add server/core/pi_agent_rpc.py server/workflows/intus/pi_agent_job.py server/tests/test_pi_agent_rpc.py server/tests/test_pi_agent_job.py
rtk git commit -m "feat: run Pi with shared prompt and structured history"
```

### Task 5: Preserve exact context across queued-job retries

**Files:**
- Modify: `server/workflows/intus/pi_agent_result_consumer.py`
- Modify: `server/tests/test_pi_agent_result_consumer.py`
- Modify: `server/tests/test_pi_agent_pipeline_e2e.py`

- [ ] **Step 1: Write failing v1/v2 republish and two-job pipeline tests**

Convert `test_queued_reconciliation_republishes_with_same_deterministic_id()` to v2 by adding this setup and these assertions around its existing two republish calls:

```python
conversation = PiAgentConversationContext(
    rolling_summary="older summary",
    recent_turns=[
        PiAgentConversationTurn(
            user_request="Earlier request",
            status="succeeded",
            outcome="no_changes",
            assistant_summary="Already satisfied",
        )
    ],
)
payload.update(
    {
        "dispatched_command_schema_version": 2,
        "dispatched_conversation": conversation.model_dump(mode="json"),
        "dispatched_system_prompt_sha256": "a" * 64,
    }
)

newer = LlmEditRepository(db_session, seeded_tenant.tenant_id).start_job(
    seeded_tenant.project_id,
    seeded_tenant.user_id,
    {"prompt": "Newer terminal request", "files": []},
    status="failed",
)
newer.error_code = "provider_error"
newer.user_message = "Newer failure"
db_session.commit()

assert await republish_queued_pi_agent_jobs(
    db_session, publisher, settings, backoff_seconds=0
) == 0
republished = publisher.calls[0][0][1]
assert republished.schema_version == 2
assert republished.conversation == conversation
assert republished.system_prompt_sha256 == "a" * 64
assert "Newer terminal request" not in republished.model_dump_json()
```

Add a separate v1 case using the existing `_job()`, `Publisher`, and settings fixtures:

```python
@pytest.mark.asyncio
async def test_queued_reconciliation_preserves_v1_context(
    db_session, seeded_tenant
):
    file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    job = _job(db_session, seeded_tenant, file, _result(seeded_tenant, file))
    payload = dict(job.request_payload)
    payload.update(
        {
            "dispatch_attempted_at": (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat(),
            "dispatch_created_at": datetime.now(timezone.utc).isoformat(),
            "dispatched_provider": "openai-codex",
            "dispatched_model": "gpt-5.5",
            "dispatched_thinking": "high",
            "dispatched_prior_prompts": ["legacy request"],
        }
    )
    job.request_payload = payload
    flag_modified(job, "request_payload")
    db_session.commit()
    publisher = Publisher()
    settings = SimpleNamespace(
        pi_agent_request_subject="request",
        pi_agent_request_max_bytes=524288,
        pi_agent_provider="openai-codex",
        pi_agent_model="gpt-5.5",
        pi_agent_thinking="high",
    )

    assert await republish_queued_pi_agent_jobs(
        db_session, publisher, settings, backoff_seconds=0
    ) == 1
    command = publisher.calls[0][0][1]
    assert command.schema_version == 1
    assert command.conversation is None
    assert command.system_prompt_sha256 is None
    assert command.prior_prompts == ["legacy request"]
```

In `test_pi_agent_pipeline_e2e.py`, extend the existing successful pipeline test after its first persisted result with the following second dispatch. Add `datetime` and `timezone` to its imports:

```python
current = db_session.get(ProjectFile, file.id)
current.content += "# REFRESHED_FILE_SENTINEL\n"
current.updated_at = datetime.now(timezone.utc)
db_session.commit()
refreshed_content = current.content
first_prompt = "Add height"
second_response = authenticated_intus_client.post(
    "/projects/default_purlin/files/llm-edit/jobs",
    json={
        "prompt": "Use completed prior request as context",
        "files": [pointer(current)],
        "active_file_id": str(current.id),
    },
)
assert second_response.status_code == 202
second_message = await run_pipeline(js, settings, db_session, monkeypatch)
second = PiAgentCommand.model_validate_json(second_message.data)
assert second.schema_version == 2
assert second.conversation.recent_turns[-1].user_request == first_prompt
assert second.conversation.recent_turns[-1].assistant_summary == "Added height"
assert second.conversation.recent_turns[-1].status == "succeeded"
assert second.files[0].content == refreshed_content
assert "REFRESHED_FILE_SENTINEL" in second.files[0].content
assert "REFRESHED_FILE_SENTINEL" not in second.conversation.model_dump_json()
```

- [ ] **Step 2: Run the tests and verify red**

```bash
rtk uv run pytest server/tests/test_pi_agent_result_consumer.py server/tests/test_pi_agent_pipeline_e2e.py -q
```

Expected: failures because republish only reconstructs schema v1 `prior_prompts`.

- [ ] **Step 3: Reconstruct the persisted command version exactly**

In `republish_queued_pi_agent_jobs()`:

```python
schema_version = int(payload.get("dispatched_command_schema_version", 1))
if schema_version == 2:
    conversation = PiAgentConversationContext.model_validate(
        payload["dispatched_conversation"]
    )
    prompt_hash = payload["dispatched_system_prompt_sha256"]
    prior_prompts = []
else:
    conversation = None
    prompt_hash = None
    prior_prompts = payload.get("dispatched_prior_prompts", [])

command = PiAgentCommand(
    schema_version=schema_version,
    job_id=job.id,
    tenant_id=job.tenant_id,
    project_id=job.project_id,
    provider=payload["dispatched_provider"],
    model=payload["dispatched_model"],
    thinking=payload["dispatched_thinking"],
    prompt=payload["prompt"],
    conversation=conversation,
    system_prompt_sha256=prompt_hash,
    prior_prompts=prior_prompts,
    active_file_id=payload.get("active_file_id"),
    files=command_files,
    created_at=datetime.fromisoformat(payload["dispatch_created_at"]),
    traceparent=payload.get("dispatch_traceparent"),
    tracestate=payload.get("dispatch_tracestate"),
)
```

Do not call the repository history query or prompt loader while reconstructing a queued command. Continue validating provider/model/thinking and the exact dispatched file manifest.

- [ ] **Step 4: Verify result summaries are safe inputs to subsequent context**

Add this exact assertion block to the success-result test after `_result_payload()` is persisted, and repeat it with `outcome="no_changes"` and no files:

```python
persisted = db_session.get(LlmEditJob, result.job_id)
assert persisted.result_payload["message"] == result.assistant_summary
turn = conversation_turn_from_job(persisted)
assert turn.outcome == result.outcome
assert turn.assistant_summary == result.assistant_summary
assert turn.changed_files == [edit.filename for edit in result.changed_files]
serialized = turn.model_dump_json()
for forbidden in (
    result.changed_files[0].content if result.changed_files else "SOURCE_SENTINEL",
    str(persisted.result_payload.get("snapshot")),
    "prompt_tokens",
    result.provider,
):
    assert forbidden not in serialized
```

- [ ] **Step 5: Run green tests and commit**

```bash
rtk uv run pytest server/tests/test_pi_agent_result_consumer.py server/tests/test_pi_agent_pipeline_e2e.py server/tests/test_pi_agent_conversation.py -q
rtk git add server/workflows/intus/pi_agent_result_consumer.py server/tests/test_pi_agent_result_consumer.py server/tests/test_pi_agent_pipeline_e2e.py
rtk git commit -m "fix: preserve Pi context across queued retries"
```

### Task 6: Remove legacy prompt configuration and enforce image/runtime parity

**Files:**
- Modify: `server/core/config.py`
- Modify: `server/tests/test_config.py`
- Modify: `server/.env.example`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/templates/pi-agent-worker.yaml`
- Modify: `infra/charts/tertius/README.md`
- Modify: `.github/workflows/tests.yml`
- Modify: `server/tests/test_pi_agent_image_config.py`
- Modify: `scripts/test-deployment-config.sh`
- Modify: `scripts/check-runtime-parity.sh`

- [ ] **Step 1: Write failing removal and image-contract checks**

Add `pi_agent_system_prompt` to the existing removed-settings set in `test_settings_removes_legacy_llm_provider_fields()`. Replace the deployment prompt fixture/check with these exact shell assertions in `scripts/test-deployment-config.sh`:

```bash
if rg -q 'PI_AGENT_SYSTEM_PROMPT|piAgent\.systemPrompt' \
  "$ROOT_DIR/server/.env.example" \
  "$ROOT_DIR/infra/charts/tertius/values.yaml" \
  "$ROOT_DIR/infra/charts/tertius/templates" \
  "$ROOT_DIR/docker-compose.yml" \
  "$ROOT_DIR/docker-compose.parity.yml"; then
  echo 'Legacy Pi system prompt runtime configuration is still present.' >&2
  exit 1
fi
```

In `server/tests/test_pi_agent_image_config.py`, add source-level assertions before the runtime image inspection:

```python
def test_pi_prompt_is_common_image_artifact_not_runtime_config():
    prompt = Path("server/core/pi_agent_system_prompt.md")
    assert prompt.is_file()
    assert prompt.read_text(encoding="utf-8").startswith("Tertius file-edit policy:")
    dockerfile = Path("Dockerfile.api").read_text(encoding="utf-8")
    assert "COPY server/core/ ./server/core/" in dockerfile
    assert "chmod 0444 /app/server/core/pi_agent_system_prompt.md" in dockerfile
    rendered_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            Path("server/.env.example"),
            Path("infra/charts/tertius/values.yaml"),
            Path("infra/charts/tertius/templates/pi-agent-worker.yaml"),
        )
    )
    assert "PI_AGENT_SYSTEM_PROMPT" not in rendered_sources
    assert "systemPrompt:" not in rendered_sources
```

The container assertions in Step 3 prove identical SHA, root ownership/mode, non-writability, and continued absence of Pi tooling from the API image.

- [ ] **Step 2: Remove the runtime override surfaces**

Delete:

- `Settings.pi_agent_system_prompt`;
- `PI_AGENT_SYSTEM_PROMPT` from `.env.example`;
- `piAgent.systemPrompt` from Helm values;
- conditional worker environment rendering;
- deployment test fixtures that set a worker-only prompt.

Do not add replacement environment variables, ConfigMaps, Secrets, mounts, or values.

- [ ] **Step 3: Add container assertions to CI**

After building both targets in `.github/workflows/tests.yml`, run equivalent assertions:

```bash
api_sha=$(docker run --rm --entrypoint sha256sum tertius-api:test /app/server/core/pi_agent_system_prompt.md | awk '{print $1}')
worker_sha=$(docker run --rm --entrypoint sha256sum tertius-pi-agent:test /app/server/core/pi_agent_system_prompt.md | awk '{print $1}')
test "$api_sha" = "$worker_sha"
docker run --rm --entrypoint sh tertius-api:test -c 'test ! -w /app/server/core/pi_agent_system_prompt.md && test ! -e /app/server/pi && ! command -v pi'
docker run --rm --entrypoint sh tertius-pi-agent:test -c 'test ! -w /app/server/core/pi_agent_system_prompt.md'
```

- [ ] **Step 4: Run config, parity, and image validation**

```bash
rtk uv run pytest server/tests/test_config.py server/tests/test_pi_agent_image_config.py -q
bash scripts/test-deployment-config.sh
bash scripts/check-runtime-parity.sh
docker build --target api -t tertius-api:test -f Dockerfile.api .
docker build --target pi-agent -t tertius-pi-agent:test -f Dockerfile.api .
```

Expected: all commands exit 0 and both images report the same prompt SHA.

- [ ] **Step 5: Commit**

```bash
rtk git add server/core/config.py server/tests/test_config.py server/.env.example infra/charts/tertius/values.yaml infra/charts/tertius/templates/pi-agent-worker.yaml infra/charts/tertius/README.md .github/workflows/tests.yml server/tests/test_pi_agent_image_config.py scripts/test-deployment-config.sh scripts/check-runtime-parity.sh
rtk git commit -m "chore: remove runtime Pi prompt overrides"
```

### Task 7: Extend telemetry safety, docs, and the two-turn live canary

**Files:**
- Modify: `server/tests/test_pi_agent_telemetry_safety.py`
- Modify: `scripts/smoke-live-flow.sh`
- Modify: `scripts/test-smoke-live-flow-config.sh`
- Modify: `docs/configuration-and-secrets.md`
- Modify: `docs/harness/runtime-parity.md`
- Modify: `docs/harness/browser-validation.md`
- Modify: `docs/harness/queries/traces.md`

- [ ] **Step 1: Expand static telemetry safety tests**

Add `server/core/pi_agent_prompt.py` and `server/core/pi_agent_conversation.py` to `FILES`. Add these exact forbidden attribute/logger identifiers:

```python
{
    "conversation",
    "history",
    "assistant_summary",
    "rolling_summary",
    "system_prompt",
    "system_prompt_sha256",
}
```

The AST safety scanner only evaluates logger arguments and telemetry/span attributes, so forbidding `system_prompt_sha256` here does not prevent it from being persisted in `request_payload`. Add both `set_attribute("system_prompt_sha256", prompt_hash)` and `logger.info("prompt hash %s", system_prompt_sha256)` to the rejected mutation set.

- [ ] **Step 2: Add an opt-in two-turn live-flow canary**

Add `LIVE_FLOW_VERIFY_CONVERSATION` defaulting to `false`. When true:

1. Generate or accept overrides for `TERTIUS_CONTEXT_USER_CANARY_<hex>` and `TERTIUS_CONTEXT_ASSISTANT_CANARY_<hex>`.
2. First request asks for a valid geometry edit, says to remember but not write the user canary, and requires the final assistant summary to end with the assistant canary.
3. Assert the first job's persisted result message contains the assistant canary and neither canary occurs in current source files.
4. Second request says: `Add a Python comment containing the codeword from my previous user request; do not change geometry.` It contains neither canary literally.
5. Poll the second job, fetch current files, assert the user canary occurs and the assistant canary does not, then compile the second job.
6. Write both generated values to `.tmp/harness/live-flow-sensitive-canaries.env` with mode `0600` without printing them.

Change `ai_edit_and_wait()` to take prompt and label arguments rather than reading a global mutable prompt. Ensure command traces never echo either prompt.

Immediately after `set -Eeuo pipefail`, define the repository root used for the
mode-`0600` canary file:

```bash
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
```

Implement the enabled branch with this concrete shell flow after the existing first compile. `api_request`, `json_get`, and `tmpfile` are the existing helpers in the script:

```bash
if [ "$LIVE_FLOW_VERIFY_CONVERSATION" = true ]; then
  random_suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
  LIVE_FLOW_USER_CANARY=${LIVE_FLOW_USER_CANARY:-TERTIUS_CONTEXT_USER_CANARY_${random_suffix}}
  LIVE_FLOW_ASSISTANT_CANARY=${LIVE_FLOW_ASSISTANT_CANARY:-TERTIUS_CONTEXT_ASSISTANT_CANARY_${random_suffix}}
  canary_file="${ROOT_DIR}/.tmp/harness/live-flow-sensitive-canaries.env"
  mkdir -p "$(dirname "$canary_file")"
  umask 077
  printf 'LIVE_FLOW_USER_CANARY=%q\nLIVE_FLOW_ASSISTANT_CANARY=%q\n' \
    "$LIVE_FLOW_USER_CANARY" "$LIVE_FLOW_ASSISTANT_CANARY" > "$canary_file"

  first_prompt="Increase the main model width by 1 mm. Remember but do not write the codeword ${LIVE_FLOW_USER_CANARY}. End your final summary with ${LIVE_FLOW_ASSISTANT_CANARY}."
  first_job_id=$(ai_edit_and_wait "$first_prompt" "AI edit context seed")
  first_status=$(api_request GET "${API_BASE_URL}/projects/${PROJECT_NAME}/files/llm-edit/jobs/${first_job_id}")
  first_message=$(json_get "$first_status" result.message)
  case "$first_message" in
    *"$LIVE_FLOW_ASSISTANT_CANARY") ;;
    *) echo 'FAIL context seed: assistant canary missing from result summary' >&2; exit 1 ;;
  esac

  design_before_followup=$(load_design_code)
  if printf '%s' "$design_before_followup" | rg -F \
    -e "$LIVE_FLOW_USER_CANARY" -e "$LIVE_FLOW_ASSISTANT_CANARY"; then
    echo 'FAIL context seed: canary was written before the follow-up' >&2
    exit 1
  fi

  second_prompt='In design.py, add a Python comment containing the codeword from my previous user request; do not change geometry.'
  second_job_id=$(ai_edit_and_wait "$second_prompt" "AI edit context follow-up")
  design_after_followup=$(load_design_code)
  printf '%s' "$design_after_followup" | rg -F "$LIVE_FLOW_USER_CANARY" >/dev/null || {
    echo 'FAIL context follow-up: previous user codeword was not applied' >&2
    exit 1
  }
  if printf '%s' "$design_after_followup" | rg -F "$LIVE_FLOW_ASSISTANT_CANARY"; then
    echo 'FAIL context follow-up: assistant summary canary leaked into source' >&2
    exit 1
  fi
  compile_and_wait "post-context-follow-up" "$second_job_id"
else
  llm_job_id=$(ai_edit_and_wait "$AI_PROMPT" "AI edit")
  compile_and_wait "post-AI-edit" "$llm_job_id"
fi
```

Apply this exact parameterization to the existing function; its polling/control flow is otherwise unchanged:

```diff
 ai_edit_and_wait() {
+  prompt=$1
+  label=$2
   metadata=$(file_metadata_json)
   request=$(tmpfile)
-  write_json "$request" llm_edit "$metadata" "${LIVE_FLOW_MODEL_ID:-}" "$AI_PROMPT"
+  write_json "$request" llm_edit "$metadata" "${LIVE_FLOW_MODEL_ID:-}" "$prompt"
   response=$(api_request POST "${API_BASE_URL}/projects/${PROJECT_NAME}/files/llm-edit/jobs" "$request")
@@
-        echo "PASS AI edit job succeeded (${job_id}, outcome=${outcome})" >&2
+        echo "PASS ${label} job succeeded (${job_id}, outcome=${outcome})" >&2
@@
-        echo "FAIL AI edit job failed" >&2
+        echo "FAIL ${label} job failed" >&2
@@
-  echo "FAIL AI edit job timed out" >&2
+  echo "FAIL ${label} job timed out" >&2
 }
```

- [ ] **Step 3: Update harness configuration tests**

Extend `scripts/test-smoke-live-flow-config.sh` with these exact static/configuration assertions; the real responses are covered by E-01/E-02 rather than a second Bash HTTP stub framework:

```bash
grep -q 'LIVE_FLOW_VERIFY_CONVERSATION' <<<"$help_output"
grep -Fq 'ROOT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)' "$SCRIPT"
grep -Fq 'LIVE_FLOW_VERIFY_CONVERSATION="${LIVE_FLOW_VERIFY_CONVERSATION:-false}"' "$SCRIPT"
grep -Fq 'LIVE_FLOW_USER_CANARY=${LIVE_FLOW_USER_CANARY:-TERTIUS_CONTEXT_USER_CANARY_' "$SCRIPT"
grep -Fq 'LIVE_FLOW_ASSISTANT_CANARY=${LIVE_FLOW_ASSISTANT_CANARY:-TERTIUS_CONTEXT_ASSISTANT_CANARY_' "$SCRIPT"
grep -Fq "second_prompt='In design.py, add a Python comment containing the codeword from my previous user request; do not change geometry.'" "$SCRIPT"
grep -Fq 'live-flow-sensitive-canaries.env' "$SCRIPT"

invalid_context_output=$(LIVE_FLOW_VERIFY_CONVERSATION=invalid "$SCRIPT" http://127.0.0.1 2>&1) && {
  echo 'invalid conversation verification flag should fail' >&2
  exit 1
}
grep -q 'LIVE_FLOW_VERIFY_CONVERSATION must be true or false' <<<"$invalid_context_output"
```

- [ ] **Step 4: Update operational documentation**

Add this exact policy paragraph to `docs/configuration-and-secrets.md` and point the other harness documents to it rather than duplicating it:

```markdown
The Tertius Pi append prompt is committed application policy at
`server/core/pi_agent_system_prompt.md`; it is not a credential or runtime
secret. Both API and Pi worker images contain identical read-only bytes, so a
prompt change requires rebuilding and restarting both images. The retained Pi
PVC contains OAuth state only. Tertius reconstructs bounded conversation
context from Postgres for each `--no-session` worker invocation. System prompt
text, current or historical user requests, assistant summaries, source text,
and prompt hashes must never be added to logs, metrics, or trace attributes.
```

Add these exact rows to the runtime matrix in `docs/harness/runtime-parity.md`:

```markdown
| Pi append policy | identical read-only image file in API and worker | identical read-only image file in API and worker | no environment, Secret, ConfigMap, workspace, or OAuth-PVC copy |
| Pi conversation continuity | bounded Postgres context, one `--no-session` worker per turn | bounded Postgres context, one `--no-session` worker per turn | Pi session files are not persisted |
```

Add this paragraph to `docs/harness/browser-validation.md` under the authenticated AI-edit flow:

```markdown
When a change affects Pi conversation continuity, run `live-flow` with
`LIVE_FLOW_VERIFY_CONVERSATION=true`. The first edit plants separate user and
assistant canaries only in persisted conversation state; the second edit must
recover the user canary without copying the assistant canary into `design.py`,
and the resulting project must compile. Do not use compile-only mode for this
proof. See `docs/configuration-and-secrets.md` for prompt and history handling.
```

Add this prohibition to `docs/harness/queries/traces.md` beside the Pi trace query:

```markdown
Pi spans may identify bounded operation, provider, model, status, and service
names only. Never attach system prompt text or hashes, current or historical
user requests, assistant summaries, source content, workspace paths, or raw
tenant/project/job identifiers. See `docs/configuration-and-secrets.md` for the
canonical policy.
```

- [ ] **Step 5: Run focused safety/harness tests and commit**

```bash
rtk uv run pytest server/tests/test_pi_agent_telemetry_safety.py -q
bash scripts/test-smoke-live-flow-config.sh
bash scripts/check-runtime-parity.sh
rtk git add server/tests/test_pi_agent_telemetry_safety.py scripts/smoke-live-flow.sh scripts/test-smoke-live-flow-config.sh docs/configuration-and-secrets.md docs/harness/runtime-parity.md docs/harness/browser-validation.md docs/harness/queries/traces.md
rtk git commit -m "test: validate stateless Pi conversation continuity"
```

### Task 8: Run the full quality gate and authenticated runtime proof

**Files:**
- Modify only files required to correct failures found by these gates; update this plan's task checkboxes as each gate completes.

- [ ] **Step 1: Run all focused Pi/context tests**

```bash
rtk uv run pytest \
  server/tests/test_repositories.py \
  server/tests/test_pi_agent_messages.py \
  server/tests/test_pi_agent_prompt.py \
  server/tests/test_pi_agent_conversation.py \
  server/tests/test_pi_agent_job.py \
  server/tests/test_pi_agent_rpc.py \
  server/tests/test_llm_file_edit_domain.py \
  server/tests/test_llm_file_edit.py \
  server/tests/test_pi_agent_pipeline_e2e.py \
  server/tests/test_pi_agent_result_consumer.py \
  server/tests/test_pi_agent_telemetry_safety.py \
  server/tests/test_pi_agent_image_config.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run static and full repository gates**

```bash
rtk uv run ruff check server
rtk uv run mypy
rtk uv run pytest
npm test --prefix server/pi
bash scripts/test-deployment-config.sh
bash scripts/check-runtime-parity.sh
bash scripts/test-smoke-live-flow-config.sh
rtk git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Build and inspect both runtime images**

```bash
docker build --target api -t tertius-api:test -f Dockerfile.api .
docker build --target pi-agent -t tertius-pi-agent:test -f Dockerfile.api .
docker run --rm --entrypoint sh tertius-api:test -c 'stat -c "%u:%g %a" /app/server/core/pi_agent_system_prompt.md; sha256sum /app/server/core/pi_agent_system_prompt.md; test ! -e /app/server/pi; ! command -v pi'
docker run --rm --entrypoint sh tertius-pi-agent:test -c 'stat -c "%u:%g %a" /app/server/core/pi_agent_system_prompt.md; sha256sum /app/server/core/pi_agent_system_prompt.md'
```

Expected: both report `0:0 444` and the same SHA; API reports no Pi tooling.

- [ ] **Step 4: Create, authenticate, enable, and run the isolated k3s release**

Create the disposable non-Flux release with the Pi worker disabled but its retained claim present:

```bash
KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 TRACES_LOCAL_PORT=10431 \
KEDA_ENABLED=true PI_AGENT_ENABLED=false scripts/harness-k3s.sh up
```

Authenticate and verify the exact release image/PVC:

```bash
KUBECONFIG=/home/johnson/.kube/config \
scripts/pi-agent-auth.sh login --namespace tertius --release tertius-live-flow-smoke
KUBECONFIG=/home/johnson/.kube/config \
scripts/pi-agent-auth.sh verify --namespace tertius --release tertius-live-flow-smoke
```

Redeploy with both KEDA and Pi enabled, then run the full two-turn flow through the same release and ports:

```bash
KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 TRACES_LOCAL_PORT=10431 \
KEDA_ENABLED=true PI_AGENT_ENABLED=true scripts/harness-k3s.sh up

KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 TRACES_LOCAL_PORT=10431 \
LIVE_FLOW_VERIFY_CONVERSATION=true scripts/harness-k3s.sh live-flow
```

Expected: pre-edit compile, first AI edit, context-dependent second AI edit, and final compile pass. Compile-only mode is not acceptable.

- [ ] **Step 5: Query safety and cross-service telemetry**

Run the documented Pi metrics and require the API -> Pi worker cross-service trace:

```bash
mkdir -p .tmp/harness
METRICS_BASE_URL=http://127.0.0.1:8430 \
  scripts/harness-query-metrics.sh \
  --file docs/harness/queries/pi-agent.promql \
  > .tmp/harness/pi-context-metrics.txt
TRACES_BASE_URL=http://127.0.0.1:10431 \
  scripts/harness-query-traces.sh --require-cross-service \
  --cross-service tertius-api \
  --cross-service tertius-pi-agent-job \
  > .tmp/harness/pi-context-traces.txt

kubectl get pods -n tertius \
  -l app.kubernetes.io/instance=tertius-live-flow-smoke \
  -o name | while read -r pod; do
    kubectl logs -n tertius "$pod" --all-containers=true --ignore-errors --prefix=true
  done > .tmp/harness/pi-context-pod-logs.txt

set -a
. .tmp/harness/live-flow-sensitive-canaries.env
set +a
if rg -F \
  -e 'Tertius file-edit policy:' \
  -e "$LIVE_FLOW_USER_CANARY" \
  -e "$LIVE_FLOW_ASSISTANT_CANARY" \
  .tmp/harness/pi-context-metrics.txt \
  .tmp/harness/pi-context-traces.txt \
  .tmp/harness/pi-context-pod-logs.txt; then
  echo 'Sensitive Pi prompt or conversation content reached telemetry/logs' >&2
  exit 1
fi
```

Expected: the query commands prove the cross-service trace exists, bounded operation/provider/model/status telemetry is present, and all three sensitive canaries are absent.

- [ ] **Step 6: Commit verification corrections and push**

```bash
rtk git status --short
rtk git add -u
rtk git commit -m "fix: complete Pi context validation"
rtk git push origin codex/pi-coding-agent-integration-plan
```

If no verification correction was required, omit the empty commit and push the existing task commits.

## 9. Acceptance Criteria

- [ ] One checked-in prompt file is the only Tertius system-policy source.
- [ ] API and Pi worker images contain identical root-owned mode-`0444` prompt bytes.
- [ ] Prompt bytes are absent from API-to-worker messages, argv, environment, Helm, Compose, logs, metrics, and traces.
- [ ] New commands use schema v2 and contain prompt SHA plus bounded structured conversation context.
- [ ] Retained schema-v1 commands remain executable for the 24-hour compatibility window.
- [ ] API and worker use the same rendered context for quota estimation and execution.
- [ ] New context includes prior user requests and assistant outcomes/status, not only user prompts.
- [ ] Rolling context never exceeds five recent turns, 8,000 summary characters, 12,000 estimated rendered-history tokens, or the existing command byte cap; the current request is never compacted.
- [ ] Failed turns are explicitly labeled and use bounded user-facing errors, never raw internal errors.
- [ ] Historical file content, tool traces, snapshots, IDs, hashes, and recursively dispatched context never enter conversation history.
- [ ] Queued retries reconstruct the exact persisted command context instead of observing newer history.
- [ ] Pi remains one-shot with `--no-session`; Postgres and current project files remain authoritative.
- [ ] Assistant summaries are captured and persisted for both changed and no-change outcomes.
- [ ] No database migration or frontend contract change is introduced.
- [ ] Focused tests, full tests, typing, lint, deployment/parity scripts, both image builds, full k3s live flow, metrics, traces, and telemetry-safety inspection pass.

## 10. References

### Repository references

| Topic | Location | Anchor |
|---|---|---|
| Existing one-shot Pi contract | [`2026-07-11-pi-coding-agent-openai-subscription.md`](./2026-07-11-pi-coding-agent-openai-subscription.md#5-fixed-runtime-contract) | `Fixed Runtime Contract` |
| Current job JSON storage | [`server/core/models.py`](../../../server/core/models.py) | `LlmEditJob` |
| Current prompt-only history | [`server/core/repositories.py`](../../../server/core/repositories.py) | `LlmEditRepository.list_recent_prompts` |
| Current API dispatch | [`server/workflows/intus/intus_server.py`](../../../server/workflows/intus/intus_server.py) | `start_llm_file_edit_job` |
| Current worker prompt | [`server/workflows/intus/pi_agent_job.py`](../../../server/workflows/intus/pi_agent_job.py) | `build_coding_agent_prompt` |
| Current RPC process | [`server/core/pi_agent_rpc.py`](../../../server/core/pi_agent_rpc.py) | `run_pi_agent` |
| Current queued republish | [`server/workflows/intus/pi_agent_result_consumer.py`](../../../server/workflows/intus/pi_agent_result_consumer.py) | `republish_queued_pi_agent_jobs` |
| Runtime quality gates | [`docs/harness/quality-gates.md`](../../harness/quality-gates.md#baseline-gates) | `Baseline Gates` |
| Runtime parity | [`docs/harness/runtime-parity.md`](../../harness/runtime-parity.md) | Runtime matrix |
| Telemetry restrictions | [`server/tests/test_pi_agent_telemetry_safety.py`](../../../server/tests/test_pi_agent_telemetry_safety.py) | `FORBIDDEN_KEYS` |

### Pi references

| Topic | Source | Relevant contract |
|---|---|---|
| System prompt files and append semantics | [Pi usage](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md#system-prompt-files) | `APPEND_SYSTEM.md` appends instead of replacing. |
| RPC subprocess integration | [Pi RPC](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md) | JSONL prompt/events and `--no-session`. |
| SDK/session alternatives | [Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md#session-management) | Explicitly excluded from this implementation. |
| Native persisted sessions | [Pi sessions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sessions.md) | Available alternative, not the source of truth here. |
| Native compaction | [Pi compaction](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md) | Not used because each Pi process is ephemeral. |

## 11. Clarity Gate Self-Review

| Check | Result |
|---|---|
| Actionable | Pass: every behavior-changing step includes exact files, concrete code/diff/assertion blocks, commands, and expected outcomes. |
| Current | Pass: reread against pushed commit `f6a328d593bd967f9861ab475ea18a9218a91b9b`. |
| Single source | Pass: prompt file, conversation context, and retry payload each have one named owner. |
| Decision, not wish | Pass: session lifecycle, bounds, schema rollout, errors, and storage are fixed. |
| Prompt-ready | Pass: no implementation step requires choosing an unspecified architecture. |
| No speculative future state | Pass: Pi-native sessions and long-lived workers are explicit non-goals. |
| No fluff | Pass: sections contain contracts, tasks, tests, or references. |
| Type identified | Pass: implementation plan. |
| Anti-patterns placed | Pass: section 5 contains eleven implementation anti-patterns. |
| Test cases placed | Pass: section 7 defines twelve unit, seven integration, and three end-to-end cases. |
| Error handling placed | Pass: section 6 covers prompt, schema, context, retry, and provider-boundary failures. |
| Deep links present | Pass: section 10 links repository and upstream Pi contracts. |
| No duplicates | Pass: the historical Pi integration plan is referenced, not rewritten. |

**AI coder understandability score:** 9.3/10

The remaining 0.7 is runtime variability in provider behavior during the two-turn canary; separate user/assistant codewords, persisted-result assertions, source checks, and compilation reduce that ambiguity.
