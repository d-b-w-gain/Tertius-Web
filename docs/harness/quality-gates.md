# Quality Gates

Use the lightest validation that covers the changed behavior, and state the
evidence in final notes or PR notes.

## Baseline Gates

- Backend changes: focused tests, then broader `uv run pytest` when shared
  behavior changed.
- Python typing-sensitive changes: `uv run mypy`.
- Frontend changes: typecheck/tests for the touched surface.
- Helm/config/runtime changes: `bash scripts/test-deployment-config.sh` and
  `bash scripts/check-runtime-parity.sh`.
- Runtime integration changes: shared HTTP smoke against the relevant runtime.
- Authenticated frontend/API workflow changes: `scripts/harness-k3s.sh
  live-flow` or `scripts/harness-compose.sh live-flow`.
- Frontend PR review runtime: use the disposable `tertius-live-flow-smoke`
  k3s release on UI port `18083` when reviewers need a real backend and smoke
  Keycloak rather than Compose dev.
- AI edit changes: full `live-flow`; compile-only mode is not sufficient final
  evidence.
- Observability backend changes: full `live-flow`, metrics queries, and
  `scripts/harness-query-traces.sh`.
- UI-facing changes: browser console and network inspection.
- Performance, compile, telemetry, or startup claims: metrics query evidence.

## k3s Required

Prefer k3s for Helm/chart changes, Dockerfile changes, auth/routing changes,
compile worker changes, NATS/KEDA/CloudNativePG/Keycloak changes, telemetry
pipeline changes, and anything that depends on Kubernetes probes, Services,
PVCs, or NetworkPolicy.

## Compose Dev Is Enough

Compose dev plus tests is usually enough for isolated UI component work,
backend pure unit changes, and docs-only changes.

## Browser Evidence

For UI-affecting changes, inspect console errors. For API/UI integration
changes, inspect failed network requests. For visual bug fixes, capture
screenshot or DOM evidence. For reported UI bugs, capture before/after evidence
when reproducible. For 3D viewer changes, verify the canvas/WebGL surface is
nonblank.

Summarize evidence; do not dump excessive raw logs.
