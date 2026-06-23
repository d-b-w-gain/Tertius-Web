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
7. Use the Frontend Preview flow when the user asks to host, preview, or share
   local UI work for browser review.

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

## Frontend Preview

For browser-reviewable UI connected to real auth/API/compile services, use the
disposable k3s frontend PR flow from `docs/harness/local-harness.md`.

Default local-only preview:

```bash
KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 TRACES_LOCAL_PORT=10431 \
KEDA_ENABLED=true scripts/harness-k3s.sh up

KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 TRACES_LOCAL_PORT=10431 \
scripts/harness-k3s.sh ports
```

When the user explicitly asks for another machine to connect, bind only the
review port-forwards to all interfaces:

```bash
KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 TRACES_LOCAL_PORT=10431 \
PORT_FORWARD_ADDRESS=0.0.0.0 scripts/harness-k3s.sh ports
```

For authenticated shared previews, Keycloak needs a DNS hostname, not a raw IP,
for its public issuer/hostname. On the user's Tailscale tailnet, prefer the
machine's MagicDNS name, for example
`http://johnson-minipc.mermaid-snake.ts.net:18083`. If MagicDNS is unavailable,
use another DNS name that resolves to the host; `sslip.io` is only a fallback.

Set the disposable preview release, not prod, to that public origin:

```bash
helm upgrade tertius-live-flow-smoke infra/charts/tertius -n tertius \
  --reuse-values --wait --timeout 10m \
  --set-string keycloak.hostname=http://johnson-minipc.mermaid-snake.ts.net:18083 \
  --set-string keycloak.adminHostname=http://johnson-minipc.mermaid-snake.ts.net:18083 \
  --set-string app.config.keycloakIssuerUrl=http://johnson-minipc.mermaid-snake.ts.net:18083/realms/tertius \
  --set-string app.config.oidcIssuerUrl=http://johnson-minipc.mermaid-snake.ts.net:18083/realms/tertius
```

Verify `/api/auth/login` redirects to the same public hostname. If Keycloak
discovery is correct but login still redirects to an old host, restart only
`deploy/tertius-live-flow-smoke-api` after Keycloak is Ready; the API may have
cached discovery metadata during reconciliation.

Report the public DNS URL, not `0.0.0.0`, as the shared preview URL. Keep
wildcard-bound previews short-lived and stop them with
`scripts/harness-k3s.sh stop-ports` or by ending the kept-open port-forward
session.

Final notes should include what changed, validation run, browser evidence when
applicable, metrics evidence when applicable, and known gaps.
