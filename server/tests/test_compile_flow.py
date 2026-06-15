from datetime import timedelta

from sqlalchemy import select

from core.models import Artifact, CompileJob, CompileJobFile, ProjectFile, now_utc
from workflows.intus import intus_server


def test_compile_enqueues_job_and_returns_immediately(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    published = []

    async def fake_publish_compile(command):
        published.append(command)

    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish_compile)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "shape = 'queued'\n", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "queued"
    assert body["job_id"]
    assert len(published) == 1

    job = db_session.get(CompileJob, body["job_id"])
    saved_file = db_session.scalar(select(ProjectFile).where(ProjectFile.filename == "design.py"))
    snapshot_rows = db_session.scalars(
        select(CompileJobFile).where(CompileJobFile.compile_job_id == job.id)
    ).all()
    snapshot = {row.filename: row.content for row in snapshot_rows}
    assert job.status == "running"
    assert job.lease_expires_at is not None
    assert job.project_id == seeded_tenant.project_id
    assert published[0].job_id == job.id
    assert published[0].tenant_id == seeded_tenant.tenant_id
    assert published[0].project_id == seeded_tenant.project_id
    assert published[0].requested_by == seeded_tenant.user_id
    assert published[0].export_format == "stl"
    assert published[0].request_id == f"compile-request:{job.id}"
    assert [(file.filename, file.content) for file in published[0].files] == [("design.py", "shape = 'queued'\n")]
    assert saved_file.content == "shape = 'queued'\n"
    assert snapshot["design.py"] == "shape = 'queued'\n"


def test_compile_marks_published_job_running_with_recovery_lease(
    authenticated_intus_client, db_session, monkeypatch
):
    async def fake_publish_compile(command):
        return None

    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish_compile)

    before_request = now_utc()
    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "shape = 'recoverable'\n", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 202
    job = db_session.get(CompileJob, response.json()["job_id"])
    assert job.status == "running"
    assert job.claimed_at is not None
    assert job.claimed_at >= before_request
    assert job.lease_expires_at is not None
    assert job.lease_expires_at > job.claimed_at
    assert job.error_code is None


def test_compile_marks_job_failed_when_source_bundle_exceeds_limit(
    authenticated_intus_client, db_session, monkeypatch
):
    published = []

    async def fake_publish_compile(command):
        published.append(command)

    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish_compile)
    settings = intus_server.get_settings()
    monkeypatch.setattr(settings, "compile_request_max_bytes", 20)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "shape = 'too large'\n", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 413
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "source_bundle_too_large"
    assert published == []

    job = db_session.get(CompileJob, body["job_id"])
    assert job.status == "failed"
    assert job.error_code == "source_bundle_too_large"


def test_compile_rejects_invalid_filename(authenticated_intus_client, monkeypatch):
    published = []

    async def fake_publish_compile(command):
        published.append(command)

    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish_compile)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "x = 1", "export_format": "stl", "file": "../design.py"},
    )

    assert response.status_code == 400
    assert published == []


def test_compile_leaves_job_queued_when_publish_fails_after_commit(authenticated_intus_client, db_session, monkeypatch):
    async def fake_publish_compile(command):
        raise RuntimeError("nats down")

    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish_compile)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "shape = 'queued'\n", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["job_id"]
    assert body["user_message"] == "Compile queued but could not be published immediately. It will be retried."
    assert body["retryable"] is True

    job = db_session.get(CompileJob, body["job_id"])
    assert job.status == "queued"
    assert job.error_code == "publish_pending"
    assert job.retryable is True


def test_compile_job_status_returns_artifact_after_success(authenticated_intus_client, db_session, seeded_tenant):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="glb",
    )
    db_session.add(job)
    db_session.flush()
    artifact = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        compile_job_id=job.id,
        kind="glb",
        storage_key="model.glb",
        content_type="model/gltf-binary",
        byte_size=5,
        content=b"model",
    )
    db_session.add(artifact)
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/default_purlin/compile/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["artifact_id"] == str(artifact.id)


def test_compile_job_status_returns_persisted_failure_fields(authenticated_intus_client, db_session, seeded_tenant):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="failed",
        export_format="glb",
        error="boom",
        error_code="sandbox_error",
        user_message="Compile failed. Fix the model source and try again.",
        retryable=True,
    )
    db_session.add(job)
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/default_purlin/compile/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "sandbox_error"
    assert body["user_message"] == "Compile failed. Fix the model source and try again."
    assert body["retryable"] is True
    assert body["artifact_id"] is None


def test_compile_job_status_marks_expired_running_job_failed(authenticated_intus_client, db_session, seeded_tenant):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="glb",
        claimed_at=now_utc() - timedelta(minutes=20),
        lease_expires_at=now_utc() - timedelta(seconds=1),
    )
    db_session.add(job)
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/default_purlin/compile/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "worker_lost"
    assert body["user_message"] == (
        "Compile worker stopped unexpectedly. The model may have exceeded available memory or the worker was restarted."
    )
    assert body["retryable"] is True
    assert body["finished_at"] is not None


def test_compile_job_status_marks_old_queued_job_failed(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    settings = intus_server.get_settings()
    monkeypatch.setattr(settings, "compile_ack_wait_seconds", 60)
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="queued",
        export_format="glb",
        created_at=now_utc() - timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/default_purlin/compile/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "worker_lost"
    assert body["retryable"] is True


def test_compile_job_status_keeps_unexpired_running_job(authenticated_intus_client, db_session, seeded_tenant):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="glb",
        claimed_at=now_utc(),
        lease_expires_at=now_utc() + timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/default_purlin/compile/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["error_code"] is None
    assert body["finished_at"] is None


def test_compile_job_status_keeps_completed_job(authenticated_intus_client, db_session, seeded_tenant):
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="glb",
        lease_expires_at=now_utc() - timedelta(minutes=5),
        finished_at=now_utc() - timedelta(minutes=4),
    )
    db_session.add(job)
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/default_purlin/compile/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["error_code"] is None
