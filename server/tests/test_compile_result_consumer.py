import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pydantic import BaseModel
from sqlalchemy import select

from core.compile_messages import CompileResultPayload
from core.models import Artifact, CompileJob, CompileJobFile, CompileUsageRecord
from core.models import now_utc


def result_payload(job, seeded_tenant, **overrides):
    payload = {
        "job_id": job.id,
        "tenant_id": seeded_tenant.tenant_id,
        "project_id": seeded_tenant.project_id,
        "export_format": job.export_format,
        "status": "succeeded",
        "artifact_content_base64": base64.b64encode(b"solid result").decode("ascii"),
        "artifact_byte_size": len(b"solid result"),
        "artifact_content_type": "model/stl",
        "worker_started_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
        "worker_finished_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return CompileResultPayload(**payload)


def consumer_settings():
    return SimpleNamespace(
        artifact_retention_limit=10,
        compile_result_max_bytes=8 * 1024 * 1024,
        compile_request_max_bytes=8 * 1024 * 1024,
        compile_request_subject="tertius.compile.request",
        compile_result_subject="tertius.compile.result",
        compile_result_consumer="compile-result-api",
        nats_url="nats://test",
        billing_rate_cents_per_hour=100,
        billing_format_multiplier_stl=1.0,
        billing_format_multiplier_step=1.5,
        billing_format_multiplier_gltf=2.0,
        billing_format_multiplier_glb=2.0,
    )


def test_apply_compile_result_records_artifact_and_marks_success(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    applied = apply_compile_result(db_session, result_payload(job, seeded_tenant), consumer_settings())

    artifact = db_session.scalar(select(Artifact).where(Artifact.compile_job_id == job.id))
    persisted = db_session.get(CompileJob, job.id)
    assert applied is True
    assert persisted.status == "succeeded"
    assert artifact.content == b"solid result"
    assert artifact.content_type == "model/stl"


def test_apply_compile_result_records_failure(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    payload = result_payload(
        job,
        seeded_tenant,
        status="failed",
        artifact_content_base64=None,
        artifact_byte_size=None,
        artifact_content_type=None,
        error_code="timeout",
        user_message="Compile timed out after 10 minutes. Try again.",
        error="timed out",
        retryable=True,
    )

    applied = apply_compile_result(db_session, payload, consumer_settings())

    persisted = db_session.get(CompileJob, job.id)
    assert applied is True
    assert persisted.status == "failed"
    assert persisted.error_code == "timeout"
    assert persisted.retryable is True
    assert db_session.scalar(select(Artifact).where(Artifact.compile_job_id == job.id)) is None


def test_apply_compile_result_acks_duplicate_terminal_without_changes(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    applied = apply_compile_result(db_session, result_payload(job, seeded_tenant), consumer_settings())

    assert applied is False
    assert db_session.scalar(select(Artifact).where(Artifact.compile_job_id == job.id)) is None


def test_apply_compile_result_ignores_identity_mismatch_without_mutating_job(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    payload = result_payload(job, seeded_tenant, export_format="glb")

    applied = apply_compile_result(db_session, payload, consumer_settings())

    persisted = db_session.get(CompileJob, job.id)
    assert applied is False
    assert persisted.status == "running"
    assert persisted.error_code is None


def test_apply_compile_result_marks_malformed_success_failed(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    payload = result_payload(job, seeded_tenant, artifact_content_base64=None, artifact_byte_size=12)

    applied = apply_compile_result(db_session, payload, consumer_settings())

    persisted = db_session.get(CompileJob, job.id)
    assert applied is True
    assert persisted.status == "failed"
    assert persisted.error_code == "invalid_result"
    assert persisted.retryable is True


def test_result_consumer_acks_after_successful_db_commit(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import handle_compile_result_message

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="stl",
    )
    db_session.add(job)
    db_session.commit()

    class FakeMsg:
        data = result_payload(job, seeded_tenant).model_dump_json().encode("utf-8")
        acked = False
        naked = False

        async def ack(self):
            self.acked = True

        async def nak(self):
            self.naked = True

        async def term(self):
            raise AssertionError("valid result should not be termed")

    msg = FakeMsg()
    asyncio.run(handle_compile_result_message(msg, db_session, consumer_settings()))

    assert msg.acked is True
    assert msg.naked is False


def test_republish_stale_queued_jobs_uses_snapshot_files_without_claiming(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import republish_stale_queued_jobs

    stale_job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
        error_code="publish_pending",
        created_at=now_utc() - timedelta(minutes=5),
    )
    fresh_job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="step",
        created_at=now_utc(),
    )
    db_session.add_all([stale_job, fresh_job])
    db_session.flush()
    db_session.add(
        CompileJobFile(
            compile_job_id=stale_job.id,
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            filename="design.py",
            content="shape = 'stale'\n",
        )
    )
    db_session.commit()
    published = []

    class FakePublisher:
        async def publish_json(self, subject: str, command, message_id: str | None = None) -> None:
            published.append((subject, command, message_id))

    republished = asyncio.run(
        republish_stale_queued_jobs(db_session, FakePublisher(), consumer_settings(), older_than_seconds=60)
    )

    assert republished == 1
    assert len(published) == 1
    subject, command, message_id = published[0]
    assert subject == "tertius.compile.request"
    assert command.job_id == stale_job.id
    assert command.files[0].filename == "design.py"
    assert command.files[0].content == "shape = 'stale'\n"
    assert message_id == f"compile-request:{stale_job.id}"
    assert command.request_id == message_id
    persisted = db_session.get(CompileJob, stale_job.id)
    assert persisted is not None
    assert persisted.status == "queued"
    assert persisted.claim_token is None


def test_republish_stale_queued_jobs_does_not_duplicate_unmarked_queued_jobs(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import republish_stale_queued_jobs

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
        created_at=now_utc() - timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        CompileJobFile(
            compile_job_id=job.id,
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            filename="design.py",
            content="shape = 'in flight'\n",
        )
    )
    db_session.commit()
    published = []

    class FakePublisher:
        async def publish_json(self, subject: str, command, message_id: str | None = None) -> None:
            published.append((subject, command, message_id))

    republished = asyncio.run(
        republish_stale_queued_jobs(db_session, FakePublisher(), consumer_settings(), older_than_seconds=60)
    )

    persisted = db_session.get(CompileJob, job.id)
    assert persisted is not None
    assert republished == 0
    assert published == []
    assert persisted.status == "queued"
    assert persisted.error_code is None


def test_republish_stale_queued_jobs_marks_oversized_snapshot_failed(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import republish_stale_queued_jobs

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
        error_code="publish_pending",
        created_at=now_utc() - timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        CompileJobFile(
            compile_job_id=job.id,
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            filename="design.py",
            content="shape = 'too large'\n",
        )
    )
    db_session.commit()
    published = []

    class FakePublisher:
        async def publish_json(self, subject: str, command, message_id: str | None = None) -> None:
            published.append((subject, command, message_id))

    settings = consumer_settings()
    settings.compile_request_max_bytes = 20

    republished = asyncio.run(
        republish_stale_queued_jobs(db_session, FakePublisher(), settings, older_than_seconds=60)
    )

    persisted = db_session.get(CompileJob, job.id)
    assert persisted is not None
    assert republished == 0
    assert published == []
    assert persisted.status == "failed"
    assert persisted.error_code == "source_bundle_too_large"
    assert persisted.retryable is False


def test_republish_stale_queued_jobs_marks_missing_snapshot_failed(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import republish_stale_queued_jobs

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="stl",
        error_code="publish_pending",
        created_at=now_utc() - timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()
    published = []

    class FakePublisher:
        async def publish_json(self, subject: str, command, message_id: str | None = None) -> None:
            published.append((subject, command, message_id))

    republished = asyncio.run(
        republish_stale_queued_jobs(db_session, FakePublisher(), consumer_settings(), older_than_seconds=60)
    )

    persisted = db_session.get(CompileJob, job.id)
    assert persisted is not None
    assert republished == 0
    assert published == []
    assert persisted.status == "failed"
    assert persisted.error_code == "missing_snapshot"
    assert persisted.retryable is True


def test_result_consumer_retries_when_initial_nats_setup_fails(monkeypatch):
    import workflows.intus.compile_result_consumer as consumer

    stop_event = asyncio.Event()
    attempts = {"connect": 0}

    class FakeConnection:
        async def close(self):
            pass

    class FakeSubscription:
        async def fetch(self, batch, timeout):
            stop_event.set()
            raise TimeoutError

    async def fake_connect_nats(url):
        attempts["connect"] += 1
        if attempts["connect"] == 1:
            raise RuntimeError("nats unavailable")
        return FakeConnection()

    async def fake_ensure_compile_stream(nc, settings):
        return object()

    async def fake_pull_compile_result_subscription(js, settings):
        return FakeSubscription()

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(consumer, "get_settings", lambda: consumer_settings())
    monkeypatch.setattr(consumer, "connect_nats", fake_connect_nats)
    monkeypatch.setattr(consumer, "ensure_compile_stream", fake_ensure_compile_stream)
    monkeypatch.setattr(consumer, "pull_compile_result_subscription", fake_pull_compile_result_subscription)
    monkeypatch.setattr(consumer.asyncio, "sleep", fake_sleep)

    asyncio.run(consumer.run_result_consumer(stop_event))

    assert attempts["connect"] == 2


def test_fail_stale_running_jobs_marks_expired_leases_retryable(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import fail_stale_running_jobs

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="stl",
        lease_expires_at=now_utc() - timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()

    failed = fail_stale_running_jobs(db_session)

    persisted = db_session.get(CompileJob, job.id)
    assert failed == 1
    assert persisted.status == "failed"
    assert persisted.error_code == "worker_lost"
    assert persisted.retryable is True


def test_apply_compile_result_creates_usage_record_on_success(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()

    applied = apply_compile_result(db_session, result_payload(job, seeded_tenant), consumer_settings())

    assert applied is True
    usage = db_session.scalar(
        select(CompileUsageRecord).where(CompileUsageRecord.compile_job_id == job.id)
    )
    assert usage is not None
    assert usage.status == "succeeded"
    assert usage.export_format == "glb"
    assert usage.compute_duration_seconds == 0.0
    assert usage.cost_cents == 0
    assert usage.base_rate_cents_per_hour == 100
    assert usage.format_multiplier == 2.0
    assert usage.artifact_byte_size == len(b"solid result")


def test_apply_compile_result_creates_usage_record_on_failure(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="step",
    )
    db_session.add(job)
    db_session.commit()

    payload = result_payload(
        job,
        seeded_tenant,
        status="failed",
        artifact_content_base64=None,
        artifact_byte_size=None,
        artifact_content_type=None,
        error_code="timeout",
        error="timed out",
    )

    applied = apply_compile_result(db_session, payload, consumer_settings())

    assert applied is True
    usage = db_session.scalar(
        select(CompileUsageRecord).where(CompileUsageRecord.compile_job_id == job.id)
    )
    assert usage is not None
    assert usage.status == "failed"
    assert usage.artifact_byte_size == 0
    assert usage.format_multiplier == 1.5


def test_apply_compile_result_uses_api_timestamps_for_usage_duration(db_session, seeded_tenant):
    from workflows.intus.compile_result_consumer import apply_compile_result

    claimed_at = datetime(2026, 6, 14, 10, 0, tzinfo=timezone.utc)
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="stl",
        claimed_at=claimed_at,
    )
    db_session.add(job)
    db_session.commit()

    payload = result_payload(
        job,
        seeded_tenant,
        worker_started_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        worker_finished_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )

    applied = apply_compile_result(db_session, payload, consumer_settings())

    usage = db_session.scalar(
        select(CompileUsageRecord).where(CompileUsageRecord.compile_job_id == job.id)
    )
    persisted = db_session.get(CompileJob, job.id)
    assert applied is True
    assert usage.compute_duration_seconds == (persisted.finished_at - claimed_at).total_seconds()
    assert usage.compute_duration_seconds > 0


def test_record_usage_is_idempotent_for_compile_job(db_session, seeded_tenant):
    from core.repositories import CompileRepository

    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="stl",
    )
    db_session.add(job)
    db_session.flush()

    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    first = repo.record_usage(
        project_id=job.project_id,
        compile_job_id=job.id,
        requested_by=job.requested_by,
        export_format=job.export_format,
        status=job.status,
        compute_duration_seconds=1.0,
        artifact_byte_size=10,
        cost_cents=1,
        base_rate_cents_per_hour=100,
        format_multiplier=1.0,
    )
    second = repo.record_usage(
        project_id=job.project_id,
        compile_job_id=job.id,
        requested_by=job.requested_by,
        export_format=job.export_format,
        status=job.status,
        compute_duration_seconds=2.0,
        artifact_byte_size=20,
        cost_cents=2,
        base_rate_cents_per_hour=100,
        format_multiplier=1.0,
    )

    records = db_session.scalars(
        select(CompileUsageRecord).where(CompileUsageRecord.compile_job_id == job.id)
    ).all()
    assert second.id == first.id
    assert len(records) == 1
    assert records[0].compute_duration_seconds == 1.0
