# VictoriaMetrics and VictoriaTraces Backend Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store Tertius OpenTelemetry metrics in VictoriaMetrics and traces in VictoriaTraces while keeping OpenTelemetry as the application boundary. Application code should continue exporting OTLP only to the OpenTelemetry Collector; Victoria-specific protocols, retention, storage, and query surfaces belong in runtime configuration and deployment manifests.

**Current state:**
- API, compile jobs, and browser UI already export OpenTelemetry telemetry to the collector.
- Compose runs VictoriaMetrics and the local collector exports metrics to it with `prometheusremotewrite`.
- Helm has an optional single-node VictoriaMetrics template behind `app.observability.metricsBackend.enabled`.
- Local k3s enables the in-chart metrics backend and validates PromQL queries through `scripts/harness-query-metrics.sh`.
- Trace export currently defaults to collector `debug`; VictoriaTraces is documented as an external exporter target, but it is not yet deployed or validated as part of the stack.

**Primary references:**
- VictoriaMetrics OpenTelemetry metrics ingestion: https://docs.victoriametrics.com/victoriametrics/integrations/opentelemetry/
- VictoriaTraces OpenTelemetry ingestion: https://docs.victoriametrics.com/victoriatraces/data-ingestion/opentelemetry/
- VictoriaTraces operation, retention, and monitoring: https://docs.victoriametrics.com/victoriatraces/
- OpenTelemetry Collector configuration: https://opentelemetry.io/docs/collector/configuration/

---

## Architecture

Telemetry flow:

```text
Tertius API / compile job / browser UI
  -> OTLP gRPC or OTLP HTTP
  -> OpenTelemetry Collector
  -> VictoriaMetrics for metrics
  -> VictoriaTraces for traces
```

Keep three boundaries explicit:

- Applications know only OpenTelemetry settings: endpoint, protocol, sampler, resource attributes, and JSON log correlation.
- The collector owns signal routing, batching, retry behavior, and backend exporter config.
- Backend templates own storage, retention, ports, services, PVCs, probes, and runtime hardening.

Preferred deployment shape:

- Local Compose: single-node VictoriaMetrics plus single-node VictoriaTraces, both exposed on localhost for manual validation.
- Local k3s smoke release: in-chart single-node VictoriaMetrics and VictoriaTraces with PVC-backed storage.
- Production-style Helm: support either in-chart single-node backends for small installs or external/shared Victoria backends by setting collector exporter endpoints and disabling bundled backends.

## Task 1: Decide Metrics Ingestion Contract

**Files:**
- Modify: `docs/observability/collector.md`
- Modify: `infra/otel/otel-collector-local.yaml`
- Modify: `infra/charts/tertius/templates/otel-collector.yaml`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`

- [x] Keep the existing `prometheusremotewrite` path as the near-term metrics contract unless we intentionally migrate query naming.
- [x] Document why: existing harness queries already use Prometheus-compatible metric names such as `tertius_api_request_count`.
- [x] Add a documented optional OTLP/HTTP metrics exporter profile for VictoriaMetrics at `/opentelemetry/v1/metrics`.
- [x] If enabling native OTLP metrics ingestion, add VictoriaMetrics flags for Prometheus-compatible naming, resource attribute promotion allow-listing, and cumulative temporality expectations.
- [x] Ensure collector metrics exporter lists are explicit per environment instead of relying on hidden defaults.
- [ ] Keep `debug` export enabled only in local or troubleshooting values.

## Task 2: Add VictoriaTraces Runtime Backend

**Files:**
- Create: `infra/charts/tertius/templates/victoriatraces.yaml`
- Modify: `infra/charts/tertius/templates/_helpers.tpl`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.parity.yml` if parity should include traces
- Modify: `infra/deploy/README.md`
- Modify: `infra/charts/tertius/README.md`

- [x] Add `app.observability.tracesBackend.enabled` with image, port `10428`, retention, storage, resources, and optional storage class settings.
- [x] Template a single-node VictoriaTraces Deployment, Service, and optional PVC.
- [x] Start VictoriaTraces with `-storageDataPath=/storage` and a configurable `-retentionPeriod`.
- [x] Expose its internal Prometheus metrics endpoint on the same HTTP service for backend health monitoring.
- [x] Wire local Compose with a `victoriatraces` service and persistent volume.
- [x] Keep the backend cluster-internal by default; expose it only through documented local port-forwarding or Grafana/Jaeger query UI integration.
- [x] Add network policy allowances from the collector to VictoriaTraces when network policies are enabled.

## Task 3: Route Collector Traces to VictoriaTraces

**Files:**
- Modify: `infra/otel/otel-collector-local.yaml`
- Modify: `infra/charts/tertius/templates/otel-collector.yaml`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `docs/observability/collector.md`

- [x] Add a collector `otlphttp/victoriatraces` exporter using `traces_endpoint: http://<victoriatraces-service>:10428/insert/opentelemetry/v1/traces`.
- [x] Add `otlphttp/victoriatraces` to local k3s `tracesExporters` when the bundled traces backend is enabled.
- [x] Add the same exporter to Compose local collector config.
- [x] Preserve `debug` trace export in local only if log volume remains acceptable.
- [x] Configure retry and sending queue settings for Victoria exporters if collector defaults are not sufficient under compile bursts.
- [x] Confirm browser OTLP HTTP spans still flow through the same-origin UI proxy or explicit local collector endpoint, not directly to cluster-only backend services.
- [x] Add collector rollout on exporter config changes so ConfigMap updates are applied.
- [x] Allow compile-job NetworkPolicy egress to the in-release collector OTLP gRPC port when observability is enabled.

## Task 4: Add Query and Validation Tooling

**Files:**
- Modify: `scripts/harness-query-metrics.sh`
- Create: `scripts/harness-query-traces.sh`
- Modify: `docs/harness/observability-validation.md`
- Create: `docs/harness/queries/traces.md` or similar trace validation reference
- Modify: `docs/harness/quality-gates.md`

- [x] Keep PromQL metric validation against VictoriaMetrics at `/api/v1/query`.
- [x] Add a trace validation script that can query VictoriaTraces by service name and recent time window using its documented query API or Jaeger-compatible API.
- [x] Validate that one authenticated shell live flow produces spans for `tertius-api` and `tertius-compile-job`; `tertius-ui` spans require browser execution because the shell smoke uses curl through the UI origin.
- [x] Validate cross-service propagation by checking at least one trace contains API and compile job spans connected through NATS context propagation.
- [x] Add collector self-metric checks for refused, dropped, and export-failed telemetry.
- [x] Update `scripts/harness-compose.sh live-flow` and `scripts/harness-k3s.sh live-flow` docs with trace validation steps.

## Task 5: Dashboards and Alerts

**Files:**
- Modify: `docs/observability/dashboards.md`
- Modify: `docs/observability/alerts.md`
- Create or document dashboard JSON under `docs/observability/` if the team wants committed Grafana assets

- [ ] Promote existing harness PromQL queries into dashboard panel definitions for API, compile, LLM, collector, and backend health.
- [x] Add trace drilldowns by service, route, compile operation, LLM provider call, and NATS publish/consume boundary.
- [x] Add VictoriaMetrics backend health panels: ingestion rate, active series, slow queries, disk usage, and retention pressure.
- [x] Add VictoriaTraces backend health panels: ingestion rate, dropped rows, query latency, disk usage, and retention pressure.
- [x] Add alerts for collector export failures to VictoriaMetrics and VictoriaTraces.
- [x] Add alerts for VictoriaTraces dropped rows, especially timestamp-out-of-retention drops.

## Task 6: Runtime Parity and Release Safety

**Files:**
- Modify: `scripts/check-runtime-parity.sh`
- Modify: `docs/harness/runtime-parity.md`
- Modify: `docs/harness/local-harness.md`
- Modify: `infra/charts/tertius/README.md`

- [x] Add static parity checks for `tracesBackend` values, collector trace exporter names, and VictoriaTraces service endpoints.
- [x] Render Helm local values and Compose configs during parity checks to confirm metrics and trace contracts are present.
- [x] Document intentional differences between Compose dev, Compose parity, local k3s, and production-style Helm.
- [x] Make backend enablement fail-open for application startup; collector or Victoria outages must not block API, compile jobs, or UI startup.
- [x] Keep storage defaults small in local values and require explicit sizing guidance for production.

## Task 7: Security and Cardinality Review

**Files:**
- Modify: `docs/observability/collector.md`
- Modify: `docs/observability/dashboards.md`
- Modify: `docs/harness/observability-validation.md`

- [x] Reconfirm that metrics labels exclude raw user IDs, tenant IDs, project IDs, job IDs, filenames, prompts, generated source, auth tokens, and exception strings.
- [x] Review trace attributes for sensitive data before increasing sampling beyond local development.
- [ ] Add collector attribute filtering if any auto-instrumentation emits unsafe or high-cardinality attributes.
- [x] Limit promoted VictoriaMetrics resource attributes to bounded fields such as service name, environment, namespace, pod, and container.
- [x] Keep browser telemetry CORS and same-origin proxy behavior explicit.

## Validation Commands

Run focused validation as implementation lands:

```bash
uv run pytest server/tests/test_config.py
scripts/check-runtime-parity.sh
helm template tertius infra/charts/tertius --values infra/charts/tertius/values-local.yaml
docker compose config
```

Run runtime validation before closing the feature:

```bash
scripts/harness-compose.sh live-flow
scripts/harness-query-metrics.sh --file docs/harness/queries/api.promql
scripts/harness-query-metrics.sh --file docs/harness/queries/collector.promql
```

After `scripts/harness-query-traces.sh` exists, add it to the required local Compose and k3s observability validation path.
