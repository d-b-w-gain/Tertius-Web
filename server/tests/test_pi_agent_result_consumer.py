import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from core.models import LlmEditJob, LlmUsageRecord, ProjectFile, SourceSnapshot
from core.pi_agent_conversation import conversation_turn_from_job
from core.pi_agent_messages import (
    PiAgentChangedFile,
    PiAgentConversationContext,
    PiAgentConversationTurn,
    PiAgentResult,
    PiAgentUsage,
)
from core.repositories import LlmEditRepository
from workflows.intus.pi_agent_result_consumer import (
    apply_pi_agent_result,
    handle_pi_agent_result_message,
    observe_pi_agent_active_jobs,
    pi_agent_billing_event_id,
    reconcile_stale_pi_agent_jobs,
    republish_queued_pi_agent_jobs,
)
from workflows.intus import pi_agent_result_consumer as consumer_module


def _result(seeded_tenant, file, *, tenant_id=None):
    content = "import build123d as bd\nlength = 200\n"
    now = datetime.now(timezone.utc)
    return PiAgentResult(
        schema_version=1,
        execution_id=uuid4(),
        job_id=uuid4(),
        tenant_id=tenant_id or seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        status="succeeded",
        outcome="changed",
        provider="openai-codex",
        model="gpt-5.5",
        assistant_summary="Updated length",
        changed_files=[PiAgentChangedFile(id=file.id, filename=file.filename, content=content, sha256=sha256(content.encode()).hexdigest())],
        usage=PiAgentUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        worker_started_at=now,
        worker_finished_at=now,
    )


def _job(db_session, seeded_tenant, file, result):
    result.job_id = uuid4()
    repo = LlmEditRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(
        seeded_tenant.project_id,
        seeded_tenant.user_id,
        {"prompt": "Change length", "files": [{"id": str(file.id), "filename": file.filename, "updated_at": file.updated_at.isoformat()}], "dispatched_manifest": [{"id": str(file.id), "filename": file.filename, "updated_at": file.updated_at.isoformat(), "sha256": sha256(file.content.encode()).hexdigest()}], "metadata": {}},
    )
    job.id = result.job_id
    db_session.commit()
    return job


class Publisher:
    def __init__(self):
        self.calls = []

    async def publish_json(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class Message:
    def __init__(self, data):
        self.data = data
        self.acked = 0
        self.nacked = 0

    async def ack(self):
        self.acked += 1

    async def nak(self):
        self.nacked += 1


@pytest.mark.asyncio
async def test_valid_changed_result_stages_usage_and_terminal_job_atomically(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)
    publisher = Publisher()

    outcome = await apply_pi_agent_result(db_session, result, SimpleNamespace(billing_llm_usage_subject="billing", billing_max_bytes=524288), publisher)
    db_session.commit()

    assert outcome == "applied"
    db_session.refresh(job)
    assert job.status == "succeeded"
    assert db_session.get(ProjectFile, file.id).content.endswith("length = 200\n")
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
    assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1
    assert len(publisher.calls) == 1
    assert job.result_payload["message"] == result.assistant_summary
    turn = conversation_turn_from_job(job)
    assert turn.outcome == result.outcome
    assert turn.assistant_summary == result.assistant_summary
    assert turn.changed_files == [edit.filename for edit in result.changed_files]
    serialized = turn.model_dump_json()
    for forbidden in (
        result.changed_files[0].content,
        str(job.result_payload.get("snapshot")),
        "prompt_tokens",
        result.provider,
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_valid_no_changes_result_persists_bounded_conversation_turn(
    db_session, seeded_tenant
):
    file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    result = _result(seeded_tenant, file).model_copy(
        update={
            "outcome": "no_changes",
            "assistant_summary": "No update needed",
            "changed_files": [],
        }
    )
    job = _job(db_session, seeded_tenant, file, result)

    outcome = await apply_pi_agent_result(
        db_session,
        result,
        SimpleNamespace(
            billing_llm_usage_subject="billing", billing_max_bytes=524288
        ),
        Publisher(),
    )
    db_session.commit()

    assert outcome == "applied"
    db_session.refresh(job)
    assert job.result_payload["message"] == result.assistant_summary
    turn = conversation_turn_from_job(job)
    assert turn.outcome == result.outcome
    assert turn.assistant_summary == result.assistant_summary
    assert turn.changed_files == []
    serialized = turn.model_dump_json()
    for forbidden in (
        file.content,
        str(job.result_payload.get("snapshot")),
        "prompt_tokens",
        result.provider,
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_terminal_duplicate_has_no_writes_or_second_billing(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    _job(db_session, seeded_tenant, file, result)
    publisher = Publisher()
    assert await apply_pi_agent_result(db_session, result, SimpleNamespace(billing_llm_usage_subject="billing", billing_max_bytes=524288), publisher) == "applied"
    db_session.commit()

    assert await apply_pi_agent_result(db_session, result, SimpleNamespace(billing_llm_usage_subject="billing", billing_max_bytes=524288), publisher) == "duplicate"
    assert len(publisher.calls) == 1
    assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1


@pytest.mark.asyncio
async def test_terminal_job_records_distinct_worker_execution_without_reapplying_files(
    db_session, seeded_tenant
):
    file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    first = _result(seeded_tenant, file)
    _job(db_session, seeded_tenant, file, first)
    publisher = Publisher()
    settings = SimpleNamespace(
        billing_llm_usage_subject="billing", billing_max_bytes=524288
    )

    assert await apply_pi_agent_result(db_session, first, settings, publisher) == "applied"
    db_session.commit()
    snapshots_after_first = db_session.scalar(
        select(func.count()).select_from(SourceSnapshot)
    )

    second = first.model_copy(update={"execution_id": uuid4()})
    assert await apply_pi_agent_result(db_session, second, settings, publisher) == "duplicate"
    db_session.commit()

    assert len(publisher.calls) == 2
    assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 2
    assert (
        db_session.scalar(select(func.count()).select_from(SourceSnapshot))
        == snapshots_after_first
    )


@pytest.mark.asyncio
async def test_identity_mismatch_is_rejected_without_writes(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    valid = _result(seeded_tenant, file)
    _job(db_session, seeded_tenant, file, valid)
    invalid = valid.model_copy(update={"tenant_id": uuid4()})

    assert await apply_pi_agent_result(db_session, invalid, SimpleNamespace(), Publisher()) == "invalid"
    job = db_session.get(LlmEditJob, valid.job_id)
    assert job.status == "queued"


@pytest.mark.asyncio
async def test_concurrent_file_change_fails_job_without_staging_or_billing(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)
    file.content = "user edit\n"
    file.updated_at = datetime.now(timezone.utc)
    db_session.commit()
    publisher = Publisher()

    assert await apply_pi_agent_result(db_session, result, SimpleNamespace(billing_llm_usage_subject="billing", billing_max_bytes=524288), publisher) == "applied"
    db_session.commit()
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "file_conflict"
    assert file.content == "user edit\n"
    assert len(publisher.calls) == 1
    assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1


@pytest.mark.asyncio
async def test_billing_publish_has_no_db_transaction_and_result_revalidates_afterward(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)

    class ConcurrentPublisher:
        async def publish_json(self, *_args, **_kwargs):
            assert not db_session.in_transaction()
            changed = db_session.get(ProjectFile, file.id)
            changed.content = "concurrent edit\n"
            changed.updated_at = datetime.now(timezone.utc)
            db_session.commit()

    settings = SimpleNamespace(billing_llm_usage_subject="billing", billing_max_bytes=524288)
    assert await apply_pi_agent_result(db_session, result, settings, ConcurrentPublisher()) == "applied"
    db_session.commit()
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "file_conflict"
    assert db_session.get(ProjectFile, file.id).content == "concurrent edit\n"
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 0


@pytest.mark.asyncio
async def test_result_cannot_change_browser_requested_file_omitted_from_dispatched_manifest(db_session, seeded_tenant):
    selected = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    excluded = ProjectFile(tenant_id=seeded_tenant.tenant_id, project_id=seeded_tenant.project_id, filename="excluded.py", content="x = 1\n")
    db_session.add(excluded)
    db_session.commit()
    result = _result(seeded_tenant, excluded)
    job = _job(db_session, seeded_tenant, selected, result)
    job.request_payload["files"].append({"id": str(excluded.id), "filename": excluded.filename, "updated_at": excluded.updated_at.isoformat()})
    flag_modified(job, "request_payload")
    db_session.commit()
    assert await apply_pi_agent_result(db_session, result, SimpleNamespace(), Publisher()) == "invalid"
    assert excluded.content == "x = 1\n"


@pytest.mark.asyncio
async def test_oversize_result_is_poison_acked_without_writes(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)
    msg = Message(result.model_dump_json().encode())
    await handle_pi_agent_result_message(msg, db_session, SimpleNamespace(pi_agent_result_max_bytes=10), Publisher())
    db_session.refresh(job)
    assert msg.acked == 1 and msg.nacked == 0
    assert job.status == "queued"


@pytest.mark.asyncio
async def test_billing_failure_naks_and_rolls_back_staged_changes(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    original = file.content
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)

    class FailingPublisher:
        async def publish_json(self, *_args, **_kwargs):
            raise RuntimeError("billing down")

    msg = Message(result.model_dump_json().encode())
    settings = SimpleNamespace(pi_agent_result_max_bytes=524288, billing_llm_usage_subject="billing", billing_max_bytes=524288)
    await handle_pi_agent_result_message(msg, db_session, settings, FailingPublisher())
    db_session.expire_all()
    assert msg.nacked == 1 and msg.acked == 0
    assert db_session.get(ProjectFile, file.id).content == original
    assert db_session.get(LlmEditJob, job.id).status == "queued"


@pytest.mark.asyncio
async def test_invalid_and_terminal_duplicate_messages_are_acked(db_session, seeded_tenant):
    invalid = Message(b"not-json")
    settings = SimpleNamespace(pi_agent_result_max_bytes=524288, billing_llm_usage_subject="billing", billing_max_bytes=524288)
    await handle_pi_agent_result_message(invalid, db_session, settings, Publisher())
    assert invalid.acked == 1 and invalid.nacked == 0

    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)
    job.status = "succeeded"
    db_session.commit()
    duplicate = Message(result.model_dump_json().encode())
    publisher = Publisher()
    await handle_pi_agent_result_message(duplicate, db_session, settings, publisher)
    assert duplicate.acked == 1 and duplicate.nacked == 0
    assert len(publisher.calls) == 1
    assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1


@pytest.mark.asyncio
async def test_persisted_file_conflict_emits_one_api_terminal_metric(
    db_session, seeded_tenant, monkeypatch
):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    _job(db_session, seeded_tenant, file, result)
    file.content = "concurrent edit\n"
    file.updated_at = datetime.now(timezone.utc)
    db_session.commit()
    metrics = []
    monkeypatch.setattr(
        consumer_module,
        "counter_add",
        lambda name, value, attrs: metrics.append((name, value, attrs)),
    )
    msg = Message(result.model_dump_json().encode())
    settings = SimpleNamespace(
        pi_agent_result_max_bytes=524288,
        billing_llm_usage_subject="billing",
        billing_max_bytes=524288,
    )

    await handle_pi_agent_result_message(msg, db_session, settings, Publisher())
    await handle_pi_agent_result_message(
        Message(result.model_dump_json().encode()), db_session, settings, Publisher()
    )

    terminal = [item for item in metrics if item[0] == "tertius.pi_agent.job.terminal.count"]
    assert len(terminal) == 1
    assert terminal[0][2] == {
        "operation": "pi_agent.api",
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "status": "failed",
        "failure_category": "file_conflict",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_result_consumer_parents_to_nats_producer_header(
    db_session, seeded_tenant, monkeypatch
):
    from opentelemetry import propagate
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file).model_copy(
        update={
            "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
        }
    )
    job = _job(db_session, seeded_tenant, file, result)
    job.status = "succeeded"
    db_session.commit()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.pi-agent-result")
    monkeypatch.setattr(consumer_module.trace, "get_tracer", lambda _name: tracer)
    headers = {}
    with tracer.start_as_current_span("NATS publish result") as producer:
        propagate.inject(headers)
        producer_context = producer.get_span_context()
    msg = Message(result.model_dump_json().encode())
    msg.headers = headers
    settings = SimpleNamespace(
        pi_agent_result_max_bytes=524288,
        billing_llm_usage_subject="billing",
        billing_max_bytes=524288,
    )

    await handle_pi_agent_result_message(msg, db_session, settings, Publisher())

    consumer = next(
        span for span in exporter.get_finished_spans() if span.name == "pi_agent.result.consume"
    )
    assert consumer.context.trace_id == producer_context.trace_id
    assert consumer.parent.span_id == producer_context.span_id


@pytest.mark.asyncio
async def test_invalid_provenance_cannot_select_trace_parent_or_metric_labels(
    db_session, seeded_tenant, monkeypatch
):
    from opentelemetry import propagate
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    valid = _result(seeded_tenant, file)
    _job(db_session, seeded_tenant, file, valid)
    malicious = valid.model_copy(
        update={
            "model": "attacker-controlled-model",
            "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
        }
    )
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.invalid-provenance")
    monkeypatch.setattr(consumer_module.trace, "get_tracer", lambda _name: tracer)
    metrics = []
    monkeypatch.setattr(
        consumer_module,
        "counter_add",
        lambda name, value, attrs: metrics.append((name, value, attrs)),
    )
    headers = {}
    with tracer.start_as_current_span("untrusted producer"):
        propagate.inject(headers)
    msg = Message(malicious.model_dump_json().encode())
    msg.headers = headers

    await handle_pi_agent_result_message(
        msg,
        db_session,
        SimpleNamespace(pi_agent_result_max_bytes=524288, pi_agent_model="gpt-5.5"),
        Publisher(),
    )

    assert msg.acked == 1
    assert metrics == []
    assert [span.name for span in exporter.get_finished_spans()] == ["untrusted producer"]


@pytest.mark.asyncio
async def test_commit_failure_redelivery_reuses_deterministic_billing_event(db_session, seeded_tenant, monkeypatch):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    _job(db_session, seeded_tenant, file, result)
    settings = SimpleNamespace(pi_agent_result_max_bytes=524288, billing_llm_usage_subject="billing", billing_max_bytes=524288)
    publisher = Publisher()
    original_commit = db_session.commit
    calls = 0

    def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("commit failed")
        original_commit()

    monkeypatch.setattr(db_session, "commit", fail_once)
    first = Message(result.model_dump_json().encode())
    await handle_pi_agent_result_message(first, db_session, settings, publisher)
    assert first.nacked == 1
    second = Message(result.model_dump_json().encode())
    await handle_pi_agent_result_message(second, db_session, settings, publisher)
    assert second.acked == 1
    event_ids = [call[0][1].event_id for call in publisher.calls]
    assert event_ids == [pi_agent_billing_event_id(result.execution_id)] * 2


def test_stale_reconciliation_uses_dynamic_running_and_queued_deadlines(
    db_session, seeded_tenant, monkeypatch
):
    now = datetime.now(timezone.utc)
    settings = SimpleNamespace(pi_agent_timeout_seconds=2000, pi_agent_ack_wait_seconds=90, pi_agent_max_deliver=2, pi_agent_stream_max_age_seconds=86400)
    stale = LlmEditJob(tenant_id=seeded_tenant.tenant_id, project_id=seeded_tenant.project_id, requested_by=seeded_tenant.user_id, status="running", request_payload={"dispatched_at": (now - timedelta(seconds=4500)).isoformat()}, created_at=now - timedelta(seconds=4500))
    long_running = LlmEditJob(tenant_id=seeded_tenant.tenant_id, project_id=seeded_tenant.project_id, requested_by=seeded_tenant.user_id, status="running", request_payload={"dispatched_at": (now - timedelta(seconds=1300)).isoformat()}, created_at=now - timedelta(seconds=1300))
    queued_backlog = LlmEditJob(tenant_id=seeded_tenant.tenant_id, project_id=seeded_tenant.project_id, requested_by=seeded_tenant.user_id, status="queued", request_payload={}, created_at=now - timedelta(seconds=5000))
    db_session.add_all([stale, long_running, queued_backlog])
    db_session.commit()
    metrics = []
    monkeypatch.setattr(
        consumer_module,
        "counter_add",
        lambda name, value, attrs: metrics.append((name, value, attrs)),
    )
    assert reconcile_stale_pi_agent_jobs(db_session, settings) == 1
    db_session.refresh(stale)
    db_session.refresh(long_running)
    db_session.refresh(queued_backlog)
    assert stale.status == "failed"
    assert long_running.status == "running"
    assert queued_backlog.status == "queued"
    assert [name for name, _, _ in metrics] == [
        "tertius.pi_agent.job.stale.count",
        "tertius.pi_agent.job.terminal.count",
    ]
    assert metrics[1][2]["failure_category"] == "worker_lost"
    assert metrics[1][2]["operation"] == "pi_agent.api"


@pytest.mark.asyncio
async def test_stale_reconciliation_runs_during_sustained_message_traffic(monkeypatch):
    stop = __import__("asyncio").Event()
    reconciled = []
    billing_streams = []

    class Nc:
        async def close(self):
            pass

    class Subscription:
        async def fetch(self, **_kwargs):
            stop.set()
            return []

    class DbContext:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *_args):
            pass

    times = iter([0.0, 61.0])
    monkeypatch.setattr(consumer_module, "get_settings", lambda: SimpleNamespace(nats_url="nats://test"))
    monkeypatch.setattr(consumer_module, "connect_nats", lambda *_args: _async_value(Nc()))
    monkeypatch.setattr(consumer_module, "ensure_pi_agent_stream", lambda *_args: _async_value(object()))
    monkeypatch.setattr(
        consumer_module,
        "ensure_billing_stream",
        lambda *_args: _async_value(billing_streams.append(True)),
    )
    monkeypatch.setattr(consumer_module, "pull_pi_agent_result_subscription", lambda *_args: _async_value(Subscription()))
    monkeypatch.setattr(consumer_module, "SessionLocal", DbContext)
    monkeypatch.setattr(consumer_module, "republish_queued_pi_agent_jobs", lambda *_args: _async_value(0))
    monkeypatch.setattr(consumer_module, "reconcile_stale_pi_agent_jobs", lambda _db, _settings: reconciled.append(True))
    monkeypatch.setattr(consumer_module.asyncio, "get_running_loop", lambda: SimpleNamespace(time=lambda: next(times)))
    await consumer_module.run_pi_agent_result_consumer(stop)
    assert billing_streams == [True]
    assert reconciled == [True]


@pytest.mark.asyncio
async def test_result_consumer_heartbeat_is_bounded_and_stops_on_cancel(monkeypatch):
    heartbeat_seen = asyncio.Event()
    metrics = []

    class Nc:
        async def close(self):
            pass

    class Subscription:
        async def fetch(self, **_kwargs):
            await asyncio.sleep(0)
            raise TimeoutError

    settings = SimpleNamespace(nats_url="nats://test")
    monkeypatch.setattr(consumer_module, "get_settings", lambda: settings)
    monkeypatch.setattr(consumer_module, "connect_nats", lambda *_: _async_value(Nc()))
    monkeypatch.setattr(
        consumer_module, "ensure_pi_agent_stream", lambda *_: _async_value(object())
    )
    monkeypatch.setattr(
        consumer_module, "ensure_billing_stream", lambda *_: _async_value(object())
    )
    monkeypatch.setattr(
        consumer_module,
        "pull_pi_agent_result_subscription",
        lambda *_: _async_value(Subscription()),
    )

    def record(name, value, attrs):
        metrics.append((name, value, attrs))
        if name == "tertius.pi_agent.result_consumer.heartbeat.count":
            if len(metrics) >= 2:
                heartbeat_seen.set()

    monkeypatch.setattr(consumer_module, "counter_add", record)
    task = asyncio.create_task(
        consumer_module.run_pi_agent_result_consumer(heartbeat_interval_seconds=0)
    )
    await heartbeat_seen.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    count_after_cancel = len(metrics)
    await asyncio.sleep(0)

    assert len(metrics) == count_after_cancel
    assert count_after_cancel >= 2
    assert set(metrics[0][2]) == {
        "operation",
        "provider",
        "model",
        "status",
        "failure_category",
        "retryable",
    }


def test_active_observer_reports_queued_db_jobs_without_nats(
    db_session, seeded_tenant, monkeypatch
):
    job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        request_payload={},
    )
    db_session.add(job)
    db_session.commit()
    metrics = []
    monkeypatch.setattr(consumer_module, "_ACTIVE_JOBS_OBSERVED", 0)
    monkeypatch.setattr(
        consumer_module,
        "up_down_counter_add",
        lambda name, value, attrs: metrics.append((name, value, attrs)),
    )

    assert observe_pi_agent_active_jobs(db_session, SimpleNamespace()) == 1

    assert metrics[0][0] == "tertius.pi_agent.jobs.active"
    assert metrics[0][1] == 1
    assert metrics[0][2]["status"] == "active"


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_main_starts_and_stops_pi_consumer_only_when_enabled(monkeypatch):
    import main

    started = []

    async def fake_run(stop_event):
        started.append(stop_event)
        await __import__("asyncio").Event().wait()

    monkeypatch.setattr(main, "settings", SimpleNamespace(pi_agent_enabled=True))
    monkeypatch.setattr(main, "run_pi_agent_result_consumer", fake_run)
    await main.start_pi_agent_result_consumer()
    await __import__("asyncio").sleep(0)
    assert len(started) == 1
    await main.stop_pi_agent_result_consumer()
    assert main._pi_agent_result_task.done()


@pytest.mark.asyncio
async def test_main_active_observer_survives_result_consumer_initialization_failure(
    db_session, seeded_tenant, monkeypatch
):
    import main

    job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        request_payload={},
    )
    db_session.add(job)
    db_session.commit()
    observed = asyncio.Event()
    metrics = []

    async def observing_database(stop_event):
        observe_pi_agent_active_jobs(db_session, SimpleNamespace())
        observed.set()
        await stop_event.wait()

    async def fail_nats_initialization(_url):
        raise RuntimeError("NATS unavailable")

    monkeypatch.setattr(main, "settings", SimpleNamespace(pi_agent_enabled=True))
    monkeypatch.setattr(
        main, "run_pi_agent_result_consumer", consumer_module.run_pi_agent_result_consumer
    )
    monkeypatch.setattr(main, "run_pi_agent_active_observer", observing_database)
    monkeypatch.setattr(
        consumer_module,
        "get_settings",
        lambda: SimpleNamespace(nats_url="nats://unavailable"),
    )
    monkeypatch.setattr(consumer_module, "connect_nats", fail_nats_initialization)
    monkeypatch.setattr(consumer_module, "_ACTIVE_JOBS_OBSERVED", 0)
    monkeypatch.setattr(
        consumer_module,
        "up_down_counter_add",
        lambda name, value, attrs: metrics.append((name, value, attrs)),
    )

    await main.start_pi_agent_active_observer()
    await main.start_pi_agent_result_consumer()
    await observed.wait()

    assert not main._pi_agent_result_task.done()
    assert metrics[0][0] == "tertius.pi_agent.jobs.active"
    assert metrics[0][1] == 1

    await main.stop_pi_agent_result_consumer()
    await main.stop_pi_agent_active_observer()


@pytest.mark.asyncio
async def test_queued_reconciliation_republishes_with_same_deterministic_id(db_session, seeded_tenant):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)
    payload = dict(job.request_payload)
    conversation = PiAgentConversationContext(
        rolling_summary="older summary",
        recent_turns=[
            PiAgentConversationTurn(
                user_request="Earlier request",
                status="succeeded",
                outcome="no_changes",
                assistant_summary="Already satisfied",
            )
        ],
    )
    payload.update(
        {
            "dispatch_attempted_at": (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat(),
            "dispatch_created_at": datetime.now(timezone.utc).isoformat(),
            "dispatched_provider": "openai-codex",
            "dispatched_model": "gpt-5.5",
            "dispatched_thinking": "high",
            "dispatched_command_schema_version": 2,
            "dispatched_conversation": conversation.model_dump(mode="json"),
            "dispatched_system_prompt_sha256": "a" * 64,
        }
    )
    job.request_payload = payload
    flag_modified(job, "request_payload")
    newer = LlmEditRepository(db_session, seeded_tenant.tenant_id).start_job(
        seeded_tenant.project_id,
        seeded_tenant.user_id,
        {"prompt": "Newer terminal request", "files": []},
        status="failed",
    )
    newer.error_code = "provider_error"
    newer.user_message = "Newer failure"
    db_session.commit()

    class AmbiguousPublisher(Publisher):
        async def publish_json(self, *args, **kwargs):
            await super().publish_json(*args, **kwargs)
            raise RuntimeError("ack lost")

    publisher = AmbiguousPublisher()
    settings = SimpleNamespace(pi_agent_request_subject="request", pi_agent_request_max_bytes=524288, pi_agent_provider="openai-codex", pi_agent_model="gpt-5.5", pi_agent_thinking="high")
    assert await republish_queued_pi_agent_jobs(db_session, publisher, settings, backoff_seconds=0) == 0
    assert await republish_queued_pi_agent_jobs(db_session, publisher, settings, backoff_seconds=0) == 0
    assert [call[1]["message_id"] for call in publisher.calls] == [f"pi-request:{job.id}"] * 2
    for call in publisher.calls:
        republished = call[0][1]
        assert republished.schema_version == 2
        assert republished.conversation == conversation
        assert republished.system_prompt_sha256 == "a" * 64
        assert republished.prior_prompts == []
        assert "Newer terminal request" not in republished.model_dump_json()
    db_session.refresh(job)
    assert job.status == "queued"


@pytest.mark.asyncio
async def test_queued_reconciliation_preserves_v1_context(db_session, seeded_tenant):
    file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    job = _job(db_session, seeded_tenant, file, _result(seeded_tenant, file))
    payload = dict(job.request_payload)
    payload.update(
        {
            "dispatch_attempted_at": (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat(),
            "dispatch_created_at": datetime.now(timezone.utc).isoformat(),
            "dispatched_provider": "openai-codex",
            "dispatched_model": "gpt-5.5",
            "dispatched_thinking": "high",
            "dispatched_prior_prompts": ["legacy request"],
        }
    )
    job.request_payload = payload
    flag_modified(job, "request_payload")
    db_session.commit()
    publisher = Publisher()
    settings = SimpleNamespace(
        pi_agent_request_subject="request",
        pi_agent_request_max_bytes=524288,
        pi_agent_provider="openai-codex",
        pi_agent_model="gpt-5.5",
        pi_agent_thinking="high",
    )

    assert (
        await republish_queued_pi_agent_jobs(
            db_session, publisher, settings, backoff_seconds=0
        )
        == 1
    )
    command = publisher.calls[0][0][1]
    assert command.schema_version == 1
    assert command.conversation is None
    assert command.system_prompt_sha256 is None
    assert command.prior_prompts == ["legacy request"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "v2_context",
    [
        {},
        {
            "dispatched_conversation": {"recent_turns": "malformed"},
            "dispatched_system_prompt_sha256": "a" * 64,
        },
    ],
)
async def test_queued_reconciliation_fails_closed_for_invalid_v2_context(
    db_session, seeded_tenant, v2_context
):
    file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    job = _job(db_session, seeded_tenant, file, _result(seeded_tenant, file))
    payload = dict(job.request_payload)
    payload.update(
        {
            "dispatch_attempted_at": (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat(),
            "dispatch_created_at": datetime.now(timezone.utc).isoformat(),
            "dispatched_provider": "openai-codex",
            "dispatched_model": "gpt-5.5",
            "dispatched_thinking": "high",
            "dispatched_command_schema_version": 2,
            **v2_context,
        }
    )
    job.request_payload = payload
    flag_modified(job, "request_payload")
    db_session.commit()
    publisher = Publisher()
    settings = SimpleNamespace(
        pi_agent_request_subject="request",
        pi_agent_request_max_bytes=524288,
        pi_agent_provider="openai-codex",
        pi_agent_model="gpt-5.5",
        pi_agent_thinking="high",
    )

    assert (
        await republish_queued_pi_agent_jobs(
            db_session, publisher, settings, backoff_seconds=0
        )
        == 0
    )
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "dispatch_config_error"
    assert job.user_message == "AI edit could not be safely retried."
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_queued_reconciliation_fails_when_manifest_file_changed(
    db_session, seeded_tenant, monkeypatch
):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)
    payload = dict(job.request_payload)
    payload.update({
        "dispatch_attempted_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        "dispatch_created_at": datetime.now(timezone.utc).isoformat(),
        "dispatched_provider": "openai-codex",
        "dispatched_model": "gpt-5.5",
        "dispatched_thinking": "high",
        "dispatched_prior_prompts": [],
    })
    job.request_payload = payload
    flag_modified(job, "request_payload")
    file.content = "changed before retry\n"
    file.updated_at = datetime.now(timezone.utc)
    db_session.commit()
    publisher = Publisher()
    metrics = []
    monkeypatch.setattr(
        consumer_module,
        "counter_add",
        lambda name, value, attrs: metrics.append((name, value, attrs)),
    )
    settings = SimpleNamespace(pi_agent_request_subject="request", pi_agent_request_max_bytes=524288, pi_agent_provider="openai-codex", pi_agent_model="gpt-5.5", pi_agent_thinking="high")
    assert await republish_queued_pi_agent_jobs(db_session, publisher, settings, backoff_seconds=0) == 0
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "file_conflict"
    assert publisher.calls == []
    terminal = [item for item in metrics if item[0] == "tertius.pi_agent.job.terminal.count"]
    assert len(terminal) == 1
    assert terminal[0][2]["failure_category"] == "file_conflict"


@pytest.mark.asyncio
async def test_queued_reconciliation_config_failure_emits_bounded_terminal(
    db_session, seeded_tenant, monkeypatch
):
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    result = _result(seeded_tenant, file)
    job = _job(db_session, seeded_tenant, file, result)
    payload = dict(job.request_payload)
    payload.update(
        {
            "dispatch_attempted_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
            "dispatch_created_at": datetime.now(timezone.utc).isoformat(),
            "dispatched_provider": "openai-codex",
            "dispatched_model": "unexpected-model",
            "dispatched_thinking": "high",
        }
    )
    job.request_payload = payload
    flag_modified(job, "request_payload")
    db_session.commit()
    metrics = []
    monkeypatch.setattr(
        consumer_module,
        "counter_add",
        lambda name, value, attrs: metrics.append((name, value, attrs)),
    )
    settings = SimpleNamespace(
        pi_agent_request_subject="request",
        pi_agent_request_max_bytes=524288,
        pi_agent_provider="openai-codex",
        pi_agent_model="gpt-5.5",
        pi_agent_thinking="high",
    )

    assert await republish_queued_pi_agent_jobs(
        db_session, Publisher(), settings, backoff_seconds=0
    ) == 0

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "dispatch_config_error"
    terminal = [item for item in metrics if item[0] == "tertius.pi_agent.job.terminal.count"]
    assert len(terminal) == 1
    assert terminal[0][2]["failure_category"] == "dispatch_config_error"
