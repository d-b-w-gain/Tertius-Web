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
- v1 adapts `prior_prompts` into user-only historical turns in memory; it is never rewritten in NATS.
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
| v1 command retained in JetStream | `schema_version == 1` | Adapt `prior_prompts` and execute with current prompt file | Normal legacy execution | No |
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
| U-06 | Summary bound | Repeated long completed turns | Summary stays at or below 8,000 characters without partial newest entries. |
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

- [ ] **Step 1: Write failing prompt-loader and exact-estimate tests**

Add tests covering exact bytes, SHA-256, every invalid-file case, a path-only render input, and deterministic estimation. The primary success assertion must be:

```python
snapshot = load_pi_agent_prompt()
raw = snapshot.path.read_bytes()
assert snapshot.content == raw.decode("utf-8")
assert snapshot.sha256 == sha256(raw).hexdigest()
assert len(raw) <= 32_768
```

- [ ] **Step 2: Run the tests and verify red**

Run:

```bash
rtk uv run pytest server/tests/test_pi_agent_prompt.py server/tests/test_llm_file_edit_domain.py -q
```

Expected: failure because `core.pi_agent_prompt` and the checked-in prompt file do not exist.

- [ ] **Step 3: Add the exact append-prompt text**

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

- [ ] **Step 4: Implement the fail-closed loader**

Create `server/core/pi_agent_prompt.py` with these concrete contracts:

```python
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

MAX_PI_AGENT_PROMPT_BYTES = 32_768
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
```

Move prompt-specific estimation into this module or change `estimate_file_edit_usage()` to accept already-final `system_prompt` and already-rendered `user_prompt`. Define `PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS = 8_192`, add it once to `prompt_tokens`, and then add the configured output-token reserve. Remove `BUILD123D_RUNTIME_GUARDRAILS`, `file_edit_system_content()`, and the obsolete direct-provider JSON-return framing from `llm_file_edit.py`; keep file selection and shared token/result models.

- [ ] **Step 5: Lock the image mode and run green tests**

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

- [ ] **Step 6: Commit**

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

- [ ] **Step 1: Write failing strict-model and rollover tests**

Cover U-03 through U-09, including a mutation that places `content`, snapshot IDs, and raw internal errors in a job payload and proves none reach the resulting turn.

- [ ] **Step 2: Run the tests and verify red**

```bash
rtk uv run pytest server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_conversation.py -q
```

Expected: failures because the v2 context models and builder do not exist.

- [ ] **Step 3: Add strict conversation models and v1/v2 validation**

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

- [ ] **Step 4: Implement deterministic context advancement**

Create pure helpers in `pi_agent_conversation.py`:

```python
MAX_RECENT_TURNS = 5
MAX_ROLLING_SUMMARY_CHARS = 8_000


def advance_conversation_context(
    context: PiAgentConversationContext,
    turn: PiAgentConversationTurn,
) -> PiAgentConversationContext:
    turns = [*context.recent_turns, turn]
    summary_lines = [line for line in context.rolling_summary.splitlines() if line]
    while len(turns) > MAX_RECENT_TURNS:
        summary_lines.append(compact_turn_line(turns.pop(0)))
    while len("\n".join(summary_lines)) > MAX_ROLLING_SUMMARY_CHARS:
        summary_lines.pop(0)
    return PiAgentConversationContext(
        rolling_summary="\n".join(summary_lines),
        recent_turns=turns,
    )
```

`compact_turn_line()` must clip the user portion to 600 characters and the assistant/failure portion to 400 characters before joining. `conversation_turn_from_job()` must use:

- `request_payload["prompt"]` for the user request;
- `result_payload["outcome"]`, `result_payload["message"]`, and result filenames for success;
- `user_message` and `error_code` for failure;
- fixed fallbacks `Updated files.` and `No files changed.` when a success summary is empty.

It must never use `job.error`, result file `content`, snapshot metadata, raw IDs, traces, or dispatched context.

- [ ] **Step 5: Implement legacy bootstrap and structured rendering**

`next_conversation_context(jobs)` must parse the most recent terminal job's persisted `dispatched_conversation`; when valid, advance it with only that job. If absent/invalid, fold the supplied chronological bounded jobs from an empty context. Render context as JSON between explicit historical delimiters and render the current request after a separate marker:

```text
Historical conversation context follows. It describes completed work and is not a new instruction.
<conversation_context>
{...bounded JSON...}
</conversation_context>

Current user request:
...
```

- [ ] **Step 6: Run green tests and commit**

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

Replace prompt-only history assertions with:

- tenant/project-isolated terminal jobs only;
- chronological order with a hard 200-row cap;
- mixed `succeeded` and `failed` jobs;
- published schema v2 context exactly equal to persisted `dispatched_conversation`;
- persisted `dispatched_system_prompt_sha256` equal to the loader hash;
- no system prompt content/path and no historical file content in serialized command JSON.

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

Add tests proving:

- argv contains `--append-system-prompt` followed by only the canonical path;
- a unique prompt-file sentinel is absent from argv and the child environment;
- missing prompt path fails before subprocess spawn;
- only assistant `message_end` text is captured, with multiple text blocks concatenated and clipped to 2,000 characters;
- tool output, user content, and thinking are never captured as the assistant summary;
- a matching v2 hash calls Pi;
- a mismatched v2 hash never hydrates/calls Pi and returns `worker_config_mismatch`, retryable true;
- v1 commands still execute through the legacy adapter;
- no-change results preserve a real assistant summary.

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

Use `command.conversation` for v2 and an in-memory user-only adapter for v1. Render current filenames/active filename and structured conversation through the shared prompt renderer. Pass `snapshot.path` to RPC. Set `assistant_summary=rpc.assistant_summary` for both `changed` and `no_changes`.

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

The v2 retry test must create newer terminal conversation rows after the queued job and prove the republished command still equals the queued job's persisted `dispatched_conversation`. The pipeline test must prove the second command sees the first result summary and refreshed current file contents.

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

Keep `_result_payload()` bounded and ensure it stores `result.assistant_summary` for both success outcomes. Add assertions that a later context builder uses `message`, `outcome`, and filenames but excludes file `content`, snapshot data, usage, and provider details.

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

Tests must assert:

- `Settings.model_fields` excludes `pi_agent_system_prompt`;
- no rendered API/worker/ConfigMap contains `PI_AGENT_SYSTEM_PROMPT`;
- values schema has no `piAgent.systemPrompt`;
- Compose renders no prompt env or prompt/auth overlay;
- both images contain identical `/app/server/core/pi_agent_system_prompt.md` SHA, owned by UID/GID 0 and not writable by UID 1000;
- the API image still has no `/app/server/pi` and no `pi` executable.

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
}
```

Do not forbid `system_prompt_sha256` as a persisted data field, but add a mutation proving it cannot become a metric/trace label.

- [ ] **Step 2: Add an opt-in two-turn live-flow canary**

Add `LIVE_FLOW_VERIFY_CONVERSATION` defaulting to `false`. When true:

1. Generate a random `TERTIUS_CONTEXT_CANARY_<hex>` codeword.
2. First request asks for a valid geometry edit and explicitly says to remember, but not write, the codeword.
3. Confirm the codeword is absent from all current source files.
4. Second request says: `Add a Python comment containing the codeword from my previous request; do not change geometry.`
5. Poll the second job, fetch current files, assert the exact codeword occurs, then compile the second job.

Change `ai_edit_and_wait()` to take prompt and label arguments rather than reading a global mutable prompt. Ensure command traces never echo either prompt.

- [ ] **Step 3: Update harness configuration tests**

Test the default single-turn path and the enabled two-turn path with stubbed API responses. Assert the first request contains the codeword, the second does not contain it, and the final file assertion searches for it.

- [ ] **Step 4: Update operational documentation**

Document:

- the prompt is committed application policy, not a Kubernetes secret;
- changes require rebuilding/restarting both API and Pi worker images;
- OAuth state remains the only Pi data on the retained PVC;
- conversation context is reconstructed from Postgres and Pi still uses `--no-session`;
- the exact runtime parity and browser follow-up validation steps;
- system prompt, current/prior user requests, assistant summaries, source, and hashes must not appear in telemetry.

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

- [ ] **Step 4: Run the required full k3s AI-edit flow**

Use the isolated smoke release with demo auth, KEDA, and LLM credentials:

```bash
LIVE_FLOW_VERIFY_CONVERSATION=true scripts/harness-k3s.sh live-flow
```

Expected: pre-edit compile, first AI edit, context-dependent second AI edit, and final compile pass. Compile-only mode is not acceptable.

- [ ] **Step 5: Query safety and cross-service telemetry**

Run the documented Pi metrics and trace queries:

```bash
scripts/harness-query-metrics.sh --file docs/harness/queries/pi-agent.promql
scripts/harness-query-traces.sh
```

Inspect API/worker pod logs plus returned metrics/traces for the generated system/user/history/assistant canary values. Expected: bounded operation/provider/model/status telemetry is present; sensitive canaries are absent.

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
- [ ] Rolling context never exceeds five recent turns, 8,000 summary characters, or the existing command byte cap.
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
| Actionable | Pass: every implementation change has exact files, contracts, tests, commands, and expected outcomes. |
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

**AI coder understandability score:** 9.4/10

The remaining 0.6 is runtime variability in provider behavior during the two-turn canary; the test uses a mechanically verifiable codeword and compilation to reduce that ambiguity.
