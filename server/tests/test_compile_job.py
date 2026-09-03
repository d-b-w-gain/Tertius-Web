import asyncio
import hashlib
import importlib
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from pydantic import BaseModel

from core.compile_artifacts import decode_compile_artifact
from core.compile_messages import (
    CompileBinaryAsset,
    CompileCommand,
    CompileSourceFile,
    serialized_message_size,
)
from core.object_store import (
    ObjectIntegrityError,
    ObjectRef,
    ObjectStoreUnavailableError,
)


def command_payload(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": uuid4(),
        "requested_by": uuid4(),
        "export_format": "stl",
        "created_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
        "files": [
            CompileSourceFile(filename="design.py", content="shape = 'queued'\n")
        ],
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


def write_compiled_design(tmp_path, **overrides):
    payload = {
        "schema_version": "1.0",
        "compiled_design_digest": "d" * 64,
        "products": [],
        "components": [],
        "connections": [],
        "unmanaged_geometry": [],
        "readiness": {
            "mechanical_graph_valid": True,
            "procurement_complete": False,
            "structural_model_complete": False,
            "structural_verified": False,
            "release_ready": False,
        },
        "diagnostics": [],
    }
    payload.update(overrides)
    path = tmp_path / "tertius-compiled-design.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_workbench_artifacts(tmp_path, **compiled_overrides):
    compiled_path = write_compiled_design(tmp_path, **compiled_overrides)
    compiled = json.loads(compiled_path.read_text(encoding="utf-8"))
    digest = compiled["compiled_design_digest"]
    schemas = {
        "procurement": "tertius.procurement.v1",
        "structural": "tertius.structural.v1",
        "drawing": "tertius.drawing.v1",
        "bounds": "tertius.bounds.v1",
    }
    paths = {"compiled_design": compiled_path}
    for kind, schema in schemas.items():
        path = tmp_path / f"tertius-{kind}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": schema,
                    "compiled_design_digest": digest,
                    "projection_digest": kind[0] * 64,
                }
            ),
            encoding="utf-8",
        )
        paths[kind] = path
    return paths


def artifact_content(result, kind: str) -> bytes:
    artifact = next(item for item in result.artifacts if item.kind == kind)
    return decode_compile_artifact(artifact)


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
        self.compress_flags = []

    async def publish_json(
        self,
        subject: str,
        message: BaseModel,
        message_id: str | None = None,
        *,
        compress: bool = False,
    ) -> None:
        if self.fail:
            raise RuntimeError("publish failed")
        self.published.append((subject, message, message_id))
        self.compress_flags.append(compress)


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
    artifact_paths = write_workbench_artifacts(tmp_path)

    def fake_run_compile_sandbox(
        project_dir, export_format, quality=None, timeout_seconds=30
    ):
        assert (project_dir / "design.py").read_text() == "shape = 'queued'\n"
        assert export_format == "stl"
        assert quality is None
        assert timeout_seconds == 600
        return SimpleNamespace(
            success=True,
            output_path=output_path,
            artifact_paths=artifact_paths,
            stdout="",
            stderr="",
            error=None,
        )

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox
    )
    msg = FakeMsg(command_payload())
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    assert msg.acked is True
    assert msg.naked is False
    subject, result, message_id = publisher.published[0]
    assert subject == "tertius.compile.result"
    assert result.status == "succeeded"
    assert artifact_content(result, "stl") == b"solid job"
    assert {artifact.kind for artifact in result.artifacts} == {
        "stl",
        "compiled_design",
        "procurement",
        "structural",
        "drawing",
        "bounds",
    }
    assert message_id == f"compile-result:{result.job_id}:succeeded"
    assert publisher.compress_flags == [True]


def test_compile_job_fetches_and_hydrates_3mf_sidecar(monkeypatch, tmp_path):
    from workflows.intus.compile_job import handle_compile_request_message

    content = b"PK\x03\x04fixture-3mf"
    digest = hashlib.sha256(content).hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=len(content),
    )
    output_path = tmp_path / "output.glb"
    output_path.write_bytes(b"glb")
    artifact_paths = write_workbench_artifacts(tmp_path)

    class FakeObjectStore:
        async def get(self, requested_ref):
            assert requested_ref == ref
            return content

    def fake_run_compile_sandbox(
        project_dir, export_format, quality=None, timeout_seconds=30
    ):
        assert (project_dir / "source.3mf").read_bytes() == content
        return SimpleNamespace(
            success=True,
            output_path=output_path,
            artifact_paths=artifact_paths,
            stdout="",
            stderr="",
            error=None,
        )

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox
    )
    msg = FakeMsg(
        command_payload(
            assets=[CompileBinaryAsset(logical_filename="source.3mf", object_ref=ref)]
        )
    )
    publisher = FakePublisher()

    asyncio.run(
        handle_compile_request_message(
            msg, publisher, job_settings(), FakeObjectStore()
        )
    )

    assert msg.acked is True
    assert msg.naked is False
    assert publisher.published[0][1].status == "succeeded"


def test_compile_job_reports_sidecar_integrity_failure_without_running_sandbox(
    monkeypatch,
):
    from workflows.intus.compile_job import handle_compile_request_message

    digest = "c" * 64
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )

    class BrokenObjectStore:
        async def get(self, requested_ref):
            raise ObjectIntegrityError("object integrity check failed")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("sandbox must not run")

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox", fail_if_called
    )
    msg = FakeMsg(
        command_payload(
            assets=[CompileBinaryAsset(logical_filename="source.3mf", object_ref=ref)]
        )
    )
    publisher = FakePublisher()

    asyncio.run(
        handle_compile_request_message(
            msg, publisher, job_settings(), BrokenObjectStore()
        )
    )

    assert msg.acked is True
    assert msg.naked is False
    result = publisher.published[0][1]
    assert result.status == "failed"
    assert result.error_code == "invalid_binary_asset"
    assert result.retryable is False


def test_compile_job_reports_sidecar_transport_failure_without_running_sandbox(
    monkeypatch,
):
    from workflows.intus.compile_job import handle_compile_request_message

    digest = "d" * 64
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )

    class UnavailableObjectStore:
        async def get(self, requested_ref):
            assert requested_ref == ref
            raise ObjectStoreUnavailableError("object store operation failed")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("sandbox must not run")

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox", fail_if_called
    )
    msg = FakeMsg(
        command_payload(
            assets=[CompileBinaryAsset(logical_filename="source.3mf", object_ref=ref)]
        )
    )
    publisher = FakePublisher()

    asyncio.run(
        handle_compile_request_message(
            msg, publisher, job_settings(), UnavailableObjectStore()
        )
    )

    assert msg.acked is True
    assert msg.naked is False
    result = publisher.published[0][1]
    assert result.status == "failed"
    assert result.error_code == "binary_asset_unavailable"
    assert result.retryable is True


def test_compile_job_naks_when_sidecar_outage_result_publish_fails(monkeypatch):
    from workflows.intus.compile_job import handle_compile_request_message

    digest = "e" * 64
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )

    class UnavailableObjectStore:
        async def get(self, requested_ref):
            raise ObjectStoreUnavailableError("object store operation failed")

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sandbox must not run")
        ),
    )
    msg = FakeMsg(
        command_payload(
            assets=[CompileBinaryAsset(logical_filename="source.3mf", object_ref=ref)]
        )
    )

    asyncio.run(
        handle_compile_request_message(
            msg, FakePublisher(fail=True), job_settings(), UnavailableObjectStore()
        )
    )

    assert msg.acked is False
    assert msg.naked is True


def test_run_once_preserves_python_only_path_without_opening_object_store(monkeypatch):
    import workflows.intus.compile_job as compile_job

    msg = FakeMsg(command_payload())

    class FakeSubscription:
        async def fetch(self, batch, timeout):
            return [msg]

    class FakeConnection:
        def jetstream(self):
            return SimpleNamespace()

        async def close(self):
            pass

    async def fake_connect(_url):
        return FakeConnection()

    async def fake_ensure(nc, settings):
        return SimpleNamespace()

    async def fake_pull(js, settings):
        return FakeSubscription()

    async def fail_if_opened(*args, **kwargs):
        raise AssertionError("object store must remain unused")

    async def fake_handle(message, publisher, settings, object_store=None):
        assert message is msg
        assert object_store is None

    monkeypatch.setattr(
        compile_job, "get_settings", lambda: SimpleNamespace(nats_url="nats://test")
    )
    monkeypatch.setattr(compile_job, "configure_telemetry", lambda *args: None)
    monkeypatch.setattr(compile_job, "connect_nats", fake_connect)
    monkeypatch.setattr(compile_job, "ensure_compile_stream", fake_ensure)
    monkeypatch.setattr(compile_job, "pull_compile_subscription", fake_pull)
    monkeypatch.setattr(compile_job, "open_compile_sidecar_store", fail_if_opened)
    monkeypatch.setattr(compile_job, "handle_compile_request_message", fake_handle)

    assert asyncio.run(compile_job.run_once()) == 0


def test_run_once_publishes_retryable_result_when_object_store_open_fails(monkeypatch):
    import workflows.intus.compile_job as compile_job

    digest = "f" * 64
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )
    msg = FakeMsg(
        command_payload(
            assets=[CompileBinaryAsset(logical_filename="source.3mf", object_ref=ref)]
        )
    )
    published = []

    class FakeSubscription:
        async def fetch(self, batch, timeout):
            return [msg]

    class FakeJetStream:
        pass

    class FakeConnection:
        async def close(self):
            pass

    class RecordingPublisher(FakePublisher):
        async def publish_json(
            self,
            subject,
            message,
            message_id=None,
            *,
            compress=False,
        ):
            published.append((subject, message, message_id))

    async def fake_connect(_url):
        return FakeConnection()

    async def fake_ensure(nc, settings):
        return FakeJetStream()

    async def fake_pull(js, settings):
        return FakeSubscription()

    async def unavailable_store(js, settings):
        raise ObjectStoreUnavailableError("object store open failed")

    monkeypatch.setattr(
        compile_job,
        "get_settings",
        lambda: job_settings(nats_url="nats://test"),
    )
    monkeypatch.setattr(compile_job, "configure_telemetry", lambda *args: None)
    monkeypatch.setattr(compile_job, "connect_nats", fake_connect)
    monkeypatch.setattr(compile_job, "ensure_compile_stream", fake_ensure)
    monkeypatch.setattr(compile_job, "pull_compile_subscription", fake_pull)
    monkeypatch.setattr(compile_job, "open_compile_sidecar_store", unavailable_store)
    monkeypatch.setattr(compile_job, "NatsPublisher", lambda js: RecordingPublisher())
    monkeypatch.setattr(
        compile_job,
        "run_compile_sandbox",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sandbox must not run")
        ),
    )

    assert asyncio.run(compile_job.run_once()) == 0
    assert msg.acked is True
    assert msg.naked is False
    assert len(published) == 1
    assert published[0][1].error_code == "binary_asset_unavailable"
    assert published[0][1].retryable is True


def test_compile_job_attaches_hashed_compiled_design_graph(
    monkeypatch,
    tmp_path,
):
    from core.compile_runtime import runtime_files_hash
    from workflows.intus.compile_job import handle_compile_request_message

    output_path = tmp_path / "output.glb"
    output_path.write_bytes(b"glb")
    artifact_paths = write_workbench_artifacts(
        tmp_path,
        products=[{"key": "fixture", "definition_digest": "p" * 64}],
    )

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            output_path=output_path,
            artifact_paths=artifact_paths,
            stdout="",
            stderr="",
            error=None,
        ),
    )
    design_source = "shape = 'queued'\n"
    source_files = [
        CompileSourceFile(filename="design.py", content=design_source),
        CompileSourceFile(filename="catalog.json.py", content='{"version":"1"}'),
    ]
    msg = FakeMsg(command_payload(export_format="glb", files=source_files))
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    result = publisher.published[0][1]
    compiled = json.loads(artifact_content(result, "compiled_design"))
    assert compiled["source_snapshot_hash"] == runtime_files_hash(
        {file.filename: file.content for file in source_files}
    )
    assert compiled["products"] == [{"key": "fixture", "definition_digest": "p" * 64}]


def test_compile_job_rejects_missing_workbench_bundle(
    monkeypatch,
    tmp_path,
):
    from workflows.intus.compile_job import handle_compile_request_message

    output_path = tmp_path / "output.glb"
    output_path.write_bytes(b"glb")
    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            output_path=output_path,
            artifact_paths={},
            stdout="",
            stderr="",
            error=None,
        ),
    )
    source_files = [
        CompileSourceFile(filename="design.py", content="shape = 'queued'\n"),
        CompileSourceFile(filename="parts.py", content="PART = 'fixture'\n"),
    ]
    msg = FakeMsg(command_payload(export_format="glb", files=source_files))
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    result = publisher.published[0][1]
    assert result.status == "failed"
    assert result.error_code == "missing_artifact_bundle"


def test_compile_job_allows_timus_settings_sidecar(monkeypatch, tmp_path):
    from workflows.intus.compile_job import handle_compile_request_message

    output_path = tmp_path / "output.timus_views"
    output_path.write_text("{}", encoding="utf-8")
    artifact_paths = write_workbench_artifacts(tmp_path)

    def fake_run_compile_sandbox(
        project_dir, export_format, quality=None, timeout_seconds=30
    ):
        assert (project_dir / "design.py").exists()
        assert (project_dir / "settings.json").read_text(
            encoding="utf-8"
        ) == '{"sheet_size":"A4"}'
        assert export_format == "timus_views"
        return SimpleNamespace(
            success=True,
            output_path=output_path,
            artifact_paths=artifact_paths,
            stdout="",
            stderr="",
            error=None,
        )

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox
    )
    msg = FakeMsg(
        command_payload(
            export_format="timus_views",
            files=[
                CompileSourceFile(filename="design.py", content="shape = 'queued'\n"),
                CompileSourceFile(
                    filename="settings.json", content='{"sheet_size":"A4"}'
                ),
            ],
        )
    )
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    assert msg.acked is True
    result = publisher.published[0][1]
    assert result.status == "succeeded"
    assert artifact_content(result, "timus_views") == b"{}"


def test_compile_job_publishes_failure_and_acks(monkeypatch):
    from workflows.intus.compile_job import handle_compile_request_message

    def fake_run_compile_sandbox(
        project_dir, export_format, quality=None, timeout_seconds=30
    ):
        return SimpleNamespace(
            success=False, output_path=None, stdout="", stderr="boom", error="boom"
        )

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox
    )
    msg = FakeMsg(command_payload())
    publisher = FakePublisher()

    asyncio.run(handle_compile_request_message(msg, publisher, job_settings()))

    assert msg.acked is True
    result = publisher.published[0][1]
    assert result.status == "failed"
    assert result.error_code == "sandbox_error"
    assert result.retryable is True


def test_compile_job_keeps_event_loop_responsive_during_compile(monkeypatch):
    import workflows.intus.compile_job as compile_job

    started = threading.Event()
    release = threading.Event()

    def blocked_execute(command, settings):
        started.set()
        if not release.wait(timeout=2):
            raise AssertionError("event loop did not release the compile thread")
        return compile_job._failed_result(
            command,
            compile_job.now_utc(),
            error="expected fixture failure",
            error_code="sandbox_error",
            user_message="Compile failed. Fix the model source and try again.",
            retryable=True,
        )

    monkeypatch.setattr(compile_job, "execute_compile_command", blocked_execute)
    msg = FakeMsg(command_payload())
    publisher = FakePublisher()

    async def run_scenario():
        operation = asyncio.create_task(
            compile_job.handle_compile_request_message(msg, publisher, job_settings())
        )
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 0.5), timeout=1)
        release.set()
        await asyncio.wait_for(operation, timeout=1)

    asyncio.run(run_scenario())

    assert msg.acked is True
    assert publisher.published[0][1].error == "expected fixture failure"


def test_compile_job_truncates_huge_sandbox_error_to_publish_failure(monkeypatch):
    from workflows.intus.compile_job import handle_compile_request_message

    huge_stderr = "sandbox exploded\n" + ("x" * 20_000)

    def fake_run_compile_sandbox(
        project_dir, export_format, quality=None, timeout_seconds=30
    ):
        return SimpleNamespace(
            success=False, output_path=None, stdout="", stderr=huge_stderr, error=""
        )

    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox", fake_run_compile_sandbox
    )
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
    artifact_paths = write_workbench_artifacts(tmp_path)
    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            output_path=output_path,
            artifact_paths=artifact_paths,
            stdout="",
            stderr="",
            error=None,
        ),
    )
    msg = FakeMsg(command_payload())

    asyncio.run(
        handle_compile_request_message(msg, FakePublisher(fail=True), job_settings())
    )

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
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )

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
    artifact_paths = write_workbench_artifacts(tmp_path)
    monkeypatch.setattr(
        "workflows.intus.compile_job.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            output_path=output_path,
            artifact_paths=artifact_paths,
            stdout="",
            stderr="",
            error=None,
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
