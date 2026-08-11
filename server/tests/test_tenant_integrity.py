import pytest
from uuid import uuid4
from sqlalchemy.exc import IntegrityError

from core.models import (
    AppUser,
    Artifact,
    CompileJob,
    CompileJobAsset,
    Project,
    ProjectAsset,
    ProjectFile,
    ProjectImportJob,
    Tenant,
    TenantMembership,
    UserWorkspaceState,
)


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
            content=b"%PDF",
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
            content=b"%PDF",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_compile_job_asset_must_match_asset_tenant_and_project(db_session):
    user = make_user()
    tenant_a = Tenant(name="Tenant A")
    tenant_b = Tenant(name="Tenant B")
    db_session.add_all([user, tenant_a, tenant_b])
    db_session.flush()
    project_a = Project(tenant_id=tenant_a.id, name="Project A", created_by=user.id)
    project_b = Project(tenant_id=tenant_b.id, name="Project B", created_by=user.id)
    db_session.add_all([project_a, project_b])
    db_session.flush()
    asset_b = ProjectAsset(
        tenant_id=tenant_b.id,
        project_id=project_b.id,
        logical_name="source.brep",
        display_name="source.brep",
        kind="derived_brep",
        media_type="application/vnd.opencascade.brep",
        content=b"brep",
        byte_size=4,
        sha256="0" * 64,
        revision=1,
    )
    job_a = CompileJob(
        tenant_id=tenant_a.id,
        project_id=project_a.id,
        requested_by=user.id,
        status="queued",
        export_format="glb",
    )
    db_session.add_all([asset_b, job_a])
    db_session.flush()
    db_session.add(
        CompileJobAsset(
            compile_job_id=job_a.id,
            tenant_id=tenant_a.id,
            project_id=project_a.id,
            project_asset_id=asset_b.id,
            logical_filename="source.brep",
            sha256=asset_b.sha256,
            byte_size=asset_b.byte_size,
            object_bucket="TERTIUS_ASSETS",
            object_key="sha256/brep",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_project_import_requested_by_must_be_member_of_job_tenant(db_session):
    user_a = make_user("a@example.com")
    user_b = make_user("b@example.com")
    tenant_a = Tenant(name="Tenant A")
    tenant_b = Tenant(name="Tenant B")
    db_session.add_all([user_a, user_b, tenant_a, tenant_b])
    db_session.flush()
    db_session.add_all(
        [
            TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role="owner"),
            TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role="owner"),
        ]
    )
    project_a = Project(tenant_id=tenant_a.id, name="Project A", created_by=user_a.id)
    db_session.add(project_a)
    db_session.flush()
    source = ProjectAsset(
        tenant_id=tenant_a.id,
        project_id=project_a.id,
        logical_name="source.3mf",
        display_name="source.3mf",
        kind="source_3mf",
        media_type="application/octet-stream",
        content=b"3mf",
        byte_size=3,
        sha256="0" * 64,
        revision=1,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add(
        ProjectImportJob(
            tenant_id=tenant_a.id,
            project_id=project_a.id,
            requested_by=user_b.id,
            source_asset_id=source.id,
            execution_id=uuid4(),
            status="queued",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_import_audit_attribution_restricts_membership_deletion(db_session):
    user = make_user("audit@example.com")
    tenant = Tenant(name="Audit tenant")
    db_session.add_all([user, tenant])
    db_session.flush()
    membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner")
    project = Project(tenant_id=tenant.id, name="Audit project", created_by=user.id)
    db_session.add_all([membership, project])
    db_session.flush()
    source = ProjectAsset(
        tenant_id=tenant.id,
        project_id=project.id,
        logical_name="source.3mf",
        display_name="source.3mf",
        kind="source_3mf",
        media_type="application/octet-stream",
        content=b"3mf",
        byte_size=3,
        sha256="0" * 64,
        revision=1,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add(
        ProjectImportJob(
            tenant_id=tenant.id,
            project_id=project.id,
            requested_by=user.id,
            source_asset_id=source.id,
            execution_id=uuid4(),
            status="failed",
        )
    )
    db_session.commit()

    db_session.delete(membership)
    with pytest.raises(IntegrityError):
        db_session.commit()
