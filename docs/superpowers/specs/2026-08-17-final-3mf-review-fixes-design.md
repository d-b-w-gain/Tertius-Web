# Final 3MF Review Fixes Design

## Scope

This change closes the two required final-review findings in the lean 3MF PR
stack without expanding its architecture:

1. align the local NATS JetStream PVC with the compile sidecar Object Store
   capacity; and
2. prevent the import dialog from implying that an in-flight request can be
   cancelled.

Compile-stream retention limits remain explicitly deferred to a separate
infrastructure-hardening change. This work does not add an import worker,
another stream, BREP conversion, request cancellation, or any other new
runtime component.

## NATS Storage Contract

The compile sidecar Object Store retains its 8 GiB application limit
(`8589934592` bytes). Both the default and local Helm configurations provide a
10 GiB NATS JetStream file-store PVC. The local value changes from 1 GiB to
10 GiB; the default value remains 10 GiB.

Deployment coverage will assert the relationship directly for both value
files: the NATS PVC capacity must be at least the configured sidecar Object
Store capacity. It will also retain an explicit assertion that the sidecar
limit is 8 GiB. A focused, explicit contract is preferred over a generic
storage-capacity framework.

This change belongs to PR #355. Descendant branches #356 through #358 will be
restacked onto the updated head.

## Pending Import Interaction

Submitting the 3MF form continues to set `importPending` before starting the
request. While that state is true, both submission and Cancel controls are
disabled. Cancel remains a dialog-close action only when no import is active;
it does not claim to abort the network request.

The existing success and activation-failure flows remain unchanged. Once the
pending import resolves successfully, the project list refreshes, the dialog
closes, and activation is attempted exactly once. Activation failure keeps the
previous project active and the imported project available for manual retry.

This change belongs to PR #358 and does not require an `AbortController`.

## Testing

The storage change will be test-first: add deployment assertions that fail
against the current 1 GiB local NATS value, then raise it to 10 GiB and rerun
the chart/deployment checks.

The UI change will also be test-first. A focused Vitest case will hold the
`import3mf` promise pending, assert that Cancel is disabled, resolve the
promise, and verify the existing successful refresh and activation path. The
activation-failure recovery regression remains intact.

Final verification includes focused backend/deployment and frontend tests,
TypeScript typecheck, production UI build, Helm lint, runtime parity, stack
topology, PR-specific scope checks, and GitHub Actions for all four restacked
heads. No PR will be merged or squashed.

## Acceptance Criteria

- Default NATS JetStream PVC: 10 GiB.
- Local NATS JetStream PVC: 10 GiB.
- Compile sidecar Object Store limit: 8 GiB.
- Deployment tests reject either NATS PVC being smaller than the sidecar
  capacity.
- Import Cancel is disabled only while `importPending` is true.
- Resolving a pending import still performs the normal refresh and one
  activation attempt.
- Existing 3MF correctness, recovery, and architecture guardrails remain
  unchanged.
- PR bases remain `master`, `codex/3mf-lean-sidecars`,
  `codex/3mf-lean-loader`, and `codex/3mf-lean-api`.
