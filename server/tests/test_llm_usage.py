from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

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
        model="test-openai-compatible-model",
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
        provider_request_id="chatcmpl-123",
        settings=Settings(),
    )
    db_session.commit()

    row = db_session.query(LlmUsageRecord).filter_by(event_id=event_id).one()
    assert row.tenant_id == seeded_tenant.tenant_id
    assert row.user_id == seeded_tenant.user_id
    assert row.project_id == seeded_tenant.project_id
    assert row.workflow == "intus"
    assert row.operation == "build_script.generate"
    assert row.provider == "openai-compatible"
    assert row.model == "test-openai-compatible-model"
    assert row.prompt == "make a bracket"
    assert row.prompt_tokens == 10
    assert row.completion_tokens == 20
    assert row.total_tokens == 30
    assert row.provider_request_id == "chatcmpl-123"
    assert row.metadata_json == {"source": "compiler_tab"}
    assert row.status == "completed"


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
