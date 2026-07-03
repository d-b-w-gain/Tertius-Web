import json
import logging

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.metrics._internal.point import ExponentialHistogramDataPoint, HistogramDataPoint, NumberDataPoint
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState, use_span

import core.telemetry as telemetry
from core.config import Settings
from core.telemetry import JsonTraceFormatter, configure_telemetry, counter_add, histogram_record, up_down_counter_add


def _reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


def _metric_points(
    reader: InMemoryMetricReader, name: str
) -> list[NumberDataPoint | HistogramDataPoint | ExponentialHistogramDataPoint]:
    points: list[NumberDataPoint | HistogramDataPoint | ExponentialHistogramDataPoint] = []
    data = reader.get_metrics_data()
    if data is None:
        return points
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    points.extend(metric.data.data_points)
    return points


def test_up_down_counter_add_records_net_delta(monkeypatch):
    reader = _reader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(telemetry, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(telemetry, "_METRIC_INSTRUMENTS", {})

    up_down_counter_add("tertius.llm.requests.in_flight", 1, {"llm.model": "m"})
    up_down_counter_add("tertius.llm.requests.in_flight", 1, {"llm.model": "m"})
    up_down_counter_add("tertius.llm.requests.in_flight", -2, {"llm.model": "m"})

    points = _metric_points(reader, "tertius.llm.requests.in_flight")
    assert len(points) == 1
    assert points[0].value == 0
    provider.shutdown()


def test_histogram_uses_explicit_bucket_boundaries_for_known_metrics(monkeypatch):
    reader = _reader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(telemetry, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(telemetry, "_METRIC_INSTRUMENTS", {})

    histogram_record("tertius.llm.tokens.input", 750, {"llm.model": "m"})

    points = _metric_points(reader, "tertius.llm.tokens.input")
    assert len(points) == 1
    # explicit boundaries include 500 and 1000; 750 lands in the (500, 1000] bucket
    explicit = list(telemetry._HISTOGRAM_BOUNDARIES["tertius.llm.tokens.input"])
    bucket_counts = list(points[0].bucket_counts)
    assert explicit[5] == 500
    assert explicit[6] == 1000
    # cumulative count up to and including the 500 boundary is 0; up to 1000 is 1
    assert bucket_counts[5] == 0
    assert bucket_counts[6] == 1
    provider.shutdown()


def test_counter_add_accepts_float_values(monkeypatch):
    reader = _reader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(telemetry, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(telemetry, "_METRIC_INSTRUMENTS", {})

    counter_add("tertius.llm.cost.usd.total", 0.0000505, {"llm.model": "m"})
    counter_add("tertius.llm.cost.usd.total", 0.0001, {"llm.model": "m"})

    points = _metric_points(reader, "tertius.llm.cost.usd.total")
    assert len(points) == 1
    assert points[0].value == pytest.approx(0.0001505)
    provider.shutdown()


def test_configure_telemetry_disabled_does_not_export():
    settings = Settings(otel_enabled=False, otel_service_name="tertius-api")

    status = configure_telemetry(settings)

    assert status.enabled is False
    assert status.exporting is False
    assert status.service_name == "tertius-api"


def test_configure_telemetry_missing_endpoint_is_no_export_mode(monkeypatch):
    import core.telemetry as telemetry

    monkeypatch.setattr(telemetry, "_CONFIGURED", False)
    settings = Settings(
        otel_enabled=True,
        otel_service_name="tertius-api",
        otel_exporter_otlp_endpoint="",
        otel_log_json=False,
    )

    status = configure_telemetry(settings)

    assert status.enabled is True
    assert status.exporting is False
    assert status.error is None


def test_json_trace_formatter_includes_active_trace_ids():
    formatter = JsonTraceFormatter("tertius-api", "test")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    span_context = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1234567890ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )

    with use_span(NonRecordingSpan(span_context), end_on_exit=False):
        payload = json.loads(formatter.format(record))

    assert payload["service.name"] == "tertius-api"
    assert payload["deployment.environment"] == "test"
    assert payload["trace_id"] == "1234567890abcdef1234567890abcdef"
    assert payload["span_id"] == "1234567890abcdef"
