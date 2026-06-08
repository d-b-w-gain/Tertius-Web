from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext, Principal
from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership, UserWorkspaceState, now_utc


DEFAULT_SCRIPT_PATH = Path(__file__).parent.parent / "workflows" / "intus" / "templates" / "default_purlin.py"


def _default_script() -> str:
    return DEFAULT_SCRIPT_PATH.read_text(encoding="utf-8")


def _tenant_name_for(principal: Principal) -> str:
    return principal.display_name or principal.username or principal.email or "Personal Workspace"


def provision_user_context(db: Session, principal: Principal) -> AuthContext:
    user = db.scalar(select(AppUser).where(AppUser.keycloak_subject == principal.keycloak_subject))
    if user is None:
        user = AppUser(
            keycloak_subject=principal.keycloak_subject,
            email=principal.email,
            username=principal.username,
            display_name=principal.display_name,
        )
        db.add(user)
        db.flush()

        tenant = Tenant(name=_tenant_name_for(principal))
        db.add(tenant)
        db.flush()

        membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner")
        project = Project(tenant_id=tenant.id, name="default_purlin", created_by=user.id)
        db.add_all([membership, project])
        db.flush()

        design_file = ProjectFile(
            tenant_id=tenant.id,
            project_id=project.id,
            filename="design.py",
            content=_default_script(),
        )
        db.add(design_file)
        db.flush()

        db.add(
            UserWorkspaceState(
                user_id=user.id,
                tenant_id=tenant.id,
                active_project_id=project.id,
                active_file_id=design_file.id,
            )
        )
        db.commit()
        return AuthContext(
            user_id=user.id,
            tenant_id=tenant.id,
            keycloak_subject=user.keycloak_subject,
            email=user.email,
        )

    user.email = principal.email
    user.username = principal.username
    user.display_name = principal.display_name
    user.last_seen_at = now_utc()

    membership = db.scalar(select(TenantMembership).where(TenantMembership.user_id == user.id))
    db.commit()
    return AuthContext(
        user_id=user.id,
        tenant_id=membership.tenant_id,
        keycloak_subject=user.keycloak_subject,
        email=user.email,
    )
