from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.import_3mf_messages import Import3mfCommand
from core.object_store import ObjectRef, ObjectStoreUnavailableError
from core.project_assets import (
    IMPORT_3MF_CONVERSION_VERSION,
    Import3mfBounds,
    Import3mfManifest,
    Import3mfPart,
)
from workflows.intus.import_3mf_converter import ConversionOutput, Import3mfError
from workflows.intus.import_3mf_job import (
    _heartbeat,
    execute_import_command,
    handle_import_request_message,
)


SOURCE = b"safe-3mf"


def ref(content: bytes) -> ObjectRef:
    digest = hashlib.sha256(content).hexdigest()
    return ObjectRef(
        bucket="TERTIUS_ASSETS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=len(content),
    )


def command() -> Import3mfCommand:
    return Import3mfCommand(
        schema_version=1,
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        attempt=1,
        execution_id=uuid4(),
        source=ref(SOURCE),
        conversion_version=IMPORT_3MF_CONVERSION_VERSION,
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        tracestate="vendor=value",
    )


def conversion_output() -> ConversionOutput:
    brep = b"brep"
    manifest = Import3mfManifest(
        schema_version=1,
        conversion_version=IMPORT_3MF_CONVERSION_VERSION,
        source_sha256=hashlib.sha256(SOURCE).hexdigest(),
        brep_sha256=hashlib.sha256(brep).hexdigest(),
        brep_byte_size=len(brep),
        source_unit="MM",
        scale_to_mm=1.0,
        object_count=1,
        total_vertices=3,
        total_triangles=1,
        warnings=(),
        parts=(
            Import3mfPart(
                index=0,
                source_name="box",
                name="part_1",
                shape_type="solid",
                boolean_capable=True,
                is_valid=True,
                vertex_count=3,
                triangle_count=1,
                bounds_mm=Import3mfBounds(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0)),
            ),
        ),
    )
    return ConversionOutput(brep_bytes=brep, manifest=manifest)


class Store:
    def __init__(self, *, fail_get: bool = False, integrity_get: bool = False):
        self.fail_get = fail_get
        self.integrity_get = integrity_get
        self.puts: list[bytes] = []

    async def get(self, _ref):
        if self.fail_get:
            raise ObjectStoreUnavailableError("transient")
        if self.integrity_get:
            from core.object_store import ObjectIntegrityError

            raise ObjectIntegrityError("digest mismatch")
        return SOURCE

    async def put(self, value: bytes):
        self.puts.append(value)
        return ref(value)


class Publisher:
    def __init__(self, failures: int = 0, events: list[str] | None = None):
        self.failures = failures
        self.messages = []
        self.calls = []
        self.events = events if events is not None else []

    async def publish_json(self, _subject, message, **_kwargs):
        self.events.append("publish")
        if self.failures:
            self.failures -= 1
            raise RuntimeError("nats unavailable")
        self.messages.append(message)
        self.calls.append((_subject, message, _kwargs))


class Message:
    def __init__(self, data: bytes, events: list[str] | None = None):
        self.data = data
        self.headers = None
        self.subject = "tertius.import.3mf.request"
        self.events = events if events is not None else []

    async def ack(self):
        self.events.append("ack")

    async def nak(self):
        self.events.append("nak")

    async def term(self):
        self.events.append("term")

    async def in_progress(self):
        self.events.append("heartbeat")


def settings():
    return SimpleNamespace(
        import_3mf_result_subject="tertius.import.3mf.result",
        import_3mf_message_max_bytes=1024 * 1024,
        import_3mf_timeout_seconds=300,
        import_3mf_ack_wait_seconds=360,
    )


@pytest.mark.asyncio
async def test_execute_fetches_converts_and_stores_only_references(monkeypatch):
    output = conversion_output()
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess",
        lambda *_args: output,
    )
    store = Store()
    progress = []

    async def report(value):
        progress.append((value.stage, value.percent))

    result = await execute_import_command(command(), store, settings(), report)

    assert result.status == "succeeded"
    assert result.traceparent == command().traceparent
    assert result.tracestate == command().tracestate
    assert store.puts == [output.brep_bytes, output.manifest.model_dump_json().encode()]
    assert progress == [("validating", 5), ("converting", 20), ("persisting", 90)]
    assert "safe-3mf" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_conversion_error_is_terminal_and_ack_follows_retried_publish(
    monkeypatch,
):
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess",
        lambda *_args: (_ for _ in ()).throw(
            Import3mfError("invalid_3mf_archive", "unsafe source detail")
        ),
    )
    events: list[str] = []
    publisher = Publisher(failures=1, events=events)
    msg = Message(command().model_dump_json().encode(), events)

    await handle_import_request_message(msg, Store(), publisher, settings())

    assert events[-1] == "ack"
    assert events.count("publish") >= 2
    result = publisher.messages[-1]
    assert result.status == "failed"
    assert result.error_code == "invalid_3mf_archive"
    assert result.user_message == "The file is not a safe 3MF archive."


@pytest.mark.asyncio
async def test_transient_object_failure_naks_without_publishing():
    publisher = Publisher()
    msg = Message(command().model_dump_json().encode())

    await handle_import_request_message(
        msg, Store(fail_get=True), publisher, settings()
    )

    assert msg.events[-1] == "nak"
    assert all(
        message.__class__.__name__ == "Import3mfProgress"
        for message in publisher.messages
    )


@pytest.mark.asyncio
async def test_source_integrity_error_publishes_terminal_result_before_ack():
    events: list[str] = []
    publisher = Publisher(events=events)
    msg = Message(command().model_dump_json().encode(), events)
    await handle_import_request_message(
        msg, Store(integrity_get=True), publisher, settings()
    )
    assert events[-2:] == ["publish", "ack"]
    result = publisher.messages[-1]
    assert result.status == "failed"
    assert result.error_code == "asset_integrity_error"
    assert "nak" not in events


@pytest.mark.asyncio
async def test_handler_propagates_linked_trace_headers_to_progress_and_result(
    monkeypatch,
):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.trace.get_tracer", provider.get_tracer
    )
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess",
        lambda *_args: conversion_output(),
    )
    cmd = command()
    publisher = Publisher()
    msg = Message(cmd.model_dump_json().encode())
    await handle_import_request_message(msg, Store(), publisher, settings())

    assert publisher.calls
    for _subject, _message, kwargs in publisher.calls:
        assert (
            kwargs["headers"]["traceparent"].split("-")[1]
            == cmd.traceparent.split("-")[1]
        )
        assert kwargs["headers"]["tracestate"] == cmd.tracestate

    fallback_publisher = Publisher()
    malformed = Message(cmd.model_dump_json().encode())
    malformed.headers = {"traceparent": "malformed"}
    await handle_import_request_message(
        malformed, Store(), fallback_publisher, settings()
    )
    published_traceparent = fallback_publisher.calls[-1][2]["headers"]["traceparent"]
    assert published_traceparent.split("-")[1] == cmd.traceparent.split("-")[1]
    assert published_traceparent.split("-")[2] != cmd.traceparent.split("-")[2]
    consume_spans = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "import_3mf.command.consume"
    ]
    assert len(consume_spans) == 2
    fallback_span = consume_spans[-1]
    assert f"{fallback_span.context.trace_id:032x}" == cmd.traceparent.split("-")[1]
    assert fallback_span.parent is not None
    assert f"{fallback_span.parent.span_id:016x}" == cmd.traceparent.split("-")[2]
    provider.shutdown()


@pytest.mark.asyncio
async def test_malformed_command_is_terminated_without_echoing_payload(caplog):
    secret = "private-source-name.3mf"
    msg = Message(f'{{"filename":"{secret}"}}'.encode())

    await handle_import_request_message(msg, Store(), Publisher(), settings())

    assert msg.events == ["term"]
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_timeout_publishes_exact_terminal_code_and_acks(monkeypatch):
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess",
        lambda *_args: (_ for _ in ()).throw(
            Import3mfError("3mf_conversion_timeout", "private timeout detail")
        ),
    )
    publisher = Publisher()
    msg = Message(command().model_dump_json().encode())
    await handle_import_request_message(msg, Store(), publisher, settings())
    assert msg.events[-1] == "ack"
    assert publisher.messages[-1].error_code == "3mf_conversion_timeout"
    assert publisher.messages[-1].user_message == "The 3MF conversion timed out."


@pytest.mark.asyncio
async def test_exhausted_result_publish_retries_nak_without_ack(monkeypatch):
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess",
        lambda *_args: conversion_output(),
    )
    publisher = Publisher(failures=100)
    msg = Message(command().model_dump_json().encode())
    await handle_import_request_message(msg, Store(), publisher, settings())
    assert msg.events[-1] == "nak"
    assert "ack" not in msg.events


@pytest.mark.asyncio
async def test_success_result_publish_precedes_request_ack(monkeypatch):
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess",
        lambda *_args: conversion_output(),
    )
    events: list[str] = []
    publisher = Publisher(events=events)
    msg = Message(command().model_dump_json().encode(), events)
    await handle_import_request_message(msg, Store(), publisher, settings())
    assert events[-2:] == ["publish", "ack"]


@pytest.mark.asyncio
async def test_heartbeat_repeats_and_failure_naks(monkeypatch):
    repeated = Message(b"{}")
    task = __import__("asyncio").create_task(_heartbeat(repeated, 0.3))
    await __import__("asyncio").sleep(0.25)
    task.cancel()
    await __import__("asyncio").gather(task, return_exceptions=True)
    assert repeated.events.count("heartbeat") >= 2

    async def failed_heartbeat(*_args):
        raise RuntimeError("nats heartbeat failed")

    async def blocked_execute(*_args):
        await __import__("asyncio").sleep(60)

    monkeypatch.setattr("workflows.intus.import_3mf_job._heartbeat", failed_heartbeat)
    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.execute_import_command", blocked_execute
    )
    msg = Message(command().model_dump_json().encode())
    await handle_import_request_message(msg, Store(), Publisher(), settings())
    assert msg.events == ["nak"]


@pytest.mark.asyncio
async def test_execute_cancellation_signals_and_joins_converter_thread(monkeypatch):
    import asyncio
    import threading

    started = threading.Event()
    stopped = threading.Event()

    def cancellable(_source, _timeout, cancel_event):
        started.set()
        assert cancel_event.wait(timeout=2)
        stopped.set()
        raise Import3mfError("conversion_cancelled", "cancelled")

    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess", cancellable
    )
    task = asyncio.create_task(execute_import_command(command(), Store(), settings()))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_conversion_heartbeat_publishes_recurring_progress_before_terminal(
    monkeypatch,
):
    import time

    def slow_converter(*_args):
        time.sleep(0.25)
        return conversion_output()

    monkeypatch.setattr(
        "workflows.intus.import_3mf_job.run_converter_subprocess", slow_converter
    )
    runtime = settings()
    runtime.import_3mf_ack_wait_seconds = 0.3
    publisher = Publisher()
    msg = Message(command().model_dump_json().encode())
    await handle_import_request_message(msg, Store(), publisher, runtime)
    heartbeat_calls = [
        call
        for call in publisher.calls
        if call[2]["message_id"].startswith("import-progress-heartbeat:")
    ]
    assert len(heartbeat_calls) >= 2
    terminal_index = next(
        index
        for index, call in enumerate(publisher.calls)
        if call[2]["message_id"].startswith("import-result:")
    )
    assert all(publisher.calls.index(call) < terminal_index for call in heartbeat_calls)


def test_import_launcher_is_executable_one_shot_entrypoint():
    launcher = Path(__file__).parents[1] / "start-import-3mf-job.sh"
    assert os.access(launcher, os.X_OK)
    text = launcher.read_text(encoding="utf-8")
    assert "set -eu" in text
    assert "exec python -m workflows.intus.import_3mf_job" in text
