from sqlalchemy import func, select

from core.auth_types import Principal
from core.models import (
    AppUser,
    Project,
    ProjectFile,
    Tenant,
    TenantMembership,
    UserWorkspaceState,
)
from core.provisioning import provision_user_context


def test_first_login_creates_tenant_membership_and_default_project(db_session):
    principal = Principal(
        keycloak_subject="kc-123",
        email="alice@example.com",
        username="alice",
        display_name="Alice Example",
    )

    ctx = provision_user_context(db_session, principal)

    user = db_session.get(AppUser, ctx.user_id)
    tenant = db_session.get(Tenant, ctx.tenant_id)
    membership = db_session.scalar(select(TenantMembership).where(TenantMembership.user_id == ctx.user_id))
    project = db_session.scalar(select(Project).where(Project.tenant_id == ctx.tenant_id))
    design_file = db_session.scalar(
        select(ProjectFile).where(ProjectFile.project_id == project.id, ProjectFile.filename == "design.py")
    )
    workspace = db_session.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == ctx.user_id,
            UserWorkspaceState.tenant_id == ctx.tenant_id,
        )
    )

    assert ctx.keycloak_subject == "kc-123"
    assert ctx.email == "alice@example.com"
    assert user.keycloak_subject == "kc-123"
    assert tenant.name == "Alice Example"
    assert membership.role == "owner"
    assert project.name == "default_purlin"
    assert project.created_by == ctx.user_id
    assert design_file.tenant_id == ctx.tenant_id
    assert "build123d" in design_file.content
    assert workspace.active_project_id == project.id
    assert workspace.active_file_id == design_file.id


def test_second_login_updates_existing_user_without_duplicate_provisioning(db_session):
    first_principal = Principal(
        keycloak_subject="kc-123",
        email="alice@example.com",
        username="alice",
        display_name="Alice Example",
    )
    first_ctx = provision_user_context(db_session, first_principal)
    first_user = db_session.get(AppUser, first_ctx.user_id)
    first_seen_at = first_user.last_seen_at

    second_principal = Principal(
        keycloak_subject="kc-123",
        email="alice.renamed@example.com",
        username="alice-renamed",
        display_name="Alice Renamed",
    )

    second_ctx = provision_user_context(db_session, second_principal)
    second_user = db_session.get(AppUser, first_ctx.user_id)

    assert second_ctx == first_ctx.__class__(
        user_id=first_ctx.user_id,
        tenant_id=first_ctx.tenant_id,
        keycloak_subject="kc-123",
        email="alice.renamed@example.com",
    )
    assert second_user.email == "alice.renamed@example.com"
    assert second_user.username == "alice-renamed"
    assert second_user.display_name == "Alice Renamed"
    assert second_user.last_seen_at >= first_seen_at
    assert db_session.scalar(select(func.count()).select_from(Tenant)) == 1
    assert db_session.scalar(select(func.count()).select_from(TenantMembership)) == 1
    assert db_session.scalar(select(func.count()).select_from(Project)) == 1
    assert db_session.scalar(select(func.count()).select_from(ProjectFile)) == 1
    assert db_session.scalar(select(func.count()).select_from(UserWorkspaceState)) == 1
