---
name: tertius-harness
description: Use when validating Tertius changes, running the local k3s harness, using Chrome DevTools MCP, querying metrics/traces, or checking Compose/Helm runtime parity.
---

# Tertius Harness

Use this skill to choose and run the correct Tertius validation path. Link to
repo docs instead of loading all details into context.

## Decision Tree

1. Use k3s for full-stack validation, Helm/chart, Dockerfile, auth/routing,
   compile worker, NATS/KEDA/CloudNativePG/Keycloak, and telemetry pipeline
   changes.
2. Use Compose dev for fast frontend/backend inner-loop checks.
3. Use Compose parity for production image and nginx sanity when k3s is too
   heavy.
4. Use Chrome DevTools MCP for UI-facing changes.
5. Query metrics for performance, compile, telemetry, and startup claims.
6. Use `live-flow` for authenticated frontend-origin compile and AI edit
   validation. Compile-only mode is not enough for AI edit changes.

## Entry Points

- Overview: `docs/harness/index.md`
- Local runtimes: `docs/harness/local-harness.md`
- Quality gates: `docs/harness/quality-gates.md`
- Runtime parity: `docs/harness/runtime-parity.md`
- Browser details: `tools/codex/skills/tertius-harness/references/browser-validation.md`
- Observability details: `tools/codex/skills/tertius-harness/references/observability-validation.md`

## Commands

```bash
scripts/harness-k3s.sh up
scripts/harness-k3s.sh ports
scripts/harness-k3s.sh smoke
scripts/harness-k3s.sh live-flow
scripts/harness-compose.sh dev-up
scripts/harness-compose.sh parity-up
scripts/harness-compose.sh live-flow
bash scripts/check-runtime-parity.sh
scripts/harness-query-metrics.sh --file docs/harness/queries/api.promql
```

Final notes should include what changed, validation run, browser evidence when
applicable, metrics evidence when applicable, and known gaps.
