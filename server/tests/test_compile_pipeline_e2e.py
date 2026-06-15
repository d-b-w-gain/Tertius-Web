"""End-to-end compile pipeline tests with real NATS JetStream.

Verifies the full message flow:
  API publish -> NATS -> Worker pull -> sandbox exec -> result publish -> NATS -> consumer -> DB persist.

All tests use @pytest.mark.asyncio to keep the NATS connection alive in a
single event loop. The nats_url fixture provides a session-scoped NATS container.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer

from sqlalchemy import select as _select

from core.compile_messages import CompileCommand, CompileResultPayload, CompileSourceFile
from core.config import Settings
from core.models import Artifact, CompileJob, CompileJobFile, now_utc
from core.nats_client import (
    NatsPublisher,
    connect_nats,
    ensure_compile_stream,
    pull_compile_result_subscription,
    pull_compile_subscription,
)
from core.repositories import CompileRepository
from workflows.intus import intus_server
from workflows.intus import compile_job as compile_job_module
from workflows.intus import compile_result_consumer as consumer_module


# ---------------------------------------------------------------------------
# NATS testcontainer fixture (session-scoped)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def nats_url() -> str:
    """Start a NATS 2.x container with JetStream enabled.

    Uses nats:2.10-alpine which is the smallest stable image with JetStream.
    The ``-js`` flag enables JetStream explicitly.
    """
    nats_image = os.environ.get("NATS_TEST_IMAGE", "nats:2.10-alpine")

    container = DockerContainer(nats_image)
    container.with_exposed_ports(4222)
    container.with_command("-js")
    container.start()

    # Wait for the server to be reachable by polling a raw socket,
    # then give JetStream a moment to initialise.
    import socket
    import time

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

    # JetStream may need a moment after the socket accepts
    time.sleep(1)

    yield f"nats://{host}:{port}"
    container.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compile_settings(nats_url: str) -> Settings:
    """Settings pointing at the test NATS container with fast ack/timeout."""
    return Settings(
        nats_url=nats_url,
        compile_stream_name="TERTIUS_COMPILE",
        compile_request_subject="tertius.compile.request",
        compile_result_subject="tertius.compile.result",
        compile_worker_queue="compile-workers",
        compile_result_consumer="compile-result-api",
        compile_ack_wait_seconds=30,
        compile_max_deliver=3,
        compile_timeout_seconds=30,
        compile_request_max_bytes=8 * 1024 * 1024,
        compile_result_max_bytes=32 * 1024 * 1024,
        artifact_retention_limit=10,
    )


def fake_sandbox_success(project_dir, export_format, timeout_seconds):
    """Return a fake CompileSandboxResult with a tiny STL payload."""
    from core.compile_sandbox import CompileSandboxResult

    stl_path = Path(project_dir) / "output.stl"
    stl_path.write_bytes(b"fake stl content")
    return CompileSandboxResult(
        success=True,
        output_path=stl_path,
        stdout="ok",
        stderr="",
        error=None,
    )


def fake_sandbox_failure(project_dir, export_format, timeout_seconds):
    """Return a fake CompileSandboxResult indicating failure."""
    from core.compile_sandbox import CompileSandboxResult

    return CompileSandboxResult(
        success=False,
        output_path=None,
        stdout="",
        stderr="NameError: name 'x' is not defined",
        error="NameError: name 'x' is not defined",
    )


# ---------------------------------------------------------------------------
# Shared async fixture helpers
# ---------------------------------------------------------------------------

async def _setup_nats(settings: Settings):
    """Connect to NATS and ensure the compile stream + consumers exist."""
    nc = await connect_nats(settings.nats_url)
    js = await ensure_compile_stream(nc, settings)
    return nc, js


def _create_job_and_snapshot(db_session, seeded_tenant, repo, content="import build123d as bd\nbox = bd.Box(10,10,10)\n"):
    """Create a CompileJob + CompileJobFile, mark dispatched, return job."""
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "stl")
    db_session.flush()

    job_file = CompileJobFile(
        compile_job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        filename="design.py",
        content=content,
    )
    db_session.add(job_file)
    repo.mark_job_dispatched(job, lease_seconds=60)
    db_session.commit()
    return job


# ---------------------------------------------------------------------------
# E2E: happy-path pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_publishes_command_worker_processes_and_consumer_persists_artifact(
    nats_url, authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    """End-to-end: API compile -> NATS -> worker -> result consumer -> DB."""
    settings = compile_settings(nats_url)
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    nc, js = await _setup_nats(settings)

    try:
        response = authenticated_intus_client.post(
            "/projects/default_purlin/compile",
            json={
                "code": "import build123d as bd\nbox = bd.Box(10,10,10)\n",
                "export_format": "stl",
                "file": "design.py",
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        job = db_session.get(CompileJob, body["job_id"])
        assert job is not None
        assert job.status == "running"
        assert job.lease_expires_at is not None

        publisher = NatsPublisher(js)

        # Worker processes the message
        monkeypatch.setattr(compile_job_module, "run_compile_sandbox", fake_sandbox_success)

        sub = await pull_compile_subscription(js, settings)
        messages = await sub.fetch(batch=1, timeout=5)
        assert len(messages) == 1
        for msg in messages:
            command = CompileCommand.model_validate_json(msg.data)
            assert command.job_id == job.id
            assert command.tenant_id == seeded_tenant.tenant_id
            assert command.project_id == seeded_tenant.project_id
            assert command.requested_by == seeded_tenant.user_id
            assert command.export_format == "stl"
            assert command.request_id == f"compile-request:{job.id}"
            assert [(file.filename, file.content) for file in command.files] == [
                ("design.py", "import build123d as bd\nbox = bd.Box(10,10,10)\n")
            ]
            await compile_job_module.handle_compile_request_message(msg, publisher, settings)

        # Result consumer persists to DB
        result_sub = await pull_compile_result_subscription(js, settings)
        result_messages = await result_sub.fetch(batch=1, timeout=5)
        assert len(result_messages) == 1
        for msg in result_messages:
            await consumer_module.handle_compile_result_message(msg, db_session, settings)

        # Verify artifact persisted
        db_session.expire_all()
        artifact = db_session.scalar(
            _select(Artifact).where(
                Artifact.tenant_id == seeded_tenant.tenant_id,
                Artifact.compile_job_id == job.id,
            )
        )
        assert artifact is not None, "Artifact should be persisted after successful compile"
        assert artifact.kind == "stl"
        assert artifact.byte_size == len(b"fake stl content")
        assert artifact.content == b"fake stl content"

        updated_job = db_session.get(CompileJob, job.id)
        assert updated_job.status == "succeeded"

    finally:
        await nc.close()


# ---------------------------------------------------------------------------
# Worker failure path: failure result is published and consumed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_publishes_failure_result_when_sandbox_fails(
    nats_url, db_session, seeded_tenant, monkeypatch
):
    """When the sandbox fails, the worker publishes a failure result which
    the consumer persists to the DB."""
    settings = compile_settings(nats_url)
    nc, js = await _setup_nats(settings)

    try:
        repo = CompileRepository(db_session, seeded_tenant.tenant_id)
        job = _create_job_and_snapshot(db_session, seeded_tenant, repo, content="bad code")

        command = CompileCommand(
            job_id=job.id,
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            requested_by=seeded_tenant.user_id,
            export_format="stl",
            created_at=job.created_at,
            files=[CompileSourceFile(filename="design.py", content="bad code")],
            request_id=f"compile-request:{job.id}",
        )

        publisher = NatsPublisher(js)
        await publisher.publish_json(
            settings.compile_request_subject,
            command,
            message_id=command.request_id,
        )

        # Worker processes: sandbox fails -> publishes failure result -> acks
        monkeypatch.setattr(compile_job_module, "run_compile_sandbox", fake_sandbox_failure)
        sub = await pull_compile_subscription(js, settings)
        messages = await sub.fetch(batch=1, timeout=5)
        for msg in messages:
            await compile_job_module.handle_compile_request_message(msg, publisher, settings)

        # Result consumer picks up the failure result and persists to DB
        result_sub = await pull_compile_result_subscription(js, settings)
        result_messages = await result_sub.fetch(batch=1, timeout=5)
        for msg in result_messages:
            await consumer_module.handle_compile_result_message(msg, db_session, settings)

        # Verify job is marked failed with the correct error
        db_session.expire_all()
        updated_job = db_session.get(CompileJob, job.id)
        assert updated_job.status == "failed"
        assert updated_job.error_code == "sandbox_error"
        assert updated_job.retryable is True

    finally:
        await nc.close()


# ---------------------------------------------------------------------------
# Invalid message -> term (no redelivery)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_terms_invalid_json_message(nats_url):
    """A message with invalid JSON should be termed (permanently failed)."""
    settings = compile_settings(nats_url)
    nc, js = await _setup_nats(settings)

    try:
        # Publish garbage JSON directly
        await js.publish(settings.compile_request_subject, b"not valid json {{{")

        sub = await pull_compile_subscription(js, settings)
        messages = await sub.fetch(batch=1, timeout=5)
        assert len(messages) == 1

        publisher = NatsPublisher(js)
        await compile_job_module.handle_compile_request_message(
            messages[0], publisher, settings
        )

        # After term, no more messages
        with pytest.raises(TimeoutError):
            await sub.fetch(batch=1, timeout=3)

    finally:
        await nc.close()


# ---------------------------------------------------------------------------
# Result consumer idempotency: duplicate success result
# ---------------------------------------------------------------------------

def test_result_consumer_skips_duplicate_terminal_job(db_session, seeded_tenant):
    """Sending a result for an already-succeeded job should be a no-op.

    This test does NOT need a NATS container — it tests the pure business
    logic in apply_compile_result which is DB-only.
    """
    settings = compile_settings("nats://localhost:4222")  # URL unused

    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "stl")
    db_session.flush()
    job.status = "succeeded"
    db_session.commit()

    result = CompileResultPayload(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        export_format="stl",
        status="succeeded",
        artifact_content_base64=base64.b64encode(b"duplicate artifact").decode("ascii"),
        artifact_byte_size=len(b"duplicate artifact"),
        worker_started_at=now_utc(),
        worker_finished_at=now_utc(),
    )

    # apply_compile_result is the inner function; it returns False for
    # already-terminal jobs so the caller (handle_compile_result_message)
    # can decide to ack without mutating anything.
    returned = consumer_module.apply_compile_result(db_session, result, settings)
    assert not returned, "apply_compile_result should return False for already-terminal job"

    # Verify no duplicate artifact was created
    db_session.expire_all()
    artifacts = db_session.scalars(
        _select(Artifact).where(
            Artifact.tenant_id == seeded_tenant.tenant_id,
            Artifact.compile_job_id == job.id,
        )
    ).all()
    assert len(artifacts) == 0, "No new artifact should be created for duplicate result"
