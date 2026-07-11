"""Real-JetStream Pi agent pipeline integration tests."""

from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from nats.js.errors import NotFoundError
from sqlalchemy import func, select
from testcontainers.core.container import DockerContainer

from core.billing_messages import LlmTokenUsageEvent
from core.config import Settings
from core.models import LlmEditJob, LlmUsageRecord, ProjectFile, SourceSnapshot
from core.nats_client import (
    NatsPublisher,
    connect_nats,
    ensure_pi_agent_stream,
    pull_pi_agent_request_subscription,
    pull_pi_agent_result_subscription,
)
from core.pi_agent_messages import PiAgentCommand, PiAgentResult, PiAgentUsage
from core.pi_agent_rpc import PiAgentRpcResult
from workflows.intus import intus_server
from workflows.intus import pi_agent_job as worker_module
from workflows.intus import pi_agent_result_consumer as result_module


@pytest.fixture(scope="session")
def pi_nats_url() -> Generator[str, None, None]:
    container = DockerContainer(os.environ.get("NATS_TEST_IMAGE", "nats:2.10-alpine"))
    container.with_exposed_ports(4222)
    container.with_command("-js")
    container.start()
    host = container.get_container_host_ip()
    port = container.get_exposed_port(4222)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    else:
        container.stop()
        raise RuntimeError("NATS container did not become reachable within 30 s")
    time.sleep(1)
    yield f"nats://{host}:{port}"
    container.stop()


def pi_settings(nats_url: str, suffix: str, *, ack_wait: int = 2) -> Settings:
    return Settings(
        nats_url=nats_url,
        pi_agent_enabled=True,
        pi_agent_stream_name=f"TERTIUS_PI_{suffix}",
        pi_agent_request_subject=f"tertius.pi.{suffix}.request",
        pi_agent_result_subject=f"tertius.pi.{suffix}.result",
        pi_agent_worker_queue=f"pi-workers-{suffix}",
        pi_agent_result_consumer=f"pi-results-{suffix}",
        pi_agent_ack_wait_seconds=ack_wait,
        pi_agent_max_deliver=2,
        pi_agent_estimated_output_tokens=100,
        billing_stream_name=f"TERTIUS_BILLING_{suffix}",
        billing_llm_usage_subject=f"tertius.billing.{suffix}.tokens",
    )


async def fake_pi_rpc(_prompt, *, cwd: Path, **_kwargs) -> PiAgentRpcResult:
    design = cwd / "design.py"
    design.write_text(design.read_text(encoding="utf-8") + "height = 200\n", encoding="utf-8")
    return PiAgentRpcResult(
        usage=PiAgentUsage(input_tokens=11, output_tokens=7, total_tokens=18),
        assistant_summary="Added height",
        turns=1,
        tool_calls=1,
        argv=["fake-pi"],
    )


def pointer(file: ProjectFile) -> dict[str, str]:
    return {"id": str(file.id), "filename": file.filename, "updated_at": file.updated_at.isoformat()}


async def run_pipeline(js, settings, db_session, monkeypatch):
    monkeypatch.setattr(worker_module, "run_pi_agent", fake_pi_rpc)
    publisher = NatsPublisher(js)
    request_sub = await pull_pi_agent_request_subscription(js, settings)
    request = (await request_sub.fetch(batch=1, timeout=5))[0]
    await worker_module.handle_pi_agent_request_message(request, publisher, settings)
    assert request.metadata.num_delivered == 1
    with pytest.raises(TimeoutError):
        await request_sub.fetch(batch=1, timeout=1)
    result_sub = await pull_pi_agent_result_subscription(js, settings)
    result = (await result_sub.fetch(batch=1, timeout=5))[0]
    await result_module.handle_pi_agent_result_message(result, db_session, settings, publisher)
    return request


async def provision_result_consumer_runtime(js, settings, monkeypatch):
    monkeypatch.setattr(result_module, "get_settings", lambda: settings)
    consumer = asyncio.create_task(result_module.run_pi_agent_result_consumer())
    try:
        for _ in range(50):
            try:
                await js.stream_info(settings.billing_stream_name)
                return
            except NotFoundError:
                await asyncio.sleep(0.1)
        pytest.fail("Pi result consumer did not provision the billing stream")
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
async def test_pi_pipeline_worker_publishes_and_api_applies_result(
    pi_nats_url, authenticated_intus_client, db_session, seeded_tenant, monkeypatch, tmp_path
):
    settings = pi_settings(pi_nats_url, "success")
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path))
    nc = await connect_nats(settings.nats_url)
    js = await ensure_pi_agent_stream(nc, settings)
    await provision_result_consumer_runtime(js, settings, monkeypatch)
    try:
        file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
        response = authenticated_intus_client.post(
            "/projects/default_purlin/files/llm-edit/jobs",
            json={"prompt": "Add height", "files": [pointer(file)], "active_file_id": str(file.id)},
        )
        assert response.status_code == 202
        request = await run_pipeline(js, settings, db_session, monkeypatch)
        assert request.metadata.num_delivered == 1
        db_session.expire_all()
        job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
        assert job.status == "succeeded"
        assert db_session.get(ProjectFile, file.id).content.endswith("height = 200\n")
        assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
        assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1

        current = db_session.get(ProjectFile, file.id)
        current.content += "# REFRESHED_FILE_SENTINEL\n"
        current.updated_at = datetime.now(timezone.utc)
        db_session.commit()
        refreshed_content = current.content
        first_prompt = "Add height"
        second_response = authenticated_intus_client.post(
            "/projects/default_purlin/files/llm-edit/jobs",
            json={
                "prompt": "Use completed prior request as context",
                "files": [pointer(current)],
                "active_file_id": str(current.id),
            },
        )
        assert second_response.status_code == 202
        second_message = await run_pipeline(
            js, settings, db_session, monkeypatch
        )
        second = PiAgentCommand.model_validate_json(second_message.data)
        assert second.schema_version == 2
        assert second.conversation.recent_turns[-1].user_request == first_prompt
        assert second.conversation.recent_turns[-1].assistant_summary == "Added height"
        assert second.conversation.recent_turns[-1].status == "succeeded"
        assert second.conversation.recent_turns[-1].outcome == "changed"
        assert second.conversation.recent_turns[-1].changed_files == [file.filename]
        assert second.files[0].content == refreshed_content
        assert "REFRESHED_FILE_SENTINEL" in second.files[0].content
        assert "REFRESHED_FILE_SENTINEL" not in second.conversation.model_dump_json()
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_unacked_command_redelivers_once_and_persists_one_terminal_result(
    pi_nats_url, authenticated_intus_client, db_session, seeded_tenant, monkeypatch, tmp_path
):
    settings = pi_settings(pi_nats_url, "redelivery", ack_wait=1)
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path))
    nc = await connect_nats(settings.nats_url)
    js = await ensure_pi_agent_stream(nc, settings)
    await provision_result_consumer_runtime(js, settings, monkeypatch)
    file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Add height", "files": [pointer(file)]},
    )
    assert response.status_code == 202
    first_sub = await pull_pi_agent_request_subscription(js, settings)
    first = (await first_sub.fetch(batch=1, timeout=5))[0]
    assert first.metadata.num_delivered == 1
    await nc.close()

    await asyncio.sleep(1.5)
    replacement_nc = await connect_nats(settings.nats_url)
    replacement_js = await ensure_pi_agent_stream(replacement_nc, settings)
    try:
        replacement_sub = await pull_pi_agent_request_subscription(replacement_js, settings)
        second = (await replacement_sub.fetch(batch=1, timeout=5))[0]
        assert second.metadata.num_delivered == 2
        monkeypatch.setattr(worker_module, "run_pi_agent", fake_pi_rpc)
        publisher = NatsPublisher(replacement_js)
        await worker_module.handle_pi_agent_request_message(second, publisher, settings)
        result_sub = await pull_pi_agent_result_subscription(replacement_js, settings)
        result = (await result_sub.fetch(batch=1, timeout=5))[0]
        result_payload = PiAgentResult.model_validate_json(result.data)
        await result_module.handle_pi_agent_result_message(result, db_session, settings, publisher)
        billing_sub = await replacement_js.pull_subscribe(
            settings.billing_llm_usage_subject,
            durable="pi-billing-redelivery-test",
            stream=settings.billing_stream_name,
        )
        billing_messages = await billing_sub.fetch(batch=1, timeout=5)
        billing = LlmTokenUsageEvent.model_validate_json(billing_messages[0].data)
        await billing_messages[0].ack()
        assert billing.event_id == result_module.pi_agent_billing_event_id(
            result_payload.execution_id
        )
        with pytest.raises(TimeoutError):
            await billing_sub.fetch(batch=1, timeout=1)
        db_session.expire_all()
        assert db_session.get(LlmEditJob, UUID(response.json()["job_id"])).status == "succeeded"
        assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1
        assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
    finally:
        await replacement_nc.close()


@pytest.mark.asyncio
async def test_short_ack_wait_heartbeat_prevents_live_handler_redelivery(
    pi_nats_url, authenticated_intus_client, db_session, seeded_tenant, monkeypatch, tmp_path
):
    settings = pi_settings(pi_nats_url, "heartbeat", ack_wait=1)
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path))

    async def slow_pi_rpc(*args, **kwargs):
        await asyncio.sleep(2)
        return await fake_pi_rpc(*args, **kwargs)

    monkeypatch.setattr(worker_module, "run_pi_agent", slow_pi_rpc)
    nc = await connect_nats(settings.nats_url)
    js = await ensure_pi_agent_stream(nc, settings)
    try:
        file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
        response = authenticated_intus_client.post(
            "/projects/default_purlin/files/llm-edit/jobs",
            json={"prompt": "Add height", "files": [pointer(file)]},
        )
        assert response.status_code == 202
        sub = await pull_pi_agent_request_subscription(js, settings)
        request = (await sub.fetch(batch=1, timeout=5))[0]
        worker = asyncio.create_task(
            worker_module.handle_pi_agent_request_message(request, NatsPublisher(js), settings)
        )
        await asyncio.sleep(1.3)
        with pytest.raises(TimeoutError):
            await sub.fetch(batch=1, timeout=0.5)
        await worker
        assert request.metadata.num_delivered == 1
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_publish_persists_then_raises_but_queued_worker_result_still_applies(
    pi_nats_url, authenticated_intus_client, db_session, seeded_tenant, monkeypatch, tmp_path
):
    settings = pi_settings(pi_nats_url, "ambiguous")
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    monkeypatch.setenv("TERTIUS_PI_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(worker_module, "run_pi_agent", fake_pi_rpc)
    nc = await connect_nats(settings.nats_url)
    js = await ensure_pi_agent_stream(nc, settings)
    await provision_result_consumer_runtime(js, settings, monkeypatch)

    real_publish = intus_server.publish_pi_agent_command

    async def persist_then_raise(_settings, command):
        await real_publish(settings, command)
        raise RuntimeError("publish acknowledgement lost")

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", persist_then_raise)
    try:
        file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))
        response = authenticated_intus_client.post(
            "/projects/default_purlin/files/llm-edit/jobs",
            json={"prompt": "Add height", "files": [pointer(file)]},
        )
        assert response.status_code == 202
        job_id = UUID(response.json()["job_id"])
        db_session.expire_all()
        assert db_session.get(LlmEditJob, job_id).status == "queued"

        publisher = NatsPublisher(js)
        request_sub = await pull_pi_agent_request_subscription(js, settings)
        request = (await request_sub.fetch(batch=1, timeout=5))[0]
        await worker_module.handle_pi_agent_request_message(request, publisher, settings)
        result_sub = await pull_pi_agent_result_subscription(js, settings)
        result = (await result_sub.fetch(batch=1, timeout=5))[0]
        await result_module.handle_pi_agent_result_message(result, db_session, settings, publisher)
        db_session.expire_all()
        assert db_session.get(LlmEditJob, job_id).status == "succeeded"
        assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1
    finally:
        await nc.close()
