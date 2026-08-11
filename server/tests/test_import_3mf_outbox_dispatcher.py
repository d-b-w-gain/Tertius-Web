from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.import_3mf_messages import Import3mfCommand
from core.models import ProjectImportJob
from core.object_store import ObjectRef
from core.project_assets import THREE_MF_MEDIA_TYPE
from core.repositories import ProjectImportRepository


def _command() -> Import3mfCommand:
    return Import3mfCommand(
        schema_version=1,
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        attempt=1,
        execution_id=uuid4(),
        source=ObjectRef(
            bucket="TERTIUS_ASSETS",
            key=f"sha256/{'a' * 64}",
            sha256="a" * 64,
            byte_size=3,
        ),
        conversion_version="tertius-3mf-brep-v1-build123d-0.8.0",
    )


@pytest.mark.asyncio
async def test_dispatch_commits_claim_before_publish_and_marks_sent(monkeypatch):
    from workflows.intus import import_3mf_outbox_dispatcher as dispatcher

    command = _command()
    row = SimpleNamespace(
        id=uuid4(),
        payload=command.model_dump_json().encode(),
        message_id="import-request:" + "b" * 64,
        dispatch_attempt=1,
    )
    events = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

    sessions = iter((Session(), Session()))

    class Repo:
        calls = 0

        def __init__(self, _db):
            pass

        def claim_batch(self, **_kwargs):
            Repo.calls += 1
            return [row]

        def mark_sent(self, outbox_id, *, lease_owner, dispatch_attempt):
            assert outbox_id == row.id
            assert lease_owner == "dispatcher-a"
            assert dispatch_attempt == row.dispatch_attempt
            events.append("mark_sent")
            return True

    class Publisher:
        async def publish_json(self, subject, message, *, message_id):
            assert events == ["commit"]
            assert subject == "tertius.import.3mf.request"
            assert message == command
            assert message.model_dump_json().encode() == row.payload
            assert message_id == row.message_id
            events.append("publish")

    monkeypatch.setattr(dispatcher, "Import3mfCommandOutboxRepository", Repo)
    outcome = await dispatcher.dispatch_import_outbox_once(
        lambda: next(sessions),
        Publisher(),
        SimpleNamespace(
            import_3mf_request_subject="tertius.import.3mf.request",
            import_3mf_outbox_lease_seconds=30,
            import_3mf_outbox_batch_size=10,
        ),
        lease_owner="dispatcher-a",
    )

    assert outcome.claimed == outcome.sent == 1
    assert outcome.failed == 0
    assert events == ["commit", "publish", "mark_sent", "commit"]


@pytest.mark.asyncio
async def test_publish_failure_is_persisted_with_fixed_error_code(monkeypatch):
    from workflows.intus import import_3mf_outbox_dispatcher as dispatcher

    command = _command()
    row = SimpleNamespace(
        id=uuid4(),
        payload=command.model_dump_json().encode(),
        message_id="import-request:" + "c" * 64,
        dispatch_attempt=2,
    )
    marked = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    class Repo:
        claims = 0

        def __init__(self, _db):
            pass

        def claim_batch(self, **_kwargs):
            Repo.claims += 1
            return [row] if Repo.claims == 1 else []

        def mark_failed(self, outbox_id, *, lease_owner, dispatch_attempt, error_code):
            marked.append((outbox_id, lease_owner, dispatch_attempt, error_code))
            return True

    class Publisher:
        async def publish_json(self, *_args, **_kwargs):
            raise RuntimeError("private NATS details")

    monkeypatch.setattr(dispatcher, "Import3mfCommandOutboxRepository", Repo)
    outcome = await dispatcher.dispatch_import_outbox_once(
        Session,
        Publisher(),
        SimpleNamespace(
            import_3mf_request_subject="tertius.import.3mf.request",
            import_3mf_outbox_lease_seconds=30,
            import_3mf_outbox_batch_size=10,
        ),
        lease_owner="dispatcher-a",
    )

    assert outcome.claimed == outcome.failed == 1
    assert outcome.sent == 0
    assert marked == [(row.id, "dispatcher-a", row.dispatch_attempt, "publish_failed")]


@pytest.mark.asyncio
async def test_reclaimed_delivery_reuses_identical_nats_message_id(monkeypatch):
    from workflows.intus import import_3mf_outbox_dispatcher as dispatcher

    command = _command()
    row = SimpleNamespace(
        id=uuid4(),
        payload=command.model_dump_json().encode(),
        message_id="import-request:" + "d" * 64,
        dispatch_attempt=3,
    )
    message_ids = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    class Repo:
        def __init__(self, _db):
            pass

        def claim_batch(self, **_kwargs):
            return [row]

        def mark_sent(self, *_args, **_kwargs):
            return False

    class Publisher:
        async def publish_json(self, _subject, _message, *, message_id):
            message_ids.append(message_id)

    monkeypatch.setattr(dispatcher, "Import3mfCommandOutboxRepository", Repo)
    settings = SimpleNamespace(
        import_3mf_request_subject="tertius.import.3mf.request",
        import_3mf_outbox_lease_seconds=30,
        import_3mf_outbox_batch_size=10,
    )
    await dispatcher.dispatch_import_outbox_once(
        Session, Publisher(), settings, lease_owner="crashed-owner"
    )
    await dispatcher.dispatch_import_outbox_once(
        Session, Publisher(), settings, lease_owner="reclaim-owner"
    )

    assert message_ids == [row.message_id, row.message_id]


@pytest.mark.asyncio
async def test_main_starts_and_stops_independent_outbox_dispatcher(monkeypatch):
    import asyncio
    import main

    started = []

    async def dispatcher(stop):
        started.append("outbox")
        await stop.wait()

    monkeypatch.setattr(main, "run_import_outbox_dispatcher", dispatcher)
    await main.start_import_3mf_outbox_dispatcher()
    await asyncio.sleep(0)
    assert started == ["outbox"]
    await main.stop_import_3mf_outbox_dispatcher()


@pytest.mark.asyncio
async def test_immediate_dispatch_observes_committed_import_job(
    postgres_url, db_session, seeded_tenant
):
    from workflows.intus.import_3mf_outbox_dispatcher import (
        dispatch_import_outbox_once,
    )

    source = b"source"
    source_digest = sha256(source).hexdigest()
    source_ref = ObjectRef(
        bucket="TERTIUS_ASSETS",
        key=f"sha256/{source_digest}",
        sha256=source_digest,
        byte_size=len(source),
    )
    _project, _asset, job, _outbox = ProjectImportRepository(
        db_session, seeded_tenant.tenant_id
    ).create_import_and_enqueue(
        project_name="fast_result",
        requested_by=seeded_tenant.user_id,
        display_name="fast.3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=source,
        source_ref=source_ref,
    )
    job_id = job.id
    db_session.commit()

    engine = create_engine(postgres_url, pool_pre_ping=True)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    observed = []

    class Publisher:
        async def publish_json(self, _subject, message, *, message_id):
            with SessionFactory() as independent:
                observed.append(
                    independent.scalar(
                        select(ProjectImportJob).where(
                            ProjectImportJob.id == message.job_id
                        )
                    )
                )
            assert message_id.startswith("import-request:")

    try:
        outcome = await dispatch_import_outbox_once(
            SessionFactory,
            Publisher(),
            SimpleNamespace(
                import_3mf_request_subject="tertius.import.3mf.request",
                import_3mf_outbox_lease_seconds=30,
                import_3mf_outbox_batch_size=10,
            ),
            lease_owner="fast-dispatcher",
        )
    finally:
        engine.dispose()

    assert outcome.sent == 1
    assert observed[0] is not None
    assert observed[0].id == job_id
