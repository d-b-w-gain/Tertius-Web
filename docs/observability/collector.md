# OpenTelemetry Collector Deployment

Local harness metrics are queryable through the PromQL-compatible endpoint
documented in `docs/harness/observability-validation.md`. Compose exposes
VictoriaMetrics on `http://localhost:8428`; local k3s enables the bundled
metrics backend through `values-local.yaml`. Compose and local k3s also expose
VictoriaTraces on `http://localhost:10428` for local trace validation.

The Helm chart deploys an OpenTelemetry Collector by default when `app.observability.enabled=true`. API, compile-worker, and browser telemetry default to the in-chart collector service:

```yaml
app:
  observability:
    enabled: true
    otlpEndpoint: ""
    collector:
      enabled: true
```

Set `app.observability.otlpEndpoint` when backend telemetry should go to a shared collector instead. If browser traces should also use that shared collector through the UI nginx proxy, set `collectorHttpHost` and `collectorHttpPort` to the shared collector's OTLP HTTP service.

```yaml
app:
  observability:
    otlpEndpoint: http://shared-otel-collector:4317
    collectorHttpHost: shared-otel-collector
    collectorHttpPort: "4318"
    collector:
      enabled: false
```

## VictoriaMetrics and VictoriaTraces

The near-term metrics contract is Prometheus remote write to VictoriaMetrics.
Existing harness queries depend on Prometheus-compatible names such as
`tertius_api_request_count`, so native OTLP metrics ingestion is optional until
the query names are intentionally migrated.

For VictoriaMetrics metrics and VictoriaTraces spans, configure the collector
exporters and pipeline exporter lists explicitly per environment:

```yaml
app:
  observability:
    collector:
      enabled: true
      exporters:
        debug:
          verbosity: basic
        otlphttp/victoriametrics:
          compression: gzip
          encoding: proto
          metrics_endpoint: http://vmsingle:8428/opentelemetry/v1/metrics
        otlphttp/victoriatraces:
          compression: gzip
          encoding: proto
          traces_endpoint: http://victoria-traces:10428/insert/opentelemetry/v1/traces
          retry_on_failure:
            enabled: true
          sending_queue:
            enabled: true
            queue_size: 2048
      tracesExporters:
        - otlphttp/victoriatraces
      metricsExporters:
        - otlphttp/victoriametrics
```

For VictoriaMetrics cluster mode, use the `vminsert` tenant endpoint for `metrics_endpoint`.

If native OTLP metrics ingestion is enabled, send protobuf OTLP/HTTP to
`/opentelemetry/v1/metrics`. Enable Prometheus-compatible naming and promote
only bounded resource attributes:

```yaml
app:
  observability:
    metricsBackend:
      enabled: true
      # Add equivalent flags to vminsert for VictoriaMetrics cluster mode.
      extraArgs:
        - -opentelemetry.usePrometheusNaming=true
        - -opentelemetry.promoteResourceAttributes=service.name,deployment.environment,k8s.namespace.name,k8s.pod.name,k8s.container.name
```

VictoriaMetrics works best with cumulative temporality. Keep SDK and collector
metric exports cumulative unless a deliberate migration adds a delta-to-cumulative
collector processor.

Keep `debug` exporters in local or short-lived troubleshooting values only. Do
not rely on collector logs as the validation boundary; use the metrics and trace
query scripts after a live flow.

## Safety

Metrics labels and trace attributes must not include raw user IDs, tenant IDs,
project IDs, job IDs, filenames, prompts, generated source, auth tokens, or raw
exception strings. Browser telemetry should continue to use the same-origin UI
proxy or an explicit local collector endpoint; it must not send directly to
cluster-only Victoria services.
