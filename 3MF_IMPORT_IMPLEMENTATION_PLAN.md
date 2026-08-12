# 3MF Import and Build123D Editing Implementation Plan

**Status:** In progress on `feature/3mf-import-editing`  
**Current revision:** `b37f66f`  
**Detailed execution plan:** `docs/superpowers/plans/2026-08-11-3mf-import-editing.md`  
**Approved design:** `docs/superpowers/specs/2026-08-11-3mf-import-editing-design.md`

## Goal

Allow an authenticated, non-guest user to upload a 3MF file as a new Tertius
project from both Generate Design and Intus. Tertius converts the mesh in an
isolated worker into a unit-normalized faceted BREP, generates editable
Build123D source, preserves immutable source and derived assets, and supports
the existing compile, AI-edit, viewer, retry, and project workflows.

The original 3MF and derived BREP must never be sent to an external AI
provider. AI editing receives only bounded, privacy-safe part metadata and the
generated source, and requires the existing external-provider consent flow.

## User Decisions

- Use 3MF, not STL.
- Use upload-time conversion to a faceted BREP (option 2).
- Every import creates a new project.
- Expose import from both Generate Design and Intus.
- Only authenticated, non-guest users may import.

## Completed Foundation

### Task 1 — Domain contracts: complete

- Strict 3MF manifest, limits, safe naming, summaries, and generated source.
- Public and AI-safe projections exclude binary content and unsafe metadata.
- Exact conversion/version, digest, size, geometry, XML, and archive limits.

### Task 2 — Persistence: complete

- Immutable, tenant/project-scoped source and derived project assets.
- Import jobs with attempts, execution fencing, progress, leases, retry state,
  and active-job uniqueness.
- Immutable compile asset snapshots.
- PostgreSQL constraints, mutation-rejection triggers, migration downgrade,
  tenant/user binding, and concurrency coverage.

### Task 3 — Object transport: complete

- Digest-addressed JetStream object storage with strict object references.
- Streaming integrity verification, bounded reads, metadata validation, and
  guaranteed subscription cleanup.
- Race-safe bucket, stream, and consumer reconciliation.
- Bounded stream/object settings without identifier leakage in telemetry.

### Task 4 — Isolated converter: complete

- Strict command, progress, and result messages with attempt/execution
  provenance and W3C trace context.
- Hardened ZIP/XML validation, including traversal, encryption, compression,
  relationship, entity, coordinate, count, size, depth, and build-item limits.
- All six 3MF unit systems normalized explicitly to millimetres.
- Deterministic parts, solid/shell classification, BREP export, semantic
  round-trip validation, and a real Build123D boolean proof.
- Bounded subprocess IPC, process-tree timeout/cancellation, structured safe
  error propagation, and memory-bounded streaming XML parsing.

### Task 5 — Worker and result consumer: complete

- One-shot converter worker with publish-before-ACK ordering and transient NAK.
- Periodic JetStream and database heartbeats.
- Source/derived integrity checks and fixed safe error mapping.
- Stale queued/running reconciliation and execution fencing.
- Cancellation kills and joins the converter process tree.
- Result persistence uses a fresh threaded database session.
- Independent ACK and database lease heartbeats survive row-lock contention.
- Concurrent result delivery creates one derived revision and one terminal
  transition.

## Current Work: Task 6 — Authenticated Import API and Durable Dispatch

Most of Task 6 is implemented:

- Auth and guest rejection occur before multipart parsing.
- Declared and chunked request limits are enforced.
- Uploads are spooled and read in chunks no larger than 1 MiB.
- Filename, MIME type, project name, form shape, tenant, and requesting user
  are validated.
- Status and retry are scoped to the original requesting user.
- Concurrent retry produces one winner and one exact active-import conflict.
- Project, source asset, job, and immutable command outbox are committed in one
  transaction.
- An independent dispatcher publishes only committed commands using leases,
  attempt fencing, deterministic message IDs, bounded retries, and reclaim.
- Queued reconciliation understands pending, leased, sent, failed, current,
  and superseded outbox executions.
- No database lock is held across NATS I/O.

### Task 6 blockers to fix before acceptance

1. **Keep API preflight cheap and isolated.**
   The API currently invokes the worker-grade validator synchronously. Split
   validation into a cheap ZIP-envelope preflight for the API and retain full
   XML/geometry validation inside the isolated converter. The API path must not
   walk millions of vertices or decompress hundreds of MiB on its event loop.

2. **Guarantee command deduplication beyond JetStream's incidental window.**
   Configure and reconcile an owned duplicate window covering the maximum
   publish-recovery horizon, or add a durable worker execution-claim gate.
   A dispatcher crash after PubAck must not start a second expensive converter.

3. **Prevent unowned uploads from exhausting shared object storage.**
   Avoid or collect objects written before failed/colliding database admission.
   Because digest objects are shared, do not eagerly delete without reference
   accounting. Add reservation/quota or reference-aware garbage collection.

4. **Remove object verification memory duplication.**
   Add a verify-only streaming hash/size path so a 128 MiB upload is not held
   alongside a second 128 MiB materialization. Add an upload concurrency/memory
   budget.

5. **Close multipart resources explicitly.**
   Close `FormData`/`UploadFile` in `finally`, including overflow/error paths.

6. **Bound outbox cleanup batches.**
   Stale and exhausted cleanup must use bounded `SKIP LOCKED` batches rather
   than locking an unbounded backlog.

### Task 6 completion gate

- Add failing tests for each blocker before changing production code.
- Run API, auth, outbox, dispatcher, reconciliation, migration, object-store,
  converter, NATS, and concurrency tests.
- Obtain fresh specification and code-quality reviews with no Critical or
  Important findings.
- Mark Task 6 checkboxes complete in the detailed plan only after both reviews
  pass.

## Remaining Product Work

### Task 7 — Compile snapshots and hydration

- Add BREP/manifest object references to compile messages.
- At submission, lock the project and snapshot the latest matching successful
  BREP/manifest pair into immutable `CompileJobAsset` rows.
- Ensure referenced objects exist and hydrate them into the compile workspace
  before the sandbox starts.
- Prove later import retries cannot change an existing compile snapshot.

### Task 8 — Build123D runtime helper

- Add `from tertius_imports import load_3mf_model` to the sandbox runtime.
- Return ordered parts, safe names, `parts_by_name`, solid/boolean flags,
  manifest, and compound.
- Verify source/BREP/manifest digests and topology before exposing shapes.
- Test transforms, solid booleans, shell-only workflows, and safe errors.

### Task 9 — Privacy-safe AI edit context

- Add bounded imported-part metadata to Generate Design and AI-edit context.
- Never include source 3MF, BREP bytes, object keys, raw metadata, or unsafe
  identifiers.
- Preserve the existing external-provider consent boundary.
- Teach guardrails and prompts how to use `load_3mf_model("source")`.

### Task 10 — Browser API and project storage

- Make the shared API client multipart-safe; do not force JSON content type for
  `FormData`.
- Add strict TypeScript upload/status/retry types and authenticated methods.
- Extend project storage without using `file.text()` for binary 3MF content.
- Preserve guest restrictions and safe response projections.

### Task 11 — Shared accessible import dialog

- Build one dialog reused by Generate Design and Intus.
- Include file picker/drop zone, project name, validation, progress/status,
  retry, cancellation/close behavior, keyboard support, focus management, and
  screen-reader announcements.

### Task 12 — Integrate both UI surfaces

- Add discoverable import actions to Generate Design and Intus.
- Navigate to the newly created project and poll the import job.
- On success, compile generated source and hand the artifact to the existing
  model viewer.
- Preserve AI conversation and normal compile/viewer behavior.

### Task 13 — Runtime wiring

- Add converter worker, result consumer, outbox dispatcher, NATS resources,
  KEDA scaling, settings, probes, limits, and secrets-safe configuration to
  Helm/local k3s.
- Add Compose development and parity wiring.
- Update `scripts/check-runtime-parity.sh` and document intentional differences.

### Task 14 — Harness and documentation

- Extend authenticated live-flow coverage for import, status, conversion,
  compile, viewer, retry, Generate Design, and Intus.
- Add operator troubleshooting, limits, expected faceted-editing behavior,
  shell limitations, privacy behavior, and recovery guidance.

### Task 15 — Full verification and Falcon 9 acceptance

- Run backend, frontend, migration, lint, type, parity, Helm, Compose, and k3s
  quality gates.
- Run the full authenticated AI-enabled live flow because Generate Design and
  AI-edit behavior are affected.
- Import the supplied Falcon 9 model:
  `http://100.86.195.45:8000/outputs/falcon9/final/falcon9_200mm.3mf`
- Verify new-project creation from both surfaces, conversion, safe manifest,
  editable Build123D operations, compile, viewer rendering, retry, and no
  binary/provider leakage.

The Falcon 9 URL has timed out from the current environment and remains a hard
acceptance dependency. Synthetic fixtures do not replace this final test.

## Required Working Practices

- Work on `feature/3mf-import-editing`; do not modify user-owned
  `tools/mechanics_fetch/`.
- Use test-driven development for each remaining task.
- After each task, run a separate specification review followed by an
  independent code-quality review; fix all Critical and Important findings.
- Keep the detailed plan checkboxes current as tasks are accepted.
- Do not merge or deploy until full verification succeeds.
- For final Tertius validation, use the authenticated local k3s live-flow. If
  credentials, provider consent, runtime access, or the sample URL is blocked,
  report the exact blocker and the focused validation that did run.

## Current Evidence

- Branch is clean except for pre-existing `tools/mechanics_fetch/`.
- Latest broad Task 6 regression reported 313 passing tests.
- Latest independent Task 6 quality run reported 85 focused tests passing but
  correctly marked the task not ready because of the blockers above.
- Tasks 1–5 have completed independent specification and quality reviews.
- Nothing has been merged or deployed.
