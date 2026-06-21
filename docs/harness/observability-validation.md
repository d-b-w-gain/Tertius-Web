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
