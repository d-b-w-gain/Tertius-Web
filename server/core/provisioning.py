from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext, Principal
from core.models import (
    AppUser,
    Project,
    ProjectFile,
    StructuralConfigurationRevision,
    Tenant,
    TenantMembership,
    UserWorkspaceState,
    now_utc,
)
from core.project_templates import default_project_files, default_structural_configuration
from core.structural.project_configuration import StructuralProjectConfiguration


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

        project_files = [
            ProjectFile(
                tenant_id=tenant.id,
                project_id=project.id,
                filename=filename,
                content=content,
            )
            for filename, content in default_project_files().items()
        ]
        db.add_all(project_files)
        db.flush()
        structural_configuration = StructuralProjectConfiguration.model_validate(
            default_structural_configuration()
        )
        db.add(
            StructuralConfigurationRevision(
                tenant_id=tenant.id,
                project_id=project.id,
                revision=1,
                digest=structural_configuration.configuration_digest,
                content=structural_configuration.model_dump(mode="json"),
                created_by=user.id,
            )
        )
        design_file = next(file for file in project_files if file.filename == "design.py")

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
            roles=principal.roles,
        )

    user.email = principal.email
    user.username = principal.username
    user.display_name = principal.display_name
    user.last_seen_at = now_utc()

    existing_membership = db.scalar(select(TenantMembership).where(TenantMembership.user_id == user.id))
    if existing_membership is None:
        raise RuntimeError(f"User {user.id} has no tenant membership")
    db.commit()
    return AuthContext(
        user_id=user.id,
        tenant_id=existing_membership.tenant_id,
        keycloak_subject=user.keycloak_subject,
        email=user.email,
        roles=principal.roles,
    )
