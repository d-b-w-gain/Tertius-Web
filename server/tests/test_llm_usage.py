from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4
from datetime import timedelta

import pytest

from core.auth_types import AuthContext
from core.config import Settings
from core.llm_usage import LlmUsageLimitExceeded, assert_llm_usage_allowed, record_llm_usage
from core.models import AppUser, LlmUsageRecord, Tenant


def _auth(seeded_tenant) -> AuthContext:
    return AuthContext(
        user_id=seeded_tenant.user_id,
        tenant_id=seeded_tenant.tenant_id,
        keycloak_subject="kc-test",
        email="test@example.com",
    )


def _request(prompt: str = "make a bracket"):
    return SimpleNamespace(prompt=prompt, metadata={"source": "compiler_tab"})


def _result(total_tokens: int = 30):
    return SimpleNamespace(
        model="gpt-5.6-sol",
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=total_tokens - 10,
            total_tokens=total_tokens,
        ),
    )


def test_record_llm_usage_persists_completed_usage_row(db_session, seeded_tenant):
    event_id = record_llm_usage(
        db_session,
        auth=_auth(seeded_tenant),
        project_id=seeded_tenant.project_id,
        request=_request(),
        result=_result(),
        provider_request_id=None,
        settings=Settings(),
        operation="files.llm_edit",
    )
    db_session.commit()

    row = db_session.query(LlmUsageRecord).filter_by(event_id=event_id).one()
    assert row.tenant_id == seeded_tenant.tenant_id
    assert row.user_id == seeded_tenant.user_id
    assert row.project_id == seeded_tenant.project_id
    assert row.workflow == "intus"
    assert row.operation == "files.llm_edit"
    assert row.provider == "openai-codex"
    assert row.model == "gpt-5.6-sol"
    assert row.prompt_tokens == 10
    assert row.completion_tokens == 20
    assert row.total_tokens == 30
    assert row.provider_request_id is None
    assert row.metadata_json == {"source": "compiler_tab"}
    assert row.status == "completed"


def test_llm_usage_today_endpoint_summarizes_current_day(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
):
    from workflows.intus import usage_server

    monkeypatch.setattr(
        usage_server,
        "get_settings",
        lambda: Settings(
            llm_tenant_daily_token_quota=1000,
            llm_user_daily_token_quota=500,
        ),
    )
    old_event_id = record_llm_usage(
        db_session, auth=_auth(seeded_tenant), project_id=seeded_tenant.project_id,
        request=_request("old edit"), result=_result(200), settings=Settings(),
        operation="files.llm_edit",
    )
    current_event_id = record_llm_usage(
        db_session, auth=_auth(seeded_tenant), project_id=seeded_tenant.project_id,
        request=_request("current edit"), result=_result(120), settings=Settings(),
        operation="files.llm_edit",
    )
    other_user_id = uuid4()
    db_session.add(AppUser(id=other_user_id, keycloak_subject="kc-tenant-peer"))
    db_session.flush()
    record_llm_usage(
        db_session,
        auth=AuthContext(user_id=other_user_id, tenant_id=seeded_tenant.tenant_id,
                         keycloak_subject="kc-tenant-peer", email=None),
        project_id=seeded_tenant.project_id, request=_request("peer edit"),
        result=_result(80), settings=Settings(), operation="files.llm_edit",
    )
    old_row = db_session.query(LlmUsageRecord).filter_by(event_id=old_event_id).one()
    old_row.created_at = old_row.created_at - timedelta(days=1)
    current_row = db_session.query(LlmUsageRecord).filter_by(event_id=current_event_id).one()
    db_session.commit()

    response = authenticated_intus_client.get("/llm-usage/today")

    assert response.status_code == 200
    data = response.json()
    assert data["tenant_daily_token_quota"] == 1000
    assert data["tenant_tokens_used_today"] == 200
    assert data["tenant_tokens_remaining_today"] == 800
    assert data["user_daily_token_quota"] == 500
    assert data["user_tokens_used_today"] == 120
    assert data["user_tokens_remaining_today"] == 380
    assert not any("usd" in key or "cost" in key or "budget" in key for key in data)
    assert data["last_edit"]["operation"] == "files.llm_edit"
    assert data["last_edit"]["model"] == "gpt-5.6-sol"
    assert data["last_edit"]["total_tokens"] == 120
    assert data["last_edit"]["created_at"] == current_row.created_at.isoformat()


def test_llm_usage_project_foreign_key_preserves_tenant_on_project_delete():
    table = cast(Any, LlmUsageRecord.__table__)
    set_null_constraints = [
        constraint
        for constraint in table.foreign_key_constraints
        if constraint.ondelete == "SET NULL (project_id)"
    ]

    assert len(set_null_constraints) == 1
    constrained_columns = {column.name for column in set_null_constraints[0].columns}
    assert constrained_columns == {"project_id", "tenant_id"}


def test_llm_usage_guard_rejects_user_minute_rate_limit(db_session, seeded_tenant):
    settings = Settings(llm_user_rate_limit_per_minute=1)
    record_llm_usage(db_session, auth=_auth(seeded_tenant), project_id=seeded_tenant.project_id, request=_request(), result=_result(), settings=settings)
    db_session.commit()

    with pytest.raises(LlmUsageLimitExceeded, match="LLM usage limit exceeded"):
        assert_llm_usage_allowed(
            db_session,
            settings,
            tenant_id=seeded_tenant.tenant_id,
            user_id=seeded_tenant.user_id,
            estimated_tokens=1,
        )


def test_llm_usage_guard_rejects_tenant_minute_rate_limit(db_session, seeded_tenant):
    settings = Settings(llm_tenant_rate_limit_per_minute=1)
    other_user_id = uuid4()
    db_session.add(AppUser(id=other_user_id, keycloak_subject="kc-other"))
    db_session.flush()
    record_llm_usage(
        db_session,
        auth=AuthContext(
            user_id=other_user_id,
            tenant_id=seeded_tenant.tenant_id,
            keycloak_subject="kc-other",
            email=None,
        ),
        project_id=seeded_tenant.project_id,
        request=_request(),
        result=_result(),
        settings=settings,
    )
    db_session.commit()

    with pytest.raises(LlmUsageLimitExceeded, match="LLM usage limit exceeded"):
        assert_llm_usage_allowed(
            db_session,
            settings,
            tenant_id=seeded_tenant.tenant_id,
            user_id=seeded_tenant.user_id,
            estimated_tokens=1,
        )


def test_llm_usage_guard_rejects_tenant_daily_token_quota(db_session, seeded_tenant):
    settings = Settings(llm_tenant_daily_token_quota=30, llm_tenant_rate_limit_per_minute=10)
    record_llm_usage(db_session, auth=_auth(seeded_tenant), project_id=seeded_tenant.project_id, request=_request(), result=_result(30), settings=settings)
    db_session.commit()

    with pytest.raises(LlmUsageLimitExceeded, match="LLM usage limit exceeded"):
        assert_llm_usage_allowed(
            db_session,
            settings,
            tenant_id=seeded_tenant.tenant_id,
            user_id=seeded_tenant.user_id,
            estimated_tokens=1,
        )


def test_llm_usage_guard_rejects_user_daily_token_quota(db_session, seeded_tenant):
    settings = Settings(llm_user_daily_token_quota=30, llm_user_rate_limit_per_minute=10)
    record_llm_usage(db_session, auth=_auth(seeded_tenant), project_id=seeded_tenant.project_id, request=_request(), result=_result(30), settings=settings)
    db_session.commit()

    with pytest.raises(LlmUsageLimitExceeded, match="LLM usage limit exceeded"):
        assert_llm_usage_allowed(
            db_session,
            settings,
            tenant_id=seeded_tenant.tenant_id,
            user_id=seeded_tenant.user_id,
            estimated_tokens=1,
        )


@pytest.mark.parametrize("pi_agent_enabled", [False, True])
def test_llm_models_endpoint_reflects_pi_agent_availability(
    authenticated_intus_client, monkeypatch, pi_agent_enabled
):
    from workflows.intus import usage_server

    monkeypatch.setattr(
        usage_server,
        "get_settings",
        lambda: Settings(pi_agent_enabled=pi_agent_enabled),
    )
    response = authenticated_intus_client.get("/llm-usage/models")

    assert response.status_code == 200
    assert response.json() == {
        "default_model_id": "gpt-5.6-sol",
        "models": [
            {
                "id": "gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "label": "GPT-5.6 Sol",
                "enabled": pi_agent_enabled,
            }
        ],
    }


def test_legacy_build_script_generation_route_is_removed(authenticated_intus_client):
    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket", "active_file": "model.py"},
    )

    assert response.status_code == 404


def test_llm_usage_guard_ignores_other_tenants(db_session, seeded_tenant):
    settings = Settings(
        llm_tenant_rate_limit_per_minute=1,
        llm_user_rate_limit_per_minute=1,
        llm_tenant_daily_token_quota=30,
        llm_user_daily_token_quota=30,
    )
    other_user_id = uuid4()
    other_tenant_id = uuid4()
    db_session.add_all(
        [
            AppUser(id=other_user_id, keycloak_subject="kc-other"),
            Tenant(id=other_tenant_id, name="Other Tenant"),
        ]
    )
    db_session.flush()
    record_llm_usage(
        db_session,
        auth=AuthContext(
            user_id=other_user_id,
            tenant_id=other_tenant_id,
            keycloak_subject="kc-other",
            email=None,
        ),
        project_id=None,
        request=_request(),
        result=_result(30),
        settings=settings,
    )
    db_session.commit()

    assert_llm_usage_allowed(
        db_session,
        settings,
        tenant_id=seeded_tenant.tenant_id,
        user_id=seeded_tenant.user_id,
        estimated_tokens=1,
    )
