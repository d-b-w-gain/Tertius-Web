import asyncio
import base64
import hashlib
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from pydantic import BaseModel

from core.compile_messages import CompileCommand, CompileSourceFile, serialized_message_size


def command_payload(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": uuid4(),
        "requested_by": uuid4(),
        "export_format": "stl",
        "created_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
        "files": [CompileSourceFile(filename="design.py", content="shape = 'queued'\n")],
        "request_id": "compile-request:test",
    }
    payload.update(overrides)
    return CompileCommand.model_validate(payload).model_dump_json().encode("utf-8")


def job_settings(**overrides):
    settings = {
        "compile_timeout_seconds": 600,
        "compile_result_max_bytes": 8 * 1024 * 1024,
        "compile_result_subject": "tertius.compile.result",
    }
    settings.update(overrides)
    return SimpleNamespace(**settings)


class FakeMsg:
    def __init__(self, data):
        self.data = data
        self.acked = False
        self.naked = False
        self.termed = False

    async def ack(self):
        self.acked = True

    async def nak(self):
        self.naked = True

    async def term(self):
        self.termed = True


class FakePublisher:
    def __init__(self, fail=False):
        self.fail = fail
        self.published = []

    async def publish_json(self, subject: str, message: BaseModel, message_id: str | None = None) -> None:
        if self.fail:
            raise RuntimeError("publish failed")
        self.published.append((subject, message, message_id))


def test_compile_job_module_does_not_import_db_bound_executor():
    module = importlib.import_module("workflows.intus.compile_job")

    source_names = set(module.__dict__)
    assert "SessionLocal" not in source_names
    assert "CompileRepository" not in source_names
    assert "execute_compile_job" not in source_names


def test_compile_job_publishes_success_and_acks(monkeypatch, tmp_path):
    from workflows.intus.compile_job import handle_compile_request_message

    output_path = tmp_path / "output.stl"
    output_path.write_bytes(b"solid job")

    def fake_run_compile_sandbox(project_dir, export_format, quality=None, timeout_seconds=30):
        assert (project_dir / "design.py").read_text() == "shape = 'queued'\n"
        assert export_format == "stl"
        assert quality is None
        assert timeout_seconds == 600
        return SimpleNamespace(success=True, output_path=output_path, stdout="", stderr="", error=None)

    monkeypatch.setattr("workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox)
    msg = FakeMsg(command_payload())
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    assert msg.acked is True
    assert msg.naked is False
    subject, result, message_id = publisher.published[0]
    assert subject == "tertius.compile.result"
    assert result.status == "succeeded"
    assert base64.b64decode(result.artifact_content_base64) == b"solid job"
    assert result.artifact_byte_size == len(b"solid job")
    assert message_id == f"compile-result:{result.job_id}:succeeded"


def test_compile_job_allows_timus_settings_sidecar(monkeypatch, tmp_path):
    from workflows.intus.compile_job import handle_compile_request_message

    output_path = tmp_path / "output.timus_views"
    output_path.write_text("{}", encoding="utf-8")

    def fake_run_compile_sandbox(project_dir, export_format, quality=None, timeout_seconds=30):
        assert (project_dir / "design.py").exists()
        assert (project_dir / "settings.json").read_text(encoding="utf-8") == '{"sheet_size":"A4"}'
        assert export_format == "timus_views"
        return SimpleNamespace(success=True, output_path=output_path, stdout="", stderr="", error=None)

    monkeypatch.setattr("workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox)
    msg = FakeMsg(command_payload(
        export_format="timus_views",
        files=[
            CompileSourceFile(filename="design.py", content="shape = 'queued'\n"),
            CompileSourceFile(filename="settings.json", content='{"sheet_size":"A4"}'),
        ],
    ))
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    assert msg.acked is True
    result = publisher.published[0][1]
    assert result.status == "succeeded"
    assert base64.b64decode(result.artifact_content_base64) == b"{}"


def test_compile_job_publishes_failure_and_acks(monkeypatch):
    from workflows.intus.compile_job import handle_compile_request_message

    def fake_run_compile_sandbox(project_dir, export_format, quality=None, timeout_seconds=30):
        return SimpleNamespace(success=False, output_path=None, stdout="", stderr="boom", error="boom")

    monkeypatch.setattr("workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox)
    msg = FakeMsg(command_payload())
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    assert msg.acked is True
    result = publisher.published[0][1]
    assert result.status == "failed"
    assert result.error_code == "sandbox_error"
    assert result.retryable is True


def test_compile_job_truncates_huge_sandbox_error_to_publish_failure(monkeypatch):
    from workflows.intus.compile_job import handle_compile_request_message

    huge_stderr = "sandbox exploded\n" + ("x" * 20_000)

    def fake_run_compile_sandbox(project_dir, export_format, quality=None, timeout_seconds=30):
        return SimpleNamespace(success=False, output_path=None, stdout="", stderr=huge_stderr, error="")

    monkeypatch.setattr("workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox)
    msg = FakeMsg(command_payload())
    publisher = FakePublisher()
    settings = job_settings(compile_result_max_bytes=1200)

    asyncio.run(handle_compile_request_message(msg, publisher, settings))

    assert msg.acked is True
    assert msg.naked is False
    result = publisher.published[0][1]
    assert result.status == "failed"
    assert result.error.endswith("[truncated]")
    assert len(result.error) < len(huge_stderr)
    assert serialized_message_size(result) <= settings.compile_result_max_bytes


def test_compile_job_does_not_ack_when_result_publish_fails(monkeypatch, tmp_path):
    from workflows.intus.compile_job import handle_compile_request_message

    output_path = tmp_path / "output.stl"
    output_path.write_bytes(b"solid job")
    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(success=True, output_path=output_path, stdout="", stderr="", error=None),
    )
    msg = FakeMsg(command_payload())

    asyncio.run(handle_compile_request_message(msg, FakePublisher(fail=True), job_settings()))

    assert msg.acked is False
    assert msg.naked is True


def test_compile_job_terms_invalid_command():
    from workflows.intus.compile_job import handle_compile_request_message

    msg = FakeMsg(b"not json")

    asyncio.run(handle_compile_request_message(msg, FakePublisher(), job_settings()))

    assert msg.termed is True
    assert msg.acked is False


def test_compile_job_span_records_originating_llm_edit_job_hash(monkeypatch, tmp_path):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    from workflows.intus.compile_job import handle_compile_request_message

    class ListExporter(SpanExporter):
        def __init__(self):
            self.spans = []

        def export(self, spans):
            self.spans.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            return None

        def force_flush(self, timeout_millis=30000):
            return True

    exporter = ListExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "workflows.intus.compile_job.get_tracer",
        lambda name: provider.get_tracer(name),
    )

    output_path = tmp_path / "output.stl"
    output_path.write_bytes(b"solid job")
    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(
            success=True, output_path=output_path, stdout="", stderr="", error=None
        ),
    )

    llm_job_id = uuid4()
    msg = FakeMsg(command_payload(originating_llm_edit_job_id=str(llm_job_id)))
    asyncio.run(handle_compile_request_message(msg, FakePublisher(), job_settings()))

    consume_spans = [
        s for s in exporter.spans if s.name == "NATS consume tertius.compile.request"
    ]
    assert len(consume_spans) == 1
    attributes = dict(consume_spans[0].attributes or {})
    expected_hash = hashlib.sha256(str(llm_job_id).encode("ascii")).hexdigest()[:16]
    assert attributes.get("tertius.originating_llm_edit_job_hash") == expected_hash
    assert "tertius.originating_llm_edit_job_id" not in attributes
    assert attributes.get("tertius.export_format") == "stl"
