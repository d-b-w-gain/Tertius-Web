import io
import inspect
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.compile_messages import CompileBinaryAsset
from core.models import Artifact, CompileJob, Project, ProjectFile, Tenant
from core.object_store import ObjectRef
from core.project_assets import SOURCE_3MF_MEDIA_TYPE, generated_3mf_design_source
from core.repositories import CompileRepository
from workflows.intus import intus_server
from tests.fixtures.three_mf import TRANSLATED_BUILD_TRANSFORM, box_mesh, make_3mf, make_box_3mf


def test_ui_proxy_accepts_bounded_3mf_uploads():
    nginx_config = (
        Path(__file__).parents[2] / "infra/deploy/nginx/default.conf.template"
    ).read_text(encoding="utf-8")

    assert "location /api/ {\n        client_max_body_size 129m;" in nginx_config


def test_import_endpoint_is_sync_and_reads_spooled_file(monkeypatch):
    content = make_box_3mf()
    upload = UploadFile(filename="source.3mf", file=io.BytesIO(content))

    async def forbid_async_read(*_args, **_kwargs):
        raise AssertionError("UploadFile.read must not run on the event loop")

    upload.read = forbid_async_read

    class FakeProjectRepository:
        def __init__(self, *_args):
            pass

        def get_project(self, _name):
            return None

    captured = {}

    def fake_create(_db, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(intus_server, "ProjectRepository", FakeProjectRepository)
    monkeypatch.setattr(intus_server, "validate_3mf_archive_bytes", lambda value: None)
    monkeypatch.setattr(intus_server, "create_imported_3mf_project", fake_create)

    assert inspect.iscoroutinefunction(intus_server.import_3mf_project) is False
    result = intus_server.import_3mf_project(
        name="threadpool_import",
        file=upload,
        ctx=SimpleNamespace(tenant_id=uuid4(), user_id=uuid4()),
        db=object(),
    )

    assert result == {"success": True, "project": "threadpool_import"}
    assert captured["content"] == content


@pytest.mark.parametrize(
    ("constraint_name", "expected_exception"),
    [
        ("uq_project_name_per_tenant", "ProjectNameConflictError"),
        ("uq_project_file_name", "IntegrityError"),
    ],
)
def test_import_creation_translates_only_project_name_integrity_error(
    monkeypatch, constraint_name, expected_exception
):
    class DriverError(Exception):
        diag = SimpleNamespace(constraint_name=constraint_name)

    integrity_error = IntegrityError("insert project", {}, DriverError())

    def fail_stage(*_args, **_kwargs):
        raise integrity_error

    class FakeDatabase:
        rolled_back = False

        def rollback(self):
            self.rolled_back = True

    db = FakeDatabase()
    monkeypatch.setattr(intus_server.ProjectRepository, "stage_project", fail_stage)

    with pytest.raises(Exception) as raised:
        intus_server.create_imported_3mf_project(
            db,
            tenant_id=uuid4(),
            user_id=uuid4(),
            name="raced_import",
            content=make_box_3mf(),
        )

    assert type(raised.value).__name__ == expected_exception
    assert db.rolled_back is True


def test_import_endpoint_returns_conflict_for_project_name_race(monkeypatch):
    class FakeProjectRepository:
        def __init__(self, *_args):
            pass

        def get_project(self, _name):
            return None

    def fail_create(*_args, **_kwargs):
        raise intus_server.ProjectNameConflictError("Project already exists")

    monkeypatch.setattr(intus_server, "ProjectRepository", FakeProjectRepository)
    monkeypatch.setattr(intus_server, "validate_3mf_archive_bytes", lambda value: None)
    monkeypatch.setattr(intus_server, "create_imported_3mf_project", fail_create)

    response = intus_server.import_3mf_project(
        name="raced_import",
        file=UploadFile(filename="source.3mf", file=io.BytesIO(make_box_3mf())),
        ctx=SimpleNamespace(tenant_id=uuid4(), user_id=uuid4()),
        db=object(),
    )

    assert response.status_code == 409
    assert response.body == b'{"error":"Project already exists"}'


def test_import_endpoint_rejects_unsupported_graph_before_create(monkeypatch):
    vertices, triangles = box_mesh()
    content = make_3mf(
        objects=[("Box", vertices, triangles)],
        build_transform=TRANSLATED_BUILD_TRANSFORM,
    )

    class FakeProjectRepository:
        def __init__(self, *_args):
            pass

        def get_project(self, _name):
            return None

    def fail_create(*_args, **_kwargs):
        raise AssertionError("unsupported graph must not start the project transaction")

    monkeypatch.setattr(intus_server, "ProjectRepository", FakeProjectRepository)
    monkeypatch.setattr(intus_server, "create_imported_3mf_project", fail_create)

    response = intus_server.import_3mf_project(
        name="unsupported_graph",
        file=UploadFile(filename="source.3mf", file=io.BytesIO(content)),
        ctx=SimpleNamespace(tenant_id=uuid4(), user_id=uuid4()),
        db=object(),
    )

    assert response.status_code == 400
    assert response.body == (
        b'{"error":"The file uses an unsupported 3MF build graph."}'
    )


def test_authenticated_import_creates_project_design_and_source_atomically(
    authenticated_intus_client, db_session, seeded_tenant
):
    content = make_box_3mf(size=10)

    response = authenticated_intus_client.post(
        "/projects/imports/3mf",
        data={"name": "imported_cube"},
        files={"file": ("cube.3mf", io.BytesIO(content), SOURCE_3MF_MEDIA_TYPE)},
    )

    assert response.status_code == 201
    assert response.json() == {"success": True, "project": "imported_cube"}
    project = db_session.scalar(
        select(Project).where(
            Project.tenant_id == seeded_tenant.tenant_id,
            Project.name == "imported_cube",
        )
    )
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == project.id,
            ProjectFile.filename == "design.py",
        )
    )
    artifact = db_session.scalar(
        select(Artifact).where(
            Artifact.tenant_id == seeded_tenant.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind == "source_3mf",
        )
    )
    assert design.content == generated_3mf_design_source()
    assert artifact.compile_job_id is None
    assert artifact.content_type == SOURCE_3MF_MEDIA_TYPE
    assert artifact.content == content


def test_import_rejects_duplicate_and_invalid_archive_without_partial_rows(
    authenticated_intus_client, db_session, seeded_tenant
):
    duplicate = authenticated_intus_client.post(
        "/projects/imports/3mf",
        data={"name": "default_purlin"},
        files={"file": ("source.3mf", io.BytesIO(make_box_3mf()), SOURCE_3MF_MEDIA_TYPE)},
    )
    invalid = authenticated_intus_client.post(
        "/projects/imports/3mf",
        data={"name": "invalid_import"},
        files={"file": ("source.3mf", io.BytesIO(b"not a zip"), SOURCE_3MF_MEDIA_TYPE)},
    )

    assert duplicate.status_code == 409
    assert invalid.status_code == 400
    assert db_session.scalar(
        select(Project).where(
            Project.tenant_id == seeded_tenant.tenant_id,
            Project.name == "invalid_import",
        )
    ) is None


def test_import_rejects_unsupported_build_graph_without_project_or_artifact_rows(
    authenticated_intus_client, db_session, seeded_tenant
):
    vertices, triangles = box_mesh()
    content = make_3mf(
        objects=[("Box", vertices, triangles)],
        build_transform=TRANSLATED_BUILD_TRANSFORM,
    )
    artifact_count_before = db_session.scalar(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.tenant_id == seeded_tenant.tenant_id)
    )

    response = authenticated_intus_client.post(
        "/projects/imports/3mf",
        data={"name": "unsupported_graph"},
        files={"file": ("source.3mf", io.BytesIO(content), SOURCE_3MF_MEDIA_TYPE)},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "The file uses an unsupported 3MF build graph."
    }
    assert db_session.scalar(
        select(Project).where(
            Project.tenant_id == seeded_tenant.tenant_id,
            Project.name == "unsupported_graph",
        )
    ) is None
    assert db_session.scalar(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.tenant_id == seeded_tenant.tenant_id)
    ) == artifact_count_before


def test_import_rolls_back_project_when_source_artifact_creation_fails(
    db_session, seeded_tenant, monkeypatch
):
    def fail_record(*_args, **_kwargs):
        raise RuntimeError("artifact write failed")

    monkeypatch.setattr(CompileRepository, "record_artifact", fail_record)

    try:
        intus_server.create_imported_3mf_project(
            db_session,
            tenant_id=seeded_tenant.tenant_id,
            user_id=seeded_tenant.user_id,
            name="atomic_failure",
            content=make_box_3mf(),
        )
    except RuntimeError as exc:
        assert str(exc) == "artifact write failed"
    else:
        raise AssertionError("artifact failure must propagate")

    assert db_session.scalar(
        select(Project).where(
            Project.tenant_id == seeded_tenant.tenant_id,
            Project.name == "atomic_failure",
        )
    ) is None


def test_project_input_lookup_is_tenant_scoped(db_session, seeded_tenant):
    other_tenant = Tenant(id=uuid4(), name="Other Tenant")
    db_session.add(other_tenant)
    db_session.flush()
    other_project = Project(
        tenant_id=other_tenant.id,
        name="other_project",
        created_by=seeded_tenant.user_id,
    )
    db_session.add(other_project)
    db_session.flush()
    foreign_source = CompileRepository(db_session, other_tenant.id).record_artifact(
        other_project.id,
        None,
        "source_3mf",
        make_box_3mf(),
        content_type=SOURCE_3MF_MEDIA_TYPE,
    )
    db_session.commit()

    assert CompileRepository(
        db_session, seeded_tenant.tenant_id
    ).project_input_artifacts(foreign_source.project_id) == []


def test_compile_submission_uses_project_input_as_object_reference(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    source_bytes = make_box_3mf()
    imported = authenticated_intus_client.post(
        "/projects/imports/3mf",
        data={"name": "compile_import"},
        files={"file": ("source.3mf", io.BytesIO(source_bytes), SOURCE_3MF_MEDIA_TYPE)},
    )
    assert imported.status_code == 201
    uploaded = []
    published = []

    async def fake_store(content):
        uploaded.append(content)
        return ObjectRef(
            bucket="TERTIUS_COMPILE_SIDECARS",
            key=f"sha256/{'a' * 64}",
            sha256="a" * 64,
            byte_size=len(content),
        )

    async def fake_publish(command):
        published.append(command)

    monkeypatch.setattr(intus_server, "store_compile_sidecar", fake_store)
    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish)

    response = authenticated_intus_client.post(
        "/projects/compile_import/compile",
        json={
            "code": generated_3mf_design_source(),
            "export_format": "glb",
            "file": "design.py",
        },
    )

    assert response.status_code == 202
    assert uploaded == [source_bytes]
    assert published[0].assets == [
        CompileBinaryAsset(
            logical_filename="source.3mf",
            object_ref=ObjectRef(
                bucket="TERTIUS_COMPILE_SIDECARS",
                key=f"sha256/{'a' * 64}",
                sha256="a" * 64,
                byte_size=len(source_bytes),
            ),
        )
    ]
    job = db_session.get(CompileJob, published[0].job_id)
    snapshots = CompileRepository(
        db_session, seeded_tenant.tenant_id
    ).job_input_artifacts(job.id)
    assert len(snapshots) == 1
    assert snapshots[0].content == source_bytes
    assert "PK" not in published[0].model_dump_json()


def test_sidecar_failure_rolls_back_compile_code_update_and_job(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    source = CompileRepository(
        db_session, seeded_tenant.tenant_id
    ).record_artifact(
        seeded_tenant.project_id,
        None,
        "source_3mf",
        make_box_3mf(),
        content_type=SOURCE_3MF_MEDIA_TYPE,
    )
    db_session.commit()
    assert source.content

    async def fail_store(_content):
        raise RuntimeError("object store unavailable")

    monkeypatch.setattr(intus_server, "store_compile_sidecar", fail_store)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "changed = True\n", "export_format": "glb", "file": "design.py"},
    )

    assert response.status_code == 503
    design = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id)
    )
    jobs = db_session.scalars(
        select(CompileJob).where(CompileJob.project_id == seeded_tenant.project_id)
    ).all()
    assert design.content == "import build123d as bd\nlength = 100\n"
    assert jobs == []


def test_compile_artifact_pruning_does_not_select_project_source_3mf(
    db_session, seeded_tenant
):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    source = repo.record_artifact(
        seeded_tenant.project_id,
        None,
        "source_3mf",
        make_box_3mf(),
        content_type=SOURCE_3MF_MEDIA_TYPE,
    )
    repo.record_artifact(
        seeded_tenant.project_id,
        None,
        "glb",
        b"old-glb",
    )
    db_session.commit()

    prunable = repo.prunable_artifacts(
        seeded_tenant.project_id, "glb", keep_latest=0
    )

    assert source not in prunable
    assert [artifact.kind for artifact in prunable] == ["glb"]
