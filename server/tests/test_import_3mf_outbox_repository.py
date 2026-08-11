from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from core.import_3mf_messages import Import3mfCommand
from core.models import Import3mfCommandOutbox, Project, ProjectImportJob
from core.object_store import ObjectRef
from core.project_assets import IMPORT_3MF_CONVERSION_VERSION, THREE_MF_MEDIA_TYPE
from core.repositories import (
    IMPORT_3MF_OUTBOX_MAX_ATTEMPTS,
    Import3mfCommandOutboxRepository,
    ProjectImportRepository,
)


def _source_ref(content: bytes = b"3mf-source") -> ObjectRef:
    digest = hashlib.sha256(content).hexdigest()
    return ObjectRef(
        bucket="project-assets",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=len(content),
    )


def _create(repo: ProjectImportRepository, seeded_tenant, *, name: str = "imported"):
    return repo.create_import_and_enqueue(
        project_name=name,
        requested_by=seeded_tenant.user_id,
        display_name="falcon9.3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=b"3mf-source",
        source_ref=_source_ref(),
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        tracestate="vendor=value",
    )


def test_create_import_and_exact_command_outbox_are_atomic(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)

    project, source, job, outbox = _create(repo, seeded_tenant)

    expected = Import3mfCommand(
        schema_version=1,
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=project.id,
        user_id=seeded_tenant.user_id,
        attempt=1,
        execution_id=job.execution_id,
        source=_source_ref(),
        conversion_version=IMPORT_3MF_CONVERSION_VERSION,
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        tracestate="vendor=value",
    )
    assert outbox.payload == expected.model_dump_json().encode("utf-8")
    identity = f"{job.id}:{job.attempt}:{job.execution_id}".encode()
    assert outbox.message_id == f"import-request:{hashlib.sha256(identity).hexdigest()}"
    assert outbox.job_id == job.id
    assert outbox.tenant_id == project.tenant_id
    assert outbox.project_id == project.id
    assert outbox.execution_id == job.execution_id
    assert outbox.status == "pending"
    assert outbox.dispatch_attempt == 0
    assert source.sha256 == expected.source.sha256

    db_session.rollback()
    assert db_session.scalar(select(Project).where(Project.id == project.id)) is None
    assert db_session.scalar(select(Import3mfCommandOutbox)) is None


def test_create_import_rolls_back_if_outbox_enqueue_fails(
    db_session, seeded_tenant, monkeypatch
):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(repo.outbox, "enqueue", fail_enqueue)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        _create(repo, seeded_tenant)

    assert db_session.scalar(select(Project).where(Project.name == "imported")) is None
    assert db_session.scalar(select(ProjectImportJob)) is None
    assert db_session.scalar(select(Import3mfCommandOutbox)) is None


def test_create_rejects_source_reference_mismatch_without_partial_state(
    db_session, seeded_tenant
):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)

    with pytest.raises(ValueError, match="source reference"):
        repo.create_import_and_enqueue(
            project_name="imported",
            requested_by=seeded_tenant.user_id,
            display_name="falcon9.3mf",
            media_type=THREE_MF_MEDIA_TYPE,
            content=b"3mf-source",
            source_ref=_source_ref(b"different"),
        )

    assert db_session.scalar(select(Project).where(Project.name == "imported")) is None
    assert db_session.scalar(select(Import3mfCommandOutbox)) is None


def test_command_identity_is_idempotent_for_same_execution(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _project, _source, job, first = _create(repo, seeded_tenant)
    command = Import3mfCommand.model_validate_json(first.payload)

    duplicate = repo.outbox.enqueue(command)

    assert duplicate.id == first.id
    assert duplicate.message_id == first.message_id
    assert (
        db_session.scalar(select(func.count()).select_from(Import3mfCommandOutbox)) == 1
    )
    assert job.execution_id == duplicate.execution_id


def test_concurrent_enqueue_returns_same_deterministic_row(
    postgres_url, db_session, seeded_tenant
):
    imports = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    project, _source, job = imports.create_import(
        project_name="imported",
        requested_by=seeded_tenant.user_id,
        display_name="falcon9.3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=b"3mf-source",
    )
    command = Import3mfCommand(
        schema_version=1,
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=project.id,
        user_id=seeded_tenant.user_id,
        attempt=job.attempt,
        execution_id=job.execution_id,
        source=_source_ref(),
        conversion_version=IMPORT_3MF_CONVERSION_VERSION,
    )
    db_session.commit()

    engine = create_engine(postgres_url, pool_pre_ping=True)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    barrier = threading.Barrier(2)
    row_ids = []
    errors: list[Exception] = []

    def enqueue() -> None:
        try:
            with SessionFactory() as session:
                barrier.wait(timeout=10)
                row = Import3mfCommandOutboxRepository(session).enqueue(command)
                session.commit()
                row_ids.append(row.id)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=enqueue, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    engine.dispose()

    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert len(row_ids) == 2
    assert len(set(row_ids)) == 1


def test_retry_enqueues_new_execution_in_same_transaction(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _project, _source, job, first = _create(repo, seeded_tenant)
    old_execution = job.execution_id
    repo.mark_failed(
        job.id,
        old_execution,
        error="worker stopped",
        error_code="worker_lost",
        user_message="Try again.",
        retryable=True,
    )

    retried, second = repo.retry_and_enqueue(job.id, source_ref=_source_ref())

    assert retried.attempt == 2
    assert retried.execution_id != old_execution
    assert second.execution_id == retried.execution_id
    assert second.message_id != first.message_id
    assert Import3mfCommand.model_validate_json(second.payload).attempt == 2
    assert first.status == "failed"
    assert first.error_code == "superseded"
    db_session.rollback()


def test_retry_rolls_back_if_new_outbox_enqueue_fails(
    db_session, seeded_tenant, monkeypatch
):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _project, _source, job, _first = _create(repo, seeded_tenant)
    original_execution = job.execution_id
    repo.mark_failed(
        job.id,
        original_execution,
        error="worker stopped",
        error_code="worker_lost",
        user_message="Try again.",
        retryable=True,
    )

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(repo.outbox, "enqueue", fail_enqueue)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        repo.retry_and_enqueue(job.id, source_ref=_source_ref())

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempt == 1
    assert job.execution_id == original_execution
    assert job.retryable is True


def test_outbox_command_identity_and_payload_are_immutable(db_session, seeded_tenant):
    _project, _source, _job, outbox = _create(
        ProjectImportRepository(db_session, seeded_tenant.tenant_id), seeded_tenant
    )

    outbox.payload = b"{}"
    with pytest.raises(RuntimeError, match="immutable"):
        db_session.flush()


def test_concurrent_claim_uses_skip_locked_and_claims_each_row_once(
    postgres_url, db_session, seeded_tenant
):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _create(repo, seeded_tenant, name="first")
    _create(repo, seeded_tenant, name="second")
    db_session.commit()

    engine = create_engine(postgres_url, pool_pre_ping=True)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    barrier = threading.Barrier(2)
    claimed: list[tuple[str, str]] = []
    errors: list[Exception] = []

    def claim(owner: str) -> None:
        try:
            with SessionFactory() as session:
                barrier.wait(timeout=10)
                rows = Import3mfCommandOutboxRepository(session).claim_batch(
                    lease_owner=owner,
                    lease_duration=timedelta(minutes=1),
                    limit=1,
                )
                session.commit()
                claimed.extend((owner, row.message_id) for row in rows)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=claim, args=("dispatcher-a",), daemon=True),
        threading.Thread(target=claim, args=("dispatcher-b",), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    engine.dispose()

    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert len(claimed) == 2
    assert len({message_id for _owner, message_id in claimed}) == 2


def test_expired_lease_is_reclaimed_and_stale_owner_cannot_complete(
    db_session, seeded_tenant
):
    _project, _source, _job, outbox = _create(
        ProjectImportRepository(db_session, seeded_tenant.tenant_id), seeded_tenant
    )
    claims = Import3mfCommandOutboxRepository(db_session)
    first = claims.claim_batch(
        lease_owner="dispatcher-a", lease_duration=timedelta(seconds=1), limit=1
    )[0]
    first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    reclaimed = claims.claim_batch(
        lease_owner="dispatcher-b", lease_duration=timedelta(minutes=1), limit=1
    )[0]

    assert reclaimed.id == outbox.id
    assert reclaimed.lease_owner == "dispatcher-b"
    assert reclaimed.dispatch_attempt == 2
    assert (
        claims.mark_sent(outbox.id, lease_owner="dispatcher-a", dispatch_attempt=1)
        is False
    )
    assert (
        claims.mark_sent(outbox.id, lease_owner="dispatcher-b", dispatch_attempt=2)
        is True
    )
    assert reclaimed.status == "sent"


def test_dispatch_attempt_fences_reused_lease_owner(db_session, seeded_tenant):
    _project, _source, _job, outbox = _create(
        ProjectImportRepository(db_session, seeded_tenant.tenant_id), seeded_tenant
    )
    repo = Import3mfCommandOutboxRepository(db_session)
    first = repo.claim_batch(
        lease_owner="dispatcher", lease_duration=timedelta(seconds=1), limit=1
    )[0]
    first_attempt = first.dispatch_attempt
    first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.flush()

    reclaimed = repo.claim_batch(
        lease_owner="dispatcher", lease_duration=timedelta(minutes=1), limit=1
    )[0]

    assert reclaimed.dispatch_attempt == first_attempt + 1
    assert (
        repo.mark_sent(
            outbox.id,
            lease_owner="dispatcher",
            dispatch_attempt=first_attempt,
        )
        is False
    )
    assert (
        repo.mark_sent(
            outbox.id,
            lease_owner="dispatcher",
            dispatch_attempt=reclaimed.dispatch_attempt,
        )
        is True
    )


def test_expired_final_lease_is_terminalized(db_session, seeded_tenant):
    _project, _source, _job, outbox = _create(
        ProjectImportRepository(db_session, seeded_tenant.tenant_id), seeded_tenant
    )
    outbox.dispatch_attempt = IMPORT_3MF_OUTBOX_MAX_ATTEMPTS - 1
    db_session.flush()
    repo = Import3mfCommandOutboxRepository(db_session)
    final_claim = repo.claim_batch(
        lease_owner="dispatcher", lease_duration=timedelta(seconds=1), limit=1
    )[0]
    final_claim.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.flush()

    assert (
        repo.claim_batch(
            lease_owner="other", lease_duration=timedelta(minutes=1), limit=1
        )
        == []
    )
    assert outbox.status == "failed"
    assert outbox.error_code == "attempts_exhausted"


def test_failure_backoff_and_attempts_are_bounded_without_raw_payload_in_error(
    db_session, seeded_tenant
):
    _project, _source, _job, outbox = _create(
        ProjectImportRepository(db_session, seeded_tenant.tenant_id), seeded_tenant
    )
    repo = Import3mfCommandOutboxRepository(db_session)

    for attempt in range(1, IMPORT_3MF_OUTBOX_MAX_ATTEMPTS + 1):
        claimed = repo.claim_batch(
            lease_owner="dispatcher", lease_duration=timedelta(minutes=1), limit=1
        )[0]
        before = datetime.now(timezone.utc)
        assert repo.mark_failed(
            claimed.id,
            lease_owner="dispatcher",
            dispatch_attempt=claimed.dispatch_attempt,
            error_code="nats_unavailable",
            now=before,
        )
        if attempt < IMPORT_3MF_OUTBOX_MAX_ATTEMPTS:
            assert claimed.status == "pending"
            assert claimed.available_at > before
            claimed.available_at = before - timedelta(seconds=1)
            db_session.flush()
        else:
            assert claimed.status == "failed"

    assert outbox.dispatch_attempt == IMPORT_3MF_OUTBOX_MAX_ATTEMPTS
    assert outbox.error_code == "nats_unavailable"
    assert outbox.payload.decode("utf-8") not in (outbox.error_code or "")
    with pytest.raises(ValueError, match="error code"):
        repo.mark_failed(
            outbox.id,
            lease_owner="dispatcher",
            dispatch_attempt=outbox.dispatch_attempt,
            error_code="x" * 65,
        )
