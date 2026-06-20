# OpenTelemetry Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenTelemetry-based traces, metrics, and correlated logs across the Tertius API, workflow apps, compile jobs, browser UI, NATS JetStream pipeline, database access, outbound HTTP/LLM calls, and Kubernetes runtime.

**Architecture:** Instrument services with OpenTelemetry SDKs and exporters. Export OTLP to an OpenTelemetry Collector. Keep application logs on stdout as structured JSON with trace correlation, and let the collector or platform log pipeline ship them. Use manual NATS context propagation because the codebase has a single NATS wrapper and no first-party auto-instrumentation is currently wired in.

**Tech Stack:** Python, FastAPI, SQLAlchemy, httpx, nats-py, React/Vite, nginx, Docker Compose, Helm, Kubernetes, NATS JetStream, CloudNativePG/Postgres, Keycloak, KEDA.

**Primary references:**
- Python OpenTelemetry: https://opentelemetry.io/docs/languages/python/getting-started/
- FastAPI instrumentation: https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html
- Browser JavaScript OpenTelemetry: https://opentelemetry.io/docs/languages/js/getting-started/browser/
- Collector Helm chart: https://opentelemetry.io/docs/platforms/kubernetes/helm/collector/
- Messaging semantic conventions: https://opentelemetry.io/docs/specs/semconv/messaging/

---

## Scope

In scope:
- API and mounted workflow app request tracing.
- SQLAlchemy/Postgres tracing and timing.
- httpx tracing for Keycloak and LLM provider calls.
- NATS JetStream publish/consume tracing with W3C context propagation.
- Compile job and compile result consumer spans and metrics.
- LLM request, token, cost, and failure metrics.
- Structured JSON logs with trace/span correlation.
- Browser page load, fetch, workflow interaction, and frontend error telemetry.
- Docker Compose collector for local validation.
- Helm values/templates for OTEL env, collector endpoint, and production deployment.
- Dashboards and alert definitions or documented dashboard queries.

Out of scope for the first implementation:
- Replacing the current log storage backend.
- Full APM vendor selection.
- Recording request/response bodies, prompts, generated model output, source files, auth tokens, or secrets.
- High-cardinality metrics labels such as raw user IDs, tenant IDs, project IDs, job IDs, filenames, prompts, or exception messages.

## Implementation Files

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `server/core/config.py`
- Create: `server/core/telemetry.py`
- Modify: `server/core/db.py`
- Modify: `server/core/nats_client.py`
- Modify: `server/main.py`
- Modify: `server/workflows/intus/compile_job.py`
- Modify: `server/workflows/intus/compile_result_consumer.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/core/llm_client.py`
- Modify: `server/start-api.sh`
- Modify: `server/start-compile-job.sh`
- Modify: `Dockerfile.ui`
- Modify: `docker-compose.yml`
- Create: `infra/otel/otel-collector-local.yaml`
- Modify: `ui/package.json`
- Modify: `ui/package-lock.json`
- Create: `ui/src/telemetry.ts`
- Modify: `ui/src/main.tsx`
- Modify: `ui/src/api/client.ts`
- Modify: `infra/deploy/nginx/default.conf.template`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `infra/charts/tertius/templates/api.yaml`
- Modify: `infra/charts/tertius/templates/compile-worker.yaml`
- Modify: `infra/charts/tertius/templates/ui.yaml`
- Create: `docs/observability/dashboards.md`
- Create: `docs/observability/alerts.md`
- Create or modify tests under `server/tests/` and `ui/src/**.test.tsx`

## Observability Model

### Service Names

Use stable service names:
- `tertius-api`
- `tertius-compile-job`
- `tertius-ui`
- `tertius-otel-collector`

Every runtime must set its own `OTEL_SERVICE_NAME` and must call the telemetry bootstrap before it does meaningful work. In this codebase that means both `server/main.py` and `server/workflows/intus/compile_job.py`; setting the environment variable alone is not sufficient for the compile job process.

Set resource attributes:
- `service.name`
- `service.version`
- `deployment.environment`
- `k8s.namespace.name`
- `k8s.pod.name`
- `container.name`

For local Compose, set at least `service.name`, `service.version`, and `deployment.environment=local`.

### Trace Boundaries

Trace these boundaries:
- Browser page load to API fetch.
- API inbound HTTP request.
- API DB queries.
- API outbound Keycloak and LLM HTTP calls.
- API NATS publish.
- Compile job NATS consume.
- Compile sandbox execution.
- Compile result NATS publish.
- API result consumer NATS consume.
- Result persistence and artifact writes.
- Billing/usage publish and handling.

### Metrics Boundaries

Use low-cardinality labels only.

Recommended labels:
- `service`
- `workflow`
- `route`
- `method`
- `status_code`
- `job_status`
- `export_format`
- `provider`
- `model_id`
- `nats_subject`
- `nats_stream`

Avoid labels:
- raw `user_id`
- raw `tenant_id`
- raw `project_id`
- raw `job_id`
- file names
- prompts
- exception strings
- URLs with query strings

### Logs

Emit JSON logs on stdout/stderr with:
- `timestamp`
- `level`
- `logger`
- `message`
- `service.name`
- `deployment.environment`
- `trace_id`
- `span_id`
- safe contextual fields where already available, such as `workflow`, `job_status`, or `export_format`

Do not log secrets, OAuth tokens, LLM prompts, generated model output, uploaded source contents, artifact contents, or full request bodies.

## Anti-Patterns

| Do not | Do instead | Why |
|---|---|---|
| Do not send browser telemetry directly to an internal collector service. | Proxy OTLP HTTP through same-origin nginx or an explicit public collector endpoint. | Browser clients cannot reach cluster-only services and CORS must be controlled. |
| Do not put user/project/job IDs on metrics labels. | Put IDs on spans only when operationally necessary, and prefer sampled traces. | Metrics cardinality can break the backend. |
| Do not record prompts, source code, generated files, or auth tokens in spans/logs. | Record bounded metadata such as model id, token counts, status, duration, and sizes. | Telemetry must not become a data exfiltration path. |
| Do not rely only on auto-instrumentation for NATS. | Manually inject/extract trace context in `server/core/nats_client.py` and consumers. | Message propagation is central to the compile pipeline. |
| Do not make telemetry required for app startup. | Fail open by default: app should run if collector is down. | Observability outages must not cause product outages. |
| Do not enable full SQL statement capture with parameters. | Capture timings and DB metadata without parameters. | SQL parameters may contain sensitive data. |
| Do not add a new vendor SDK directly into business logic. | Keep OpenTelemetry as the app boundary and configure exporters externally. | Vendor portability and testability stay intact. |
| Do not assume Kubernetes pod env vars change an already-built Vite bundle. | Bake `VITE_OTEL_*` values at UI image build time or add an explicit runtime config file. | The UI runs as static files under nginx, so browser config must exist in the shipped assets. |

---

## Task 1: Add Backend OpenTelemetry Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] Add Python dependencies:
  - `opentelemetry-api`
  - `opentelemetry-sdk`
  - `opentelemetry-exporter-otlp`
  - `opentelemetry-instrumentation-fastapi`
  - `opentelemetry-instrumentation-httpx`
  - `opentelemetry-instrumentation-sqlalchemy`
  - `opentelemetry-instrumentation-logging`
  - `opentelemetry-instrumentation-asyncio`
- [ ] Run dependency lock update:

```bash
UV_CACHE_DIR=.uv-cache uv lock
```

- [ ] Run the existing backend test suite baseline:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest server/tests -q
```

Expected: tests should still pass before telemetry is wired in.

## Task 2: Add Telemetry Configuration

**Files:**
- Modify: `server/core/config.py`
- Modify: `server/tests/test_config.py`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `docker-compose.yml`

- [ ] Add settings to `Settings`:
  - `otel_enabled: bool = True`
  - `otel_service_name: str = "tertius-api"`
  - `otel_exporter_otlp_endpoint: str = ""`
  - `otel_exporter_otlp_protocol: str = "grpc"`
  - `otel_traces_sampler: str = "parentbased_traceidratio"`
  - `otel_traces_sampler_arg: str = "1.0"`
  - `otel_resource_attributes: str = ""`
  - `otel_log_json: bool = True`
- [ ] Add tests verifying defaults and env overrides.
- [ ] Add corresponding Helm values under `app.observability`.
- [ ] Render shared OTEL env vars in `configmap.yaml`, but set `OTEL_SERVICE_NAME` per workload so API pods use `tertius-api` and compile jobs use `tertius-compile-job`.
- [ ] Add local Compose env vars pointing to the local collector endpoint once the collector is added, again with distinct `OTEL_SERVICE_NAME` values for `backend` and `compile-job-runner`.
- [ ] Default `OTEL_EXPORTER_OTLP_ENDPOINT` to the in-chart collector when observability and the chart collector are enabled; set `app.observability.otlpEndpoint` for an external collector.

## Task 3: Create Backend Telemetry Bootstrap

**Files:**
- Create: `server/core/telemetry.py`
- Modify: `server/tests/` with focused unit tests if practical

- [ ] Implement `configure_telemetry(settings, service_name_override: str | None = None)`.
- [ ] Configure a `Resource` with service name, version, environment, and extra attributes.
- [ ] Configure tracing:
  - `TracerProvider`
  - OTLP exporter when endpoint is set
  - batch span processor
  - sampler from env
- [ ] Configure metrics:
  - `MeterProvider`
  - periodic OTLP metric reader when endpoint is set
- [ ] Configure log correlation:
  - OpenTelemetry logging instrumentation
  - JSON formatter with trace/span IDs
- [ ] Make the bootstrap idempotent so tests and reloads do not double-instrument.
- [ ] Make telemetry fail open. Log a warning if setup fails, but do not crash the app.
- [ ] Return a small status object or boolean from the bootstrap so tests can assert disabled/enabled/fail-open behavior without depending on private OpenTelemetry globals.
- [ ] Treat missing OTLP endpoint as local no-export mode, not as a startup failure.
- [ ] Add helper functions:
  - `get_tracer(name: str)`
  - `get_meter(name: str)`
  - `record_exception_attributes(...)` only if needed

## Task 4: Instrument FastAPI, SQLAlchemy, httpx, and asyncio

**Files:**
- Modify: `server/main.py`
- Modify: `server/core/db.py`
- Modify: `server/core/telemetry.py`
- Modify: `server/workflows/intus/compile_job.py`

- [ ] Call `configure_telemetry(settings, "tertius-api")` in `server/main.py` before serving the main FastAPI app and before any outbound HTTP calls can occur.
- [ ] Call `configure_telemetry(settings, "tertius-compile-job")` in `server/workflows/intus/compile_job.py` before connecting to NATS, fetching a message, or running the sandbox.
- [ ] Instrument the main `FastAPI` app after it is created.
- [ ] Confirm mounted apps under `/api/intus`, `/api/artus`, `/api/extus`, and `/api/timus` are represented with useful route names.
- [ ] Instrument SQLAlchemy after `engine` is created in `server/core/db.py`; account for the current import-time engine creation by making instrumentation explicit and idempotent.
- [ ] Instrument httpx globally.
- [ ] Instrument asyncio if it provides useful task spans without excessive noise.
- [ ] If asyncio instrumentation creates noisy internal task spans in the result consumer loop, leave it disabled and document the decision in `server/core/telemetry.py`.
- [ ] Add a request hook to attach safe request context:
  - route
  - method
  - status code
  - workflow inferred from route prefix
- [ ] Do not attach cookies, Authorization headers, request body, response body, or query string values.

## Task 5: Add NATS Trace Propagation

**Files:**
- Modify: `server/core/nats_client.py`
- Modify: `server/workflows/intus/compile_job.py`
- Modify: `server/workflows/intus/compile_result_consumer.py`
- Modify: `server/tests/test_nats_client.py`
- Modify: `server/tests/test_compile_job.py`
- Modify: `server/tests/test_compile_result_consumer.py`

- [ ] In `NatsPublisher.publish_json()`, start a producer span around `jetstream.publish()`.
- [ ] Inject W3C trace context into NATS headers.
- [ ] Preserve existing `Nats-Msg-Id` behavior.
- [ ] Add an optional headers parameter or internal merge helper so injected `traceparent`/`tracestate` headers cannot overwrite `Nats-Msg-Id`, and future callers can add safe headers without bypassing propagation.
- [ ] Add messaging attributes:
  - `messaging.system = "nats"`
  - `messaging.destination.name`
  - `messaging.operation.name = "publish"`
  - stream name where available
  - message id where available
- [ ] Add a helper for extracting context from NATS message headers.
- [ ] Wrap `handle_compile_request_message()` in a consumer span using extracted context.
- [ ] Wrap `handle_compile_result_message()` in a consumer span using extracted context.
- [ ] Wrap stale queued job republish work in `republish_stale_queued_jobs()` so recovery publishes are visible and parented to the result consumer's recovery span.
- [ ] Record message ack/nack/term outcomes on spans.
- [ ] Add tests verifying trace headers are injected on publish.
- [ ] Add tests verifying consumer span parent context is extracted from headers.

## Task 6: Add Compile Pipeline Metrics

**Files:**
- Modify: `server/workflows/intus/compile_job.py`
- Modify: `server/workflows/intus/compile_result_consumer.py`
- Modify: `server/core/nats_client.py`

- [ ] Add counters:
  - `tertius.nats.publish.count`
  - `tertius.nats.publish.error.count`
  - `tertius.compile.job.started.count`
  - `tertius.compile.job.finished.count`
  - `tertius.compile.job.failed.count`
  - `tertius.compile.result.processed.count`
  - `tertius.compile.result.error.count`
- [ ] Add histograms:
  - `tertius.nats.publish.duration`
  - `tertius.compile.job.duration`
  - `tertius.compile.queue.latency`
  - `tertius.compile.result.processing.duration`
- [ ] Labels should stay bounded:
  - `job_status`
  - `export_format`
  - `nats_subject`
  - `nats_stream`
- [ ] Record compile timeout and stale recovery paths separately.
- [ ] Record publish metrics for both compile request/result subjects and billing usage subjects, using bounded subject/stream names only.

## Task 7: Add LLM and Billing Telemetry

**Files:**
- Modify: `server/core/llm_client.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/core/llm_usage.py`
- Modify: `server/core/billing.py` if useful

- [ ] Add spans around LLM provider requests.
- [ ] Record safe attributes:
  - provider API type
  - configured model id
  - status category
  - input token count
  - output token count
  - cached token counts where available
  - duration
- [ ] Do not record prompt text, generated response text, source file contents, endpoint secrets, or full provider errors.
- [ ] Add metrics:
  - `tertius.llm.request.count`
  - `tertius.llm.request.error.count`
  - `tertius.llm.request.duration`
  - `tertius.llm.tokens.input`
  - `tertius.llm.tokens.output`
  - `tertius.llm.cost.usd`
  - `tertius.billing.publish.error.count`
- [ ] Add logs with trace correlation for provider auth failures, rate limits, and billing publish failures.

## Task 8: Add Structured JSON Logging

**Files:**
- Modify: `server/core/telemetry.py`
- Modify: existing logger calls only where context improves operational value

- [ ] Implement JSON log formatting when `OTEL_LOG_JSON=true`.
- [ ] Include `trace_id` and `span_id` fields when a span is active.
- [ ] Include service and environment fields.
- [ ] Keep log levels configurable through normal Python/uvicorn mechanisms.
- [ ] Ensure uvicorn access/error logs remain readable and correlated where possible.
- [ ] Audit existing `logger.exception` and `logger.warning` calls for sensitive data.

## Task 9: Add Runtime Entrypoint Support

**Files:**
- Modify: `server/start-api.sh`
- Modify: `server/start-compile-job.sh`
- Modify: `Dockerfile`
- Modify: `Dockerfile.api`

- [ ] Ensure API and compile job entrypoints set `PYTHONPATH` as today.
- [ ] Prefer code-based instrumentation over `opentelemetry-instrument` CLI for predictable startup and tests.
- [ ] Set `OTEL_SERVICE_NAME=tertius-api` for API deployment and local Compose `backend`.
- [ ] Set `OTEL_SERVICE_NAME=tertius-compile-job` for KEDA compile jobs and local Compose `compile-job-runner`.
- [ ] Set `OTEL_RESOURCE_ATTRIBUTES` with `deployment.environment` in both entrypoint paths, and add Kubernetes pod/container attributes through environment or collector enrichment.
- [ ] Confirm migrations do not emit noisy or misleading spans unless intentionally configured.
- [ ] Confirm `server/start-compile-job.sh` still exits after one `run_once()` in Kubernetes; local Compose may continue to wrap it in the existing loop.

## Task 10: Add Local OpenTelemetry Collector

**Files:**
- Modify: `docker-compose.yml`
- Create: `infra/otel/otel-collector-local.yaml`

- [ ] Add an `otel-collector` service using the official collector image.
- [ ] Expose:
  - OTLP gRPC `4317`
  - OTLP HTTP `4318`
  - collector metrics `8888`
- [ ] Configure receivers:
  - `otlp`
- [ ] Configure processors:
  - `memory_limiter`
  - `batch`
- [ ] Configure exporters for local development:
  - `debug` exporter initially
  - optional Prometheus exporter for metrics
- [ ] Wire backend and compile-job-runner env:
  - `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`
  - `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`
  - `OTEL_SERVICE_NAME=tertius-api` for `backend`
  - `OTEL_SERVICE_NAME=tertius-compile-job` for `compile-job-runner`
  - `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=local`
- [ ] Configure the local collector to accept browser OTLP HTTP with controlled CORS if using Vite dev directly against `localhost:4318`; otherwise route browser OTLP through nginx as in Kubernetes.
- [ ] Document how to run and inspect collector output.

## Task 11: Add Helm Observability Configuration

**Files:**
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `infra/charts/tertius/templates/api.yaml`
- Modify: `infra/charts/tertius/templates/compile-worker.yaml`
- Modify: `infra/charts/tertius/templates/ui.yaml`
- Optionally create: `infra/charts/tertius/templates/otel-collector.yaml`

- [ ] Add values:

```yaml
app:
  observability:
    enabled: true
    otlpEndpoint: http://tertius-otel-collector:4317
    otlpProtocol: grpc
    tracesSampler: parentbased_traceidratio
    tracesSamplerArg: "1.0"
    logJson: true
```

- [ ] Inject OTEL env vars into API pods.
- [ ] Inject OTEL env vars into compile jobs.
- [ ] Inject or template UI nginx upstream env vars for the browser OTLP proxy:
  - `OTEL_COLLECTOR_HTTP_HOST`
  - `OTEL_COLLECTOR_HTTP_PORT`
- [ ] Extend `NGINX_ENVSUBST_FILTER` in `ui.yaml` so the collector host/port placeholders are rendered into `default.conf`.
- [ ] Add pod annotations for logs and metrics if the target platform expects them.
- [ ] If bundling a collector in this chart, make it optional.
- [ ] Use the in-chart OpenTelemetry Collector by default; set `app.observability.collector.enabled=false` when cluster-level observability is shared across apps.

## Task 12: Add Kubernetes Platform Metrics

**Files:**
- Modify or document under `infra/`

- [ ] Scrape NATS monitoring endpoint `:8222`.
- [ ] Enable or scrape CloudNativePG/Postgres metrics.
- [ ] Enable or scrape Keycloak metrics if available in the deployed mode.
- [ ] Scrape KEDA metrics.
- [ ] Collect Kubernetes pod/container/node metrics through collector receivers or existing platform agents.
- [ ] Add collector `k8sattributes` processor in production collector config.

## Task 13: Add Browser Telemetry

**Files:**
- Modify: `ui/package.json`
- Modify: `ui/package-lock.json`
- Create: `ui/src/telemetry.ts`
- Modify: `ui/src/main.tsx`
- Modify: `ui/src/api/client.ts`
- Modify: `Dockerfile.ui`
- Modify: `infra/deploy/nginx/default.conf.template`

- [ ] Add browser OpenTelemetry packages:
  - `@opentelemetry/api`
  - `@opentelemetry/sdk-trace-web`
  - `@opentelemetry/instrumentation`
  - `@opentelemetry/instrumentation-fetch`
  - `@opentelemetry/instrumentation-document-load`
  - `@opentelemetry/context-zone`
  - OTLP HTTP trace exporter package
- [ ] Create `ui/src/telemetry.ts`.
- [ ] Initialize telemetry before React renders in `main.tsx`.
- [ ] Instrument document load.
- [ ] Instrument `fetch`.
- [ ] Propagate trace headers only to same-origin API calls.
- [ ] Add manual spans for major UI interactions:
  - compile submit
  - LLM build script generation
  - LLM file edit submit
  - artifact download
  - 3D viewer load
- [ ] Capture frontend errors as span events or error telemetry without leaking user content.
- [ ] Add nginx route for browser OTLP HTTP, for example `/otel/v1/traces`, proxied to the collector.
- [ ] Make the nginx OTLP proxy route strip or preserve the path intentionally so the collector receives `/v1/traces`.
- [ ] Add collector upstream placeholders to `infra/deploy/nginx/default.conf.template`:
  - `${OTEL_COLLECTOR_HTTP_HOST}`
  - `${OTEL_COLLECTOR_HTTP_PORT}`
- [ ] Add Vite env vars:
  - `VITE_OTEL_ENABLED`
  - `VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
  - `VITE_OTEL_SERVICE_NAME`
- [ ] Add matching `ARG`/`ENV` build-time support in `Dockerfile.ui` for the `VITE_OTEL_*` values.
- [ ] For production UI builds, set the browser trace endpoint to same-origin `/otel/v1/traces` at build time, or add a runtime-served config file before relying on Helm values to control browser telemetry.

## Task 14: Dashboards

**Files:**
- Create: `docs/observability/dashboards.md`
- Optionally create dashboard JSON under `infra/dashboards/`

- [ ] API dashboard:
  - request rate
  - error rate
  - p50/p95/p99 latency
  - top slow routes
  - DB duration
- [ ] Compile pipeline dashboard:
  - queued/running/succeeded/failed jobs
  - compile duration
  - queue latency
  - NATS publish failures
  - result consumer errors
  - KEDA job scaling
- [ ] LLM dashboard:
  - request count
  - provider latency
  - provider errors
  - rate limits
  - token usage
  - cost
- [ ] Infrastructure dashboard:
  - API pod CPU/memory/restarts
  - compile job CPU/memory/ephemeral storage
  - Postgres connections and latency
  - NATS connections, messages, JetStream lag
  - collector dropped spans/metrics/logs

## Task 15: Alerts

**Files:**
- Create: `docs/observability/alerts.md`
- Optionally create alert rules under `infra/alerts/`

- [ ] API 5xx rate above threshold.
- [ ] API p95 latency above threshold.
- [ ] Collector export failures or dropped data.
- [ ] NATS consumer lag above threshold.
- [ ] Compile failure spike.
- [ ] Compile job timeout spike.
- [ ] Compile result consumer errors.
- [ ] LLM provider auth failures.
- [ ] LLM provider rate-limit spike.
- [ ] Postgres connection saturation.
- [ ] Pod crash loops or restart spike.

## Task 16: Tests

**Backend tests:**
- Modify: `server/tests/test_config.py`
- Modify: `server/tests/test_nats_client.py`
- Modify: `server/tests/test_compile_job.py`
- Modify: `server/tests/test_compile_result_consumer.py`
- Add: `server/tests/test_telemetry.py`

- [ ] Test telemetry config defaults.
- [ ] Test telemetry can be disabled with env.
- [ ] Test telemetry setup does not crash when exporter endpoint is absent.
- [ ] Test NATS publish injects trace headers.
- [ ] Test NATS consumer extracts trace headers.
- [ ] Test compile worker records success and failure metrics without high-cardinality labels.
- [ ] Test JSON logging includes trace/span IDs when a span is active.

**Frontend tests:**
- Add or modify relevant `ui/src/**/*.test.tsx`

- [ ] Test telemetry is disabled by default in test environment.
- [ ] Test fetch instrumentation does not attach trace headers to cross-origin URLs.
- [ ] Test API client still sends same-origin requests with credentials.

**Deployment/render tests:**
- Modify existing Helm/render validation scripts where practical.

- [ ] Test API and compile worker manifests render distinct `OTEL_SERVICE_NAME` values.
- [ ] Test UI manifest renders collector host/port env vars and includes them in `NGINX_ENVSUBST_FILTER`.
- [ ] Test nginx config contains a browser OTLP route that forwards to the collector HTTP port.

## Task 17: Verification

- [ ] Run backend tests:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest server/tests -q
```

- [ ] Run backend type checking:

```bash
UV_CACHE_DIR=.uv-cache uv run mypy
```

- [ ] Run frontend tests:

```bash
cd ui && npm test
```

- [ ] Run frontend typecheck and build:

```bash
cd ui && npm run typecheck && npm run build
```

- [ ] Run local Compose:

```bash
docker compose up --build
```

- [ ] Hit the API root and verify a server span reaches the local collector.
- [ ] Run one local compile job and verify `tertius-compile-job` emits at least one span or metric to the local collector.
- [ ] Submit a compile job and verify trace continuity:
  - browser span
  - API HTTP span
  - DB spans
  - NATS publish span
  - compile job consume span
  - compile execution span
  - result publish span
  - result consumer span
  - DB update span
- [ ] Trigger a known LLM path in a non-production environment and verify provider span plus token/cost metrics.
- [ ] Load the browser UI and verify document-load/fetch spans export through the configured `/otel/v1/traces` path or through the documented local CORS endpoint.
- [ ] Confirm app still works when the collector is stopped.
- [ ] Confirm no prompts, source files, generated content, cookies, auth headers, or tokens appear in telemetry output.

---

## Build Order

1. Backend dependencies and config.
2. Backend telemetry bootstrap with disabled/fail-open behavior.
3. FastAPI, SQLAlchemy, httpx, logging instrumentation.
4. NATS propagation and compile pipeline spans.
5. Compile, LLM, billing, and NATS metrics.
6. Local collector in Docker Compose.
7. Helm env and production collector configuration.
8. Browser telemetry and nginx OTLP HTTP proxy.
9. Dashboards, alerts, and runbook docs.
10. End-to-end validation through a real compile job.

## Definition of Done

- API traces include inbound HTTP, DB, outbound HTTP, NATS publish, and background consumer spans.
- Compile traces continue across NATS from API to compile job and back to result consumer.
- API and compile job telemetry use distinct service names in traces and metrics.
- Metrics exist for API health, compile pipeline health, LLM usage/cost, NATS publishing, and result processing.
- Logs are JSON and include trace/span correlation.
- Browser fetch traces connect to backend traces for same-origin API requests.
- Browser telemetry exports through a same-origin nginx path in Kubernetes, with collector host/port rendered from chart values.
- Docker Compose can run with a local collector.
- Helm deployment can send OTLP to a configured collector.
- Telemetry fails open if the collector is unavailable.
- Tests cover config, disabled behavior, NATS propagation, and log correlation.
- Sensitive data is not present in logs, span attributes, or metric labels.
