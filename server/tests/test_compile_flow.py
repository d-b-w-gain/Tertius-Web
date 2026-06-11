from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from core.models import Artifact, CompileJob, ProjectFile
from workflows.intus import intus_server


def test_compile_records_failed_job_for_invalid_code(authenticated_intus_client, db_session):
    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "raise RuntimeError('boom')", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    job = db_session.scalar(select(CompileJob))
    assert job.status == "failed"
    assert "RuntimeError" in job.error
    assert db_session.scalar(select(Artifact)) is None


def test_compile_rejects_invalid_filename(authenticated_intus_client):
    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "x = 1", "export_format": "stl", "file": "../design.py"},
    )

    assert response.status_code == 400


def test_compile_records_artifact_for_successful_sandbox(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    tmp_path,
):
    output_path = tmp_path / "output.stl"
    output_path.write_bytes(b"solid mocked")

    def fake_run_compile_sandbox(project_dir: Path, export_format: str, timeout_seconds: int = 30):
        assert export_format == "stl"
        assert (project_dir / "design.py").read_text(encoding="utf-8") == "shape = 'updated'\n"
        return SimpleNamespace(
            success=True,
            output_path=output_path,
            stdout="compiled",
            stderr="",
            error=None,
        )

    monkeypatch.setattr(intus_server, "run_compile_sandbox", fake_run_compile_sandbox, raising=False)
    monkeypatch.setattr(
        intus_server,
        "get_settings",
        lambda: SimpleNamespace(artifact_retention_limit=10),
        raising=False,
    )

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "shape = 'updated'\n", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["format"] == "stl"
    assert response.json()["artifact_id"]

    job = db_session.scalar(select(CompileJob))
    artifact = db_session.scalar(select(Artifact))
    saved_file = db_session.scalar(select(ProjectFile).where(ProjectFile.filename == "design.py"))

    assert job.status == "succeeded"
    assert job.project_id == seeded_tenant.project_id
    assert artifact.compile_job_id == job.id
    assert artifact.project_id == seeded_tenant.project_id
    assert artifact.content == b"solid mocked"
    assert artifact.byte_size == len(b"solid mocked")
    assert saved_file.content == "shape = 'updated'\n"


def test_compile_prunes_old_artifact_rows_after_successful_compile(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    tmp_path,
):
    old_artifact = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        kind="stl",
        storage_key="old.stl",
        content_type="application/octet-stream",
        byte_size=len(b"solid old"),
        content=b"solid old",
    )
    other_artifact = Artifact(
        id=uuid4(),
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        kind="step",
        storage_key="other.step",
        content_type="application/step",
        byte_size=len(b"ISO-10303"),
        content=b"ISO-10303",
    )
    db_session.add_all([old_artifact, other_artifact])
    db_session.commit()

    output_path = tmp_path / "output.stl"
    output_path.write_bytes(b"solid new")

    def fake_run_compile_sandbox(project_dir: Path, export_format: str, timeout_seconds: int = 30):
        return SimpleNamespace(
            success=True,
            output_path=output_path,
            stdout="compiled",
            stderr="",
            error=None,
        )

    monkeypatch.setattr(intus_server, "run_compile_sandbox", fake_run_compile_sandbox, raising=False)
    monkeypatch.setattr(
        intus_server,
        "get_settings",
        lambda: SimpleNamespace(artifact_retention_limit=1),
        raising=False,
    )

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "shape = 'new'\n", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True

    artifacts = db_session.scalars(select(Artifact).order_by(Artifact.kind, Artifact.created_at)).all()
    new_artifact = db_session.get(Artifact, response.json()["artifact_id"])
    assert [(artifact.kind, artifact.content) for artifact in artifacts] == [
        ("step", b"ISO-10303"),
        ("stl", b"solid new"),
    ]
    assert new_artifact.storage_key.endswith(".stl")
    assert db_session.get(Artifact, old_artifact.id) is None
