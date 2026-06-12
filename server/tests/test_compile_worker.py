import asyncio
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from core.compile_messages import CompileCommand, CompileResultEvent
from core.models import Artifact, CompileJob, now_utc
from workflows.intus.compile_executor import execute_compile_job


def test_execute_compile_job_records_artifact_and_success(db_session, seeded_tenant, monkeypatch, tmp_path):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    output_path = tmp_path / "output.stl"
    output_path.write_bytes(b"solid worker")

    def fake_run_compile_sandbox(project_dir: Path, export_format: str, timeout_seconds: int):
        assert timeout_seconds == 600
        assert export_format == "stl"
        assert (project_dir / "design.py").exists()
        return SimpleNamespace(success=True, output_path=output_path, stdout="", stderr="", error=None)

    monkeypatch.setattr("workflows.intus.compile_executor.run_compile_sandbox", fake_run_compile_sandbox)

    event = execute_compile_job(db_session, job.id, timeout_seconds=600, artifact_retention_limit=10)

    persisted_job = db_session.get(CompileJob, job.id)
    artifact = db_session.scalar(select(Artifact).where(Artifact.compile_job_id == job.id))
    assert persisted_job.status == "succeeded"
    assert artifact.content == b"solid worker"
    assert event.status == "succeeded"
    assert event.artifact_id == artifact.id


def test_execute_compile_job_records_failure(db_session, seeded_tenant, monkeypatch):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    def fake_run_compile_sandbox(project_dir: Path, export_format: str, timeout_seconds: int):
        return SimpleNamespace(success=False, output_path=None, stdout="", stderr="boom", error="boom")

    monkeypatch.setattr("workflows.intus.compile_executor.run_compile_sandbox", fake_run_compile_sandbox)

    event = execute_compile_job(db_session, job.id, timeout_seconds=600, artifact_retention_limit=10)

    persisted_job = db_session.get(CompileJob, job.id)
    assert persisted_job.status == "failed"
    assert persisted_job.error == "boom"
    assert persisted_job.error_code == "sandbox_error"
    assert persisted_job.user_message == "Compile failed. Fix the model source and try again."
    assert persisted_job.retryable is True
    assert db_session.scalar(select(Artifact)) is None
    assert event.status == "failed"
    assert event.error_code == "sandbox_error"
    assert event.user_message == "Compile failed. Fix the model source and try again."
    assert event.error == "boom"
    assert event.retryable is True


def command_payload(job, seeded_tenant, export_format="stl"):
    return CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format=export_format,
        created_at=now_utc(),
    ).model_dump_json().encode("utf-8")


class FakeMsg:
    def __init__(self, data: bytes):
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


def test_worker_publishes_success_subject_and_acks(monkeypatch, db_session, seeded_tenant):
    from workflows.intus.compile_worker import handle_compile_message

    published = []
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()
    settings = SimpleNamespace(
        compile_timeout_seconds=600,
        artifact_retention_limit=10,
        compile_succeeded_subject="tertius.compile.succeeded",
        compile_failed_subject="tertius.compile.failed",
    )

    def fake_execute(db, job_id, timeout_seconds, artifact_retention_limit):
        return SimpleNamespace(status="succeeded", model_dump_json=lambda: "{}")

    class FakePublisher:
        async def publish_json(self, subject, event):
            published.append(subject)

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)

    msg = FakeMsg(command_payload(job, seeded_tenant))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), settings))

    assert published == ["tertius.compile.succeeded"]
    assert msg.acked is True
    assert msg.naked is False
    assert msg.termed is False


def test_worker_publishes_failed_subject_and_acks(monkeypatch, db_session, seeded_tenant):
    from workflows.intus.compile_worker import handle_compile_message

    published = []
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()
    settings = SimpleNamespace(
        compile_timeout_seconds=600,
        artifact_retention_limit=10,
        compile_succeeded_subject="tertius.compile.succeeded",
        compile_failed_subject="tertius.compile.failed",
    )

    def fake_execute(db, job_id, timeout_seconds, artifact_retention_limit):
        return CompileResultEvent(
            job_id=job_id,
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            status="failed",
            export_format="stl",
            error_code="sandbox_error",
            user_message="Compile failed. Fix the model source and try again.",
            error="boom",
            retryable=True,
            finished_at=now_utc(),
        )

    class FakePublisher:
        async def publish_json(self, subject, event):
            published.append((subject, event))

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)

    msg = FakeMsg(command_payload(job, seeded_tenant))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), settings))

    assert published[0][0] == "tertius.compile.failed"
    assert published[0][1].user_message == "Compile failed. Fix the model source and try again."
    assert published[0][1].retryable is True
    assert msg.acked is True


def test_worker_rejects_mismatched_command_identity(db_session, seeded_tenant):
    from workflows.intus.compile_worker import handle_compile_message

    published = []
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()
    settings = SimpleNamespace(
        compile_timeout_seconds=600,
        artifact_retention_limit=10,
        compile_succeeded_subject="tertius.compile.succeeded",
        compile_failed_subject="tertius.compile.failed",
    )

    class FakePublisher:
        async def publish_json(self, subject, event):
            published.append((subject, event))

    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), settings))

    persisted = db_session.get(CompileJob, job.id)
    assert persisted.status == "failed"
    assert persisted.error_code == "invalid_command"
    assert persisted.retryable is False
    assert published[0][0] == "tertius.compile.failed"
    assert msg.acked is True
