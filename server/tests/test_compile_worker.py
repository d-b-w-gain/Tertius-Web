import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from core.compile_messages import CompileCommand, CompileResultEvent
from core.models import Artifact, CompileJob, now_utc
from core.repositories import CompileRepository
from workflows.intus.compile_executor import execute_compile_job


def worker_settings():
    return SimpleNamespace(
        compile_timeout_seconds=600,
        compile_ack_wait_seconds=660,
        artifact_retention_limit=10,
        compile_succeeded_subject="tertius.compile.succeeded",
        compile_failed_subject="tertius.compile.failed",
    )


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
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    repo.snapshot_job_files(job, {"design.py": "shape = 'snapshot'\n"})
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="stl",
        created_at=job.created_at,
    )
    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    db_session.commit()

    output_path = tmp_path / "output.stl"
    output_path.write_bytes(b"solid worker")

    def fake_run_compile_sandbox(project_dir: Path, export_format: str, timeout_seconds: int):
        assert timeout_seconds == 600
        assert export_format == "stl"
        assert (project_dir / "design.py").read_text() == "shape = 'snapshot'\n"
        return SimpleNamespace(success=True, output_path=output_path, stdout="", stderr="", error=None)

    monkeypatch.setattr("workflows.intus.compile_executor.run_compile_sandbox", fake_run_compile_sandbox)

    event = execute_compile_job(
        db_session,
        job.id,
        claim_token=claimed.claim_token,
        timeout_seconds=600,
        artifact_retention_limit=10,
    )

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
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    repo.snapshot_job_files(job, {"design.py": "shape = 'snapshot'\n"})
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="stl",
        created_at=job.created_at,
    )
    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    db_session.commit()

    def fake_run_compile_sandbox(project_dir: Path, export_format: str, timeout_seconds: int):
        return SimpleNamespace(success=False, output_path=None, stdout="", stderr="boom", error="boom")

    monkeypatch.setattr("workflows.intus.compile_executor.run_compile_sandbox", fake_run_compile_sandbox)

    event = execute_compile_job(
        db_session,
        job.id,
        claim_token=claimed.claim_token,
        timeout_seconds=600,
        artifact_retention_limit=10,
    )

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


def test_execute_compile_job_rolls_back_output_when_claim_is_lost(db_session, seeded_tenant, monkeypatch, tmp_path):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    repo.snapshot_job_files(job, {"design.py": "shape = 'snapshot'\n"})
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )
    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    lost_token = claimed.claim_token
    claimed.claim_token = uuid4()
    db_session.commit()

    output_path = tmp_path / "model.glb"
    output_path.write_bytes(b"stale")
    monkeypatch.setattr(
        "workflows.intus.compile_executor.run_compile_sandbox",
        lambda *args, **kwargs: SimpleNamespace(success=True, output_path=output_path, error=None, stderr=None),
    )

    event = execute_compile_job(
        db_session,
        job.id,
        claim_token=lost_token,
        timeout_seconds=600,
        artifact_retention_limit=10,
    )

    assert event is None
    persisted = db_session.get(CompileJob, job.id)
    assert persisted.status == "running"
    assert persisted.claim_token != lost_token
    assert db_session.scalar(select(Artifact).where(Artifact.compile_job_id == job.id)) is None


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
        self.nak_delay = None
        self.termed = False

    async def ack(self):
        self.acked = True

    async def nak(self, delay=None):
        self.naked = True
        self.nak_delay = delay

    async def term(self):
        self.termed = True


class FakePublisher:
    async def publish_json(self, subject, event, message_id=None):
        pass


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
    settings = worker_settings()

    def fake_execute(db, job_id, claim_token, timeout_seconds, artifact_retention_limit):
        return SimpleNamespace(job_id=job_id, status="succeeded", model_dump_json=lambda: "{}")

    class FakePublisher:
        async def publish_json(self, subject, event, message_id=None):
            published.append((subject, message_id))

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)

    msg = FakeMsg(command_payload(job, seeded_tenant))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), settings))

    assert published == [("tertius.compile.succeeded", f"compile-result:{job.id}:succeeded")]
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
    settings = worker_settings()

    def fake_execute(db, job_id, claim_token, timeout_seconds, artifact_retention_limit):
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
        async def publish_json(self, subject, event, message_id=None):
            published.append((subject, event, message_id))

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)

    msg = FakeMsg(command_payload(job, seeded_tenant))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), settings))

    assert published[0][0] == "tertius.compile.failed"
    assert published[0][1].user_message == "Compile failed. Fix the model source and try again."
    assert published[0][1].retryable is True
    assert published[0][2] == f"compile-result:{job.id}:failed"
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
    settings = worker_settings()

    class FakePublisher:
        async def publish_json(self, subject, event, message_id=None):
            published.append((subject, event, message_id))

    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), settings))

    persisted = db_session.get(CompileJob, job.id)
    assert persisted.status == "failed"
    assert persisted.error_code == "invalid_command"
    assert persisted.retryable is False
    assert published[0][0] == "tertius.compile.failed"
    assert published[0][2] == f"compile-result:{job.id}:failed"
    assert msg.acked is True


def test_worker_acks_duplicate_terminal_job_without_reexecuting(db_session, seeded_tenant, monkeypatch):
    from workflows.intus.compile_worker import handle_compile_message

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()
    called = False

    def fake_execute(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)
    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), worker_settings()))

    assert msg.acked is True
    assert called is False


def test_worker_republishes_terminal_job_on_duplicate_redelivery(db_session, seeded_tenant, monkeypatch):
    from workflows.intus.compile_worker import handle_compile_message

    published = []
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="glb",
        finished_at=now_utc(),
    )
    db_session.add(job)
    db_session.commit()
    called = False

    def fake_execute(*args, **kwargs):
        nonlocal called
        called = True

    class FakePublisher:
        async def publish_json(self, subject, event, message_id=None):
            published.append((subject, event, message_id))

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)
    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), worker_settings()))

    assert msg.acked is True
    assert called is False
    assert published[0][0] == "tertius.compile.succeeded"
    assert published[0][1].job_id == job.id
    assert published[0][2] == f"compile-result:{job.id}:succeeded"


def test_worker_acks_duplicate_running_job_with_active_lease_without_reexecuting(db_session, seeded_tenant, monkeypatch):
    from workflows.intus.compile_worker import handle_compile_message

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="glb",
        claim_token=uuid4(),
        claimed_at=now_utc(),
        lease_expires_at=now_utc() + timedelta(seconds=600),
        attempt_count=1,
    )
    db_session.add(job)
    db_session.commit()
    called = False

    def fake_execute(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)
    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), worker_settings()))

    assert msg.acked is False
    assert msg.naked is True
    assert called is False


def test_worker_acks_without_publish_when_executor_loses_claim(db_session, seeded_tenant, monkeypatch):
    from workflows.intus.compile_worker import handle_compile_message

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()
    published = []

    def fake_execute(*args, **kwargs):
        return None

    class FakePublisher:
        async def publish_json(self, subject, event):
            published.append((subject, event))

    monkeypatch.setattr("workflows.intus.compile_worker.execute_compile_job", fake_execute)
    msg = FakeMsg(command_payload(job, seeded_tenant, export_format="glb"))
    asyncio.run(handle_compile_message(msg, db_session, FakePublisher(), worker_settings()))

    assert msg.acked is True
    assert published == []


def test_worker_republishes_stale_queued_jobs(db_session, seeded_tenant):
    from workflows.intus.compile_worker import republish_stale_queued_jobs

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="glb",
    )
    db_session.add(job)
    db_session.flush()
    job.created_at = now_utc() - timedelta(minutes=5)
    db_session.commit()

    published = []

    class FakePublisher:
        async def publish_json(self, subject, command):
            published.append((subject, command))

    settings = SimpleNamespace(compile_request_subject="tertius.compile.request")
    asyncio.run(republish_stale_queued_jobs(db_session, FakePublisher(), settings, older_than_seconds=60))

    assert len(published) == 1
    assert published[0][0] == "tertius.compile.request"
    assert published[0][1].job_id == job.id
