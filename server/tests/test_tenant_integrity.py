import pytest
from sqlalchemy.exc import IntegrityError

from core.models import AppUser, Artifact, CompileJob, Project, ProjectFile, Tenant, UserWorkspaceState


def make_user(email: str = "user@example.com") -> AppUser:
    return AppUser(keycloak_subject=email, email=email, username=email, display_name=email)


def test_workspace_active_project_must_belong_to_workspace_tenant(db_session):
    user = make_user()
    tenant_a = Tenant(name="Tenant A")
    tenant_b = Tenant(name="Tenant B")
    db_session.add_all([user, tenant_a, tenant_b])
    db_session.flush()

    project_b = Project(tenant_id=tenant_b.id, name="Project B", created_by=user.id)
    db_session.add(project_b)
    db_session.flush()

    db_session.add(
        UserWorkspaceState(
            user_id=user.id,
            tenant_id=tenant_a.id,
            active_project_id=project_b.id,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_workspace_active_file_must_belong_to_workspace_tenant(db_session):
    user = make_user()
    tenant_a = Tenant(name="Tenant A")
    tenant_b = Tenant(name="Tenant B")
    db_session.add_all([user, tenant_a, tenant_b])
    db_session.flush()

    project_b = Project(tenant_id=tenant_b.id, name="Project B", created_by=user.id)
    db_session.add(project_b)
    db_session.flush()

    file_b = ProjectFile(
        tenant_id=tenant_b.id,
        project_id=project_b.id,
        filename="main.intus",
        content="content",
    )
    db_session.add(file_b)
    db_session.flush()

    db_session.add(
        UserWorkspaceState(
            user_id=user.id,
            tenant_id=tenant_a.id,
            active_file_id=file_b.id,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_artifact_compile_job_must_match_artifact_tenant(db_session):
    user = make_user()
    tenant_a = Tenant(name="Tenant A")
    tenant_b = Tenant(name="Tenant B")
    db_session.add_all([user, tenant_a, tenant_b])
    db_session.flush()

    project_a = Project(tenant_id=tenant_a.id, name="Project A", created_by=user.id)
    project_b = Project(tenant_id=tenant_b.id, name="Project B", created_by=user.id)
    db_session.add_all([project_a, project_b])
    db_session.flush()

    job_b = CompileJob(
        tenant_id=tenant_b.id,
        project_id=project_b.id,
        requested_by=user.id,
        status="completed",
        export_format="pdf",
    )
    db_session.add(job_b)
    db_session.flush()

    db_session.add(
        Artifact(
            tenant_id=tenant_a.id,
            project_id=project_a.id,
            compile_job_id=job_b.id,
            kind="pdf",
            storage_key="artifacts/output.pdf",
            content_type="application/pdf",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_artifact_compile_job_must_match_artifact_project(db_session):
    user = make_user()
    tenant = Tenant(name="Tenant")
    db_session.add_all([user, tenant])
    db_session.flush()

    project_a = Project(tenant_id=tenant.id, name="Project A", created_by=user.id)
    project_b = Project(tenant_id=tenant.id, name="Project B", created_by=user.id)
    db_session.add_all([project_a, project_b])
    db_session.flush()

    job_b = CompileJob(
        tenant_id=tenant.id,
        project_id=project_b.id,
        requested_by=user.id,
        status="completed",
        export_format="pdf",
    )
    db_session.add(job_b)
    db_session.flush()

    db_session.add(
        Artifact(
            tenant_id=tenant.id,
            project_id=project_a.id,
            compile_job_id=job_b.id,
            kind="pdf",
            storage_key="artifacts/output.pdf",
            content_type="application/pdf",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
