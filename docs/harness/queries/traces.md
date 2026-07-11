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

For a Pi agent edit, inspect one trace and require this parent-linked chain:

```text
tertius-api HTTP request
  -> NATS publish
  -> tertius-pi-agent-job pi_agent.command.consume
  -> NATS publish
  -> tertius-api pi_agent.result.consume
```

NATS headers are authoritative for each consume boundary. Envelope
`traceparent`/`tracestate` fields are used only when headers are absent for a
republished or legacy message. No span attribute may include prompts, source,
filenames, auth material, or raw tenant, user, project, or job identifiers.

`tertius-ui` spans require a browser run because the shell live-flow validates
the UI origin and proxy with curl but does not execute frontend JavaScript.

The default script window is 30 minutes. Use `TRACES_BASE_URL` when the
VictoriaTraces endpoint is port-forwarded on a non-default port.
