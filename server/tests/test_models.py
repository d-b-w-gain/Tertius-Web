from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership


def test_multitenant_models_expose_expected_columns():
    assert "keycloak_subject" in AppUser.__table__.columns
    assert "tenant_id" in Project.__table__.columns
    assert "tenant_id" in ProjectFile.__table__.columns
    assert "role" in TenantMembership.__table__.columns
    assert Tenant.__tablename__ == "tenants"
