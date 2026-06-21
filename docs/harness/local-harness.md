# Local Harness Runtime

Tertius has three local runtime paths. Kubernetes through Helm/k3s is canonical
for production-shaped validation. Compose remains optimized for fast editing.

## Required Tools

- Docker with Compose
- `kubectl`
- Helm
- `curl`
- Chrome for browser validation

Optional on Windows: PowerShell for the local k3s Docker container helpers.

## Compose Dev

Use Compose dev when you need fast feedback with bind-mounted backend and UI
source:

```bash
docker compose up -d postgres keycloak nats otel-collector victoriametrics
docker compose up backend compile-job-runner frontend
```

Default URLs:

- UI: `http://localhost:5173`
- API: `http://localhost:8000`
- Metrics query endpoint: `http://localhost:8428`
- OTLP HTTP endpoint: `http://localhost:4318`

Stop without deleting named volumes:

```bash
scripts/harness-compose.sh down
```

Delete Compose data only when intended:

```bash
DELETE_DATA=true scripts/harness-compose.sh down
```

## k3s Harness

Use k3s for full-stack validation, chart changes, Dockerfile changes, auth,
routing, compile worker, NATS/KEDA/CloudNativePG/Keycloak, and telemetry
pipeline changes.

```bash
scripts/harness-k3s.sh up
scripts/harness-k3s.sh ports
scripts/harness-k3s.sh status
scripts/harness-k3s.sh smoke
scripts/harness-k3s.sh live-flow
```

Defaults:

- UI: `http://localhost:18080`
- API direct port-forward: `http://localhost:18000`
- Keycloak: dynamic local port unless `KEYCLOAK_LOCAL_PORT` is set
- Metrics: `http://localhost:8428` after the metrics backend is enabled and
  port-forwarded

Cleanup commands:

```bash
scripts/harness-k3s.sh down
scripts/harness-k3s.sh delete-data
```

`delete-data` prompts before removing database clusters and PVCs unless
`HARNESS_ASSUME_YES=true`.

After `up` completes, the wrapper starts local port-forwards when the deploy
smoke forwards have fully released their ports, and writes `.tmp/harness/k3s.env`
with `UI_BASE_URL`, `API_BASE_URL`, `METRICS_BASE_URL`, and
`KEYCLOAK_TOKEN_URL` when a release-local Keycloak service is available. If the
deploy smoke forwards are still draining, `up` leaves the release deployed and
prints a prompt to run `scripts/harness-k3s.sh ports` or
`scripts/harness-k3s.sh live-flow` in a fresh command. `down` stops
wrapper-owned port-forwards.

Use `scripts/harness-k3s.sh ports` to validate an already-running release
without redeploying it. This is the right entry point for Flux-managed or shared
local releases. Use `scripts/harness-k3s.sh stop-ports` to stop only wrapper
owned port-forwards.

Use `scripts/test-k3s-deployment.sh` directly for CI-compatible debugging or
when you need the raw script flags. The wrapper delegates to it for deploy and
cleanup.

The k3s deploy script fails early when it detects a Keycloak operator that only
watches its own namespace and no operator is running in the target namespace.
For isolated smoke namespaces, install a cluster-wide/target-namespace
Keycloak operator or use a namespace already watched by the operator. Set
`ALLOW_KEYCLOAK_OPERATOR_SCOPE_MISMATCH=true` only when another reconciler is
known to handle the target namespace.

## Compose Parity

Use Compose parity for image-level and nginx-routing sanity without k3s:

```bash
scripts/harness-compose.sh parity-up
scripts/harness-compose.sh smoke
scripts/harness-compose.sh live-flow
scripts/harness-compose.sh down
```

Defaults:

- UI: `http://localhost:18080`
- API direct: `http://localhost:18000`
- Metrics query endpoint: `http://localhost:8428`

Override ports when running beside k3s:

```bash
COMPOSE_PARITY_UI_PORT=18081 COMPOSE_PARITY_API_PORT=18001 \
  scripts/harness-compose.sh parity-up
```

k3s and Compose parity intentionally share default UI/API host ports. Do not run
both at the defaults at the same time.

## Live Flow

`scripts/smoke-live-flow.sh` is the shared authenticated workflow validator.
It sends all project, compile, and AI edit API calls through the UI base URL
(`/api/intus/...`) so nginx/Vite proxy behavior is part of the proof.

Required environment:

```bash
KEYCLOAK_TOKEN_URL=http://localhost:8080/realms/tertius/protocol/openid-connect/token
KEYCLOAK_SMOKE_USERNAME=demo
KEYCLOAK_SMOKE_PASSWORD=demo
```

When using `scripts/harness-k3s.sh live-flow`, the wrapper sources
`.tmp/harness/k3s.env` and exports `KEYCLOAK_TOKEN_URL` automatically after it
starts the Keycloak port-forward. Direct calls to `scripts/smoke-live-flow.sh`
still need `KEYCLOAK_TOKEN_URL` in the environment. The smoke script tries the
password grant first for local smoke clients and falls back to a
non-interactive authorization-code login when direct access grants are disabled.

Full compile plus live AI edit validation also requires the runtime API to have
`LLM_API_KEY` and a file edit system prompt configured. k3s live-flow requires a
compile worker; deploy validation releases with `KEDA_ENABLED=true` so the
compile `ScaledJob` exists. To avoid a paid/provider call when the change only
needs compile coverage:

```bash
LIVE_FLOW_COMPILE_ONLY=true scripts/harness-k3s.sh live-flow
```

For AI-facing changes, do not use compile-only mode as final evidence. The full
flow must show: auth token acquired, seed code saved through the UI origin,
pre-edit compile succeeded, AI edit job succeeded, and post-edit compile
succeeded.

Fast path for AI edit validation: deploy or reuse an isolated local-values k3s
smoke release instead of a shared or Flux-managed production-style release. The
local-values release provides the `demo / demo` smoke user, direct-grant-friendly
Keycloak settings, KEDA compile workers, and release-local LLM secrets when
configured. A typical isolated run uses separate ports:

```bash
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 METRICS_LOCAL_PORT=8430 \
KEDA_ENABLED=true scripts/harness-k3s.sh up

NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 METRICS_LOCAL_PORT=8430 \
scripts/harness-k3s.sh live-flow
```

Use a shared or Flux-managed release for live-flow only when that release's auth,
routing, or production-shaped behavior is the thing being validated.
