# OpenTelemetry Collector Deployment

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

The default collector config exports to `debug`. For VictoriaMetrics metrics and VictoriaTraces spans, configure the collector exporters and pipeline exporter lists:

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
      tracesExporters:
        - otlphttp/victoriatraces
      metricsExporters:
        - otlphttp/victoriametrics
```

For VictoriaMetrics cluster mode, use the `vminsert` tenant endpoint for `metrics_endpoint`.
