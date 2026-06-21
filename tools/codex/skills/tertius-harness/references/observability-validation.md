# Observability Validation Reference

Read `docs/harness/observability-validation.md` before making telemetry claims.

Use `scripts/harness-query-metrics.sh` with the query files under
`docs/harness/queries/`. Set `METRICS_BASE_URL` when querying a non-default
endpoint.

Never record secrets, prompts, generated source, uploaded model files, auth
tokens, raw user IDs, raw project IDs, or raw job IDs in telemetry.
