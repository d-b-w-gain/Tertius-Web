import json
import logging

from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState, use_span

from core.config import Settings
from core.telemetry import JsonTraceFormatter, configure_telemetry


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
