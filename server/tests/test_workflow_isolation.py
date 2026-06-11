from datetime import datetime, timedelta, timezone
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
    db_session.commit()

    captured = {}

    class FakeBox:
        min = type("Point", (), {"X": 0, "Y": 0, "Z": 0})()
        max = type("Point", (), {"X": 12, "Y": 6, "Z": 3})()

    class FakeCompound:
        def bounding_box(self):
            return FakeBox()

    def fake_compound_from_code(code, project_dir=None):
        captured["code"] = code
        return FakeCompound()

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

    class FakePoint:
        X = 0
        Y = 0
        Z = 0

    class FakeBox:
        min = FakePoint()
        max = type("Point", (), {"X": 10, "Y": 10, "Z": 10})()

        def center(self):
            return FakePoint()

    class FakeCompound:
        def bounding_box(self):
            return FakeBox()

        def project_to_viewport(self, **kwargs):
            return [], []

    drawn_segments = []

    monkeypatch.setattr(timus_server, "get_compound_from_code", lambda code, project_dir=None: FakeCompound())
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
    assert f"{seeded_tenant.tenant_id}:{seeded_tenant.project_id}:default_purlin" in timus_server.PROJECTION_CACHE
