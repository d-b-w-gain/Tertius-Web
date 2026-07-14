from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from core.pi_agent_conversation import (
    render_conversation_context,
    render_legacy_prior_prompts,
)
from core.pi_agent_messages import (
    PiAgentCommand,
    PiAgentConversationContext,
    PiAgentConversationTurn,
    PiAgentSourceFile,
)
from core.pi_agent_messages import PiAgentUsage
from core.pi_agent_prompt import (
    PiAgentPromptError,
    load_pi_agent_prompt,
    render_pi_agent_user_prompt,
)
from core.pi_agent_rpc import PiAgentRpcResult
from workflows.intus.pi_agent_job import (
    WorkspaceError,
    hydrate_workspace,
    scan_workspace,
)


def command(files):
    return PiAgentCommand(
        schema_version=1,
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        provider="openai-codex",
        model="gpt-5.6",
        thinking="medium",
        prompt="edit",
        files=files,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_worker_passes_coding_agent_contract_and_job_correlation_id(
    monkeypatch, tmp_path
):
    import workflows.intus.pi_agent_job as job

    active = source("parts/model.py", "SOURCE_SENTINEL")
    support = source("dimensions.py", "WIDTH = 12")
    request = command([active, support]).model_copy(
        update={
            "prompt": "Current request",
            "prior_prompts": ["First request", "Second request"],
            "active_file_id": active.id,
        }
    )
    captured = {}

    async def fake_rpc(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return PiAgentRpcResult(
            usage=PiAgentUsage(),
            assistant_summary="",
            turns=1,
            tool_calls=0,
            argv=["pi"],
        )

    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(job, "run_pi_agent", fake_rpc)

    result = await job.execute_pi_agent_command(request, worker_settings())

    assert result.status == "succeeded"
    prompt = captured["prompt"]
    assert prompt == render_pi_agent_user_prompt(
        conversation_prompt=render_legacy_prior_prompts(
            ["First request", "Second request"], "Current request"
        ),
        editable_filenames=["parts/model.py", "dimensions.py"],
        active_filename="parts/model.py",
    )
    legacy, current = captured["prompt"].split("Current user request:\n", maxsplit=1)
    assert "<legacy_user_requests>" in legacy
    assert '"First request"' in legacy
    assert '"Second request"' in legacy
    assert '"outcome":' not in legacy
    assert '"status":' not in legacy
    assert "succeeded" not in legacy
    assert current.startswith("Current request")
    assert "Active file:\nparts/model.py" in prompt
    assert "- parts/model.py\n- dimensions.py" in prompt
    assert "Edit the existing files in place" in prompt
    assert "Do not create, delete, or rename files" in prompt
    assert "SOURCE_SENTINEL" not in prompt
    assert "WIDTH = 12" not in prompt
    assert captured["kwargs"]["system_prompt_path"] == load_pi_agent_prompt().path
    assert captured["kwargs"]["correlation_id"] == str(request.job_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_v2_worker_uses_structured_context_and_preserves_assistant_summary(
    monkeypatch, tmp_path, changed
):
    import workflows.intus.pi_agent_job as job

    active = source("parts/model.py", "SOURCE_SENTINEL")
    context = PiAgentConversationContext(
        rolling_summary="Older work was completed.",
        recent_turns=[
            PiAgentConversationTurn(
                user_request="Earlier request",
                status="succeeded",
                outcome="no_changes",
                assistant_summary="Already satisfied",
            )
        ],
    )
    snapshot = load_pi_agent_prompt()
    request = command([active]).model_copy(
        update={
            "schema_version": 2,
            "prompt": "Current request",
            "conversation": context,
            "system_prompt_sha256": snapshot.sha256,
            "active_file_id": active.id,
        }
    )
    captured = {}

    async def fake_rpc(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        if changed:
            (Path(kwargs["cwd"]) / active.filename).write_text(
                "UPDATED_SOURCE", encoding="utf-8"
            )
        return PiAgentRpcResult(
            usage=PiAgentUsage(),
            assistant_summary="Adjusted the design.",
            turns=1,
            tool_calls=0,
            argv=["pi"],
        )

    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(job, "run_pi_agent", fake_rpc)

    result = await job.execute_pi_agent_command(request, worker_settings())

    assert result.status == "succeeded"
    assert result.outcome == ("changed" if changed else "no_changes")
    assert result.assistant_summary == "Adjusted the design."
    assert captured["prompt"] == render_pi_agent_user_prompt(
        conversation_prompt=render_conversation_context(context, "Current request"),
        editable_filenames=["parts/model.py"],
        active_filename="parts/model.py",
    )
    assert captured["kwargs"]["system_prompt_path"] == snapshot.path


@pytest.mark.asyncio
async def test_worker_rejects_v2_prompt_hash_mismatch_without_calling_pi(
    monkeypatch, tmp_path
):
    import workflows.intus.pi_agent_job as job

    request = command([source("design.py", "x = 1")]).model_copy(
        update={
            "schema_version": 2,
            "prior_prompts": [],
            "conversation": PiAgentConversationContext(),
            "system_prompt_sha256": "0" * 64,
        }
    )

    async def forbidden_rpc(*_args, **_kwargs):
        raise AssertionError("Pi must not run on prompt mismatch")

    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(job, "run_pi_agent", forbidden_rpc)

    result = await job.execute_pi_agent_command(request, worker_settings())

    assert result.status == "failed"
    assert result.error_code == "worker_config_mismatch"
    assert result.error_message == (
        "AI worker configuration changed; retry after deployment completes."
    )
    assert result.retryable is True
    assert not (tmp_path / "workspace").exists()


def source(filename, content):
    return PiAgentSourceFile(
        id=uuid4(),
        filename=filename,
        content=content,
        updated_at=datetime.now(timezone.utc),
        sha256=hashlib.sha256(content.encode()).hexdigest(),
    )


@pytest.mark.asyncio
async def test_worker_returns_fixed_nonretryable_failure_when_policy_is_unavailable(
    monkeypatch, tmp_path, caplog
):
    import workflows.intus.pi_agent_job as job

    request = command([source("design.py", "length = 10\n")])

    def unavailable_prompt():
        raise PiAgentPromptError("secret prompt path /tmp/policy")

    async def forbidden_rpc(*_args, **_kwargs):
        raise AssertionError("provider must not run")

    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(job, "load_pi_agent_prompt", unavailable_prompt)
    monkeypatch.setattr(job, "run_pi_agent", forbidden_rpc)

    result = await job.execute_pi_agent_command(request, worker_settings())

    assert result.status == "failed"
    assert result.error_code == "worker_config_error"
    assert result.error_message == "Pi agent policy is unavailable."
    assert result.retryable is False
    assert result.outcome is None
    assert result.changed_files == []
    assert "secret prompt path" not in caplog.text
    assert not (tmp_path / "workspace").exists()


def test_worker_hydrates_fresh_manifest_with_secure_modes(tmp_path):
    root = tmp_path / "repo"
    item = source("nested/model.py", "print(1)\n")
    manifest = hydrate_workspace(command([item]), root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "nested").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "nested/model.py").stat().st_mode) == 0o600
    assert manifest["nested/model.py"].sha256 == item.sha256


def test_worker_scan_returns_only_changed_manifest_files(tmp_path):
    root = tmp_path / "repo"
    item = source("model.py", "old")
    manifest = hydrate_workspace(command([item]), root)
    (root / "model.py").write_text("new")
    changed = scan_workspace(root, manifest)
    assert [file.content for file in changed] == ["new"]


@pytest.mark.parametrize("mutation", ["add", "delete", "symlink"])
def test_worker_rejects_file_set_or_type_changes(tmp_path, mutation):
    root = tmp_path / "repo"
    item = source("model.py", "old")
    manifest = hydrate_workspace(command([item]), root)
    if mutation == "add":
        (root / "extra.py").write_text("x")
    elif mutation == "delete":
        (root / "model.py").unlink()
    else:
        (root / "model.py").unlink()
        (root / "model.py").symlink_to("/etc/passwd")
    with pytest.raises(WorkspaceError):
        scan_workspace(root, manifest)


class FakeMessage:
    def __init__(self, data):
        self.data = data
        self.actions = []
        self.progress = 0

    async def ack(self):
        self.actions.append("ack")

    async def nak(self):
        self.actions.append("nak")

    async def term(self):
        self.actions.append("term")

    async def in_progress(self):
        self.progress += 1


class FakePublisher:
    def __init__(self, fail=False, fail_times=0):
        self.fail = fail
        self.fail_times = fail_times
        self.calls = []

    async def publish_json(self, subject, message, **kwargs):
        self.calls.append((subject, message, kwargs))
        if self.fail or len(self.calls) <= self.fail_times:
            raise TimeoutError


def worker_settings(**overrides):
    from types import SimpleNamespace

    values = {
        "pi_agent_model": "gpt-5.6",
        "pi_agent_thinking": "medium",
        "pi_agent_request_max_bytes": 524288,
        "pi_agent_result_max_bytes": 524288,
        "pi_agent_result_subject": "tertius.pi.result",
        "pi_agent_ack_wait_seconds": 90,
        "pi_agent_timeout_seconds": 480,
        "pi_agent_max_turns": 12,
        "pi_agent_max_tool_calls": 48,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_worker_uses_writable_preprovisioned_workspace_without_chmodding_base(
    monkeypatch, tmp_path
):
    import workflows.intus.pi_agent_job as job

    workspace_base = tmp_path / "workspace"
    workspace_base.mkdir()
    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(workspace_base))
    original_chmod = os.chmod

    def kubelet_owned_chmod(path, mode):
        if Path(path) == workspace_base:
            raise PermissionError(errno.EPERM, "Operation not permitted", path)
        original_chmod(path, mode)

    async def fake_rpc(*_args, **_kwargs):
        return PiAgentRpcResult(
            usage=PiAgentUsage(),
            assistant_summary="",
            turns=1,
            tool_calls=0,
            argv=["pi"],
        )

    monkeypatch.setattr(job.os, "chmod", kubelet_owned_chmod)
    monkeypatch.setattr(job, "run_pi_agent", fake_rpc)

    result = await job.execute_pi_agent_command(
        command([source("model.py", "old")]), worker_settings()
    )

    assert result.status == "succeeded"
    assert result.outcome == "no_changes"
    assert result.assistant_summary == "No files changed."


@pytest.mark.asyncio
async def test_worker_returns_bounded_failure_when_workspace_setup_fails(
    monkeypatch, tmp_path
):
    import workflows.intus.pi_agent_job as job

    workspace_base = tmp_path / "not-a-directory"
    workspace_base.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(workspace_base))

    result = await job.execute_pi_agent_command(
        command([source("model.py", "old")]), worker_settings()
    )

    assert result.status == "failed"
    assert result.error_code == "worker_error"
    assert result.error_message == "Pi agent worker failed"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_worker_acks_only_after_confirmed_publish(monkeypatch):
    import workflows.intus.pi_agent_job as job

    item = source("model.py", "old")
    request = command([item])
    result = job._failure(
        request, datetime.now(timezone.utc), "provider_auth", "failed", False
    )
    monkeypatch.setattr(
        job, "execute_pi_agent_command", lambda *_: asyncio.sleep(0, result=result)
    )
    msg = FakeMessage(request.model_dump_json().encode())
    publisher = FakePublisher()

    await job.handle_pi_agent_request_message(msg, publisher, worker_settings())

    assert msg.actions == ["ack"]
    assert publisher.calls[0][2]["message_id"] == (
        f"pi-result:{result.job_id}:{result.execution_id}"
    )
    assert str(request.job_id) not in publisher.calls[0][2]["telemetry_message_id"]


@pytest.mark.asyncio
async def test_worker_emits_bounded_completion_metrics_and_result_trace_context(
    monkeypatch,
):
    import workflows.intus.pi_agent_job as job

    request = command([source("model.py", "old")]).model_copy(
        update={
            "traceparent": "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
        }
    )
    result = job._failure(
        request, datetime.now(timezone.utc), "provider_auth", "failed", False
    )
    monkeypatch.setattr(
        job, "execute_pi_agent_command", lambda *_: asyncio.sleep(0, result=result)
    )
    metrics = []
    monkeypatch.setattr(
        job,
        "counter_add",
        lambda name, value, attrs: metrics.append((name, attrs)),
    )
    monkeypatch.setattr(
        job,
        "histogram_record",
        lambda name, value, attrs: metrics.append((name, attrs)),
    )
    publisher = FakePublisher()

    await job.handle_pi_agent_request_message(
        FakeMessage(request.model_dump_json().encode()), publisher, worker_settings()
    )

    published = publisher.calls[0][1]
    assert published.traceparent is not None
    assert all(
        set(attrs)
        <= {"operation", "provider", "model", "status", "failure_category", "retryable"}
        for _, attrs in metrics
    )
    assert any(
        name == "tertius.pi_agent.worker.completed.count"
        and attrs["failure_category"] == "provider_auth"
        and attrs["retryable"] is False
        for name, attrs in metrics
    )
    assert {name for name, _ in metrics} >= {
        "tertius.pi_agent.tokens.input",
        "tertius.pi_agent.tokens.output",
        "tertius.pi_agent.tokens.cache_read",
        "tertius.pi_agent.tokens.cache_write",
        "tertius.pi_agent.tokens.total",
    }


@pytest.mark.asyncio
async def test_worker_prefers_nats_header_parent_over_payload_fallback(monkeypatch):
    from opentelemetry import propagate
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    import workflows.intus.pi_agent_job as job

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.pi-agent")
    monkeypatch.setattr(job.trace, "get_tracer", lambda _name: tracer)
    request = command([source("model.py", "old")]).model_copy(
        update={
            "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
        }
    )
    result = job._failure(
        request, datetime.now(timezone.utc), "provider_auth", "failed", False
    )
    monkeypatch.setattr(
        job, "execute_pi_agent_command", lambda *_: asyncio.sleep(0, result=result)
    )
    headers = {}
    with tracer.start_as_current_span("NATS publish command") as producer:
        propagate.inject(headers)
        producer_context = producer.get_span_context()
    msg = FakeMessage(request.model_dump_json().encode())
    msg.headers = headers

    await job.handle_pi_agent_request_message(msg, FakePublisher(), worker_settings())

    consumer = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "pi_agent.command.consume"
    )
    assert consumer.context.trace_id == producer_context.trace_id
    assert consumer.parent.span_id == producer_context.span_id


@pytest.mark.asyncio
async def test_worker_retries_transient_publish_failure_without_rerunning_provider(
    monkeypatch,
):
    import workflows.intus.pi_agent_job as job

    request = command([source("model.py", "old")])
    result = job._failure(
        request, datetime.now(timezone.utc), "provider_auth", "failed", False
    )
    monkeypatch.setattr(
        job, "execute_pi_agent_command", lambda *_: asyncio.sleep(0, result=result)
    )
    msg = FakeMessage(request.model_dump_json().encode())

    publisher = FakePublisher(fail_times=1)
    await job.handle_pi_agent_request_message(msg, publisher, worker_settings())

    assert msg.actions == ["ack"]
    assert len(publisher.calls) == 2


@pytest.mark.asyncio
async def test_worker_naks_persistent_publish_failure(monkeypatch):
    import workflows.intus.pi_agent_job as job

    request = command([source("model.py", "old")])
    result = job._failure(
        request, datetime.now(timezone.utc), "provider_auth", "failed", False
    )
    monkeypatch.setattr(
        job, "execute_pi_agent_command", lambda *_: asyncio.sleep(0, result=result)
    )
    msg = FakeMessage(request.model_dump_json().encode())
    publisher = FakePublisher(fail=True)

    await job.handle_pi_agent_request_message(msg, publisher, worker_settings())

    assert msg.actions == ["nak"]
    assert len(publisher.calls) == 3


@pytest.mark.asyncio
async def test_handler_cancellation_stops_inflight_publish_without_ack(monkeypatch):
    import workflows.intus.pi_agent_job as job

    request = command([source("model.py", "old")])
    result = job._failure(
        request, datetime.now(timezone.utc), "provider_auth", "failed", False
    )
    monkeypatch.setattr(
        job, "execute_pi_agent_command", lambda *_: asyncio.sleep(0, result=result)
    )
    started = asyncio.Event()
    cleaned = asyncio.Event()

    class BlockingPublisher(FakePublisher):
        async def publish_json(self, subject, message, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    msg = FakeMessage(request.model_dump_json().encode())
    handler = asyncio.create_task(
        job.handle_pi_agent_request_message(msg, BlockingPublisher(), worker_settings())
    )
    await started.wait()
    handler.cancel()
    await asyncio.gather(handler, return_exceptions=True)

    assert cleaned.is_set()
    assert msg.actions == []


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_execution_and_naks(monkeypatch):
    import workflows.intus.pi_agent_job as job

    request = command([source("model.py", "old")])
    cancelled = asyncio.Event()

    async def blocked(*_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def failed_heartbeat(_, _ack_wait_seconds):
        raise ConnectionError("lost ack lease")

    monkeypatch.setattr(job, "execute_pi_agent_command", blocked)
    monkeypatch.setattr(job, "_heartbeat", failed_heartbeat)
    msg = FakeMessage(request.model_dump_json().encode())

    await job.handle_pi_agent_request_message(msg, FakePublisher(), worker_settings())

    assert cancelled.is_set()
    assert msg.actions == ["nak"]


@pytest.mark.asyncio
async def test_heartbeat_reports_progress_repeatedly(monkeypatch):
    import workflows.intus.pi_agent_job as job

    delays = []

    original_sleep = asyncio.sleep

    async def yielding_sleep(delay):
        delays.append(delay)
        await original_sleep(0)

    monkeypatch.setattr(job.asyncio, "sleep", yielding_sleep)
    msg = FakeMessage(b"{}")
    task = asyncio.create_task(job._heartbeat(msg))
    while msg.progress < 2:
        await original_sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert delays[:2] == [30, 30]
    assert msg.progress >= 2


@pytest.mark.asyncio
async def test_heartbeat_interval_stays_below_short_ack_wait(monkeypatch):
    import workflows.intus.pi_agent_job as job

    delays = []

    async def stop_after_sleep(delay):
        delays.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(job.asyncio, "sleep", stop_after_sleep)
    await asyncio.gather(
        job._heartbeat(FakeMessage(b"{}"), 0.6), return_exceptions=True
    )
    assert delays == [pytest.approx(0.2)]


@pytest.mark.asyncio
async def test_worker_rejects_oversized_command_before_execution(monkeypatch):
    import workflows.intus.pi_agent_job as job

    request = command([source("model.py", "old")])
    called = False

    async def execute(*_):
        nonlocal called
        called = True

    monkeypatch.setattr(job, "execute_pi_agent_command", execute)
    msg = FakeMessage(request.model_dump_json().encode())

    await job.handle_pi_agent_request_message(
        msg, FakePublisher(), worker_settings(pi_agent_request_max_bytes=1)
    )

    assert msg.actions == ["term"]
    assert called is False
