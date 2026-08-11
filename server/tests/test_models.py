from sqlalchemy import CheckConstraint

from core.models import (
    AppUser,
    CompileJobAsset,
    Project,
    ProjectAsset,
    ProjectFile,
    ProjectImportJob,
    Tenant,
    TenantMembership,
)


def test_multitenant_models_expose_expected_columns():
    assert "keycloak_subject" in AppUser.__table__.columns
    assert "tenant_id" in Project.__table__.columns
    assert "tenant_id" in ProjectFile.__table__.columns
    assert "role" in TenantMembership.__table__.columns
    assert Tenant.__tablename__ == "tenants"


def test_import_asset_models_expose_immutable_columns_and_checks():
    assert ProjectAsset.__table__.c.content.nullable is False
    assert ProjectAsset.__table__.c.byte_size.nullable is False
    assert ProjectAsset.__table__.c.sha256.type.length == 64
    assert ProjectImportJob.__table__.c.execution_id.nullable is False
    assert ProjectImportJob.__table__.c.progress_payload.nullable is False
    assert CompileJobAsset.__table__.c.object_bucket.nullable is False
    assert CompileJobAsset.__table__.c.object_key.nullable is False

    asset_checks = {constraint.name for constraint in ProjectAsset.__table__.constraints if isinstance(constraint, CheckConstraint)}
    job_checks = {constraint.name for constraint in ProjectImportJob.__table__.constraints if isinstance(constraint, CheckConstraint)}
    assert {
        "ck_project_assets_kind",
        "ck_project_assets_byte_size_nonnegative",
        "ck_project_assets_sha256",
        "ck_project_assets_revision_positive",
    } <= asset_checks
    assert {
        "ck_project_import_jobs_status",
        "ck_project_import_jobs_attempt_positive",
    } <= job_checks
