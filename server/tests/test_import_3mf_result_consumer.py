from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.import_3mf_messages import (
    Import3mfCommand,
    Import3mfProgress,
    Import3mfResult,
)
from core.object_store import ObjectRef, ObjectStoreUnavailableError
from core.project_assets import IMPORT_3MF_CONVERSION_VERSION, public_manifest_summary
from workflows.intus.import_3mf_result_consumer import (
    handle_import_result_message,
    reconcile_stale_import_jobs,
)
from test_import_3mf_job import SOURCE, conversion_output, ref


def command() -> Import3mfCommand:
    return Import3mfCommand(
        schema_version=1,
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        attempt=2,
        execution_id=uuid4(),
        source=ref(SOURCE),
        conversion_version=IMPORT_3MF_CONVERSION_VERSION,
    )


def success_result(
    cmd: Import3mfCommand,
) -> tuple[Import3mfResult, dict[ObjectRef, bytes]]:
    output = conversion_output()
    manifest_bytes = output.manifest.model_dump_json().encode()
    result = Import3mfResult.success_for(
        cmd,
        brep=ref(output.brep_bytes),
        manifest=ref(manifest_bytes),
        summary=public_manifest_summary(output.manifest),
        duration_ms=10,
    )
    return result, {result.brep: output.brep_bytes, result.manifest: manifest_bytes}


class Message:
    def __init__(self, data: bytes):
        self.data = data
        self.headers = None
        self.subject = "tertius.import.3mf.result"
        self.events: list[str] = []

    async def ack(self):
        self.events.append("ack")

    async def nak(self):
        self.events.append("nak")

    async def term(self):
        self.events.append("term")


class Store:
    def __init__(self, values, *, transient=False):
        self.values = values
        self.transient = transient

    async def get(self, key):
        if self.transient:
            raise ObjectStoreUnavailableError("temporary")
        return self.values[key]


class DB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def settings():
    return SimpleNamespace(
        project_asset_object_bucket="TERTIUS_ASSETS",
        import_3mf_message_max_bytes=1024 * 1024,
        import_3mf_running_lease_seconds=360,
    )


@pytest.mark.asyncio
async def test_success_verifies_manifest_and_uses_locked_requester(monkeypatch):
    cmd = command()
    result, values = success_result(cmd)
    captured = {}

    job = SimpleNamespace(
        status="running",
        requested_by=cmd.user_id,
        attempt=cmd.attempt,
        execution_id=cmd.execution_id,
        project_id=cmd.project_id,
    )
    source = SimpleNamespace(sha256=cmd.source.sha256)

    class Repo:
        def __init__(self, _db, tenant_id):
            assert tenant_id == cmd.tenant_id

        def apply_success(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer.ProjectImportRepository", Repo
    )
    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._load_result_job",
        lambda _db, _result: (job, source),
    )
    db = DB()
    msg = Message(result.model_dump_json().encode())

    await handle_import_result_message(msg, db, Store(values), settings())

    assert msg.events == ["ack"]
    assert db.commits == 1
    assert captured["user_id"] == cmd.user_id
    assert captured["brep_content"] == values[result.brep]


@pytest.mark.asyncio
async def test_duplicate_and_stale_results_ack_without_fetch(monkeypatch):
    cmd = command()
    result, _ = success_result(cmd)

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._load_result_job",
        lambda _db, _result: None,
    )
    msg = Message(result.model_dump_json().encode())
    await handle_import_result_message(msg, DB(), Store({}, transient=True), settings())
    assert msg.events == ["ack"]


@pytest.mark.asyncio
async def test_invalid_manifest_becomes_safe_terminal_failure(monkeypatch):
    cmd = command()
    result, values = success_result(cmd)
    values[result.manifest] = b'{"source_name":"private.3mf"}'
    failed = {}

    job = SimpleNamespace(status="running", requested_by=cmd.user_id)

    class Repo:
        def __init__(self, *_args):
            pass

        def mark_failed(self, *_args, **kwargs):
            failed.update(kwargs)

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer.ProjectImportRepository", Repo
    )
    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._load_result_job",
        lambda _db, _result: (job, SimpleNamespace()),
    )
    msg = Message(result.model_dump_json().encode())
    db = DB()
    await handle_import_result_message(msg, db, Store(values), settings())
    assert msg.events == ["ack"]
    assert failed["error_code"] == "invalid_result"
    assert "private.3mf" not in str(failed)


@pytest.mark.asyncio
async def test_transient_fetch_naks_and_rolls_back(monkeypatch):
    cmd = command()
    result, _ = success_result(cmd)

    job = SimpleNamespace(status="running", requested_by=cmd.user_id)

    class Repo:
        def __init__(self, *_args):
            pass

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer.ProjectImportRepository", Repo
    )
    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._load_result_job",
        lambda _db, _result: (job, SimpleNamespace()),
    )
    db = DB()
    msg = Message(result.model_dump_json().encode())
    await handle_import_result_message(msg, db, Store({}, transient=True), settings())
    assert msg.events == ["nak"]
    assert db.rollbacks >= 1


@pytest.mark.asyncio
async def test_cross_bucket_result_is_safely_failed_without_fetch(monkeypatch):
    cmd = command()
    result, _ = success_result(cmd)
    assert result.brep is not None and result.manifest is not None
    foreign = "FOREIGN_ASSETS"
    result = result.model_copy(
        update={
            "source": result.source.model_copy(update={"bucket": foreign}),
            "brep": result.brep.model_copy(update={"bucket": foreign}),
            "manifest": result.manifest.model_copy(update={"bucket": foreign}),
        }
    )
    failed = {}
    job = SimpleNamespace(status="running", requested_by=cmd.user_id)

    class Repo:
        def __init__(self, *_args):
            pass

        def mark_failed(self, *_args, **kwargs):
            failed.update(kwargs)

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer.ProjectImportRepository", Repo
    )
    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._load_result_job",
        lambda _db, _result: (job, SimpleNamespace()),
    )
    msg = Message(result.model_dump_json().encode())
    await handle_import_result_message(msg, DB(), Store({}, transient=True), settings())
    assert msg.events == ["ack"]
    assert failed["error_code"] == "invalid_result"


@pytest.mark.asyncio
async def test_oversize_and_malformed_envelopes_are_terminated_without_echo(caplog):
    private = "customer-private-filename.3mf"
    for payload in (
        b"x" * (settings().import_3mf_message_max_bytes + 1),
        f'{{"filename":"{private}"}}'.encode(),
    ):
        msg = Message(payload)
        await handle_import_result_message(msg, DB(), Store({}), settings())
        assert msg.events == ["term"]
    assert private not in caplog.text


@pytest.mark.asyncio
async def test_terminal_worker_failure_uses_fixed_message_and_acks(monkeypatch):
    cmd = command()
    result = Import3mfResult.failure_for(
        cmd,
        error_code="invalid_3mf_geometry",
        user_message="source metadata that must not be persisted",
        duration_ms=2,
    )
    failed = {}
    job = SimpleNamespace(status="running", requested_by=cmd.user_id)

    class Repo:
        def __init__(self, *_args):
            pass

        def mark_failed(self, *_args, **kwargs):
            failed.update(kwargs)

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer.ProjectImportRepository", Repo
    )
    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._load_result_job",
        lambda _db, _result: (job, SimpleNamespace()),
    )
    msg = Message(result.model_dump_json().encode())
    await handle_import_result_message(msg, DB(), Store({}), settings())
    assert msg.events == ["ack"]
    assert failed["user_message"] == "The 3MF geometry is invalid or unsupported."
    assert "source metadata" not in str(failed)


@pytest.mark.asyncio
async def test_progress_marks_running_and_is_idempotent(monkeypatch):
    cmd = command()
    progress = Import3mfProgress.for_command(cmd, stage="converting", percent=20)
    calls = []

    class Repo:
        def __init__(self, *_args):
            pass

        def mark_running(self, job_id, execution_id):
            calls.append(("running", job_id, execution_id))

        def mark_progress(self, job_id, execution_id, value):
            calls.append(("progress", job_id, execution_id, value))

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer.ProjectImportRepository", Repo
    )
    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._progress_tenant_id",
        lambda _db, _progress: cmd.tenant_id,
    )
    msg = Message(progress.model_dump_json().encode())
    await handle_import_result_message(msg, DB(), Store({}), settings())
    assert msg.events == ["ack"]
    assert calls[0] == ("running", progress.job_id, progress.execution_id)
    assert calls[1][0:3] == ("progress", progress.job_id, progress.execution_id)


def test_reconciles_only_stale_running_jobs(monkeypatch):
    stale = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        execution_id=uuid4(),
        started_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    fresh = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        execution_id=uuid4(),
        started_at=datetime.now(timezone.utc),
    )
    failed = []
    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer._running_jobs",
        lambda _db: [stale, fresh],
    )

    class Repo:
        def __init__(self, *_args):
            pass

        def mark_failed(self, job_id, *_args, **kwargs):
            failed.append((job_id, kwargs))

    monkeypatch.setattr(
        "workflows.intus.import_3mf_result_consumer.ProjectImportRepository", Repo
    )
    db = DB()
    assert reconcile_stale_import_jobs(db, settings()) == 1
    assert failed[0][1]["retryable"] is True
    assert db.commits == 1
