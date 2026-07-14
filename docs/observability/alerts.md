# Observability Alerts

Tune thresholds per environment after baseline traffic is known. Production alerts should page only on sustained user impact or data loss risk; lower-severity conditions can notify the owning channel.

Harness query references live under `docs/harness/queries/`. Promote a harness
query into an alert only after choosing production thresholds and cardinality-safe
labels.

## API

- API 5xx rate: page when `tertius.api.request.count{status_code=~"5.."}` is above 2% of requests for 10 minutes.
- API latency: notify when p95 `tertius.api.request.duration` is above 2 seconds for 15 minutes.
- API availability: page when healthy pod count is zero or readiness failures persist for 5 minutes.

## Compile Pipeline

- Compile failure spike: notify when `tertius.compile.job.failed.count` exceeds 10% of finished jobs for 15 minutes.
- Compile timeout spike: notify when failed jobs tagged as timeout/worker timeout increase for 10 minutes.
- Result consumer errors: page when `tertius.compile.result.error.count` is non-zero for 10 minutes.
- Queue latency: notify when p95 `tertius.compile.queue.latency` is above the expected KEDA scale-up window for 15 minutes.
- NATS publish failures: page when `tertius.nats.publish.error.count` is non-zero for 5 minutes.
- NATS consumer lag: notify when JetStream consumer lag grows continuously for 10 minutes.

## LLM And Billing

- Provider auth failures: page on any sustained `error_category="auth"` provider failure for 5 minutes.
- Provider rate limits: notify when `error_category="rate_limit"` provider failures exceed 5% of LLM requests for 10 minutes.
- Provider 5xx/timeouts: notify when `error_category=~"provider_5xx|timeout"` failures exceed the environment threshold for 10 minutes.
- AI edit job failure spike: notify when `tertius.llm.job.failed.count` exceeds 10% of finished AI edit jobs for 15 minutes, sliced by bounded `failure_category`.
- Provider latency: notify when p95 `tertius.llm.request.duration` exceeds the environment threshold for 15 minutes.
- LLM retry storm: notify when `tertius.llm.retry.count` exceeds 5% of LLM requests for 10 minutes, sliced by `llm.retry_reason`.
- In-flight saturation: notify when `tertius.llm.requests.in_flight` stays at the environment ceiling for 10 minutes.
- AI edit job saturation: notify when `tertius.llm.jobs.active` stays above the environment ceiling for 10 minutes.
- Billing publish errors: page when `tertius.billing.publish.error.count` is non-zero for 10 minutes.
- Cost anomaly: notify when `tertius.llm.cost.usd.total` (cumulative counter) exceeds the configured daily budget burn-rate threshold.

## Pi Agent

- Provider auth failure: page when `failure_category="provider_auth"` terminal
  jobs remain non-zero for 5 minutes; the retained OAuth state needs operator
  attention rather than automated retries.
- No result consumer: the NATS monitoring endpoint exposes durable
  `num_pending`, `num_ack_pending`, and ack-floor state, but the current
  collector does not export those fields. Until a NATS exporter is configured,
  alert only when `max(tertius.pi_agent.jobs.active) > 0` and the sum of the
  five-minute increase in `tertius.pi_agent.result_consumer.heartbeat.count`
  is zero. Enable this rule only when Pi is enabled. Do not subtract attempt
  counters because retries can mask or inflate lag. Active observation runs in
  a separate API lifespan task and therefore remains available when NATS
  subscription initialization fails.
- Stale jobs: notify on any `tertius.pi_agent.job.stale.count`; page when the
  increase exceeds 3 in 15 minutes.
- Repeated worker loss: page when `failure_category="worker_lost"` increases
  more than 3 times in 15 minutes on `tertius.pi_agent.job.terminal.count`
  with `operation="pi_agent.api"`, grouped only by provider and model. This
  terminal series includes stale reconciliations; do not add the stale counter.
- Queue lag: after a NATS exporter is configured, alert directly on the Pi
  request and result durable consumers' pending, ack-pending, ack-floor lag, and
  oldest unacked age. Until then, use sustained DB-observed active jobs plus the
  unique stale counter; do not use attempt-counter subtraction.
- Overdue jobs: notify on any increase in the unique
  `tertius.pi_agent.job.stale.count` and page on repeated
  `failure_category="worker_lost"` terminal increases. Active-job gauges alone
  are never a timeout alert.

## Data Stores And Platform

- Postgres connection saturation: page when active connections exceed 85% of the configured limit for 10 minutes.
- Postgres latency: notify when query latency or transaction duration exceeds baseline by 3x for 15 minutes.
- Pod crash loops: page on repeated restarts for API, UI, NATS, Keycloak, Postgres, or compile jobs.
- Collector export failures: page when export failures to VictoriaMetrics or
  VictoriaTraces, or dropped telemetry, are non-zero for 10 minutes.
- Collector backpressure: notify when collector memory limiter drops data or queue length remains high for 10 minutes.
- VictoriaTraces dropped rows: page when dropped rows are non-zero for 10
  minutes, especially timestamp-out-of-retention drops.
