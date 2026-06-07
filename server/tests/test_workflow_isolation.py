from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from core.artifacts import ArtifactStore
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
    monkeypatch,
    tmp_path,
):
    artifact_root = tmp_path / "artifacts"
    store = ArtifactStore(artifact_root)

    other_user = AppUser(id=uuid4(), keycloak_subject="kc-other", email="other@example.com")
    other_tenant = Tenant(id=uuid4(), name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    other_project = Project(tenant_id=other_tenant.id, name="default_purlin", created_by=other_user.id)
    db_session.add(other_project)
    db_session.flush()

    other_stored = store.write_bytes(other_tenant.id, other_project.id, "stl", b"solid other")
    older_stored = store.write_bytes(seeded_tenant.tenant_id, seeded_tenant.project_id, "stl", b"solid older")
    latest_stored = store.write_bytes(seeded_tenant.tenant_id, seeded_tenant.project_id, "stl", b"solid latest")
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Artifact(
                tenant_id=other_tenant.id,
                project_id=other_project.id,
                kind="stl",
                storage_key=other_stored.storage_key,
                content_type=other_stored.content_type,
                byte_size=other_stored.byte_size,
                created_at=now + timedelta(minutes=10),
            ),
            Artifact(
                tenant_id=seeded_tenant.tenant_id,
                project_id=seeded_tenant.project_id,
                kind="stl",
                storage_key=older_stored.storage_key,
                content_type=older_stored.content_type,
                byte_size=older_stored.byte_size,
                created_at=now,
            ),
            Artifact(
                tenant_id=seeded_tenant.tenant_id,
                project_id=seeded_tenant.project_id,
                kind="stl",
                storage_key=latest_stored.storage_key,
                content_type=latest_stored.content_type,
                byte_size=latest_stored.byte_size,
                created_at=now + timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        extus_server,
        "get_settings",
        lambda: type("Settings", (), {"artifact_root": str(artifact_root)})(),
        raising=False,
    )

    project_response = authenticated_extus_client.get("/project_name")
    status_response = authenticated_extus_client.get("/status")
    model_response = authenticated_extus_client.get("/model")

    assert project_response.status_code == 200
    assert project_response.json() == {"project_name": "default_purlin"}
    assert status_response.status_code == 200
    assert status_response.json()["mtime"] > 0
    assert model_response.status_code == 200
    assert model_response.content == b"solid latest"


def test_extus_model_returns_404_when_active_artifact_file_is_missing(
    authenticated_extus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    tmp_path,
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
    monkeypatch.setattr(
        extus_server,
        "get_settings",
        lambda: type("Settings", (), {"artifact_root": str(tmp_path / "artifacts")})(),
        raising=False,
    )

    response = authenticated_extus_client.get("/model")

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
    tmp_path,
):
    tenant_code = "TENANT_DESIGN = True"
    global_code = "GLOBAL_DESIGN = True"
    db_design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    db_design.content = tenant_code
    db_session.commit()
    global_project = tmp_path / "default_purlin"
    global_project.mkdir()
    (global_project / "design.py").write_text(global_code, encoding="utf-8")

    captured = {}

    class FakeBox:
        min = type("Point", (), {"X": 0, "Y": 0, "Z": 0})()
        max = type("Point", (), {"X": 12, "Y": 6, "Z": 3})()

    class FakeCompound:
        def bounding_box(self):
            return FakeBox()

    def fake_compound_from_code(code):
        captured["code"] = code
        return FakeCompound()

    monkeypatch.setattr(timus_server, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(timus_server, "get_compound_from_code", fake_compound_from_code)

    response = authenticated_timus_client.get("/projects/default_purlin/bounds")

    assert response.status_code == 200
    assert response.json() == {"max_dim": 12}
    assert captured["code"] == tenant_code


def test_timus_drafting_pdf_does_not_read_other_or_global_design(
    authenticated_timus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    tmp_path,
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
    global_project = tmp_path / "default_purlin"
    global_project.mkdir()
    (global_project / "design.py").write_text("GLOBAL_DESIGN = True", encoding="utf-8")

    monkeypatch.setattr(timus_server, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(timus_server, "get_compound_from_code", lambda code: object())
    monkeypatch.setattr(
        timus_server,
        "get_projected_views",
        lambda name, compound, mtime: {"top": object(), "front": object(), "side": object(), "iso": object()},
    )
    monkeypatch.setattr(timus_server, "_draw_compound_view", lambda *args, **kwargs: None)

    response = authenticated_timus_client.get("/projects/default_purlin/drafting.pdf")

    assert response.status_code == 404
