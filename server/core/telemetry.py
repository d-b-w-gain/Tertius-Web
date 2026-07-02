from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from importlib.metadata import PackageNotFoundError, version
from time import perf_counter
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

_CONFIGURED = False
_FASTAPI_INSTRUMENTED_IDS: set[int] = set()
_SQLALCHEMY_INSTRUMENTED_IDS: set[int] = set()
_HTTPX_INSTRUMENTED = False
_ASYNCIO_INSTRUMENTED = False
_METRIC_INSTRUMENTS: dict[tuple[str, str], Any] = {}


@dataclass(frozen=True)
class TelemetryStatus:
    enabled: bool
    exporting: bool
    service_name: str
    error: str | None = None


class JsonTraceFormatter(logging.Formatter):
    def __init__(self, service_name: str, environment: str):
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        span_context = trace.get_current_span().get_span_context()
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service.name": self.service_name,
            "deployment.environment": self.environment,
            "trace_id": "",
            "span_id": "",
        }
        if span_context.is_valid:
            payload["trace_id"] = f"{span_context.trace_id:032x}"
            payload["span_id"] = f"{span_context.span_id:016x}"
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_telemetry(settings, service_name_override: str | None = None) -> TelemetryStatus:
    global _CONFIGURED, _HTTPX_INSTRUMENTED, _ASYNCIO_INSTRUMENTED

    service_name = service_name_override or settings.otel_service_name
    if not settings.otel_enabled:
        return TelemetryStatus(enabled=False, exporting=False, service_name=service_name)
    if _CONFIGURED:
        return TelemetryStatus(
            enabled=True,
            exporting=bool(settings.otel_exporter_otlp_endpoint),
            service_name=service_name,
        )

    try:
        resource = _build_resource(settings, service_name)
        span_provider = TracerProvider(resource=resource, sampler=_sampler(settings))
        metric_provider = MeterProvider(resource=resource)
        exporting = bool(settings.otel_exporter_otlp_endpoint.strip())

        if exporting:
            span_exporter = _span_exporter(settings)
            metric_exporter = _metric_exporter(settings)
            span_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            metric_provider = MeterProvider(
                resource=resource,
                metric_readers=[PeriodicExportingMetricReader(metric_exporter)],
            )

        trace.set_tracer_provider(span_provider)
        metrics.set_meter_provider(metric_provider)

        if not _HTTPX_INSTRUMENTED:
            HTTPXClientInstrumentor().instrument()
            _HTTPX_INSTRUMENTED = True

        if _should_instrument_asyncio(settings) and not _ASYNCIO_INSTRUMENTED:
            AsyncioInstrumentor().instrument()
            _ASYNCIO_INSTRUMENTED = True

        LoggingInstrumentor().instrument(set_logging_format=False)
        if settings.otel_log_json:
            _configure_json_logging(service_name, _deployment_environment(settings))

        _CONFIGURED = True
        return TelemetryStatus(enabled=True, exporting=exporting, service_name=service_name)
    except Exception as exc:
        logger.warning("OpenTelemetry setup failed; continuing without telemetry: %s", exc)
        return TelemetryStatus(enabled=True, exporting=False, service_name=service_name, error=str(exc))


def get_tracer(name: str):
    return trace.get_tracer(name)


def get_meter(name: str):
    return metrics.get_meter(name)


def instrument_fastapi_app(app) -> None:
    marker = id(app)
    if marker in _FASTAPI_INSTRUMENTED_IDS:
        return
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/")
    _FASTAPI_INSTRUMENTED_IDS.add(marker)


def instrument_sqlalchemy_engine(engine) -> None:
    marker = id(engine)
    if marker in _SQLALCHEMY_INSTRUMENTED_IDS:
        return
    SQLAlchemyInstrumentor().instrument(engine=engine)
    _SQLALCHEMY_INSTRUMENTED_IDS.add(marker)


def counter_add(name: str, value: int | float = 1, attributes: dict[str, Any] | None = None) -> None:
    counter = _instrument("counter", name)
    counter.add(value, attributes or {})


def histogram_record(name: str, value: float, attributes: dict[str, Any] | None = None) -> None:
    histogram = _instrument("histogram", name)
    histogram.record(value, attributes or {})


def up_down_counter_add(name: str, value: int = 1, attributes: dict[str, Any] | None = None) -> None:
    counter = _instrument("up_down_counter", name)
    counter.add(value, attributes or {})


def record_exception(span, exc: BaseException, *, status_description: str | None = None) -> None:
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, status_description or exc.__class__.__name__))


def workflow_from_route(path: str) -> str:
    for prefix, workflow in (
        ("/api/intus", "intus"),
        ("/api/artus", "artus"),
        ("/api/extus", "extus"),
        ("/api/timus", "timus"),
    ):
        if path.startswith(prefix):
            return workflow
    return "api"


def timed() -> float:
    return perf_counter()


def elapsed_seconds(start: float) -> float:
    return max(0.0, perf_counter() - start)


_HISTOGRAM_BOUNDARIES: dict[str, tuple[float, ...]] = {
    "tertius.llm.request.duration": (
        0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0,
    ),
    "tertius.llm.tokens.input": (
        1, 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000,
    ),
    "tertius.llm.tokens.output": (
        1, 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000,
    ),
    "tertius.llm.tokens.total": (
        1, 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000,
    ),
    "tertius.llm.tokens.cached": (
        1, 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000,
    ),
    "tertius.llm.tokens.cache_creation": (
        1, 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000,
    ),
    "tertius.llm.cost.usd": (
        0.0001, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0,
    ),
}


def _instrument(kind: str, name: str):
    key = (kind, name)
    existing = _METRIC_INSTRUMENTS.get(key)
    if existing is not None:
        return existing
    meter = get_meter("tertius.telemetry")
    if kind == "counter":
        instrument = meter.create_counter(name)
    elif kind == "up_down_counter":
        instrument = meter.create_up_down_counter(name)
    elif kind == "histogram":
        boundaries = _HISTOGRAM_BOUNDARIES.get(name)
        kwargs: dict[str, Any] = {}
        if boundaries is not None:
            kwargs["explicit_bucket_boundaries_advisory"] = list(boundaries)
        instrument = meter.create_histogram(name, **kwargs)
    else:
        raise ValueError(f"Unknown metric kind: {kind}")
    _METRIC_INSTRUMENTS[key] = instrument
    return instrument


def _build_resource(settings, service_name: str) -> Resource:
    attributes = {
        SERVICE_NAME: service_name,
        SERVICE_VERSION: _service_version(),
        DEPLOYMENT_ENVIRONMENT: _deployment_environment(settings),
    }
    attributes.update(_parse_resource_attributes(settings.otel_resource_attributes))
    return Resource.create(attributes)


def _parse_resource_attributes(raw: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for part in raw.split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key.strip() and value.strip():
            attributes[key.strip()] = value.strip()
    return attributes


def _deployment_environment(settings) -> str:
    for part in settings.otel_resource_attributes.split(","):
        key, _, value = part.partition("=")
        if key.strip() == DEPLOYMENT_ENVIRONMENT and value.strip():
            return value.strip()
    return os.getenv("APP_ENV") or os.getenv("DEPLOYMENT_ENVIRONMENT") or "local"


def _service_version() -> str:
    try:
        return version("tertius-web")
    except PackageNotFoundError:
        return "0.1.0"


def _sampler(settings):
    sampler = settings.otel_traces_sampler.strip().lower()
    arg = settings.otel_traces_sampler_arg.strip()
    ratio = 1.0
    if arg:
        try:
            ratio = min(1.0, max(0.0, float(arg)))
        except ValueError:
            ratio = 1.0
    if sampler in {"always_off", "alwaysoff"}:
        return ALWAYS_OFF
    if sampler in {"always_on", "alwayson"}:
        return ALWAYS_ON
    if sampler in {"traceidratio", "trace_id_ratio"}:
        return TraceIdRatioBased(ratio)
    return ParentBased(TraceIdRatioBased(ratio))


def _span_exporter(settings):
    protocol = settings.otel_exporter_otlp_protocol.strip().lower()
    endpoint = settings.otel_exporter_otlp_endpoint.strip()
    if protocol in {"http/protobuf", "http", "http_proto"}:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=_http_signal_endpoint(endpoint, "v1/traces"))

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=endpoint)


def _metric_exporter(settings):
    protocol = settings.otel_exporter_otlp_protocol.strip().lower()
    endpoint = settings.otel_exporter_otlp_endpoint.strip()
    if protocol in {"http/protobuf", "http", "http_proto"}:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        return OTLPMetricExporter(endpoint=_http_signal_endpoint(endpoint, "v1/metrics"))

    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

    return OTLPMetricExporter(endpoint=endpoint)


def _http_signal_endpoint(endpoint: str, signal_path: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith(signal_path):
        return normalized
    return f"{normalized}/{signal_path}"


def _should_instrument_asyncio(settings) -> bool:
    return os.getenv("OTEL_INSTRUMENT_ASYNCIO", "").strip().lower() in {"1", "true", "yes"} and bool(settings)


def _configure_json_logging(service_name: str, environment: str) -> None:
    formatter = JsonTraceFormatter(service_name, environment)
    root = logging.getLogger()
    if not root.handlers:
        new_handler = logging.StreamHandler()
        new_handler.setFormatter(formatter)
        root.addHandler(new_handler)
        return
    for handler in root.handlers:
        handler.setFormatter(formatter)
