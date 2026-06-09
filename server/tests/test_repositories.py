from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import (
    AppUser,
    Project,
    ProjectFile,
    SourceSnapshot,
    SourceSnapshotFile,
    Tenant,
    TenantMembership,
    UserWorkspaceState,
)
from core.repositories import ProjectRepository, require_valid_project_name, require_valid_python_filename


def seed_two_tenants(db: Session):
    user_a = AppUser(keycloak_subject="a", email="a@example.com")
    user_b = AppUser(keycloak_subject="b", email="b@example.com")
    tenant_a = Tenant(name="Tenant A")
    tenant_b = Tenant(name="Tenant B")
    db.add_all([user_a, user_b, tenant_a, tenant_b])
    db.flush()

    db.add_all(
        [
            TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role="owner"),
            TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role="owner"),
        ]
    )
    project_a = Project(tenant_id=tenant_a.id, name="same_name", created_by=user_a.id)
    project_b = Project(tenant_id=tenant_b.id, name="same_name", created_by=user_b.id)
    db.add_all([project_a, project_b])
    db.flush()

    db.add_all(
        [
            ProjectFile(tenant_id=tenant_a.id, project_id=project_a.id, filename="design.py", content="a = 1"),
            ProjectFile(tenant_id=tenant_a.id, project_id=project_a.id, filename="helper.py", content="helper = 1"),
            ProjectFile(tenant_id=tenant_b.id, project_id=project_b.id, filename="design.py", content="b = 2"),
        ]
    )
    db.commit()
    return {
        "tenant_a": tenant_a.id,
        "tenant_b": tenant_b.id,
        "user_a": user_a.id,
        "user_b": user_b.id,
        "project_a": project_a.id,
        "project_b": project_b.id,
    }


def test_project_repository_only_reads_current_tenant(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = ProjectRepository(db_session, seeded["tenant_a"])
    repo_b = ProjectRepository(db_session, seeded["tenant_b"])

    assert repo_a.list_projects() == ["same_name"]
    assert repo_b.list_projects() == ["same_name"]
    assert repo_a.get_code("same_name", "design.py") == "a = 1"
    assert repo_b.get_code("same_name", "design.py") == "b = 2"
    assert repo_a.list_files("same_name") == ["design.py", "helper.py"]
    assert repo_b.files_for_runtime("same_name") == {"design.py": "b = 2"}


def test_project_repository_create_project_stays_tenant_scoped(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = ProjectRepository(db_session, seeded["tenant_a"])
    repo_b = ProjectRepository(db_session, seeded["tenant_b"])

    project = repo_a.create_project("new_project", seeded["user_a"], "from build123d import *")

    assert project.tenant_id == seeded["tenant_a"]
    assert repo_a.list_projects() == ["new_project", "same_name"]
    assert repo_b.list_projects() == ["same_name"]
    assert repo_a.get_code("new_project", "design.py") == "from build123d import *"
    assert repo_b.get_project("new_project") is None


def test_project_repository_set_active_project_updates_workspace_state_and_active_file(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    project = repo.create_project("active_project", seeded["user_a"], "from build123d import *")
    project_file = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded["tenant_a"],
            ProjectFile.project_id == project.id,
            ProjectFile.filename == "design.py",
        )
    )

    assert repo.set_active_project(seeded["user_a"], project.id) is True

    state = db_session.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == seeded["user_a"],
            UserWorkspaceState.tenant_id == seeded["tenant_a"],
        )
    )
    assert state.active_project_id == project.id
    assert state.active_file_id == project_file.id


def test_project_repository_set_active_project_rejects_cross_tenant_project(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert repo.set_active_project(seeded["user_a"], seeded["project_b"]) is False
    assert db_session.scalar(select(UserWorkspaceState)) is None


def test_project_repository_save_code_updates_project_and_snapshots_all_files(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])
    project = db_session.get(Project, seeded["project_a"])
    project.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    assert repo.save_code("same_name", "helper.py", "helper = 42", seeded["user_a"], "Update helper") is True

    db_session.refresh(project)
    snapshot = db_session.scalar(select(SourceSnapshot).where(SourceSnapshot.project_id == project.id))
    snapshot_files = db_session.scalars(
        select(SourceSnapshotFile).where(SourceSnapshotFile.snapshot_id == snapshot.id).order_by(SourceSnapshotFile.filename)
    ).all()

    assert project.updated_at > datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert repo.get_code("same_name", "helper.py") == "helper = 42"
    assert snapshot.tenant_id == seeded["tenant_a"]
    assert snapshot.created_by == seeded["user_a"]
    assert snapshot.message == "Update helper"
    assert [(row.filename, row.content) for row in snapshot_files] == [
        ("design.py", "a = 1"),
        ("helper.py", "helper = 42"),
    ]


def test_project_repository_save_code_returns_false_for_missing_project(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert repo.save_code("missing", "design.py", "x = 1", seeded["user_a"], "No-op") is False
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 0


def test_project_repository_delete_file_rejects_design_py_and_invalid_names(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert repo.delete_file("same_name", "helper.py") is True
    assert repo.get_code("same_name", "helper.py") is None
    assert repo.delete_file("same_name", "missing.py") is False

    with pytest.raises(ValueError):
        repo.delete_file("same_name", "design.py")

    with pytest.raises(ValueError):
        repo.delete_file("same_name", "../helper.py")


def test_project_repository_rejects_invalid_filename_and_project_name(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert require_valid_python_filename("design.py") == "design.py"
    assert require_valid_project_name("valid-project_1") == "valid-project_1"

    with pytest.raises(ValueError):
        repo.get_code("same_name", "../design.py")

    with pytest.raises(ValueError):
        repo.get_code("same_name", "notes.txt")

    with pytest.raises(ValueError):
        repo.get_project("../same_name")


def test_project_file_cannot_cross_tenant_project_boundary(db_session):
    seeded = seed_two_tenants(db_session)
    db_session.add(
        ProjectFile(
            tenant_id=seeded["tenant_a"],
            project_id=seeded["project_b"],
            filename="illegal.py",
            content="x = 1",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
