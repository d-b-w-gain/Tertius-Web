# Pi Progress Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development`. Keep each checkbox current as work
> lands.

**Document type:** Implementation plan

**Goal:** Stream a privacy-safe, batched subset of Pi reasoning summaries and
tool milestones through the existing polling flow and show it in a collapsed
conversation Activity disclosure.

**Architecture:** Parse and sanitize existing Pi RPC events, coalesce them in
the worker, publish strict progress batches on the ordered Pi result subject,
idempotently persist a bounded snapshot in Postgres, return it through the
current status/history endpoints, and merge it through the existing React
polling loop.

**Tech stack:** Python 3.14, FastAPI, Pydantic v2, SQLAlchemy/Postgres, Alembic,
NATS JetStream, Pi RPC 0.80.6, React 19, TypeScript, Vitest, Helm/local k3s.

**Approved design:**
`docs/superpowers/specs/2026-07-27-pi-progress-events-design.md`

## Clarity Gate

| Dimension | Score | Evidence |
|---|---:|---|
| Goal and user outcome | 10/10 | Running and terminal Activity behavior is explicit. |
| Scope and non-goals | 10/10 | Polling, privacy, and no-Codex/no-SSE boundaries are fixed. |
| Data contracts | 9/10 | Envelope, event, snapshot, persistence, and bounds are specified. |
| Error handling | 9/10 | Worker, transport, consumer, API, and UI failures are mapped. |
| Testability | 10/10 | Unit, integration, safety, runtime, and browser cases are listed. |
| Operational fit | 9/10 | Existing subject/consumer avoids new runtime configuration. |

**Overall:** 9.5/10 — ready to implement.

## Task 1: Define strict progress contracts

**Files**

- Modify: `server/core/pi_agent_messages.py`
- Modify: `server/tests/test_pi_agent_messages.py`
- Modify: `server/tests/test_pi_agent_telemetry_safety.py`

- [x] Add failing model tests for valid reasoning/tool events, invalid
  cross-field combinations, allow-listed tool names, character/event bounds,
  UTC timestamps, deterministic IDs, and message size.
- [x] Run the focused tests and confirm the new tests fail for missing symbols.
- [x] Implement `PiAgentProgressEvent`, `PiAgentProgressBatch`, size assertion,
  and deterministic progress message ID.
- [x] Run focused tests and Ruff for the touched files.

## Task 2: Preserve only safe Pi RPC events

**Files**

- Modify: `server/core/pi_agent_rpc.py`
- Modify: `server/tests/test_pi_agent_rpc.py`

- [x] Add failing tests for Pi `thinking_delta`, safe tool start/end events,
  workspace-relative targets, traversal/absolute-outside rejection, ignored
  tool updates, and raw args/results/source exclusion.
- [x] Run the focused RPC tests and observe the red state.
- [x] Add a typed async progress callback to `run_pi_agent`.
- [x] Normalize reasoning deltas and tool milestones while retaining only the
  allow-listed tool name and safe target.
- [x] Preserve existing turn/tool limits, error classification, and final
  assistant summary.
- [x] Run focused tests, Ruff, and mypy for the touched module.

## Task 3: Batch and publish progress without risking the edit

**Files**

- Modify: `server/workflows/intus/pi_agent_job.py`
- Modify: `server/tests/test_pi_agent_job.py`

- [x] Add failing tests for adjacent-reasoning coalescing, 16-event/1,000-char
  bounds, timed flush, tool milestone flush, three publish attempts, fixed
  content-free warnings, progress failure isolation, and final
  flush-before-terminal-result ordering.
- [x] Run the focused worker tests and observe the red state.
- [x] Implement a worker-local progress batcher with one execution ID and
  monotonic sequence.
- [x] Pass its callback into `run_pi_agent`; flush/close it in every completion,
  failure, and cancellation path.
- [x] Publish batches to `pi_agent_result_subject` with safe telemetry IDs.
- [x] Run focused tests, Ruff, and mypy.

## Task 4: Persist a bounded progress snapshot idempotently

**Files**

- Create: `server/migrations/versions/0010_llm_edit_job_progress.py`
- Modify: `server/core/models.py`
- Modify: `server/core/repositories.py`
- Modify: `server/tests/test_repositories.py`
- Modify: `server/tests/test_migrations.py`

- [ ] Add failing tests for batch-sequence idempotency, new-execution reset,
  tenant/project scope, terminal-job ignore, ordered merge, and
  128-event/64-KiB trimming.
- [ ] Run the focused repository tests and observe the red state.
- [ ] Add a non-null `progress_payload` JSON field and Alembic migration with an
  empty-object default/backfill.
- [ ] Add a repository method that locks the scoped job row, validates the
  execution/batch order, merges the strict snapshot, and marks JSON modified.
- [ ] Run focused repository/migration tests, Ruff, and mypy.

## Task 5: Route progress through the durable result consumer

**Files**

- Modify: `server/workflows/intus/pi_agent_result_consumer.py`
- Modify: `server/tests/test_pi_agent_result_consumer.py`
- Modify: `server/tests/test_pi_agent_pipeline_e2e.py`

- [ ] Add failing tests for progress/result discrimination, strict size and
  provenance checks, idempotent ACK, invalid ACK/discard, database NAK/retry,
  and progress-before-result pipeline order.
- [ ] Run focused consumer/pipeline tests and observe the red state.
- [ ] Route `message_type=progress` to a dedicated progress handler; preserve
  the existing result path unchanged.
- [ ] Persist through `LlmEditRepository` and record only bounded kind/tool/state
  telemetry.
- [ ] Run focused tests, Ruff, and mypy.

## Task 6: Return progress from status and history

**Files**

- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/tests/test_llm_file_edit.py`
- Modify: `server/tests/test_intus_endpoints.py`

- [ ] Add failing tests for optional progress in status and terminal
  conversation history, tenant/project ownership, and unchanged legacy response
  fields.
- [ ] Run focused endpoint tests and observe the red state.
- [ ] Serialize the validated bounded snapshot only after the existing scoped
  job lookup succeeds.
- [ ] Run focused tests, Ruff, and mypy.

## Task 7: Add batched polling and Activity UI

**Files**

- Modify: `ui/src/workflows/shared/projectStorage.ts`
- Modify: `ui/src/workflows/shared/projectStorage.test.ts`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`

- [ ] Add failing client tests for typed progress snapshot fields in status and
  history responses.
- [ ] Add failing UI tests for snapshot merge/reset, expanded running
  disclosure, collapsed terminal disclosure, truncation notice, reasoning
  text, safe tool labels, and error status.
- [ ] Run focused Vitest and observe the red state.
- [ ] Add the optional progress snapshot to the existing typed client contracts.
- [ ] Keep per-turn progress in the existing conversation model; reset on a new
  execution and otherwise merge by sequence within the server-provided
  truncation boundary.
- [ ] Render accessible native `details/summary`; default it open while active
  and remount it collapsed when the turn becomes terminal.
- [ ] Run focused Vitest, frontend typecheck, lint, and build.

## Task 8: Cross-cutting safety and regression verification

**Files**

- Modify only if required by failing assertions:
  `scripts/check-runtime-parity.sh`,
  `scripts/test-deployment-config.sh`,
  `docs/harness/browser-validation.md`,
  `docs/harness/runtime-parity.md`

- [ ] Run the complete backend test suite.
- [ ] Run Ruff and mypy.
- [ ] Run the complete frontend test, typecheck, lint, and build gates.
- [ ] Run deployment-config and runtime-parity scripts; no new runtime
  environment variable is expected.
- [ ] Search logs/telemetry code and run sentinel safety tests to confirm event
  text/paths/args/results never enter telemetry or fixed warnings.
- [ ] Inspect the final diff for unrelated or generated changes.

## Task 9: Runtime validation, review, and PR

- [ ] Build/deploy the isolated local-values k3s smoke release.
- [ ] Run full authenticated `scripts/harness-k3s.sh live-flow` with AI edit;
  compile-only is not acceptable.
- [ ] In the browser, confirm Activity updates during Pi execution, terminal
  Activity collapses, final files/message still update, and console/network
  inspection is clean.
- [ ] Request specification and code-quality reviews; resolve findings and rerun
  affected gates.
- [ ] Run fresh final verification, commit the intentional diff, push
  `feat/pi-progress-events`, open the PR, and confirm the hosted check rollup.

## Anti-Patterns

- Do not expose raw Pi tool arguments/results, prompts, source, or private
  chain-of-thought.
- Do not replace the current terminal result with progress state.
- Do not introduce SSE/WebSockets or a second progress consumer for this
  batched-polling scope.
- Do not add a runtime environment variable for a fixed internal contract.
- Do not make a progress publish or render failure fail the edit.
- Do not use raw job/project/user IDs or progress text/path as telemetry labels.
- Do not append unbounded progress to `request_payload` or `result_payload`.
- Do not load raw or unbounded progress into the history-list response.
- Do not rewrite the historical Pi context plan; this plan supersedes only its
  former UI/progress non-goal.
