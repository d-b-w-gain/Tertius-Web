# Observability Dashboards

These dashboards assume OpenTelemetry metrics and traces are exported through the configured collector. Names are intentionally vendor-neutral; translate them into PromQL, TraceQL, SQL, or the target backend query language at deployment time.

## API

- Request rate by `route`, `method`, and `status_code`: `tertius.api.request.count`
- Error rate for `status_code >= 500`: `tertius.api.request.count`
- p50/p95/p99 request duration by route: `tertius.api.request.duration`
- Slow route table using trace span duration and `http.route`
- Database latency from SQLAlchemy spans
- Outbound HTTP latency and errors from httpx spans for Keycloak and LLM providers

## Compile Pipeline

- Jobs started, finished, and failed: `tertius.compile.job.started.count`, `tertius.compile.job.finished.count`, `tertius.compile.job.failed.count`
- Compile duration by `export_format` and `job_status`: `tertius.compile.job.duration`
- Queue latency by `export_format`: `tertius.compile.queue.latency`
- Result processing rate and failures: `tertius.compile.result.processed.count`, `tertius.compile.result.error.count`
- Result processing duration: `tertius.compile.result.processing.duration`
- NATS publish rate, latency, and errors: `tertius.nats.publish.count`, `tertius.nats.publish.duration`, `tertius.nats.publish.error.count`
- KEDA ScaledJob active jobs, failed jobs, and scale decisions from platform metrics

## LLM

- Request count and failures by `provider`, `model_id`, and operation: `tertius.llm.request.count`, `tertius.llm.request.error.count`
- Provider latency by `provider` and `model_id`: `tertius.llm.request.duration`
- Input and output tokens: `tertius.llm.tokens.input`, `tertius.llm.tokens.output`
- Estimated cost: `tertius.llm.cost.usd`
- Billing publish errors: `tertius.billing.publish.error.count`
- Trace drilldown from API request to provider span, excluding prompts and generated content

## Browser

- Document load spans for `tertius-ui`
- Same-origin API fetch spans with propagated trace headers
- UI interaction spans: compile submit, LLM file edit submit, history exploration, artifact download, and 3D viewer load
- Frontend error spans and events by safe error type/source

## Infrastructure

- API and UI pod CPU, memory, restarts, and readiness
- Compile job CPU, memory, ephemeral storage, restart count, and failed pod count
- Postgres connections, transaction rate, and query latency from CloudNativePG or platform metrics
- NATS connections, JetStream messages, publish errors, and consumer lag from the monitoring endpoint
- Collector accepted, refused, dropped, and export-failed telemetry from collector self-metrics
