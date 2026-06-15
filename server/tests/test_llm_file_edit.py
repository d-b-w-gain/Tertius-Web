from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.config import Settings
from core.db import get_db
from core.llm_client import (
    LlmBillingError,
    LlmInvalidFileEditError,
    TokenUsage,
)
from core.llm_usage import LlmUsageLimitExceeded
from core.models import (
    AppUser,
    LlmUsageRecord,
    Project,
    ProjectFile,
    SourceSnapshot,
    Tenant,
    TenantMembership,
)
from workflows.intus import intus_server
from workflows.intus.intus_server import app


class FakeBillingPublisher:
    def __init__(self, raise_on_publish=False):
        self.raise_on_publish = raise_on_publish
        self.published = []

    async def publish_json(self, subject, message, message_id=None):
        self.published.append((subject, message_id))
        if self.raise_on_publish:
            raise RuntimeError("billing publish failed")


def enable_llm(monkeypatch):
    monkeypatch.setattr(intus_server, "get_settings", lambda: Settings(llm_api_key="test-key"))


def make_fake_generate_file_edits(return_value=None, raises=None):
    async def fake_generate_file_edits(
        request,
        *,
        files,
        settings,
        auth,
        project_id,
        openai_client=None,
        billing_publisher=None,
    ):
        if billing_publisher is not None:
            try:
                await billing_publisher.publish_json(
                    settings.billing_llm_usage_subject,
                    {"event": "test"},
                    message_id="test-event",
                )
            except Exception as exc:
                raise LlmBillingError("LLM billing failed") from exc
        if raises is not None:
            raise raises
        assert request.prompt == "Refactor purlin into helper"
        assert {str(f.id) for f in request.files} == {str(f.id) for f in files}
        assert auth.tenant_id is not None
        assert project_id is not None
        return return_value

    return fake_generate_file_edits


def test_list_files_includes_metadata(authenticated_intus_client, db_session, seeded_tenant):
    db_session.add(
        ProjectFile(
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            filename="helper.py",
            content="answer = 42\n",
        )
    )
    db_session.commit()

    response = authenticated_intus_client.get("/projects/default_purlin/files")

    assert response.status_code == 200
    body = response.json()
    assert body["files"] == ["design.py", "helper.py"]
    assert isinstance(body["file_metadata"], list)
    assert len(body["file_metadata"]) == 2
    by_name = {row["filename"]: row for row in body["file_metadata"]}
    assert by_name["design.py"]["filename"] == "design.py"
    assert by_name["helper.py"]["filename"] == "helper.py"
    for row in body["file_metadata"]:
        assert UUID(row["id"])
        assert isinstance(row["updated_at"], str) and row["updated_at"]


def test_llm_file_edit_returns_changed_files_and_persists_state(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    helper = ProjectFile(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        filename="helper.py",
        content="def make_purlin():\n    return None\n",
    )
    db_session.add(helper)
    db_session.commit()

    design_id = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    ).id

    fake_result = SimpleNamespace(
        success=True,
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
            SimpleNamespace(
                file_id=helper.id, content="def make_purlin():\n    return 1\n", summary="Update helper"
            ),
        ],
        model="deepseek-v4-flash",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
        provider_request_id="chatcmpl-test",
        billing_event_id=uuid4(),
    )

    monkeypatch.setattr(intus_server, "generate_file_edits", make_fake_generate_file_edits(return_value=fake_result))

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(), None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [
                {"id": str(design_id), "filename": "design.py"},
                {"id": str(helper.id), "filename": "helper.py"},
            ],
            "active_file_id": str(design_id),
            "metadata": {"source": "compiler_tab"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["model"] == "deepseek-v4-flash"
    assert body["usage"] == {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
    assert body["snapshot"]["id"]
    assert body["snapshot"]["content_hash"]
    assert body["snapshot"]["message"]
    assert isinstance(body["files"], list)
    assert len(body["files"]) == 2
    by_name = {f["filename"]: f for f in body["files"]}
    assert by_name["design.py"]["content"] == "import helper\n"
    assert by_name["helper.py"]["content"] == "def make_purlin():\n    return 1\n"
    assert by_name["design.py"]["changed"] is True
    assert by_name["helper.py"]["changed"] is True

    design_row = db_session.get(ProjectFile, design_id)
    helper_row = db_session.get(ProjectFile, helper.id)
    assert design_row.content == "import helper\n"
    assert helper_row.content == "def make_purlin():\n    return 1\n"

    snapshots = db_session.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == seeded_tenant.tenant_id,
            SourceSnapshot.project_id == seeded_tenant.project_id,
        )
    ).all()
    assert len(snapshots) == 1
    assert snapshots[0].message

    usage_count = db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
            LlmUsageRecord.operation == "files.llm_edit",
        )
    )
    assert usage_count == 1


def test_llm_file_edit_rejects_cross_tenant_file_pointer(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    other_user = AppUser(id=uuid4(), keycloak_subject="kc-other", email="other@example.com")
    other_tenant = Tenant(id=uuid4(), name="Other Tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    other_project = Project(
        id=uuid4(), tenant_id=other_tenant.id, name="other_project", created_by=other_user.id
    )
    db_session.add(other_project)
    db_session.flush()
    other_file = ProjectFile(
        tenant_id=other_tenant.id,
        project_id=other_project.id,
        filename="other.py",
        content="x = 1\n",
    )
    db_session.add(other_file)
    db_session.commit()

    design_id = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    ).id

    provider_called = False

    async def fake_generate_file_edits(*args, **kwargs):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider should not be called for cross-tenant request")

    async def fake_create_billing_publisher(settings):
        raise AssertionError("billing publisher should not be opened for cross-tenant request")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [
                {"id": str(design_id), "filename": "design.py"},
                {"id": str(other_file.id), "filename": "other.py"},
            ],
        },
    )

    assert response.status_code == 404
    assert response.json() == {"success": False, "error": "File not found"}
    assert provider_called is False

    snapshots = db_session.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == seeded_tenant.tenant_id,
        )
    ).all()
    assert snapshots == []

    usage_count = db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
        )
    )
    assert usage_count == 0


def test_llm_file_edit_returns_502_when_provider_returns_unauthorized_file(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design_id = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    ).id
    original_content = db_session.get(ProjectFile, design_id).content

    async def fake_generate_file_edits(*args, **kwargs):
        raise LlmInvalidFileEditError("provider returned unauthorized file_id")

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(), None

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [{"id": str(design_id), "filename": "design.py"}],
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "success": False,
        "error": "LLM returned invalid file edits",
        "retryable": True,
    }

    db_session.expire_all()
    assert db_session.get(ProjectFile, design_id).content == original_content

    usage_count = db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
        )
    )
    assert usage_count == 0


def test_llm_file_edit_returns_503_when_billing_publisher_unavailable(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design_id = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    ).id
    original_content = db_session.get(ProjectFile, design_id).content

    async def fake_generate_file_edits(*args, **kwargs):
        raise AssertionError("provider should not be called when billing setup fails")

    async def fake_create_billing_publisher(settings):
        raise LlmBillingError("LLM billing failed")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [{"id": str(design_id), "filename": "design.py"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "LLM billing failed",
        "retryable": True,
    }

    db_session.expire_all()
    assert db_session.get(ProjectFile, design_id).content == original_content

    usage_count = db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
        )
    )
    assert usage_count == 0


def test_llm_file_edit_returns_503_when_billing_publish_fails_after_provider(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design_id = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    ).id
    original_content = db_session.get(ProjectFile, design_id).content

    fake_result = SimpleNamespace(
        success=True,
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        model="deepseek-v4-flash",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        provider_request_id="chatcmpl-test",
        billing_event_id=uuid4(),
    )

    monkeypatch.setattr(
        intus_server,
        "generate_file_edits",
        make_fake_generate_file_edits(return_value=fake_result),
    )

    async def fake_create_billing_publisher(settings):
        return FakeBillingPublisher(raise_on_publish=True), None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [{"id": str(design_id), "filename": "design.py"}],
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "LLM billing failed",
        "retryable": True,
    }

    db_session.expire_all()
    assert db_session.get(ProjectFile, design_id).content == original_content

    snapshots = db_session.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == seeded_tenant.tenant_id,
        )
    ).all()
    assert snapshots == []

    usage_count = db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
        )
    )
    assert usage_count == 0


def test_llm_file_edit_public_mounted_route(
    db_session, seeded_tenant, monkeypatch
):
    from main import app as main_app

    enable_llm(monkeypatch)
    design_id = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    ).id

    fake_result = SimpleNamespace(
        success=True,
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        model="deepseek-v4-flash",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        provider_request_id="chatcmpl-test",
        billing_event_id=uuid4(),
    )

    monkeypatch.setattr(
        intus_server,
        "generate_file_edits",
        make_fake_generate_file_edits(return_value=fake_result),
    )

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
            "/api/intus/projects/default_purlin/files/llm-edit",
            json={
                "prompt": "Refactor purlin into helper",
                "files": [{"id": str(design_id), "filename": "design.py"}],
            },
        )
    finally:
        intus_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["model"] == "deepseek-v4-flash"
    assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    assert body["snapshot"]["id"]
    assert len(body["files"]) == 1
    assert body["files"][0]["filename"] == "design.py"
