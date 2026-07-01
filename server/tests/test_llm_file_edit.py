from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import core.db as core_db
from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.config import Settings
from core.db import get_db
from core.llm_client import (
    LlmBillingError,
    LlmFileEditTruncatedError,
    LlmGenerationError,
    LlmInvalidFileEditError,
    LlmProviderAuthenticationError,
    LlmProviderRateLimitError,
    TokenUsage,
)
from core.llm_usage import LlmUsageLimitExceeded
from core.models import (
    AppUser,
    Artifact,
    CompileJob,
    LlmEditJob,
    LlmUsageRecord,
    Project,
    ProjectFile,
    SourceSnapshot,
    Tenant,
    TenantMembership,
)
from llm_test_helpers import make_llm_settings
from workflows.intus import intus_server
from workflows.intus.intus_server import app


class FakeBillingPublisher:
    def __init__(self, raise_on_publish=False):
        self.raise_on_publish = raise_on_publish
        self.published = []

    async def publish_json(self, subject, message, message_id=None):
        self.published.append((subject, message, message_id))
        if self.raise_on_publish:
            raise RuntimeError("billing publish failed")


def enable_llm(monkeypatch):
    monkeypatch.setattr(
        intus_server,
        "get_settings",
        lambda: make_llm_settings(llm_api_key="test-key"),
    )


def file_pointer(file: ProjectFile) -> dict[str, str]:
    return {
        "id": str(file.id),
        "filename": file.filename,
        "updated_at": file.updated_at.isoformat(),
    }


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
        if raises is not None:
            raise raises
        assert request.prompt == "Refactor purlin into helper"
        assert {str(f.id) for f in files}.issubset({str(f.id) for f in request.files})
        assert auth.tenant_id is not None
        assert project_id is not None
        if billing_publisher is not None:
            event_id = uuid4()
            return_value.billing_event_id = event_id
            try:
                await billing_publisher.publish_json(
                    "tertius.billing.usage.llm.tokens",
                    SimpleNamespace(
                        event_id=event_id,
                        operation="files.llm_edit",
                        tenant_id=auth.tenant_id,
                        user_id=auth.user_id,
                        project_id=project_id,
                        prompt=request.prompt,
                    ),
                    message_id=f"billing-usage:{event_id}",
                )
            except Exception as exc:
                raise LlmBillingError("LLM billing failed") from exc
        return return_value

    return fake_generate_file_edits


def use_test_background_session(monkeypatch, db_session):
    testing_session_local = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    monkeypatch.setattr(core_db, "SessionLocal", testing_session_local)


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

    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
            SimpleNamespace(
                file_id=helper.id, content="def make_purlin():\n    return 1\n", summary="Update helper"
            ),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )

    monkeypatch.setattr(intus_server, "generate_file_edits", make_fake_generate_file_edits(return_value=fake_result))

    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [
                file_pointer(design),
                file_pointer(helper),
            ],
            "active_file_id": str(design_id),
            "metadata": {"source": "compiler_tab"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["outcome"] == "changed"
    assert body["message"] == ""
    assert body["provider"] == "openai-chat-completions"
    assert body["model"] == "test-openai-compatible-model"
    assert body["usage"]["total_tokens"] == 300
    assert body["cost_usd"] == 0.0005
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

    usage_records = db_session.scalars(
        select(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
            LlmUsageRecord.operation == "files.llm_edit",
        )
    ).all()
    assert len(usage_records) == 1
    assert len(publisher.published) == 1
    subject, event, message_id = publisher.published[0]
    assert subject == "tertius.billing.usage.llm.tokens"
    assert event.operation == "files.llm_edit"
    assert usage_records[0].event_id == event.event_id
    assert message_id is not None


def test_llm_file_edit_allows_provider_to_return_changed_subset(
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

    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_helper_content = helper.content

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design.id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )

    monkeypatch.setattr(intus_server, "generate_file_edits", make_fake_generate_file_edits(return_value=fake_result))

    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [
                file_pointer(design),
                file_pointer(helper),
            ],
            "active_file_id": str(design.id),
            "metadata": {"source": "compiler_tab"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["files"]) == 1
    assert body["files"][0]["filename"] == "design.py"

    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == "import helper\n"
    assert db_session.get(ProjectFile, helper.id).content == original_helper_content
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
    assert db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
            LlmUsageRecord.operation == "files.llm_edit",
        )
    ) == 1
    assert len(publisher.published) == 1


def test_llm_file_edit_returns_409_when_file_version_changes_before_persist(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_pointer = file_pointer(design)

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design.id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )

    async def fake_generate_file_edits(*args, **kwargs):
        design.content = "user changed while llm ran\n"
        design.updated_at = design.updated_at + timedelta(seconds=1)
        db_session.commit()
        return fake_result

    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [original_pointer],
            "active_file_id": str(design.id),
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "error": "Files changed while AI edit was running. Reload and try again.",
        "retryable": False,
    }
    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == "user changed while llm ran\n"
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []
    assert publisher.published == []


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

    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id

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
                file_pointer(design),
                file_pointer(other_file),
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
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id
    original_content = design.content

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
            "files": [file_pointer(design)],
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


def test_llm_file_edit_no_change_returns_200_records_usage_without_snapshot(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content

    fake_result = SimpleNamespace(
        success=True,
        outcome="no_change",
        message="Already matches the request",
        files=[],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )

    monkeypatch.setattr(
        intus_server,
        "generate_file_edits",
        make_fake_generate_file_edits(return_value=fake_result),
    )
    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["outcome"] == "no_change"
    assert body["message"] == "Already matches the request"
    assert body["snapshot"] is None
    assert body["files"] == []
    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
            LlmUsageRecord.operation == "files.llm_edit",
        )
    ) == 1
    assert len(publisher.published) == 1


def test_llm_file_edit_cannot_complete_returns_200_records_usage_without_snapshot(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content

    fake_result = SimpleNamespace(
        success=True,
        outcome="cannot_complete",
        message="Creating a new file is required",
        files=[],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )
    monkeypatch.setattr(
        intus_server,
        "generate_file_edits",
        make_fake_generate_file_edits(return_value=fake_result),
    )
    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["outcome"] == "cannot_complete"
    assert body["message"] == "Creating a new file is required"
    assert body["snapshot"] is None
    assert body["files"] == []
    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
            LlmUsageRecord.operation == "files.llm_edit",
        )
    ) == 1
    assert len(publisher.published) == 1


def test_llm_file_edit_billing_failure_on_no_change_returns_503_without_snapshot(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )

    fake_result = SimpleNamespace(
        success=True,
        outcome="no_change",
        message="Already matches the request",
        files=[],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
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
            "files": [file_pointer(design)],
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "LLM billing failed",
        "retryable": True,
    }
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []


def test_llm_file_edit_truncated_response_does_not_persist(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content

    async def fake_generate_file_edits(*args, **kwargs):
        raise LlmFileEditTruncatedError("truncated")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "success": False,
        "error": "LLM response was truncated",
        "retryable": True,
    }
    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []


def test_llm_file_edit_returns_provider_authentication_failure(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content

    async def fake_generate_file_edits(*args, **kwargs):
        raise LlmProviderAuthenticationError("LLM provider authentication failed")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "LLM provider authentication failed",
        "retryable": False,
    }
    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []


def test_llm_file_edit_returns_503_when_billing_publisher_unavailable(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id
    original_content = design.content

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )

    async def fake_create_billing_publisher(settings):
        raise LlmBillingError("LLM billing failed")

    monkeypatch.setattr(
        intus_server,
        "generate_file_edits",
        make_fake_generate_file_edits(return_value=fake_result),
    )
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
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
    assert db_session.scalars(select(SourceSnapshot)).all() == []

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
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id
    original_content = design.content

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
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
            "files": [file_pointer(design)],
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


def test_llm_file_edit_job_completes_and_status_returns_result(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )

    monkeypatch.setattr(
        intus_server,
        "generate_file_edits",
        make_fake_generate_file_edits(return_value=fake_result),
    )
    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design_id),
            "metadata": {"source": "generate_design_window"},
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "queued"
    job_id = UUID(body["job_id"])

    db_session.expire_all()
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["job_id"] == str(job_id)
    assert status_body["status"] == "succeeded"
    assert status_body["error"] is None
    assert status_body["finished_at"]
    assert status_body["result"]["success"] is True
    assert status_body["result"]["outcome"] == "changed"
    assert status_body["result"]["provider"] == "openai-chat-completions"
    assert status_body["result"]["usage"]["total_tokens"] == 30
    assert status_body["result"]["snapshot"]["id"]
    assert len(status_body["result"]["files"]) == 1
    assert status_body["result"]["files"][0]["id"] == str(design_id)
    assert status_body["result"]["files"][0]["filename"] == "design.py"
    assert status_body["result"]["files"][0]["content"] == "import helper\n"
    assert status_body["result"]["files"][0]["changed"] is True
    assert status_body["result"]["files"][0]["summary"] == "Use helper"

    history_response = authenticated_intus_client.get(
        "/projects/default_purlin/files/llm-edit/jobs"
    )
    assert history_response.status_code == 200
    history_message = next(
        message
        for message in history_response.json()["messages"]
        if message["job_id"] == str(job_id)
    )
    assert history_message["metadata"] == {"source": "generate_design_window"}

    db_session.expire_all()
    assert db_session.get(ProjectFile, design_id).content == "import helper\n"
    job = db_session.get(LlmEditJob, job_id)
    assert job.status == "succeeded"
    assert job.attempt_count == 1
    assert job.requested_by == seeded_tenant.user_id
    assert job.result_payload["outcome"] == "changed"
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
    assert db_session.scalar(
        select(func.count()).select_from(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == seeded_tenant.tenant_id,
            LlmUsageRecord.operation == "files.llm_edit",
        )
    ) == 1
    assert len(publisher.published) == 1


def test_llm_file_edit_job_records_provider_auth_failure(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content

    async def fake_generate_file_edits(*args, **kwargs):
        raise LlmProviderAuthenticationError("LLM provider authentication failed")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
        },
    )

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])

    db_session.expire_all()
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "failed"
    assert status_body["result"] is None
    assert status_body["error"] == "LLM provider authentication failed"
    assert status_body["user_message"] == "LLM provider authentication failed"
    assert status_body["retryable"] is False
    assert status_body["finished_at"]

    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.get(LlmEditJob, job_id).attempt_count == 1
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []


def test_llm_file_edit_job_retries_provider_rate_limit_with_backoff_then_succeeds(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id
    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )
    attempts = 0

    async def fake_generate_file_edits(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LlmProviderRateLimitError("LLM provider rate limit exceeded")
        return fake_result

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design_id),
        },
    )

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])

    db_session.expire_all()
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "succeeded"
    assert status_body["error"] is None
    assert status_body["result"]["files"][0]["content"] == "import helper\n"
    assert attempts == 2

    db_session.expire_all()
    job = db_session.get(LlmEditJob, job_id)
    assert job.status == "succeeded"
    assert job.attempt_count == 2
    assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1


def test_llm_file_edit_job_retries_provider_rate_limit_up_to_four_attempts(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content
    attempts = 0

    async def fake_generate_file_edits(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise LlmProviderRateLimitError("LLM provider rate limit exceeded")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
        },
    )

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])

    db_session.expire_all()
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "failed"
    assert status_body["error"] == "LLM provider rate limit exceeded"
    assert status_body["user_message"] == "LLM provider rate limit exceeded"
    assert status_body["retryable"] is True
    assert attempts == 4

    db_session.expire_all()
    job = db_session.get(LlmEditJob, job_id)
    assert job.status == "failed"
    assert job.attempt_count == 4
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []


def test_llm_file_edit_job_does_not_retry_truncated_output(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content
    attempts = 0

    async def fake_generate_file_edits(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise LlmFileEditTruncatedError("LLM output truncated before completion")

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
        },
    )

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])

    db_session.expire_all()
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "failed"
    assert status_body["error"] == "LLM output truncated before completion"
    assert attempts == 1

    db_session.expire_all()
    job = db_session.get(LlmEditJob, job_id)
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.scalars(select(LlmUsageRecord)).all() == []


def test_llm_file_edit_job_retries_generation_failure_once_then_succeeds(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id
    provider_message = "LLM provider request failed (APITimeoutError): request timed out"
    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
    )
    attempts = 0

    async def fake_generate_file_edits(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LlmGenerationError(provider_message)
        return fake_result

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design_id),
        },
    )

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])

    db_session.expire_all()
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "succeeded"
    assert status_body["error"] is None
    assert status_body["result"]["files"][0]["content"] == "import helper\n"
    assert attempts == 2

    db_session.expire_all()
    job = db_session.get(LlmEditJob, job_id)
    assert job.status == "succeeded"
    assert job.attempt_count == 2
    assert job.retryable is False
    assert db_session.get(ProjectFile, design_id).content == "import helper\n"
    assert db_session.scalar(select(func.count()).select_from(LlmUsageRecord)) == 1

def test_llm_file_edit_job_records_provider_generation_detail(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    original_content = design.content
    provider_message = (
        "LLM provider request failed (APITimeoutError): request timed out"
    )

    async def fake_generate_file_edits(*args, **kwargs):
        raise LlmGenerationError(provider_message)

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
        },
    )

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])

    db_session.expire_all()
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "failed"
    assert status_body["error"] == provider_message
    assert status_body["user_message"] == provider_message
    assert status_body["retryable"] is True

    history_response = authenticated_intus_client.get(
        "/projects/default_purlin/files/llm-edit/jobs?limit=10"
    )
    assert history_response.status_code == 200
    messages = history_response.json()["messages"]
    assert messages[0]["job_id"] == str(job_id)
    assert messages[0]["content"] == provider_message

    db_session.expire_all()
    assert db_session.get(ProjectFile, design.id).content == original_content
    assert db_session.get(LlmEditJob, job_id).error == provider_message
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []


def test_llm_file_edit_job_list_returns_history_with_compile_and_reconciles_stale_jobs(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    monkeypatch.setattr(intus_server, "get_settings", lambda: make_llm_settings(llm_timeout_seconds=1))
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    succeeded = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        request_payload={"prompt": "Make it taller", "files": [{"id": str(uuid4()), "filename": "design.py"}]},
        result_payload={
            "success": True,
            "outcome": "changed",
            "message": "",
            "model": "test-model",
            "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
            "files": [
                {
                    "id": str(uuid4()),
                    "filename": "design.py",
                    "content": "length = 200\n",
                    "changed": True,
                    "summary": "Increased height",
                }
            ],
        },
        created_at=created,
        finished_at=created + timedelta(seconds=2),
    )
    failed = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="failed",
        error="provider failed",
        user_message="LLM generation failed",
        retryable=True,
        request_payload={"prompt": "Try a fillet", "files": [], "model_id": "old-requested-model"},
        created_at=created + timedelta(seconds=3),
        finished_at=created + timedelta(seconds=4),
    )
    running = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        request_payload={"prompt": "Still working", "files": [{"id": str(uuid4()), "filename": "design.py"}]},
        created_at=datetime.now(timezone.utc),
    )
    stale = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        request_payload={"prompt": "Lost worker", "files": []},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db_session.add_all([succeeded, failed, running, stale])
    db_session.flush()
    compile_job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        export_format="glb",
        originating_llm_edit_job_id=succeeded.id,
        created_at=created + timedelta(seconds=5),
        finished_at=created + timedelta(seconds=8),
    )
    db_session.add(compile_job)
    db_session.flush()
    artifact = Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        compile_job_id=compile_job.id,
        kind="glb",
        storage_key="artifacts/test.glb",
        content_type="model/gltf-binary",
        byte_size=5,
        content=b"model",
    )
    db_session.add(artifact)
    db_session.commit()

    response = authenticated_intus_client.get("/projects/default_purlin/files/llm-edit/jobs?limit=200")

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [message["job_id"] for message in messages] == [
        str(succeeded.id),
        str(failed.id),
        str(stale.id),
        str(running.id),
    ]
    first = messages[0]
    assert first["prompt"] == "Make it taller"
    assert first["content"] == "Updated 1 file(s). Model: test-model. Increased height"
    assert first["model"] == "test-model"
    assert first["usage"]["total_tokens"] == 9
    assert first["requested_file_count"] == 1
    assert first["files"] == [
        {
            "id": first["files"][0]["id"],
            "filename": "design.py",
            "changed": True,
            "summary": "Increased height",
        }
    ]
    assert "content" not in first["files"][0]
    assert first["compile"] == {
        "job_id": str(compile_job.id),
        "status": "succeeded",
        "artifact_id": str(artifact.id),
        "export_format": "glb",
    }
    assert messages[1]["content"] == "LLM generation failed"
    assert messages[1]["model"] == "old-requested-model"
    assert messages[1]["compile"] is None
    assert messages[2]["status"] == "failed"
    assert messages[2]["content"] == "AI generation stopped unexpectedly. Try again."
    assert messages[3]["status"] == "running"
    assert messages[3]["content"] == ""
    assert messages[3]["requested_file_count"] == 1


def test_compile_project_persists_originating_llm_edit_job_id(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    async def fake_publish_compile_command(command):
        return None

    monkeypatch.setattr(intus_server, "publish_compile_command", fake_publish_compile_command)
    llm_job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        request_payload={"prompt": "Generate a design", "files": []},
        result_payload={"success": True, "message": "Done"},
    )
    db_session.add(llm_job)
    db_session.commit()

    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={
            "code": "import build123d as bd\nlength = 150\n",
            "export_format": "glb",
            "file": "design.py",
            "originating_llm_edit_job_id": str(llm_job.id),
        },
    )

    assert response.status_code == 202
    job = db_session.get(CompileJob, UUID(response.json()["job_id"]))
    assert job is not None
    assert job.originating_llm_edit_job_id == llm_job.id


def test_llm_file_edit_job_public_mounted_route(
    db_session, seeded_tenant, monkeypatch
):
    from main import app as main_app

    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id

    fake_result = SimpleNamespace(
        success=True,
        outcome="no_change",
        message="Already matches the request",
        files=[],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7),
        cost_usd=0.0001,
        provider_request_id="chatcmpl-test",
        billing_event_id=None,
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
        client = TestClient(main_app)
        start_response = client.post(
            "/api/intus/projects/default_purlin/files/llm-edit/jobs",
            json={
                "prompt": "Refactor purlin into helper",
                "files": [file_pointer(design)],
                "active_file_id": str(design_id),
            },
        )
        assert start_response.status_code == 202
        job_id = start_response.json()["job_id"]
        db_session.expire_all()
        status_response = client.get(
            f"/api/intus/projects/default_purlin/files/llm-edit/jobs/{job_id}"
        )
    finally:
        intus_server.app.dependency_overrides.clear()

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["outcome"] == "no_change"
    assert body["result"]["message"] == "Already matches the request"
    assert body["result"]["usage"]["total_tokens"] == 7


def test_llm_file_edit_public_mounted_route(
    db_session, seeded_tenant, monkeypatch
):
    from main import app as main_app

    enable_llm(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id

    fake_result = SimpleNamespace(
        success=True,
        outcome="changed",
        message="",
        files=[
            SimpleNamespace(file_id=design_id, content="import helper\n", summary="Use helper"),
        ],
        provider="openai-chat-completions",
        model="test-openai-compatible-model",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        cost_usd=0.0005,
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
                "files": [file_pointer(design)],
            },
        )
    finally:
        intus_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["provider"] == "openai-chat-completions"
    assert body["model"] == "test-openai-compatible-model"
    assert body["usage"]["total_tokens"] == 30
    assert body["cost_usd"] == 0.0005
    assert body["snapshot"]["id"]
    assert len(body["files"]) == 1
    assert body["files"][0]["filename"] == "design.py"
