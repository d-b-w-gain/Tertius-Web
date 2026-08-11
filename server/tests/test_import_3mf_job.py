from __future__ import annotations

import hashlib
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
    def __init__(self, *, fail_get: bool = False):
        self.fail_get = fail_get
        self.puts: list[bytes] = []

    async def get(self, _ref):
        if self.fail_get:
            raise ObjectStoreUnavailableError("transient")
        return SOURCE

    async def put(self, value: bytes):
        self.puts.append(value)
        return ref(value)


class Publisher:
    def __init__(self, failures: int = 0, events: list[str] | None = None):
        self.failures = failures
        self.messages = []
        self.events = events if events is not None else []

    async def publish_json(self, _subject, message, **_kwargs):
        self.events.append("publish")
        if self.failures:
            self.failures -= 1
            raise RuntimeError("nats unavailable")
        self.messages.append(message)


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
        lambda source, timeout: output,
    )
    store = Store()
    progress = []

    async def report(value):
        progress.append((value.stage, value.percent))

    result = await execute_import_command(command(), store, settings(), report)

    assert result.status == "succeeded"
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
async def test_malformed_command_is_terminated_without_echoing_payload(caplog):
    secret = "private-source-name.3mf"
    msg = Message(f'{{"filename":"{secret}"}}'.encode())

    await handle_import_request_message(msg, Store(), Publisher(), settings())

    assert msg.events == ["term"]
    assert secret not in caplog.text
