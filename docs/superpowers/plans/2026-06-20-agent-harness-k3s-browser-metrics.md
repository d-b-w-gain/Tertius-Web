# Agent Harness, Browser MCP, Metrics, and Runtime Parity Implementation Plan

> **For agentic workers:** Implement this task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. Keep this plan updated as work lands. Do not treat this document as a prompt dump; it is the source of truth for the rollout.

**Goal:** Adopt the practical parts of OpenAI's harness engineering approach for Tertius: make the application, browser state, observability signals, and runtime contracts legible to Codex so agents can validate their own work with a production-shaped local harness.

**Architecture:** Kubernetes/Helm is the canonical deployable runtime. Docker Compose remains the fast development adapter. A repo-owned Codex skill drives repeatable agent behavior, while durable product/runtime knowledge stays in versioned docs. OpenTelemetry remains the application telemetry boundary. Chrome DevTools MCP provides browser legibility for UI validation. Runtime parity checks prevent Docker Compose, Helm, and CI smoke behavior from drifting silently.

**Primary references:**
- OpenAI Harness Engineering article: https://openai.com/index/harness-engineering/
- Chrome DevTools MCP source: https://github.com/ChromeDevTools/chrome-devtools-mcp
- Existing OpenTelemetry plan: `docs/superpowers/plans/2026-06-20-opentelemetry-observability.md`
- Existing local k3s harness: `scripts/test-k3s-deployment.sh`
- Existing local k3s docs: `infra/deploy/README.md`, `scripts/README-local-k3s.md`

---

## Product Decisions

- Kubernetes is the canonical full-stack validation target.
- Compose is retained for fast local development and should not be forced to emulate every Kubernetes concern.
- Compose parity mode should exist for production-shaped local container checks, but it is secondary to k3s.
- Agent harness validation should prefer k3s unless the task is explicitly frontend/backend inner-loop work.
- The Tertius harness skill should be source-controlled in the repo and installed into the local Codex skill directory by a script.
- Durable facts belong in `docs/harness/`; procedural agent behavior belongs in the skill.
- Browser validation should use Chrome DevTools MCP over a local-only Chrome debugging target.
- Metrics should be queryable locally through PromQL-compatible endpoints, not only printed by the collector debug exporter.
- Helm/local k3s values are the canonical source for production-shaped config. Compose parity checks compare against Helm-rendered local behavior.
- Any intentional runtime difference must be documented so agents do not "fix" it incorrectly.

## Non-Goals

- Do not remove Docker Compose.
- Do not replace the existing k3s smoke script.
- Do not introduce a production APM vendor dependency into application code.
- Do not require telemetry backends for application startup.
- Do not record prompts, generated source, uploaded model files, auth tokens, raw user IDs, raw project IDs, or raw job IDs in metrics/logs.
- Do not add remote browser debugging endpoints bound to non-local interfaces.
- Do not make local harness scripts destructive by default.

## Implementation Files

Create:
- `AGENTS.md`
- `docs/harness/index.md`
- `docs/harness/local-harness.md`
- `docs/harness/browser-validation.md`
- `docs/harness/observability-validation.md`
- `docs/harness/runtime-parity.md`
- `docs/harness/quality-gates.md`
- `docs/harness/queries/api.promql`
- `docs/harness/queries/compile.promql`
- `docs/harness/queries/llm.promql`
- `docs/harness/queries/collector.promql`
- `tools/codex/skills/tertius-harness/SKILL.md`
- `tools/codex/skills/tertius-harness/references/browser-validation.md`
- `tools/codex/skills/tertius-harness/references/observability-validation.md`
- `scripts/install-tertius-harness-skill.sh`
- `scripts/harness-k3s.sh`
- `scripts/harness-compose.sh`
- `scripts/harness-status.sh`
- `scripts/harness-query-metrics.sh`
- `scripts/smoke-http.sh`
- `scripts/check-runtime-parity.sh`
- `docker-compose.parity.yml`

Modify:
- `README.md`
- `.gitignore`
- `docker-compose.yml`
- `infra/otel/otel-collector-local.yaml`
- `infra/charts/tertius/values.yaml`
- `infra/charts/tertius/values-local.yaml`
- `infra/charts/tertius/templates/otel-collector.yaml`
- `infra/charts/tertius/README.md`
- `infra/deploy/README.md`
- `docs/observability/collector.md`
- `docs/observability/dashboards.md`
- `docs/observability/alerts.md`
- `scripts/README-local-k3s.md`
- `scripts/test-k3s-deployment.sh`
- `scripts/test-deployment-config.sh`
- `.github/workflows/chart-tests.yml`

Only if needed:
- `Dockerfile.api`
- `Dockerfile.ui`
- `infra/deploy/nginx/default.conf.template`
- `infra/charts/tertius/templates/victoriametrics.yaml`

---

## Runtime Model

### Fast Dev Runtime

Use Docker Compose for fast editing:

```bash
docker compose up -d postgres keycloak nats otel-collector
docker compose up backend compile-job-runner frontend
```

Expected behavior:
- Backend code is bind-mounted.
- UI code is bind-mounted.
- Vite HMR remains available on `http://localhost:5173`.
- The stack prioritizes speed over deployment parity.

### Production-Shaped Runtime

Use k3s for canonical validation:

```bash
scripts/harness-k3s.sh up
```

Expected behavior:
- Builds deployable API and UI images.
- Loads images into k3s.
- Installs or upgrades the Helm release with `values-local.yaml`.
- Waits for Kubernetes resources.
- Starts or reports port-forwards.
- Leaves a usable UI endpoint, normally `http://localhost:18080`.
- Runs shared smoke checks.

### Compose Parity Runtime

Use Compose parity for image-level local checks when k3s is too heavy:

```bash
scripts/harness-compose.sh parity-up
```

Expected behavior:
- Uses production Dockerfiles or production-shaped images.
- Serves static UI through the same nginx routing assumptions as Kubernetes.
- Uses `/api` same-origin routing.
- Exports OTEL to the local collector.
- Does not replace k3s validation.

---

## Phase 1: Repository Knowledge Map

**Goal:** Give agents a compact map and durable harness docs without overloading context.

### Task 1.1: Add `AGENTS.md`

**Files:**
- Create: `AGENTS.md`

- [x] Add a short repo map, not a manual.
- [x] Point to `README.md` for setup.
- [x] Point to `docs/harness/index.md` for harness validation.
- [x] Point to `docs/observability/*` for telemetry.
- [x] Point to `docs/superpowers/plans/*` for execution plans.
- [x] State that Kubernetes is canonical for full-stack validation.
- [x] State that Compose is the fast dev adapter.
- [x] State that agents must not add secrets or high-cardinality IDs to telemetry.
- [x] Keep the file under roughly 120 lines.

Acceptance criteria:
- `AGENTS.md` exists at the repository root.
- It contains links to the harness docs.
- It does not duplicate detailed setup instructions from other docs.

### Task 1.2: Add Harness Docs Index

**Files:**
- Create: `docs/harness/index.md`

- [x] Explain the purpose of the harness docs.
- [x] Link to local runtime, browser validation, observability validation, runtime parity, and quality gates.
- [x] Include a "which runtime should I use?" table:
  - Fast React/Python iteration: Compose dev.
  - Full-stack agent validation: k3s.
  - Image/nginx sanity without k3s: Compose parity.
  - CI deploy smoke: existing GitHub k3s workflow.

Acceptance criteria:
- A new contributor or agent can identify the correct validation path from one page.

### Task 1.3: Document Local Harness Runtime

**Files:**
- Create: `docs/harness/local-harness.md`
- Modify: `README.md`
- Modify: `infra/deploy/README.md`
- Modify: `scripts/README-local-k3s.md`

- [x] Document Compose dev startup.
- [x] Document k3s harness startup.
- [x] Document Compose parity startup.
- [x] Document required local tools: Docker, kubectl, Helm, curl, Chrome.
- [x] Document optional local tools: PowerShell for Windows k3s container startup.
- [x] Document expected URLs:
  - Compose dev UI: `http://localhost:5173`
  - Compose dev API: `http://localhost:8000`
  - k3s UI: `http://localhost:18080`
  - k3s API direct port-forward: `http://localhost:18000`
  - Compose parity UI: `http://localhost:18080` by default, override with `COMPOSE_PARITY_UI_PORT`
  - Compose parity API: `http://localhost:18000` by default, override with `COMPOSE_PARITY_API_PORT`
  - local metrics endpoint after metrics backend is added: `http://localhost:8428`
- [x] Document that k3s and Compose parity intentionally share default host ports and should not be run together without overriding one runtime's ports.
- [x] Document cleanup commands.
- [x] Link from `README.md` development section.

Acceptance criteria:
- The docs tell a developer how to start each runtime without reading shell scripts.
- Existing k3s docs are not contradicted.

---

## Phase 2: Tertius Harness Codex Skill

**Goal:** Make repeatable validation behavior triggerable by Codex without putting all harness knowledge into the prompt.

### Task 2.1: Create Repo-Owned Skill Source

**Files:**
- Create: `tools/codex/skills/tertius-harness/SKILL.md`
- Create: `tools/codex/skills/tertius-harness/references/browser-validation.md`
- Create: `tools/codex/skills/tertius-harness/references/observability-validation.md`

- [x] Name the skill `tertius-harness`.
- [x] Write frontmatter with a trigger description covering:
  - validating Tertius changes;
  - using Chrome DevTools MCP;
  - running local k3s harness;
  - querying metrics/traces;
  - checking runtime parity.
- [x] Keep `SKILL.md` concise and procedural.
- [x] Instruct the agent to choose a runtime:
  - k3s for full-stack validation;
  - Compose dev for fast inner-loop checks;
  - Compose parity for image/nginx sanity.
- [x] Link to repo docs instead of duplicating them.
- [x] Put browser-specific workflow details in `references/browser-validation.md`.
- [x] Put metrics/query workflow details in `references/observability-validation.md`.
- [x] Avoid extra files such as README files inside the skill folder.

Acceptance criteria:
- Skill content can be copied into `${CODEX_HOME:-$HOME/.codex}/skills/tertius-harness`.
- The skill tells Codex what to do, not just what the system is.

### Task 2.2: Add Skill Install Script

**Files:**
- Create: `scripts/install-tertius-harness-skill.sh`

- [x] Copy `tools/codex/skills/tertius-harness` to `${CODEX_HOME:-$HOME/.codex}/skills/tertius-harness`.
- [x] Create the destination directory if missing.
- [x] Remove the previous installed copy before copying.
- [x] Print the installed path.
- [x] Refuse to run if the source skill is missing.
- [x] Avoid touching unrelated skills.

Validation:

```bash
bash scripts/install-tertius-harness-skill.sh
test -f "${CODEX_HOME:-$HOME/.codex}/skills/tertius-harness/SKILL.md"
```

Acceptance criteria:
- The install script is idempotent.
- It does not require network access.

### Task 2.3: Document Skill Usage

**Files:**
- Modify: `docs/harness/index.md`
- Modify: `README.md`

- [x] Document how to install the skill.
- [x] Explain that the repo source remains under `tools/codex/skills`.
- [x] Explain that durable docs remain under `docs/harness`.
- [x] Provide one example trigger phrase: "Use the Tertius harness to validate this UI change."

Acceptance criteria:
- A developer can install and understand the skill without knowing Codex internals.

---

## Phase 3: Local k3s Harness Wrapper

**Goal:** Preserve the existing CI smoke script while adding a friendlier local agent entry point.

### Task 3.1: Add `scripts/harness-k3s.sh`

**Files:**
- Create: `scripts/harness-k3s.sh`

Required commands:

```bash
scripts/harness-k3s.sh up
scripts/harness-k3s.sh smoke
scripts/harness-k3s.sh status
scripts/harness-k3s.sh down
scripts/harness-k3s.sh delete-data
```

- [x] `up` should call `scripts/test-k3s-deployment.sh` with local defaults.
- [x] `smoke` should run shared smoke checks against current port-forwards when possible.
- [x] `status` should print namespace resources, current port-forward hints, UI URL, API URL, and key pods.
- [x] `down` should call `scripts/test-k3s-deployment.sh --cleanup`.
- [x] `delete-data` should call `scripts/test-k3s-deployment.sh --cleanup --delete-data` after a confirmation prompt unless `HARNESS_ASSUME_YES=true`.
- [x] Pass through `NAMESPACE`, `RELEASE_NAME`, `KUBECONFIG`, `K3S_CONTAINER`, `KEDA_ENABLED`, and image env vars.
- [x] Default `NAMESPACE=tertius` and `RELEASE_NAME=tertius`.
- [x] Preflight `UI_LOCAL_PORT` and `API_LOCAL_PORT` before starting port-forwards; fail with a clear message if Compose parity, another k3s run, or any local process already owns the chosen ports.
- [x] Document that k3s defaults to `UI_LOCAL_PORT=18080` and `API_LOCAL_PORT=18000`, and that callers can override them when running k3s beside Compose parity.
- [x] Refuse to operate on a Flux-managed release unless the existing `ALLOW_FLUX_MANAGED_RELEASE=true` is set.

Acceptance criteria:
- Existing `scripts/test-k3s-deployment.sh` remains the CI-compatible implementation.
- The wrapper provides stable commands for agents and humans.

### Task 3.2: Make k3s Smoke Port-Forwards Discoverable

**Files:**
- Modify: `scripts/test-k3s-deployment.sh`

- [x] After `smoke_test_http`, print the final UI and API local URLs.
- [x] Write a small status file under `.tmp/harness/k3s.env` with:
  - `NAMESPACE`
  - `RELEASE_NAME`
  - `UI_BASE_URL`
  - `API_BASE_URL`
  - `KEYCLOAK_BASE_URL` when available
- [x] Ensure `.tmp/` is ignored by git if it is not already.
- [x] Do not make this status file required for CI success.

Acceptance criteria:
- Browser MCP instructions can read the local UI URL from a stable file.

### Task 3.3: Add k3s Harness Docs

**Files:**
- Modify: `docs/harness/local-harness.md`
- Modify: `scripts/README-local-k3s.md`

- [x] Document wrapper commands.
- [x] Document when to use `scripts/test-k3s-deployment.sh` directly.
- [x] Document how this maps to the GitHub Actions smoke workflow.

Acceptance criteria:
- The wrapper does not obscure the underlying CI script.

---

## Phase 4: Compose Dev and Compose Parity

**Goal:** Keep Compose useful while making differences from Kubernetes explicit and checkable.

### Task 4.1: Document Intentional Runtime Differences

**Files:**
- Create: `docs/harness/runtime-parity.md`

- [x] Add a table with columns:
  - Concern
  - Kubernetes behavior
  - Compose dev behavior
  - Compose parity behavior
  - Drift policy
- [x] Cover:
  - API process
  - UI serving
  - `/api` routing
  - Postgres
  - Keycloak
  - NATS
  - Valkey
  - compile worker model
  - KEDA ScaledJob
  - CloudNativePG
  - PVCs
  - NetworkPolicy
  - OTEL collector
  - metrics backend
  - environment variables
  - image build path
- [x] Mark Compose dev HMR and bind mounts as intentional differences.
- [x] Mark NATS subject names, max payloads, auth assumptions, OTEL names, and `/api` routing as parity-required.

Acceptance criteria:
- Agents have a written distinction between intentional differences and drift.

### Task 4.2: Add Compose Parity File

**Files:**
- Create: `docker-compose.parity.yml`

- [x] Define production-shaped `backend` using `Dockerfile.api`.
- [x] Define production-shaped `frontend` using `Dockerfile.ui`.
- [x] Build UI with `VITE_API_URL=/api`.
- [x] Serve frontend as static files with nginx, not Vite.
- [x] Keep Postgres, Keycloak, NATS, and OTEL collector from the base Compose file.
- [x] Add a local metrics backend once Phase 7 lands.
- [x] Explicitly neutralize dev-only settings inherited from `docker-compose.yml` for API/UI parity services:
  - no API/UI source bind mounts in rendered config;
  - no frontend `node:20` image in rendered config;
  - no `npm install`, `npm run dev`, Vite command, or HMR-only env in rendered config;
  - no inherited `5173:5173` frontend port in rendered config;
  - only production-shaped UI/API ports should be exposed.
- [x] Use Docker Compose reset/override syntax only if the repository's supported Compose version renders the desired result; otherwise define separate parity service names and make `scripts/harness-compose.sh parity-*` target those services.
- [x] Use stable ports:
  - UI: `18080:80`
  - API: `18000:8000` if direct API exposure is needed
- [x] Allow `COMPOSE_PARITY_UI_PORT` and `COMPOSE_PARITY_API_PORT` to override the default host ports.
- [x] Keep dev Compose behavior working as-is.

Validation:

```bash
docker compose -f docker-compose.yml -f docker-compose.parity.yml config >/tmp/tertius-compose-parity.yml
if grep -q '5173:5173' /tmp/tertius-compose-parity.yml; then
  echo "Compose parity must not expose the Vite dev port."
  exit 1
fi
if grep -Eq 'node:20|npm install|npm run dev|/app/server|/app($|[^[:alnum:]_-])' /tmp/tertius-compose-parity.yml; then
  echo "Compose parity still contains dev image, command, working dir, or bind-mount settings."
  exit 1
fi
docker compose -f docker-compose.yml -f docker-compose.parity.yml up -d --build
bash scripts/smoke-http.sh http://localhost:18080 http://localhost:18000
```

Acceptance criteria:
- Compose parity serves UI through production-shaped static assets.
- Same-origin `/api` routing works in parity mode.
- Rendered Compose parity config proves dev bind mounts, Vite dev server settings, and `5173` exposure are absent.

### Task 4.3: Add Compose Harness Wrapper

**Files:**
- Create: `scripts/harness-compose.sh`

Required commands:

```bash
scripts/harness-compose.sh dev-up
scripts/harness-compose.sh parity-up
scripts/harness-compose.sh smoke
scripts/harness-compose.sh status
scripts/harness-compose.sh down
```

- [x] `dev-up` should start the current Compose dev stack.
- [x] `parity-up` should start Compose with `docker-compose.parity.yml`.
- [x] `smoke` should call `scripts/smoke-http.sh`.
- [x] `status` should print service status and URLs.
- [x] `down` should stop Compose services without deleting named volumes unless `DELETE_DATA=true`.
- [x] `parity-up` should preflight `COMPOSE_PARITY_UI_PORT` and `COMPOSE_PARITY_API_PORT`; fail with a clear message if k3s port-forwards or another local process already owns them.
- [x] `status` should report whether default parity ports conflict with k3s defaults and show the env vars needed to override them.

Acceptance criteria:
- Agents have one stable entry point for Compose.
- The wrapper does not remove existing direct Compose usage.

---

## Phase 5: Shared Smoke Checks

**Goal:** Run the same behavioral checks against Compose and k3s where possible.

### Task 5.1: Extract HTTP Smoke Checks

**Files:**
- Create: `scripts/smoke-http.sh`
- Modify: `scripts/test-k3s-deployment.sh`

Required usage:

```bash
scripts/smoke-http.sh "$UI_BASE_URL" "$API_BASE_URL"
```

- [x] Check UI root returns HTML.
- [x] Check direct API root responds.
- [x] Check direct `/api/intus/health` responds.
- [x] Check frontend `/api/` proxy returns same body as direct API root.
- [x] Check frontend `/api/intus/health` proxy returns same body as direct API health.
- [x] Print concise pass/fail output.
- [x] Preserve detailed response body on failure.
- [x] Avoid auth-required checks in this script.

Acceptance criteria:
- k3s smoke and Compose smoke use the same HTTP checks.

### Task 5.2: Extract Optional Auth and Compile Smoke Checks

**Files:**
- Create or extend: `scripts/smoke-http.sh`
- Modify: `scripts/test-k3s-deployment.sh`

- [x] Add optional flags:
  - `--auth`
  - `--compile`
- [x] For `--auth`, request a Keycloak token using configured smoke credentials.
- [x] For `--compile`, submit a minimal Intus compile request and poll until success.
- [x] Keep k3s-specific in-cluster checks in `scripts/test-k3s-deployment.sh`.
- [x] Keep the default HTTP smoke fast.

Acceptance criteria:
- Compile lifecycle validation can be reused outside the k3s script when the runtime supports it.

---

## Phase 6: Chrome DevTools MCP Browser Harness

**Goal:** Let Codex validate UI behavior through an inspectable browser rather than screenshots alone.

### Task 6.1: Verify and Pin Chrome DevTools MCP Source

**Files:**
- Create: `docs/harness/browser-validation.md`

- [x] Verify the trusted Chrome DevTools MCP package/source before adding install instructions.
- [x] Prefer an official or clearly maintained source.
- [x] Pin the package name and version in docs or config examples.
- [x] Do not document `chrome-devtools-mcp@latest` as the repo-standard install; docs must use the verified exact version, for example `chrome-devtools-mcp@<verified-version>`.
- [x] Document why that source was chosen.
- [x] Document local-only security expectations and browser data exposure risks.
- [x] Document privacy/egress controls for the MCP server:
  - pass `--no-usage-statistics`;
  - pass `--no-performance-crux` unless the validation explicitly needs CrUX-backed performance insights;
  - set `CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS=true`;
  - set `CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=true` for repeatable harness runs.

Acceptance criteria:
- The repo does not instruct agents to run an unverified MCP server package.
- Browser MCP instructions avoid default telemetry/update-check behavior unless a human explicitly opts in.

### Task 6.2: Add Browser Launch Instructions

**Files:**
- Modify: `docs/harness/browser-validation.md`
- Modify: `tools/codex/skills/tertius-harness/references/browser-validation.md`

- [x] Document launching Chrome with:
  - isolated user data dir under `.tmp/chrome-harness`;
  - local-only remote debugging port;
  - no reuse of personal browser profile.
- [x] Document connecting MCP to that browser with `--browser-url=http://127.0.0.1:<port>`, never a wildcard or externally reachable host.
- [x] Document that browser sessions must not contain personal accounts, production credentials, or unrelated tabs because the MCP server can inspect browser contents.
- [x] Document cleanup of the harness profile.
- [x] Document expected target URL discovery:
  - use `.tmp/harness/k3s.env` when present;
  - otherwise use Compose dev/parity defaults.
- [x] Include troubleshooting for:
  - stale port-forward;
  - auth cookie mismatch;
  - Keycloak issuer mismatch;
  - blank WebGL/canvas surface;
  - frontend telemetry CORS failure.

Acceptance criteria:
- An agent can start a browser target without exposing a remote debugging port externally.

### Task 6.3: Define Canonical Browser Journeys

**Files:**
- Modify: `docs/harness/browser-validation.md`
- Create optional sections under: `docs/harness/browser-validation.md`

- [x] Define journey: anonymous UI load.
- [x] Define journey: demo login.
- [x] Define journey: Intus compile submit and status.
- [x] Define journey: Extus viewer load.
- [x] Define journey: Artus feature tree load and AI edit entry path.
- [x] Define journey: Timus drafting load.
- [x] Define journey: artifact download.
- [x] For each journey, list:
  - preconditions;
  - navigation path;
  - user actions;
  - pass signals;
  - console/network failure signals;
  - relevant metrics/traces to query.

Acceptance criteria:
- Browser validation is repeatable and scoped.

### Task 6.4: Add Browser Evidence Standard

**Files:**
- Modify: `docs/harness/quality-gates.md`
- Modify: `tools/codex/skills/tertius-harness/SKILL.md`

- [x] Require console error inspection for UI-affecting changes.
- [x] Require network failure inspection for API/UI integration changes.
- [x] Require screenshot or DOM evidence for visual bug fixes.
- [x] Require before/after evidence when reproducing a reported UI bug.
- [x] Require canvas/WebGL nonblank checks for 3D viewer changes.
- [x] State that evidence should be summarized in final notes or PR notes without dumping excessive raw logs.

Acceptance criteria:
- Agents know what proof is expected from browser validation.

---

## Phase 7: Local Metrics Backend and PromQL Queries

**Goal:** Make local metrics queryable by agents and humans.

### Task 7.1: Add Local Metrics Backend

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.parity.yml`
- Modify: `infra/otel/otel-collector-local.yaml`
- Modify: `docs/observability/collector.md`

- [x] Add a PromQL-compatible local metrics backend. Prefer VictoriaMetrics for OTLP ingestion and PromQL querying unless implementation research shows a better fit.
- [x] Expose local query endpoint on `localhost:8428`.
- [x] Configure the OTEL collector metrics pipeline to export application metrics to the metrics backend.
- [x] Keep collector debug exporter available for local troubleshooting.
- [x] Ensure the app still runs when the metrics backend is unavailable.
- [x] Avoid adding vendor SDKs to app code.

Acceptance criteria:
- Metrics emitted by backend/compile/UI reach a queryable local backend.
- Collector startup remains fail-open for app services.

### Task 7.2: Add Helm Metrics Backend Option

**Files:**
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `infra/charts/tertius/templates/otel-collector.yaml`
- Possibly create: `infra/charts/tertius/templates/victoriametrics.yaml`

- [x] Add local-only values to enable a metrics backend in k3s.
- [x] Default production values should not force a bundled metrics backend unless explicitly enabled.
- [x] Wire collector metrics exporters from values.
- [x] Expose the metrics service through port-forward instructions, not public ingress.
- [x] Add collector self-metrics scrape/query guidance.

Acceptance criteria:
- Local k3s can query application metrics through PromQL-compatible endpoints.
- Production remains configurable and vendor-neutral.

### Task 7.3: Add Metrics Query Script

**Files:**
- Create: `scripts/harness-query-metrics.sh`

Required usage:

```bash
scripts/harness-query-metrics.sh 'sum(rate(tertius_api_request_count[5m]))'
scripts/harness-query-metrics.sh --file docs/harness/queries/api.promql
scripts/harness-query-metrics.sh --file docs/harness/queries/api.promql --name api_request_rate
```

- [x] Default metrics base URL to `http://localhost:8428`.
- [x] Accept `METRICS_BASE_URL`.
- [x] Support a single query argument.
- [x] Support `--file` for named query files.
- [x] Support `--name <query_name>` to run one query from a query file.
- [x] Define query file format:
  - comments start with `#`;
  - a named query starts with `# name: snake_case_query_name`;
  - the PromQL expression is the following non-empty line;
  - blank lines separate entries;
  - multiline PromQL is not required for the first implementation.
- [x] When `--file` is provided without `--name`, run every named query in the file in order and print the query name with each result.
- [x] Return nonzero if a query file contains unnamed expressions, duplicate names, or a `# name:` entry without a following expression.
- [x] Print compact JSON or a readable table.
- [x] Return nonzero when the query endpoint is unavailable.

Acceptance criteria:
- Agents can query metrics without remembering backend-specific curl syntax.
- Agents can run all queries in a domain file or target one named query deterministically.

### Task 7.4: Add PromQL Query References

**Files:**
- Create: `docs/harness/queries/api.promql`
- Create: `docs/harness/queries/compile.promql`
- Create: `docs/harness/queries/llm.promql`
- Create: `docs/harness/queries/collector.promql`
- Modify: `docs/observability/dashboards.md`
- Modify: `docs/observability/alerts.md`

- [x] Add API request rate query.
- [x] Add API p95 duration query.
- [x] Add API 5xx ratio query.
- [x] Add compile started/finished/failed queries.
- [x] Add compile duration p95 query.
- [x] Add compile queue latency query.
- [x] Add NATS publish failure query.
- [x] Add LLM request/failure/cost/token queries.
- [x] Add collector refused/dropped/export-failed telemetry query.
- [x] Use the `# name:` query-file format required by `scripts/harness-query-metrics.sh`.
- [x] Keep each first-pass query expression on a single non-empty line so the script can parse it predictably.
- [x] Include comments describing healthy ranges where known.
- [x] Note when a query depends on histogram bucket names generated by OpenTelemetry exporters.

Acceptance criteria:
- Common validation prompts can point to a file instead of inventing queries.

---

## Phase 8: Runtime Drift Prevention

**Goal:** Detect meaningful drift between Compose, Helm, and CI before it breaks agents or deployment.

### Task 8.1: Add Runtime Parity Check Script

**Files:**
- Create: `scripts/check-runtime-parity.sh`

- [x] Render Helm local values with `helm template`.
- [x] Render Compose dev config with `docker compose config`.
- [x] Render Compose parity config with `docker compose -f docker-compose.yml -f docker-compose.parity.yml config`.
- [x] Check parity-required values:
  - NATS URL target names where applicable;
  - compile stream name;
  - compile request subject;
  - compile result subject;
  - compile worker queue;
  - compile result consumer;
  - compile request max bytes;
  - compile result max bytes;
  - billing stream and subject names;
  - OTEL service names;
  - OTEL protocol;
  - UI `VITE_API_URL=/api` in production-shaped runtimes;
  - Compose parity rendered config has no API/UI bind mounts from the dev stack;
  - Compose parity rendered config has no Vite dev command, `node:20` frontend image, HMR-only env, or `5173` host port;
  - browser OTEL HTTP path;
  - Keycloak realm/client/audience assumptions;
  - auth cookie secure local defaults;
  - exposed local ports documented in harness docs.
- [x] Print clear remediation messages for each failure.
- [x] Allow documented intentional differences.
- [x] Avoid requiring a running cluster.

Validation:

```bash
bash scripts/check-runtime-parity.sh
```

Acceptance criteria:
- The script fails when Compose parity and Helm local config disagree on required contracts.

### Task 8.2: Wire Parity Checks Into Existing Config Tests

**Files:**
- Modify: `scripts/test-deployment-config.sh`
- Modify: `.github/workflows/chart-tests.yml`

- [x] Call `scripts/check-runtime-parity.sh` from `scripts/test-deployment-config.sh` or as a separate GitHub Actions step.
- [x] Ensure CI installs only required tools.
- [x] Keep the check fast enough for PRs.
- [x] Do not require a running Docker daemon unless `docker compose config` is essential. If Docker is unavailable in the config test job, add a graceful skip with a clear local command.

Acceptance criteria:
- CI or local config tests catch runtime drift.

### Task 8.3: Add Drift Policy Docs

**Files:**
- Modify: `docs/harness/runtime-parity.md`
- Modify: `AGENTS.md`

- [x] State Helm/k3s is canonical for production-shaped behavior.
- [x] State Compose dev differences must be documented, not silently copied into Helm.
- [x] State Compose parity should match Helm for routing, image behavior, and env contracts.
- [x] State all new runtime env vars must be added to:
  - Helm values/templates;
  - Compose dev if needed;
  - Compose parity if production-shaped;
  - parity check script when required.

Acceptance criteria:
- Future changes have a clear update checklist.

---

## Phase 9: Quality Gates and Agent Validation Loop

**Goal:** Define what "validated" means for harness-aware agent work.

### Task 9.1: Add Quality Gates Doc

**Files:**
- Create: `docs/harness/quality-gates.md`

- [x] Define baseline gates:
  - backend tests;
  - frontend typecheck/tests;
  - chart/config tests;
  - runtime smoke;
  - browser evidence for UI changes;
  - metrics check for performance/telemetry-sensitive changes.
- [x] Define when k3s validation is required:
  - Helm/chart changes;
  - Dockerfile changes;
  - auth/routing changes;
  - compile worker changes;
  - NATS/KEDA/CloudNativePG/Keycloak changes;
  - telemetry pipeline changes.
- [x] Define when Compose dev is enough:
  - isolated UI component work with tests;
  - backend pure unit changes with tests;
  - docs-only changes.
- [x] Define final response evidence format:
  - what changed;
  - what validation ran;
  - browser evidence if applicable;
  - metrics evidence if applicable;
  - known gaps.

Acceptance criteria:
- Agents and humans can choose validation depth consistently.

### Task 9.2: Update Tertius Harness Skill With Quality Gates

**Files:**
- Modify: `tools/codex/skills/tertius-harness/SKILL.md`

- [x] Add a short validation decision tree.
- [x] Link to `docs/harness/quality-gates.md`.
- [x] Tell agents to prefer k3s for full-stack validation.
- [x] Tell agents to use browser MCP for UI-facing changes.
- [x] Tell agents to query metrics for performance, compile, telemetry, and startup claims.

Acceptance criteria:
- The skill changes agent behavior in a concrete, repeatable way.

---

## Phase 10: CI and Documentation Integration

**Goal:** Make the harness discoverable and keep it maintained.

### Task 10.1: Add README Entry Points

**Files:**
- Modify: `README.md`

- [x] Add a concise "Agent Harness and Validation" section under development.
- [x] Link to `docs/harness/index.md`.
- [x] Mention the three runtime paths:
  - Compose dev;
  - k3s harness;
  - Compose parity.
- [x] Mention the skill install script.

Acceptance criteria:
- A contributor can discover the harness without knowing the plan exists.

### Task 10.2: Update Deployment Docs

**Files:**
- Modify: `infra/deploy/README.md`
- Modify: `infra/charts/tertius/README.md`

- [x] Link the local k3s harness wrapper.
- [x] Explain relationship between wrapper and CI smoke.
- [x] Link runtime parity docs.
- [x] Link metrics query docs once metrics backend lands.

Acceptance criteria:
- Deployment docs and harness docs do not diverge.

### Task 10.3: Add CI Checks

**Files:**
- Modify: `.github/workflows/chart-tests.yml`

- [x] Ensure harness docs changes trigger relevant config checks.
- [x] Ensure parity check script runs when Compose, Helm, Dockerfile, nginx, or harness scripts change.
- [x] Avoid running full k3s smoke for docs-only harness changes unless workflow dispatch requests it.

Acceptance criteria:
- Drift prevention runs automatically where practical.

---

## Phase 11: Verification Matrix

Run these validations after implementation.

### Documentation and Skill

```bash
test -f AGENTS.md
test -f docs/harness/index.md
test -f tools/codex/skills/tertius-harness/SKILL.md
bash scripts/install-tertius-harness-skill.sh
```

Expected:
- All files exist.
- Skill installs cleanly.

### Runtime Parity

```bash
bash scripts/check-runtime-parity.sh
```

Expected:
- Script exits zero.
- Any intentional difference appears in `docs/harness/runtime-parity.md`.

### Compose Dev

```bash
bash scripts/harness-compose.sh dev-up
bash scripts/harness-compose.sh smoke
bash scripts/harness-compose.sh down
```

Expected:
- UI loads.
- API health works.
- `/api` proxy behavior is correct where supported.
- The wrapper reports actionable guidance if requested ports are already in use.

### Compose Parity

```bash
bash scripts/harness-compose.sh parity-up
bash scripts/harness-compose.sh smoke
bash scripts/harness-compose.sh down
```

Expected:
- Static UI image serves successfully.
- Same-origin `/api` routing works.
- The wrapper reports actionable guidance if requested ports are already in use.

### k3s Harness

```bash
bash scripts/harness-k3s.sh up
bash scripts/harness-k3s.sh status
bash scripts/harness-k3s.sh smoke
```

Expected:
- Helm install succeeds.
- UI/API port-forwards work.
- Shared smoke checks pass.
- Existing in-cluster checks pass.
- Compile smoke passes when KEDA is enabled.

### Metrics

```bash
bash scripts/harness-query-metrics.sh --file docs/harness/queries/api.promql
bash scripts/harness-query-metrics.sh --file docs/harness/queries/collector.promql
```

Expected:
- Query endpoint responds.
- Collector metrics are visible.
- Application metrics are visible after smoke traffic.

### Browser MCP

Manual/agent validation:
- Start k3s harness.
- Launch Chrome with local-only debugging profile.
- Attach Chrome DevTools MCP.
- Run anonymous load journey.
- Inspect console errors.
- Inspect failed network requests.
- Capture screenshot evidence.
- Query API/collector metrics after journey.

Expected:
- UI renders.
- No unexpected console/network failures.
- Metrics show request activity.

---

## Implementation Order

Use this order to keep risk low:

1. Add docs and `AGENTS.md`.
2. Add Tertius harness skill source and install script.
3. Add shared smoke script.
4. Add k3s wrapper around existing smoke script.
5. Add Compose parity file and wrapper.
6. Add runtime parity docs and check script.
7. Wire parity checks into config tests.
8. Add local metrics backend and query script.
9. Add PromQL query references.
10. Add Chrome DevTools MCP docs and browser journey definitions.
11. Update README/deployment docs.
12. Run full verification matrix.

## Rollback Plan

- Docs and skill changes can be reverted independently.
- `scripts/harness-k3s.sh` can be removed without affecting CI because `scripts/test-k3s-deployment.sh` remains canonical.
- `docker-compose.parity.yml` can be removed without affecting Compose dev.
- Metrics backend changes should be behind opt-in local values or Compose service dependencies that do not block app startup.
- CI parity checks can be temporarily disabled if they block urgent deployment work, but the failure should be tracked in this plan or a follow-up issue.

## Completion Criteria

- `AGENTS.md` exists and points to harness docs.
- The Tertius harness skill is source-controlled and installable.
- k3s is the documented canonical full-stack validation runtime.
- Compose dev remains fast and documented.
- Compose parity exists for production-shaped container validation.
- Shared smoke checks run against both k3s and Compose.
- Runtime parity checks catch config drift.
- Local metrics are queryable through a stable script.
- Browser validation with Chrome DevTools MCP is documented and tied to canonical journeys.
- README and deployment docs point to the harness.
