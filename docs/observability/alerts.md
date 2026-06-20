# Observability Alerts

Tune thresholds per environment after baseline traffic is known. Production alerts should page only on sustained user impact or data loss risk; lower-severity conditions can notify the owning channel.

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

- Provider auth failures: page on any sustained auth/permission failure category for 5 minutes.
- Provider rate limits: notify when rate-limit failures exceed 5% of LLM requests for 10 minutes.
- Provider latency: notify when p95 `tertius.llm.request.duration` exceeds the environment threshold for 15 minutes.
- Billing publish errors: page when `tertius.billing.publish.error.count` is non-zero for 10 minutes.
- Cost anomaly: notify when `tertius.llm.cost.usd` exceeds the configured daily budget burn-rate threshold.

## Data Stores And Platform

- Postgres connection saturation: page when active connections exceed 85% of the configured limit for 10 minutes.
- Postgres latency: notify when query latency or transaction duration exceeds baseline by 3x for 15 minutes.
- Pod crash loops: page on repeated restarts for API, UI, NATS, Keycloak, Postgres, or compile jobs.
- Collector export failures: page when collector export failures or dropped telemetry are non-zero for 10 minutes.
- Collector backpressure: notify when collector memory limiter drops data or queue length remains high for 10 minutes.
