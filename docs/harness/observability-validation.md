# Observability Validation

Tertius uses OpenTelemetry as the application telemetry boundary. Local
validation should query metrics through a PromQL-compatible endpoint instead of
relying only on collector debug logs.

## Local Metrics

Compose exposes VictoriaMetrics at:

```text
http://localhost:8428
```

Query with:

```bash
scripts/harness-query-metrics.sh 'up'
scripts/harness-query-metrics.sh --file docs/harness/queries/api.promql
scripts/harness-query-metrics.sh --file docs/harness/queries/api.promql --name api_request_rate
```

Set `METRICS_BASE_URL` for a different endpoint.

## Local Traces

Compose exposes VictoriaTraces at:

```text
http://localhost:10428
```

Local k3s exposes the same endpoint after port-forwarding the bundled traces
backend service. Set `TRACES_BASE_URL` for a different endpoint.

Query recent services and traces with:

```bash
scripts/harness-query-traces.sh
scripts/harness-query-traces.sh --service tertius-api --window-minutes 30
scripts/harness-query-traces.sh --service tertius-ui --window-minutes 30
scripts/harness-query-traces.sh --require-cross-service \
  --cross-service tertius-api \
  --cross-service tertius-compile-job
```

## Query Files

Query files use single-line PromQL expressions:

```text
# name: api_request_rate
sum(rate(tertius_api_request_count[5m]))
```

Comments start with `#`; every expression must follow a `# name:` marker. Blank
lines separate entries.

## What to Check

- API/routing changes: request rate, duration p95, and 5xx ratio.
- Compile changes: started, finished, failed, queue latency, and duration p95.
- LLM changes: request count, failure count, token and cost metrics. Never add
  prompts or generated source to telemetry.
- Collector changes: refused, dropped, and export-failed telemetry.
- Trace backend changes: spans for `tertius-api` and `tertius-compile-job`,
  plus at least one trace connecting those services after an authenticated live
  flow. `tertius-ui` spans require browser execution because
  `scripts/smoke-live-flow.sh` calls the UI origin with curl and does not run
  frontend JavaScript.
