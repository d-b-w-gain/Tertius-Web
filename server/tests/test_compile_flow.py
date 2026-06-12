from sqlalchemy import select

from core.models import Artifact, CompileJob, ProjectFile
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
    assert job.status == "queued"
    assert job.project_id == seeded_tenant.project_id
    assert published[0].job_id == job.id
    assert published[0].tenant_id == seeded_tenant.tenant_id
    assert published[0].project_id == seeded_tenant.project_id
    assert published[0].requested_by == seeded_tenant.user_id
    assert published[0].export_format == "stl"
    assert saved_file.content == "shape = 'queued'\n"


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


def test_compile_marks_job_failed_when_enqueue_fails(authenticated_intus_client, db_session, monkeypatch):
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

    job = db_session.get(CompileJob, body["job_id"])
    assert job.status == "failed"
    assert job.error_code == "enqueue_failed"
    assert job.user_message == "Compile could not be started. Try again."
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
