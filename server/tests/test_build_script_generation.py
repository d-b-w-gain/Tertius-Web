from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.config import Settings
from core.db import get_db
from core.llm_client import TokenUsage
from core.llm_usage import LlmUsageLimitExceeded
from core.models import AppUser, Project, Tenant, TenantMembership
from workflows.intus import intus_server
from workflows.intus.intus_server import app


class FakeBillingPublisher:
    async def publish_json(self, subject, message, message_id=None):
        return None


def enable_llm(monkeypatch):
    monkeypatch.setattr(
        intus_server,
        "get_settings",
        lambda: Settings(llm_api_key="test-key", llm_model="test-openai-compatible-model"),
    )


def test_build_script_generation_requires_existing_project(authenticated_intus_client):
    response = authenticated_intus_client.post(
        "/projects/missing_project/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 404
    assert response.json() == {"success": False, "error": "Project not found"}


def test_build_script_generation_does_not_cross_tenant(authenticated_intus_client, db_session):
    other_user = AppUser(keycloak_subject="kc-other", email="other@example.com")
    other_tenant = Tenant(name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    db_session.add(Project(tenant_id=other_tenant.id, name="other_project", created_by=other_user.id))
    db_session.commit()

    response = authenticated_intus_client.post(
        "/projects/other_project/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 404


def test_build_script_generation_returns_generated_script(authenticated_intus_client, seeded_tenant, monkeypatch):
    enable_llm(monkeypatch)

    async def fake_generate_build_script(request, *, settings, auth, project_id, openai_client=None, billing_publisher=None):
        assert request.prompt == "make a bracket"
        assert request.active_file == "design.py"
        assert request.metadata == {"source": "compiler_tab"}
        assert auth.tenant_id == seeded_tenant.tenant_id
        assert project_id == seeded_tenant.project_id
        return SimpleNamespace(
            success=True,
            script="import build123d as bd\npart = bd.Box(1, 2, 3)",
            model="test-openai-compatible-model",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            provider_request_id="chatcmpl-test",
            model_dump=lambda: {
                "success": True,
                "script": "import build123d as bd\npart = bd.Box(1, 2, 3)",
                "model": "test-openai-compatible-model",
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(), None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={
            "prompt": "make a bracket",
            "active_file": "design.py",
            "current_code": "import build123d as bd\n",
            "metadata": {"source": "compiler_tab"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "script": "import build123d as bd\npart = bd.Box(1, 2, 3)",
        "model": "test-openai-compatible-model",
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def test_build_script_generation_is_authenticated(db_session, seeded_tenant):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.post(
            "/projects/default_purlin/build-script/generate",
            json={"prompt": "make a bracket"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_build_script_generation_public_mounted_route(db_session, seeded_tenant, monkeypatch):
    from main import app as main_app

    enable_llm(monkeypatch)

    async def fake_generate_build_script(request, *, settings, auth, project_id, openai_client=None, billing_publisher=None):
        return SimpleNamespace(
            success=True,
            script="import build123d as bd\npart = bd.Box(1, 2, 3)",
            model="test-openai-compatible-model",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            provider_request_id="chatcmpl-test",
            model_dump=lambda: {
                "success": True,
                "script": "import build123d as bd\npart = bd.Box(1, 2, 3)",
                "model": "test-openai-compatible-model",
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(), None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    def override_db():
        yield db_session

    def override_auth():
        return AuthContext(
            user_id=seeded_tenant.user_id,
            tenant_id=seeded_tenant.tenant_id,
            keycloak_subject="kc-test",
            email="test@example.com",
        )

    intus_server.app.dependency_overrides[get_db] = override_db
    intus_server.app.dependency_overrides[get_auth_context] = override_auth
    try:
        response = TestClient(main_app).post(
            "/api/intus/projects/default_purlin/build-script/generate",
            json={"prompt": "make a bracket"},
        )
    finally:
        intus_server.app.dependency_overrides.clear()

    assert response.status_code == 200


def test_build_script_generation_rejects_invalid_active_file(authenticated_intus_client):
    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket", "active_file": "../design.py"},
    )

    assert response.status_code == 400
    assert response.json()["success"] is False


def test_build_script_generation_reports_missing_provider_key(authenticated_intus_client, monkeypatch):
    async def fake_generate_build_script(*args, **kwargs):
        from core.llm_client import LlmNotConfiguredError

        raise LlmNotConfiguredError("LLM provider is not configured")

    async def fake_create_billing_publisher(settings):
        return None, None

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "LLM provider is not configured",
        "retryable": False,
    }


def test_build_script_generation_fails_closed_when_billing_publisher_unavailable(authenticated_intus_client, monkeypatch):
    enable_llm(monkeypatch)

    async def fake_generate_build_script(*args, **kwargs):
        raise AssertionError("provider should not be called when billing setup fails")

    async def fake_create_billing_publisher(settings):
        raise intus_server.LlmBillingError("LLM billing failed")

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "LLM billing failed",
        "retryable": True,
    }


def test_build_script_generation_returns_429_when_llm_limit_exceeded(authenticated_intus_client, monkeypatch):
    enable_llm(monkeypatch)

    def fake_assert_llm_usage_allowed(*args, **kwargs):
        raise LlmUsageLimitExceeded("LLM usage limit exceeded")

    monkeypatch.setattr(intus_server, "assert_llm_usage_allowed", fake_assert_llm_usage_allowed)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 429
    assert response.json() == {
        "success": False,
        "error": "LLM usage limit exceeded",
        "retryable": True,
    }


def test_build_script_generation_estimates_prompt_and_completion_tokens_for_quota(authenticated_intus_client, monkeypatch):
    enable_llm(monkeypatch)

    seen_estimates = []

    def fake_assert_llm_usage_allowed(*args, **kwargs):
        seen_estimates.append(kwargs["estimated_tokens"])

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(), None

    async def fake_generate_build_script(request, *, settings, auth, project_id, openai_client=None, billing_publisher=None):
        return SimpleNamespace(
            success=True,
            script="import build123d as bd\npart = bd.Box(1, 2, 3)",
            model="test-openai-compatible-model",
            usage=TokenUsage(prompt_tokens=2000, completion_tokens=2, total_tokens=2002),
            provider_request_id="chatcmpl-test",
        )

    monkeypatch.setattr(intus_server, "assert_llm_usage_allowed", fake_assert_llm_usage_allowed)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)
    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={
            "prompt": "make a bracket " * 200,
            "current_code": "import build123d as bd\n" * 200,
        },
    )

    assert response.status_code == 200
    assert seen_estimates
    assert seen_estimates[0] > intus_server.get_settings().llm_max_output_tokens


def test_build_script_generation_reports_provider_failure(authenticated_intus_client, monkeypatch):
    enable_llm(monkeypatch)

    async def fake_generate_build_script(*args, **kwargs):
        raise RuntimeError("provider timed out")

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(), None

    monkeypatch.setattr(intus_server, "generate_build_script", fake_generate_build_script)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/build-script/generate",
        json={"prompt": "make a bracket"},
    )

    assert response.status_code == 503
    assert response.json()["retryable"] is True
