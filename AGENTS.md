# Tertius Agent Map

This file is a compact map for automated and human agents working in this
repository. Use `README.md` for setup details and the linked docs below for
runtime-specific validation.

## Repository Shape

- `ui/`: React/Vite frontend and workflow UI surfaces.
- `server/`: FastAPI backend, workflow APIs, auth BFF, persistence, and compile
  orchestration.
- `infra/charts/tertius/`: Helm chart for the canonical deployable runtime.
- `infra/deploy/`: deployment docs and nginx runtime config.
- `infra/otel/`: local OpenTelemetry collector config.
- `scripts/`: local k3s, Compose, smoke, config, and support scripts.
- `docs/harness/`: harness validation docs for agents and contributors.
- `docs/observability/`: telemetry design, dashboards, and alert guidance.
- `docs/superpowers/plans/`: execution plans; keep checkboxes current as work
  lands.

## Runtime Rules

- Kubernetes via Helm/local k3s is canonical for full-stack validation.
- Docker Compose is the fast development adapter for inner-loop work.
- Compose parity is for image/nginx sanity when k3s is too heavy.
- Do not silently copy Compose dev conveniences into Helm. Document intentional
  differences in `docs/harness/runtime-parity.md`.
- New runtime environment variables must be considered for Helm values/templates,
  Compose dev, Compose parity, and `scripts/check-runtime-parity.sh`.
- Use `scripts/harness-k3s.sh live-flow` or `scripts/harness-compose.sh
  live-flow` for authenticated frontend-origin compile/AI edit validation.
  `LIVE_FLOW_COMPILE_ONLY=true` is only acceptable when the change does not
  touch AI edit behavior.
- Treat Generate Design, AI edit tab, AI edit conversation history, and
  AI-edit-linked model viewer changes as AI edit behavior even when the patch is
  frontend-only. Before finalizing those changes, run full `live-flow`; if the
  local runtime, auth, provider credentials, or port-forwarding are unavailable,
  report the exact blocker and the focused validation that did run.
- For fastest AI edit validation, use an isolated local-values k3s smoke
  release, not a shared or Flux-managed production-style release. The smoke
  release should provide demo auth, direct-grant-friendly Keycloak config, KEDA,
  and LLM secrets; only validate against a shared/Flux release when that release
  behavior is the subject of the change.

## Validation Entry Points

- Harness overview: `docs/harness/index.md`
- Local runtimes: `docs/harness/local-harness.md`
- Browser validation: `docs/harness/browser-validation.md`
- Observability validation: `docs/harness/observability-validation.md`
- Runtime parity: `docs/harness/runtime-parity.md`
- Quality gates: `docs/harness/quality-gates.md`
- Live compile/AI edit flow: `scripts/smoke-live-flow.sh`

## Telemetry Safety

Do not add secrets, prompts, generated source, uploaded model files, auth tokens,
raw user IDs, raw project IDs, raw job IDs, or other high-cardinality identifiers
to metrics or logs. Prefer bounded labels and hashed or aggregated identifiers
only when product requirements justify them.
