from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from core.models import (
    Artifact,
    AppUser,
    Project,
    ProjectFile,
    SourceSnapshot,
    Tenant,
    TenantMembership,
    TimusSettings,
    UserWorkspaceState,
)
from workflows.extus import extus_server
from workflows.timus import timus_server


def create_named_project(db_session, seeded_tenant, name: str) -> Project:
    project = Project(tenant_id=seeded_tenant.tenant_id, name=name, created_by=seeded_tenant.user_id)
    db_session.add(project)
    db_session.flush()
    db_session.add(
        ProjectFile(
            tenant_id=seeded_tenant.tenant_id,
            project_id=project.id,
            filename="design.py",
            content="import build123d as bd\nlength = 200\n",
        )
    )
    db_session.commit()
    return project


def assert_active_project(db_session, seeded_tenant, project: Project):
    state = db_session.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == seeded_tenant.user_id,
            UserWorkspaceState.tenant_id == seeded_tenant.tenant_id,
        )
    )
    assert state.active_project_id == project.id


def test_artus_activate_project_uses_repository_workspace_state(
    authenticated_artus_client,
    db_session,
    seeded_tenant,
):
    project = create_named_project(db_session, seeded_tenant, "artus_project")

    response = authenticated_artus_client.post("/projects/artus_project/activate")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert_active_project(db_session, seeded_tenant, project)


def test_extus_activate_project_uses_repository_workspace_state(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    project = create_named_project(db_session, seeded_tenant, "extus_project")

    response = authenticated_extus_client.post("/projects/extus_project/activate")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert_active_project(db_session, seeded_tenant, project)


def test_timus_activate_project_uses_repository_workspace_state(
    authenticated_timus_client,
    db_session,
    seeded_tenant,
):
    project = create_named_project(db_session, seeded_tenant, "timus_project")

    response = authenticated_timus_client.post("/projects/timus_project/activate")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert_active_project(db_session, seeded_tenant, project)


def test_artus_features_and_updates_use_authenticated_workspace(
    authenticated_artus_client,
    db_session,
    seeded_tenant,
):
    features_response = authenticated_artus_client.get("/features")

    assert features_response.status_code == 200
    assert features_response.json()["project_name"] == "default_purlin"
    assert features_response.json()["features"] == [
        {"name": "length", "value": 100, "type": "int", "description": ""}
    ]

    update_response = authenticated_artus_client.post("/update_features", json={"updates": {"length": 125}})

    assert update_response.status_code == 200
    assert update_response.json() == {"success": True}

    saved_file = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    assert saved_file.content == "import build123d as bd\nlength = 125\n"

    snapshot = db_session.scalar(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == seeded_tenant.tenant_id,
            SourceSnapshot.project_id == seeded_tenant.project_id,
        )
    )
    assert snapshot is not None
    assert snapshot.message == "Updated features via Artus"


def test_extus_serves_latest_authenticated_tenant_artifact(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    other_user = AppUser(id=uuid4(), keycloak_subject="kc-other", email="other@example.com")
    other_tenant = Tenant(id=uuid4(), name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    other_project = Project(tenant_id=other_tenant.id, name="default_purlin", created_by=other_user.id)
    db_session.add(other_project)
    db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Artifact(
                tenant_id=other_tenant.id,
                project_id=other_project.id,
                kind="stl",
                storage_key="other.stl",
                content_type="application/octet-stream",
                byte_size=len(b"solid other"),
                content=b"solid other",
                created_at=now + timedelta(minutes=10),
            ),
            Artifact(
                tenant_id=seeded_tenant.tenant_id,
                project_id=seeded_tenant.project_id,
                kind="stl",
                storage_key="older.stl",
                content_type="application/octet-stream",
                byte_size=len(b"solid older"),
                content=b"solid older",
                created_at=now,
            ),
            Artifact(
                tenant_id=seeded_tenant.tenant_id,
                project_id=seeded_tenant.project_id,
                kind="stl",
                storage_key="latest.stl",
                content_type="application/octet-stream",
                byte_size=len(b"solid latest"),
                content=b"solid latest",
                created_at=now + timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()

    project_response = authenticated_extus_client.get("/project_name")
    status_response = authenticated_extus_client.get("/status")
    model_response = authenticated_extus_client.get("/model")

    assert project_response.status_code == 200
    assert project_response.json() == {"project_name": "default_purlin"}
    assert status_response.status_code == 200
    assert status_response.json()["mtime"] > 0
    assert model_response.status_code == 200
    assert model_response.content == b"solid latest"


def test_extus_model_returns_404_when_active_artifact_content_is_missing(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    db_session.add(
        Artifact(
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            kind="stl",
            storage_key=f"{seeded_tenant.tenant_id}/{seeded_tenant.project_id}/missing.stl",
            content_type="application/octet-stream",
            byte_size=10,
        )
    )
    db_session.commit()

    response = authenticated_extus_client.get("/model")

    assert response.status_code == 404


def test_extus_serves_historical_model_artifact_by_id(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    now = datetime.now(timezone.utc)
    older = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        kind="glb",
        storage_key="older.glb",
        content_type="model/gltf-binary",
        byte_size=len(b"older glb"),
        content=b"older glb",
        created_at=now,
    )
    newer = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        kind="glb",
        storage_key="newer.glb",
        content_type="model/gltf-binary",
        byte_size=len(b"newer glb"),
        content=b"newer glb",
        created_at=now + timedelta(minutes=1),
    )
    db_session.add_all([older, newer])
    db_session.commit()

    latest_response = authenticated_extus_client.get("/model")
    historical_response = authenticated_extus_client.get(f"/artifacts/{older.id}/model")

    assert latest_response.status_code == 200
    assert latest_response.content == b"newer glb"
    assert historical_response.status_code == 200
    assert historical_response.content == b"older glb"
    assert historical_response.headers["content-type"] == "model/gltf-binary"


def test_extus_historical_model_rejects_cross_tenant_artifact(
    authenticated_extus_client,
    db_session,
):
    other_user = AppUser(id=uuid4(), keycloak_subject="kc-other", email="other@example.com")
    other_tenant = Tenant(id=uuid4(), name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    other_project = Project(tenant_id=other_tenant.id, name="default_purlin", created_by=other_user.id)
    db_session.add(other_project)
    db_session.flush()
    artifact = Artifact(
        tenant_id=other_tenant.id,
        project_id=other_project.id,
        kind="glb",
        storage_key="other.glb",
        content_type="model/gltf-binary",
        byte_size=len(b"other glb"),
        content=b"other glb",
    )
    db_session.add(artifact)
    db_session.commit()

    response = authenticated_extus_client.get(f"/artifacts/{artifact.id}/model")

    assert response.status_code == 404


def test_extus_historical_model_rejects_inactive_project_artifact(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    other_project = create_named_project(db_session, seeded_tenant, "inactive_project")
    artifact = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=other_project.id,
        kind="glb",
        storage_key="inactive.glb",
        content_type="model/gltf-binary",
        byte_size=len(b"inactive glb"),
        content=b"inactive glb",
    )
    db_session.add(artifact)
    db_session.commit()

    response = authenticated_extus_client.get(f"/artifacts/{artifact.id}/model")

    assert response.status_code == 404


def test_extus_historical_model_allows_named_project_artifact(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    other_project = create_named_project(db_session, seeded_tenant, "inactive_project")
    artifact = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=other_project.id,
        kind="glb",
        storage_key="inactive.glb",
        content_type="model/gltf-binary",
        byte_size=len(b"inactive glb"),
        content=b"inactive glb",
    )
    db_session.add(artifact)
    db_session.commit()

    response = authenticated_extus_client.get(f"/artifacts/{artifact.id}/model?project=inactive_project")

    assert response.status_code == 200
    assert response.content == b"inactive glb"


def test_extus_historical_model_rejects_non_model_artifact_kind(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    artifact = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        kind="pdf",
        storage_key="drawing.pdf",
        content_type="application/pdf",
        byte_size=len(b"pdf"),
        content=b"pdf",
    )
    db_session.add(artifact)
    db_session.commit()

    response = authenticated_extus_client.get(f"/artifacts/{artifact.id}/model")

    assert response.status_code == 404


def test_extus_historical_model_returns_404_when_content_is_missing(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
):
    artifact = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        kind="glb",
        storage_key="missing.glb",
        content_type="model/gltf-binary",
        byte_size=10,
    )
    db_session.add(artifact)
    db_session.commit()

    response = authenticated_extus_client.get(f"/artifacts/{artifact.id}/model")

    assert response.status_code == 404


def test_timus_settings_round_trip(authenticated_timus_client, db_session, seeded_tenant):
    payload = {
        "title": "PART A",
        "stamp_text": "APPROVED",
        "show_redline": True,
        "show_hidden_lines": False,
        "scale": 0.25,
        "sheet_size": "A3",
    }

    default_response = authenticated_timus_client.get("/projects/default_purlin/settings")
    save_response = authenticated_timus_client.put("/projects/default_purlin/settings", json=payload)
    load_response = authenticated_timus_client.get("/projects/default_purlin/settings")

    assert default_response.status_code == 200
    assert default_response.json()["sheet_size"] == "A4"
    assert save_response.status_code == 200
    assert save_response.json() == {"success": True}
    assert load_response.status_code == 200
    assert load_response.json() == payload

    settings = db_session.scalar(
        select(TimusSettings).where(
            TimusSettings.user_id == seeded_tenant.user_id,
            TimusSettings.tenant_id == seeded_tenant.tenant_id,
            TimusSettings.project_id == seeded_tenant.project_id,
        )
    )
    assert settings is not None


def test_timus_settings_validate_sheet_size(authenticated_timus_client):
    response = authenticated_timus_client.put(
        "/projects/default_purlin/settings",
        json={
            "title": "PART A",
            "stamp_text": "APPROVED",
            "show_redline": True,
            "show_hidden_lines": False,
            "scale": 1.0,
            "sheet_size": "LETTER",
        },
    )

    assert response.status_code == 422


def test_timus_bounds_use_authenticated_tenant_db_design(
    authenticated_timus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
):
    tenant_code = "TENANT_DESIGN = True"
    db_design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    db_design.content = tenant_code
    db_session.add(
        ProjectFile(
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            filename="shared.py",
            content="SHARED_VALUE = 12",
        )
    )
    db_session.commit()

    captured = {}

    def fake_run_compile_sandbox(project_dir, export_format, quality=None, timeout_seconds=30):
        captured["export_format"] = export_format
        captured["design"] = (project_dir / "design.py").read_text(encoding="utf-8")
        captured["shared"] = (project_dir / "shared.py").read_text(encoding="utf-8")
        output_path = project_dir / "output.timus_bounds"
        output_path.write_text('{"max_dim": 12}', encoding="utf-8")
        return SimpleNamespace(success=True, output_path=output_path, error=None)

    monkeypatch.setattr(timus_server, "run_compile_sandbox", fake_run_compile_sandbox)

    response = authenticated_timus_client.get("/projects/default_purlin/bounds")

    assert response.status_code == 200
    assert response.json() == {"max_dim": 12}
    assert captured == {
        "export_format": "timus_bounds",
        "design": tenant_code,
        "shared": "SHARED_VALUE = 12",
    }


def test_timus_drafting_pdf_does_not_read_other_or_global_design(
    authenticated_timus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
):
    db_design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    workspace = db_session.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == seeded_tenant.user_id,
            UserWorkspaceState.tenant_id == seeded_tenant.tenant_id,
        )
    )
    workspace.active_file_id = None
    db_session.flush()
    db_session.delete(db_design)

    other_user = AppUser(id=uuid4(), keycloak_subject="kc-other", email="other@example.com")
    other_tenant = Tenant(id=uuid4(), name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    other_project = Project(tenant_id=other_tenant.id, name="default_purlin", created_by=other_user.id)
    db_session.add(other_project)
    db_session.flush()
    db_session.add(
        ProjectFile(
            tenant_id=other_tenant.id,
            project_id=other_project.id,
            filename="design.py",
            content="OTHER_TENANT_DESIGN = True",
        )
    )
    db_session.commit()

    monkeypatch.setattr(timus_server, "get_compound_from_code", lambda code, project_dir=None: object())
    monkeypatch.setattr(
        timus_server,
        "get_projected_views",
        lambda name, compound, mtime: {"top": object(), "front": object(), "side": object(), "iso": object()},
    )
    monkeypatch.setattr(timus_server, "_draw_compound_view", lambda *args, **kwargs: None)

    response = authenticated_timus_client.get("/projects/default_purlin/drafting.pdf")

    assert response.status_code == 404


def test_timus_drafting_pdf_cache_is_scoped_by_tenant_and_project(
    authenticated_timus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
):
    db_design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    timus_server.PROJECTION_CACHE.clear()
    poisoned_views = {"top": ["poison"], "front": ["poison"], "side": ["poison"], "iso": ["poison"]}
    timus_server.PROJECTION_CACHE["default_purlin"] = (db_design.updated_at.timestamp(), poisoned_views)
    current_views = b'{"top":[[[0,0],[1,0],false]],"front":[[[0,0],[0,1],false]],"side":[[[0,0],[1,1],false]],"iso":[[[0,0],[2,2],false]]}'
    db_session.add(
        Artifact(
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            compile_job_id=None,
            kind="timus_views",
            storage_key="test/current-timus-views.json",
            content_type="application/json",
            byte_size=len(current_views),
            content=current_views,
        )
    )
    db_session.commit()

    drawn_segments = []

    monkeypatch.setattr(
        timus_server,
        "get_compound_from_code",
        lambda code, project_dir=None: (_ for _ in ()).throw(
            AssertionError("PDF download must use the stored timus_views artifact")
        ),
    )
    monkeypatch.setattr(timus_server, "_draw_drafting_sheet_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(timus_server, "_draw_gorton_text", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        timus_server,
        "_draw_compound_view",
        lambda pdf, segments, *args, **kwargs: drawn_segments.append(segments),
    )

    response = authenticated_timus_client.get("/projects/default_purlin/drafting.pdf")

    assert response.status_code == 200
    assert ["poison"] not in drawn_segments
    assert drawn_segments
