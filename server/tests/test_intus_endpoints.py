from uuid import uuid4

from sqlalchemy import select

from core.models import AppUser, Project, ProjectFile, SourceSnapshot, Tenant, TenantMembership


def test_projects_are_scoped_to_authenticated_tenant(db_session, authenticated_intus_client, seeded_tenant):
    other_user = AppUser(id=uuid4(), keycloak_subject="kc-other")
    other_tenant = Tenant(id=uuid4(), name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    db_session.add(Project(tenant_id=other_tenant.id, name="other_project", created_by=other_user.id))
    db_session.commit()

    response = authenticated_intus_client.get("/projects")

    assert response.status_code == 200
    assert response.json() == {"projects": ["default_purlin"]}


def test_create_save_list_code_git_status_and_delete_are_tenant_scoped(
    db_session, authenticated_intus_client, seeded_tenant
):
    create_response = authenticated_intus_client.post("/projects/new_part/new")
    assert create_response.status_code == 200
    assert create_response.json() == {"success": True, "project": "new_part"}

    duplicate_response = authenticated_intus_client.post("/projects/new_part/new")
    assert duplicate_response.status_code == 400

    save_response = authenticated_intus_client.post(
        "/projects/new_part/save",
        json={"file": "helper.py", "code": "answer = 42\n"},
    )
    assert save_response.status_code == 200
    assert save_response.json() == {"success": True}

    files_response = authenticated_intus_client.get("/projects/new_part/files")
    assert files_response.status_code == 200
    assert files_response.json()["files"] == ["design.py", "helper.py"]

    code_response = authenticated_intus_client.get("/projects/new_part/code", params={"file": "helper.py"})
    assert code_response.status_code == 200
    assert code_response.json() == {"code": "answer = 42\n"}

    file_status_response = authenticated_intus_client.get("/projects/new_part/status", params={"file": "helper.py"})
    assert file_status_response.status_code == 200
    assert file_status_response.json()["mtime"] > 0

    status_response = authenticated_intus_client.get("/projects/new_part/git_status")
    assert status_response.status_code == 200
    assert status_response.json()["is_git"] is True
    assert status_response.json()["history"][0].endswith("Manual save helper.py via Intus")

    delete_response = authenticated_intus_client.delete("/projects/new_part/file", params={"file": "helper.py"})
    assert delete_response.status_code == 200
    assert delete_response.json() == {"success": True}

    remaining_files_response = authenticated_intus_client.get("/projects/new_part/files")
    assert remaining_files_response.status_code == 200
    assert remaining_files_response.json()["files"] == ["design.py"]

    assert db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "helper.py",
        )
    ) is None
    assert db_session.scalar(
        select(SourceSnapshot).where(SourceSnapshot.tenant_id == seeded_tenant.tenant_id)
    ) is not None


def test_intus_rejects_invalid_project_names_and_filenames(authenticated_intus_client):
    invalid_project_response = authenticated_intus_client.post("/projects/bad%20name/new")
    assert invalid_project_response.status_code == 400

    invalid_filename_response = authenticated_intus_client.post(
        "/projects/default_purlin/save",
        json={"file": "../design.py", "code": "x = 1"},
    )
    assert invalid_filename_response.status_code == 400

    missing_project_response = authenticated_intus_client.get("/projects/missing_project/code")
    assert missing_project_response.status_code == 404
