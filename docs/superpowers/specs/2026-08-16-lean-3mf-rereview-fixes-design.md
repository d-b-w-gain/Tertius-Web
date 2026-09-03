# Lean 3MF Re-review Fixes Design

## Goal

Resolve the remaining second-review findings on stacked PRs #355 through #358
without expanding the lean 3MF architecture, changing their intended bases, or
merging or squashing the stack.

## Stack Boundaries

The implementation preserves these bases:

- `codex/3mf-lean-sidecars` on `master`;
- `codex/3mf-lean-loader` on `codex/3mf-lean-sidecars`;
- `codex/3mf-lean-api` on `codex/3mf-lean-loader`;
- `codex/3mf-lean-ui` on `codex/3mf-lean-api`.

It does not add import workers, conversion streams, BREP persistence, polling
jobs, conversion manifests, or any architecture from superseded PRs #346–#349.

## PR #355: Sidecar Failure and Configuration Semantics

Keep `compileMaxDeliver: 1` as an intentional result-driven delivery policy.
When a compile command is valid but a sidecar cannot be read because the Object
Store is temporarily unavailable, the worker returns a failed
`CompileResultPayload` with error code `binary_asset_unavailable` and
`retryable=True`. It publishes the result and ACKs the command only after the
publish succeeds. A result-publish failure continues through the existing NAK
path because no terminal outcome was delivered.

Object integrity, reference, digest, size, bucket, and missing immutable-object
failures remain `invalid_binary_asset` and non-retryable. Temporary transport
failure is never reported as corruption. If opening the Object Store fails after
the command has been fetched, `run_once()` passes that failure into the normal
message/result path so the command receives the same retryable result when the
JetStream connection can still publish it.

When the Object Store bucket already exists, inspect its status and use the
complete backing stream configuration as the basis for an update. Change only
`max_age` and `max_bytes` when they differ from the configured TTL and capacity.
Preserve subjects and all Object Store-specific stream fields, and never
destroy or recreate a populated bucket. The existing create-race recovery
remains intact.

Remove the unrelated stale Kubernetes `resourceVersion` cleanup commit and its
test changes from this PR.

## PR #356 and #357: Supported 3MF Subset Parity

Keep the injected compile-runtime graph guard. A shared fixture matrix defines
the contract exercised by both runtime and HTTP-preflight tests:

- accept one identity-built mesh;
- accept multiple identity-built meshes;
- reject a build-item transform;
- reject a repeated build object;
- reject component assemblies;
- reject a mesh subset;
- reject a missing build object;
- reject a non-mesh build object;
- reject a missing build; and
- reject archives with no mesh objects.

The HTTP preflight raises a named unsupported-build-graph exception with a
stable user-facing message. The endpoint maps it to HTTP 400 before committing
the project transaction, so no project or artifact rows survive rejection.
Native parser and geometry failures remain outside this semantic guarantee.

## Bounded Streaming XML Preflight

Replace full-DOM parsing in the API preflight with a small bounded streaming
parser for root relationships and the primary model. A capped reader enforces
the XML byte limit independently of ZIP metadata. Event-driven parsing records
only relationship targets, object identifiers and mesh/component classification,
and build item references/transforms. Processed elements are cleared so vertex,
triangle, metadata, and extension content are not retained.

Track element depth and reject documents above a conservative fixed nesting
limit. Continue using `defusedxml` so DTD, entity, and XXE payloads are rejected.
The parser validates the core model namespace and gathers only enough state to
enforce the supported subset; it does not implement component or transform
semantics.

## Durable Source Capacity Policy

Retain `MAX_3MF_UPLOAD_BYTES` at 128 MiB. Raise the default application
PostgreSQL PVC from 2 GiB to 32 GiB in the Helm defaults and local values.
Document that operators must size PostgreSQL for durable original 3MF sources,
database overhead, indexes, WAL, temporary space, and backups. Add deployment
configuration coverage binding the 128 MiB upload contract to the 32 GiB
default. This policy adds no new storage service or quota subsystem.

## PR #358: Import and Activation Recovery

Treat import persistence and project activation as separate state transitions.
Immediately after a successful import, refresh the project list so it contains
the durable new project, then close and reset the upload dialog. Attempt
activation without issuing another import request. If activation fails, retain
the current active project, alert the user, and leave the imported project in
the selector for a normal activation retry. Broadcast the active-project event
only after activation succeeds.

Remove the unrelated Site Workbench PDF response-fixture commit from this PR.

## Validation

Use test-first red/green cycles for every behavior change. Focused validation
includes sidecar outage, integrity, open-failure, ACK/NAK, bucket reconciliation,
create-race, build-graph parity, endpoint rollback, XML byte/depth and
DTD/entity limits, large irrelevant XML, PostgreSQL capacity configuration, and
UI activation recovery without a second import POST.

After each branch is corrected, restack its descendants without squashing and
run inherited relevant suites. Run the repository quality gates and attempt the
full authenticated `live-flow` because project import and activation affect the
AI-edit-linked workflow. If local runtime, authentication, provider credentials,
or port forwarding prevent that flow, report the exact blocker and all focused
validation that completed.

Push only the four existing PR branches. Confirm their final heads, bases,
mergeability, and CI state. Do not merge or squash.
