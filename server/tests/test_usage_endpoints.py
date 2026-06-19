import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.models import AppUser, CompileJob, CompileUsageRecord, Project, Tenant, TenantMembership
from core.repositories import UsageRepository
from workflows.intus.intus_server import app as intus_app


def _make_project(db_session: Session, tenant_id, user_id) -> Project:
    project = Project(
        tenant_id=tenant_id,
        name=f"test-project-{uuid.uuid4().hex[:8]}",
        created_by=user_id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_usage_record(db_session: Session, tenant_id, user_id, **kwargs):
    now = datetime.now(timezone.utc)
    project = _make_project(db_session, tenant_id, user_id)
    job = CompileJob(
        tenant_id=tenant_id,
        project_id=project.id,
        requested_by=user_id,
        status=kwargs.get("status", "succeeded"),
        export_format=kwargs.get("export_format", "stl"),
    )
    db_session.add(job)
    db_session.flush()
    record = CompileUsageRecord(
        tenant_id=tenant_id,
        project_id=project.id,
        compile_job_id=job.id,
        requested_by=user_id,
        export_format=kwargs.get("export_format", "stl"),
        status=kwargs.get("status", "succeeded"),
        compute_duration_seconds=kwargs.get("compute_duration_seconds", 120.0),
        artifact_byte_size=kwargs.get("artifact_byte_size", 5000),
        cost_cents=kwargs.get("cost_cents", 10),
        base_rate_cents_per_hour=kwargs.get("base_rate_cents_per_hour", 100),
        format_multiplier=kwargs.get("format_multiplier", 1.0),
        created_at=kwargs.get("created_at", now),
    )
    db_session.add(record)
    db_session.flush()
    return record


class TestUsageRepository:
    def test_total_summary_empty(self, db_session, seeded_tenant):
        repo = UsageRepository(db_session, seeded_tenant.tenant_id)
        summary = repo.total_summary()
        assert summary["total_jobs"] == 0
        assert summary["total_cost_cents"] == 0

    def test_total_summary_with_data(self, db_session, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id, cost_cents=15)
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id, cost_cents=25)
        repo = UsageRepository(db_session, seeded_tenant.tenant_id)
        summary = repo.total_summary()
        assert summary["total_jobs"] == 2
        assert summary["total_cost_cents"] == 40

    def test_recent_jobs_includes_username(self, db_session, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id)
        repo = UsageRepository(db_session, seeded_tenant.tenant_id)
        recent = repo.recent_jobs(limit=10)
        assert len(recent) >= 1
        assert "username" in recent[0]

    def test_tenant_isolation(self, db_session, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id, cost_cents=50)
        other_tenant_id = uuid.uuid4()
        repo = UsageRepository(db_session, other_tenant_id)
        summary = repo.total_summary()
        assert summary["total_jobs"] == 0
        assert summary["total_cost_cents"] == 0


class TestUsageEndpoints:
    def test_summary_empty(self, db_session, authenticated_intus_client, seeded_tenant):
        res = authenticated_intus_client.get("/usage/summary")
        assert res.status_code == 200
        data = res.json()
        assert data["total_jobs"] == 0
        assert data["total_cost_cents"] == 0

    def test_summary_with_data(self, db_session, authenticated_intus_client, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id, cost_cents=42)
        res = authenticated_intus_client.get("/usage/summary")
        assert res.status_code == 200
        data = res.json()
        assert data["total_jobs"] == 1
        assert data["total_cost_cents"] == 42

    def test_daily_breakdown(self, db_session, authenticated_intus_client, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id)
        res = authenticated_intus_client.get("/usage/daily")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "day" in data[0]

    def test_monthly_breakdown(self, db_session, authenticated_intus_client, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id)
        res = authenticated_intus_client.get("/usage/monthly")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)

    def test_by_format(self, db_session, authenticated_intus_client, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id, export_format="stl", cost_cents=10)
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id, export_format="glb", cost_cents=30)
        res = authenticated_intus_client.get("/usage/by-format")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        formats = {r["export_format"] for r in data}
        assert "stl" in formats
        assert "glb" in formats

    def test_recent_jobs(self, db_session, authenticated_intus_client, seeded_tenant):
        _make_usage_record(db_session, seeded_tenant.tenant_id, seeded_tenant.user_id)
        res = authenticated_intus_client.get("/usage/recent")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "username" in data[0]

    def test_non_owner_gets_403(self, db_session, seeded_tenant):
        # Create a non-owner member
        member_user = AppUser(id=uuid.uuid4(), keycloak_subject="kc-member", email="member@example.com")
        member_tenant = Tenant(id=uuid.uuid4(), name="Member Tenant")
        db_session.add_all([member_user, member_tenant])
        db_session.flush()
        db_session.add(TenantMembership(tenant_id=member_tenant.id, user_id=member_user.id, role="member"))
        db_session.commit()

        intus_app.dependency_overrides[get_db] = lambda: db_session
        intus_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
            user_id=member_user.id,
            tenant_id=member_tenant.id,
            keycloak_subject="kc-member",
            email="member@example.com",
        )
        try:
            client = TestClient(intus_app)
            res = client.get("/usage/summary")
            assert res.status_code == 403
        finally:
            intus_app.dependency_overrides.clear()
