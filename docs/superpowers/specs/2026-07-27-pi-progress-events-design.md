# Pi Progress Events Design

**Status:** Approved

**Date:** 2026-07-27

## Goal

Show batched, near-live Pi reasoning summaries and tool activity inside the
Generate Design conversation while preserving the existing one-job/one-Pi
process model, terminal result contract, polling fallback, and privacy boundary.

## User Experience

- The existing 1.5-second job-status poll also receives the current bounded
  progress snapshot.
- While a job is queued or running, an expanded native `Activity` disclosure
  shows reasoning-summary chunks and tool start/finish milestones.
- When the job reaches a terminal state, the disclosure remains attached to the
  current assistant turn but collapses automatically.
- The existing final assistant message, changed-file handling, retry behavior,
  and conversation history remain authoritative.

## Architecture

Pi 0.80.6 already emits the required JSONL RPC events, but its OpenAI adapter
collapses both reasoning-summary and raw-reasoning provider events into
`thinking_delta`. The pinned install hardener therefore adds a strict
source-provenance boolean before Pi starts. Tertius will preserve a strictly
sanitized subset:

1. `server/pi/pi-install-security.ts` marks summary-derived
   `thinking_delta` events as safe and raw-reasoning events as unsafe. The
   hardener fails when the pinned source shape changes.
2. `server/core/pi_agent_rpc.py` recognizes only provenance-marked summary
   deltas plus `tool_execution_start` and `tool_execution_end` events. Missing
   or false provenance fails closed and emits no reasoning progress.
3. A worker-side batcher coalesces adjacent reasoning deltas, assigns
   per-execution sequence numbers, and publishes bounded progress batches.
4. Progress batches use the existing ordered Pi result JetStream subject and
   durable API consumer. This avoids a new runtime setting and preserves publish
   order: all acknowledged progress batches precede the terminal result.
5. The API consumer discriminates progress envelopes from existing result
   envelopes and idempotently updates a bounded progress snapshot on the
   existing job row.
6. The existing job-status and conversation-history endpoints return that
   tenant/project-scoped snapshot.
7. The React conversation reducer replaces or merges the snapshot and renders
   it in a native disclosure.

The terminal result remains authoritative. Progress publishing is
best-effort: bounded retries are attempted, but progress transport failure must
not turn a successful edit into a failed edit.

## Wire Contract

`PiAgentProgressBatch` is a strict Pydantic model:

- `message_type`: literal `progress`
- `schema_version`: literal `1`
- `execution_id`, `execution_started_at`, `job_id`, `tenant_id`, `project_id`
- optional W3C trace context
- `batch_sequence`: positive and monotonic within one execution
- `events`: 1 to 16 `PiAgentProgressEvent` values

Each event contains:

- `sequence`: positive, monotonic within one execution
- `kind`: `reasoning_delta`, `tool_started`, or `tool_finished`
- `text`: only for reasoning, bounded to 1,000 characters
- `tool_name`: only the allow-listed Pi tool name
- `target`: optional normalized workspace-relative path, bounded to 512
  characters
- `is_error`: only for a tool-finished event
- `occurred_at`: UTC timestamp

No raw reasoning, raw tool arguments, tool results, user prompts, source text,
assistant text deltas, provider errors, or Pi session data enter the progress
contract.

The worker publishes progress to the existing `pi_agent_result_subject`.
Existing `PiAgentResult` JSON remains unchanged for rolling compatibility.
The consumer checks `message_type` before validating either envelope.

## Batching and Bounds

- Adjacent reasoning deltas are coalesced up to 1,000 characters per event.
- A batch contains at most 16 events and stays below the existing Pi result
  message-size limit.
- A timer flushes pending progress within 500 ms; tool milestones also trigger a
  prompt flush.
- Progress publishing retries three times with the existing short exponential
  delays. After the final failure, the batch is dropped with a fixed,
  content-free warning and execution continues.
- At most 128 coalesced events and 64 KiB of serialized progress are retained
  per job. Oldest events are pruned and `truncated_before_sequence` records the
  lost prefix.

## Safe Tool Labels

The only exposed tool names are the runtime allow-list:
`read`, `edit`, `write`, `grep`, `find`, and `ls`.

For the optional target label, Tertius reads only a path-like field from the
tool arguments, resolves it against the job workspace, and returns it only when
it stays inside that workspace. Absolute workspace paths are converted to
relative POSIX paths. Traversal, NULs, overlong paths, and non-string values
produce no target label. Search patterns, replacement content, write content,
and tool results are never inspected for display.

## Persistence and Idempotency

Migration `0010` adds `LlmEditJob.progress_payload`, a non-null JSON object with
an empty-object default. The persisted snapshot contains:

- `schema_version`
- current `execution_id`
- current `execution_started_at`
- `last_batch_sequence`
- `last_sequence`
- `truncated_before_sequence`
- the bounded safe `events` array

The repository locks the already tenant/project-scoped job row before applying
a batch. A different execution ID resets the snapshot only when its
`execution_started_at` is newer than the persisted execution; delayed batches
from older executions are acknowledged and ignored. A batch sequence at or
below the persisted `last_batch_sequence` is a redelivery and becomes a no-op.
Events are merged in sequence order and trimmed to both the event-count and
serialized-byte bounds. Progress is ignored for terminal jobs.

This JSON is deliberately separate from `request_payload`, which is the
immutable dispatch/retry contract, and `result_payload`, which is replaced by
terminal completion. The shared durable consumer preserves progress and
terminal-result ordering without another subject, consumer, or background
task.

## API Contract

`GET /projects/{name}/files/llm-edit/jobs/{job_id}` adds:

- `progress`: the full bounded snapshot, or `null` before the first event

The conversation-history entry for a terminal job carries an optional compact
preview containing at most the eight most recent events, with reasoning text
clipped to 240 characters. Its truncation marker records omitted events. The
per-job status endpoint remains the source for the full bounded snapshot. This
keeps Activity useful after reload without multiplying a 64-KiB snapshot across
up to 200 history entries. Existing status/result/error fields are unchanged.
Tenant/project/job ownership continues to be enforced before progress is
returned.

## Failure Handling

| Failure | Behavior |
|---|---|
| Malformed or unsupported Pi event | Ignore it; terminal execution continues. |
| Unsafe tool name or target | Omit the event or target; never forward raw data. |
| Oversize progress envelope | Split/drop the affected safe batch; terminal execution continues. |
| Progress publish failure | Retry three times, log a fixed warning, continue. |
| Invalid progress provenance | ACK and discard with a fixed warning. |
| Delayed batch from an older execution | ACK and discard without replacing newer progress. |
| Corrupt/unsupported persisted snapshot | ACK and reject progress with a fixed warning; do not redeliver forever. |
| Duplicate/old progress batch | ACK after idempotent no-op. |
| Progress database failure | Roll back and NAK for JetStream redelivery. |
| UI progress poll failure | Use existing transient retry behavior; keep accumulated activity. |
| Terminal result arrives | Apply it normally, preserve returned progress, then collapse Activity. |

## Telemetry and Privacy

Metrics and traces may use only bounded event kind, allow-listed tool name, and
success/failure state. They must not include event text, target paths, message
bodies, prompts, generated source, tool arguments/results, or raw identifiers.
Logs use fixed diagnostics and never serialize a progress envelope.

## Tests

- RPC parser tests cover reasoning deltas, safe path normalization, tool
  start/end, unknown events, and exclusion of args/results/source.
- Message tests cover strict field combinations, bounds, serialized size, and
  deterministic message IDs.
- Worker tests cover coalescing, timer/size flushes, publish retries, final
  flush-before-result, and progress failure isolation.
- Repository/migration tests cover tenant scope, execution reset, batch
  idempotency, and the 128-event/64-KiB retention bounds.
- Consumer tests cover envelope routing, provenance rejection, duplicate
  delivery, rollback/NAK, and unchanged terminal results.
- Endpoint tests cover ownership, full status snapshots, compact history
  previews, response compatibility, and terminal snapshots.
- UI tests cover snapshot merge/reset, deduplication, expanded running Activity,
  collapsed terminal Activity, truncation labels, tool error labels, and
  unchanged final result behavior.
- Telemetry-safety tests use sentinels to ensure progress content and paths do
  not reach logs or telemetry.
- Final validation includes full backend/frontend gates, deployment/runtime
  parity, authenticated k3s `live-flow`, and browser console/network checks.

## Explicit Non-Goals

- Server-sent events or WebSockets.
- Raw chain-of-thought.
- Raw tool arguments, partial results, or final tool results.
- Pi session persistence or a long-lived Pi process.
- Replacing Pi with Codex.
- Replaying activity into future model context.
- Loading raw or unbounded activity into conversation history.
