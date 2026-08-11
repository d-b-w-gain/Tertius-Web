from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.models import Project, ProjectAsset, ProjectImportJob
from core.object_store import ObjectRef
from core.project_assets import (
    IMPORT_3MF_CONVERSION_VERSION,
    MAX_3MF_UPLOAD_BYTES,
    OCTET_STREAM_MEDIA_TYPE,
    THREE_MF_MEDIA_TYPE,
    Import3mfManifest,
)
from core.repositories import ProjectImportRepository
from workflows.intus import intus_server


@pytest.fixture()
def import_transport(monkeypatch):
    published = []
    stored = []

    async def put(content: bytes) -> ObjectRef:
        stored.append(content)
        digest = sha256(content).hexdigest()
        return ObjectRef(
            bucket="TERTIUS_ASSETS",
            key=f"sha256/{digest}",
            sha256=digest,
            byte_size=len(content),
        )

    async def publish(command):
        published.append(command)

    monkeypatch.setattr(intus_server, "put_import_source", put)
    monkeypatch.setattr(intus_server, "publish_import_3mf_command", publish)
    return stored, published


def _post(
    client,
    *,
    name="falcon9",
    filename="falcon9.3mf",
    content=b"3MF",
    media_type=THREE_MF_MEDIA_TYPE,
):
    return client.post(
        "/projects/imports/3mf",
        data={"project_name": name},
        files={"file": (filename, content, media_type)},
    )


def test_import_3mf_creates_new_project_and_queues(
    authenticated_intus_client, db_session, seeded_tenant, import_transport
):
    stored, published = import_transport

    response = _post(authenticated_intus_client)

    assert response.status_code == 202
    assert response.json().keys() == {"success", "job_id", "project_name", "status"}
    assert response.json() == {
        "success": True,
        "job_id": response.json()["job_id"],
        "project_name": "falcon9",
        "status": "queued",
    }
    project = db_session.scalar(select(Project).where(Project.name == "falcon9"))
    assert project is not None
    asset = db_session.scalars(
        select(ProjectAsset).where(ProjectAsset.project_id == project.id)
    ).one()
    assert (
        ProjectImportRepository(db_session, seeded_tenant.tenant_id).assets.get_content(
            asset.id
        )
        == b"3MF"
    )
    job = db_session.scalars(
        select(ProjectImportJob).where(ProjectImportJob.project_id == project.id)
    ).one()
    assert stored == [b"3MF"]
    assert len(published) == 1
    command = published[0]
    assert (command.job_id, command.project_id, command.tenant_id, command.user_id) == (
        job.id,
        project.id,
        seeded_tenant.tenant_id,
        seeded_tenant.user_id,
    )
    assert command.attempt == job.attempt == 1
    assert command.execution_id == job.execution_id
    assert command.source.sha256 == sha256(b"3MF").hexdigest()
    assert command.source.byte_size == 3


def test_import_3mf_accepts_generic_octet_stream(
    authenticated_intus_client, import_transport
):
    assert (
        _post(
            authenticated_intus_client, media_type=OCTET_STREAM_MEDIA_TYPE
        ).status_code
        == 202
    )


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"data": {"project_name": "falcon9"}},
        {"files": {"file": ("falcon9.3mf", b"3MF", THREE_MF_MEDIA_TYPE)}},
    ],
)
def test_import_3mf_rejects_missing_multipart_fields_as_bad_request(
    authenticated_intus_client, import_transport, request_kwargs
):
    response = authenticated_intus_client.post(
        "/projects/imports/3mf", **request_kwargs
    )
    assert response.status_code == 400
    assert response.json()["error"] in {"invalid_3mf_upload", "invalid_project_name"}
    assert import_transport == ([], [])


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("falcon9.stl", THREE_MF_MEDIA_TYPE),
        ("../falcon9.3mf", THREE_MF_MEDIA_TYPE),
        ("falcon9.3mf", "model/3mf"),
        ("falcon\x00.3mf", THREE_MF_MEDIA_TYPE),
    ],
)
def test_import_3mf_rejects_unsafe_filename_and_content_type(
    authenticated_intus_client, import_transport, filename, media_type
):
    response = _post(
        authenticated_intus_client, filename=filename, media_type=media_type
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_3mf_upload"}
    assert import_transport == ([], [])


def test_import_3mf_rejects_invalid_name_and_collision_without_partial_rows(
    authenticated_intus_client, db_session, import_transport
):
    invalid = _post(authenticated_intus_client, name="bad name")
    collision = _post(authenticated_intus_client, name="default_purlin")

    assert invalid.status_code == 400
    assert invalid.json() == {"error": "invalid_project_name"}
    assert collision.status_code == 409
    assert collision.json() == {"error": "project_name_conflict"}
    assert db_session.scalar(select(Project).where(Project.name == "bad name")) is None
    assert db_session.scalar(select(ProjectImportJob)) is None
    assert db_session.scalar(select(ProjectAsset)) is None


@pytest.mark.parametrize("failed_stage", ["put", "publish"])
def test_import_3mf_rolls_back_database_when_transport_fails(
    authenticated_intus_client, db_session, monkeypatch, failed_stage
):
    async def put(content: bytes) -> ObjectRef:
        if failed_stage == "put":
            raise RuntimeError("private store failure")
        digest = sha256(content).hexdigest()
        return ObjectRef(
            bucket="TERTIUS_ASSETS",
            key=f"sha256/{digest}",
            sha256=digest,
            byte_size=len(content),
        )

    async def publish(_command):
        if failed_stage == "publish":
            raise RuntimeError("private publish failure")

    monkeypatch.setattr(intus_server, "put_import_source", put)
    monkeypatch.setattr(intus_server, "publish_import_3mf_command", publish)

    response = _post(authenticated_intus_client)

    assert response.status_code == 503
    assert response.json() == {"error": "import_unavailable"}
    assert "private" not in response.text
    assert db_session.scalar(select(Project).where(Project.name == "falcon9")) is None
    assert db_session.scalar(select(ProjectImportJob)) is None
    assert db_session.scalar(select(ProjectAsset)) is None


def test_import_3mf_rejects_guest_before_storage(
    authenticated_intus_client, seeded_tenant, import_transport
):
    intus_server.app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id=seeded_tenant.user_id,
        tenant_id=seeded_tenant.tenant_id,
        keycloak_subject="guest",
        email=None,
        roles=frozenset({"guest"}),
    )
    responses = (
        _post(authenticated_intus_client),
        authenticated_intus_client.get(f"/projects/imports/3mf/jobs/{uuid4()}"),
        authenticated_intus_client.post(f"/projects/imports/3mf/jobs/{uuid4()}/retry"),
    )
    assert all(response.status_code == 403 for response in responses)
    assert all(
        response.json() == {"error": "authentication_required"}
        for response in responses
    )
    assert import_transport == ([], [])


def test_import_3mf_requires_authentication(db_session):
    intus_server.app.dependency_overrides.clear()
    intus_server.app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(intus_server.app) as client:
            responses = (
                _post(client),
                client.get(f"/projects/imports/3mf/jobs/{uuid4()}"),
                client.post(f"/projects/imports/3mf/jobs/{uuid4()}/retry"),
            )
        assert all(response.status_code in {401, 403} for response in responses)
    finally:
        intus_server.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_bounded_reader_accepts_exact_limit_and_reads_at_most_one_mib(
    monkeypatch,
):
    assert MAX_3MF_UPLOAD_BYTES == 128 * 1024 * 1024
    monkeypatch.setattr(
        intus_server, "MAX_3MF_UPLOAD_BYTES", 3 * intus_server.UPLOAD_CHUNK_BYTES
    )

    class Upload:
        calls = []
        remaining = 3 * intus_server.UPLOAD_CHUNK_BYTES

        async def read(self, size):
            self.calls.append(size)
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

    upload = Upload()
    content = await intus_server._read_bounded_3mf(upload)
    assert len(content) == 3 * intus_server.UPLOAD_CHUNK_BYTES
    assert upload.calls and max(upload.calls) <= 1024 * 1024


@pytest.mark.anyio
async def test_bounded_reader_rejects_one_byte_over_exact_limit(monkeypatch):
    monkeypatch.setattr(intus_server, "MAX_3MF_UPLOAD_BYTES", 2)

    class Upload:
        calls = []
        chunks = iter((b"ab", b"c"))

        async def read(self, size):
            self.calls.append(size)
            return next(self.chunks, b"")

    upload = Upload()
    with pytest.raises(OverflowError):
        await intus_server._read_bounded_3mf(upload)
    assert max(upload.calls) <= 1024 * 1024


def test_status_is_tenant_scoped_and_has_no_private_asset_fields(
    authenticated_intus_client, db_session, seeded_tenant, import_transport
):
    created = _post(authenticated_intus_client).json()
    job = db_session.get(ProjectImportJob, created["job_id"])
    job.status = "running"
    job.progress_payload = {
        "stage": "converting",
        "percent": 25,
        "execution_id": "PRIVATE_EXECUTION",
        "job_id": "PRIVATE_JOB_COPY",
        "attempt": 7,
    }
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/imports/3mf/jobs/{job.id}")
    assert response.status_code == 200
    assert response.json() == {
        "job_id": str(job.id),
        "project_name": "falcon9",
        "status": "running",
        "progress": {"stage": "converting", "percent": 25},
        "warnings": [],
        "error_code": None,
        "user_message": None,
        "retryable": False,
        "manifest": None,
    }
    serialized = response.text.lower()
    for private_name in (
        "content",
        "object",
        "bucket",
        "sha256",
        "execution_id",
        "source_asset_id",
        "tenant_id",
        "project_id",
    ):
        assert private_name not in serialized

    intus_server.app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="other", email=None
    )
    isolated = authenticated_intus_client.get(f"/projects/imports/3mf/jobs/{job.id}")
    assert isolated.status_code == 404
    assert isolated.json() == {"error": "import_job_not_found"}


def test_succeeded_status_exposes_only_bounded_public_manifest(
    authenticated_intus_client, db_session, seeded_tenant, import_transport
):
    created = _post(authenticated_intus_client).json()
    job = db_session.get(ProjectImportJob, created["job_id"])
    source = ProjectImportRepository(
        db_session, seeded_tenant.tenant_id
    ).assets.get_metadata(job.source_asset_id)
    brep = b"BREP"
    manifest = Import3mfManifest.model_validate(
        {
            "schema_version": 1,
            "conversion_version": IMPORT_3MF_CONVERSION_VERSION,
            "source_sha256": source.sha256,
            "brep_sha256": sha256(brep).hexdigest(),
            "brep_byte_size": len(brep),
            "source_unit": "MM",
            "scale_to_mm": 1.0,
            "object_count": 1,
            "total_vertices": 8,
            "total_triangles": 12,
            "warnings": ("Faceted geometry",),
            "parts": (
                {
                    "index": 0,
                    "name": "part_001",
                    "source_name": "PRIVATE_SOURCE_NAME",
                    "shape_type": "solid",
                    "boolean_capable": True,
                    "is_valid": True,
                    "vertex_count": 8,
                    "triangle_count": 12,
                    "bounds_mm": {"min": (0.0, 0.0, 0.0), "max": (1.0, 2.0, 3.0)},
                },
            ),
        }
    )
    ProjectImportRepository(db_session, seeded_tenant.tenant_id).apply_success(
        job_id=job.id,
        execution_id=job.execution_id,
        source_sha256=source.sha256,
        brep_content=brep,
        manifest_content=manifest.model_dump_json().encode(),
        user_id=seeded_tenant.user_id,
    )
    db_session.commit()

    response = authenticated_intus_client.get(f"/projects/imports/3mf/jobs/{job.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["warnings"] == ["Faceted geometry"]
    assert payload["manifest"]["parts"][0].keys() == {
        "index",
        "name",
        "shape_type",
        "boolean_capable",
        "is_valid",
        "bounds_mm",
    }
    assert "PRIVATE_SOURCE_NAME" not in response.text
    assert "sha256" not in response.text.lower()


def test_import_command_captures_current_trace_context(
    authenticated_intus_client, import_transport, monkeypatch
):
    _stored, published = import_transport

    def inject(headers):
        headers["traceparent"] = (
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        )
        headers["tracestate"] = "vendor=value"

    monkeypatch.setattr(intus_server.propagate, "inject", inject)
    assert _post(authenticated_intus_client).status_code == 202
    assert (
        published[0].traceparent
        == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert published[0].tracestate == "vendor=value"


def test_failed_import_can_retry_with_same_source_and_new_execution(
    authenticated_intus_client, db_session, seeded_tenant, import_transport
):
    _stored, published = import_transport
    created = _post(authenticated_intus_client).json()
    job = db_session.get(ProjectImportJob, created["job_id"])
    original_execution = job.execution_id
    original_source = job.source_asset_id
    ProjectImportRepository(db_session, seeded_tenant.tenant_id).mark_failed(
        job.id,
        job.execution_id,
        error="conversion failed",
        error_code="invalid_3mf",
        user_message="This 3MF could not be converted.",
        retryable=True,
    )
    db_session.commit()

    failed_status = authenticated_intus_client.get(
        f"/projects/imports/3mf/jobs/{job.id}"
    )
    assert failed_status.status_code == 200
    assert failed_status.json()["status"] == "failed"
    assert failed_status.json()["error_code"] == "invalid_3mf"
    assert failed_status.json()["user_message"] == "This 3MF could not be converted."
    assert failed_status.json()["retryable"] is True
    assert "conversion failed" not in failed_status.text

    response = authenticated_intus_client.post(
        f"/projects/imports/3mf/jobs/{job.id}/retry"
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    db_session.refresh(job)
    assert job.attempt == 2
    assert job.execution_id != original_execution
    assert job.source_asset_id == original_source
    retry_command = published[-1]
    assert retry_command.attempt == 2
    assert retry_command.execution_id == job.execution_id
    assert retry_command.source.sha256 == published[0].source.sha256


def test_retry_rejects_active_job_with_exact_conflict(
    authenticated_intus_client, import_transport
):
    created = _post(authenticated_intus_client).json()
    response = authenticated_intus_client.post(
        f"/projects/imports/3mf/jobs/{created['job_id']}/retry"
    )
    assert response.status_code == 409
    assert response.json() == {"error": "import_already_active"}


def test_retry_publish_failure_restores_failed_attempt(
    authenticated_intus_client, db_session, seeded_tenant, import_transport, monkeypatch
):
    created = _post(authenticated_intus_client).json()
    job = db_session.get(ProjectImportJob, created["job_id"])
    original_execution = job.execution_id
    ProjectImportRepository(db_session, seeded_tenant.tenant_id).mark_failed(
        job.id,
        job.execution_id,
        error="conversion failed",
        error_code="invalid_3mf",
        user_message="This 3MF could not be converted.",
        retryable=True,
    )
    db_session.commit()

    async def fail_publish(_command):
        raise RuntimeError("private publish failure")

    monkeypatch.setattr(intus_server, "publish_import_3mf_command", fail_publish)
    response = authenticated_intus_client.post(
        f"/projects/imports/3mf/jobs/{job.id}/retry"
    )

    assert response.status_code == 503
    assert response.json() == {"error": "import_unavailable"}
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempt == 1
    assert job.execution_id == original_execution
    assert job.retryable is True
