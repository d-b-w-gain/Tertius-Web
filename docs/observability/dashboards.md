# Observability Dashboards

These dashboards assume OpenTelemetry metrics and traces are exported through the configured collector. Names are intentionally vendor-neutral; translate them into PromQL, TraceQL, SQL, or the target backend query language at deployment time.

Harness query references live under `docs/harness/queries/` and can be run with
`scripts/harness-query-metrics.sh`. Use those files as the starting point for
dashboard panels so agent validation and dashboards stay aligned.

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

- Request count and failures by `provider`, `model_id`, `model`, operation, and bounded `error_category`: `tertius.llm.request.count`, `tertius.llm.request.error.count`
- In-flight provider requests: `tertius.llm.requests.in_flight` (up/down counter)
- Active AI edit background jobs: `tertius.llm.jobs.active` (up/down counter)
- AI edit jobs queued, started, finished, and failed by bounded `failure_category`: `tertius.llm.job.queued.count`, `tertius.llm.job.started.count`, `tertius.llm.job.finished.count`, `tertius.llm.job.failed.count`
- AI edit job duration by `job_status`, `failure_category`, and `retryable`: `tertius.llm.job.duration`
- Provider latency by `provider` and `model_id`: `tertius.llm.request.duration`
- Retries by reason (`rate_limit`, `generation_error`): `tertius.llm.retry.count` with `llm.retry` span events carrying attempt and backoff
- Input and output tokens: `tertius.llm.tokens.input`, `tertius.llm.tokens.output` (histograms and `.total` cumulative counters)
- Total, cached, and cache-creation token histograms: `tertius.llm.tokens.total`, `tertius.llm.tokens.cached`, `tertius.llm.tokens.cache_creation`; cached and cache-creation also emit cumulative counters as `.total`
- Estimated cost: `tertius.llm.cost.usd` (histogram) and `tertius.llm.cost.usd.total` (cumulative counter for budget burn-rate)
- Finish reason slicing on `llm.build_script.generate` / `llm.files.edit` spans via the `llm.finish_reason` attribute
- Billing publish errors: `tertius.billing.publish.error.count`
- Trace drilldown from API request to provider span, excluding prompts and generated content
- AI-edit-to-compile correlation via the `tertius.originating_llm_edit_job_hash` span attribute on compile consume spans

## Pi Agent

- Queued, started, worker-completed, API-terminal, and result-processed rates from
  `docs/harness/queries/pi-agent.promql`, sliced only by operation, provider,
  model, status, failure category, and retryability.
- Worker duration p50/p95/p99, turns, tool calls, and separate input, output,
  cache-read, and cache-write token histograms.
- Auth failures, DB-observed active jobs, unique stale reconciliations, and
  worker-loss rate. Add JetStream durable pending, ack-pending, ack-floor lag,
  and oldest-unacked age only when the NATS monitoring endpoint is exported;
  never derive these panels by subtracting retryable attempt counters.
- Render DB-observed active jobs with `max` across API replicas, never `sum`.
  This panel is informational: rolling scrapes and process restarts can briefly
  show mixed replica values. Pair it with the summed result-consumer heartbeat
  rate and unique stale/worker-loss increases for actionable state. The active
  observer is independent of NATS consumer initialization.
- Trace drilldown through `pi_agent.command.consume` and
  `pi_agent.result.consume`; use trace IDs for correlation, never raw domain IDs.

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
- VictoriaMetrics ingestion rate, active series, slow queries, disk usage, and
  retention pressure
- VictoriaTraces ingestion rate, dropped rows, query latency, disk usage, and
  retention pressure
- Trace drilldowns by service, route, compile operation, LLM provider call, and
  NATS publish/consume boundary
