# Trace Validation Queries

Tertius validates local traces through VictoriaTraces' Jaeger-compatible HTTP
API. Run these after `scripts/harness-compose.sh live-flow` or
`scripts/harness-k3s.sh live-flow`:

```bash
scripts/harness-query-traces.sh
scripts/harness-query-traces.sh --require-cross-service \
  --cross-service tertius-api \
  --cross-service tertius-compile-job
```

Expected services after `scripts/smoke-live-flow.sh`:

- `tertius-api`
- `tertius-compile-job`

`tertius-ui` spans require a browser run because the shell live-flow validates
the UI origin and proxy with curl but does not execute frontend JavaScript.

The default script window is 30 minutes. Use `TRACES_BASE_URL` when the
VictoriaTraces endpoint is port-forwarded on a non-default port.
