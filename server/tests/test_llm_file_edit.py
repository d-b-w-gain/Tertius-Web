from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import core.db as core_db
from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.llm_client import (
    LlmBillingError,
    LlmFileEditTruncatedError,
    LlmGenerationError,
    LlmProviderAuthenticationError,
    LlmProviderRateLimitError,
    TokenUsage,
)
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
from core.repositories import LlmEditRepository
from llm_test_helpers import make_llm_settings
from workflows.intus import intus_server


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
        prior_prompts=(),
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


def capture_intus_metrics(monkeypatch):
    counters = []
    histograms = []
    up_down_counters = []
    monkeypatch.setattr(
        intus_server,
        "counter_add",
        lambda name, value=1, attributes=None: counters.append((name, value, attributes or {})),
    )
    monkeypatch.setattr(
        intus_server,
        "histogram_record",
        lambda name, value, attributes=None: histograms.append((name, value, attributes or {})),
    )
    monkeypatch.setattr(
        intus_server,
        "up_down_counter_add",
        lambda name, value=1, attributes=None: up_down_counters.append((name, value, attributes or {})),
    )
    return counters, histograms, up_down_counters


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


def test_llm_file_edit_job_completes_and_status_returns_result(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    counters, histograms, up_down_counters = capture_intus_metrics(monkeypatch)
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
    base_labels = {"llm.operation": "files.llm_edit", "workflow": "intus"}
    assert ("tertius.llm.job.queued.count", 1, base_labels) in counters
    assert ("tertius.llm.job.started.count", 1, base_labels) in counters
    assert (
        "tertius.llm.job.finished.count",
        1,
        {**base_labels, "job_status": "succeeded"},
    ) in counters
    assert not any(name == "tertius.llm.job.failed.count" for name, _value, _attrs in counters)
    assert any(
        name == "tertius.llm.job.duration"
        and attrs == {**base_labels, "job_status": "succeeded"}
        and value >= 0
        for name, value, attrs in histograms
    )
    assert ("tertius.llm.jobs.active", 1, base_labels) in up_down_counters
    assert ("tertius.llm.jobs.active", -1, base_labels) in up_down_counters


def test_llm_file_edit_job_includes_recent_prompts_excluding_current_job(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    llm_repo = LlmEditRepository(db_session, seeded_tenant.tenant_id)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for index in range(6):
        llm_repo.start_job(
            seeded_tenant.project_id,
            seeded_tenant.user_id,
            {"prompt": f"history-{index}", "files": []},
            status="succeeded",
        )
    for index, job in enumerate(llm_repo.list_jobs_for_project(seeded_tenant.project_id, limit=6)):
        job.created_at = base_time + timedelta(minutes=index)
    db_session.commit()

    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )

    captured_prompts: list[str] = []
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

    async def fake_generate_file_edits(
        request,
        *,
        files,
        settings,
        auth,
        project_id,
        prior_prompts=(),
        openai_client=None,
        billing_publisher=None,
    ):
        captured_prompts[:] = list(prior_prompts)
        return fake_result

    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "current request",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
            "metadata": {"source": "generate_design_window"},
        },
    )

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "succeeded"
    assert captured_prompts == [
        "history-1",
        "history-2",
        "history-3",
        "history-4",
        "history-5",
    ]


def test_llm_file_edit_job_records_billing_publish_error_metric(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_llm(monkeypatch)
    use_test_background_session(monkeypatch, db_session)
    counters, _histograms, _up_down_counters = capture_intus_metrics(monkeypatch)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )

    fake_result = SimpleNamespace(
        success=True,
        outcome="no_change",
        message="Looks good",
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
    publisher = FakeBillingPublisher(raise_on_publish=True)

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

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
    status_response = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job_id}"
    )

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "failed"
    assert (
        "tertius.billing.publish.error.count",
        1,
        {
            "provider": "openai-chat-completions",
            "model_id": "test-openai-compatible-model",
            "operation": "files.llm_edit",
        },
    ) in counters


def test_llm_file_edit_job_rejects_cross_tenant_file_pointer_before_enqueue(
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
        "/projects/default_purlin/files/llm-edit/jobs",
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
    assert db_session.scalar(
        select(func.count()).select_from(LlmEditJob).where(
            LlmEditJob.tenant_id == seeded_tenant.tenant_id,
        )
    ) == 0


def test_llm_file_edit_job_detects_file_version_change_before_persist(
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
    original_pointer = file_pointer(design)
    original_updated_at = design.updated_at
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
        design.updated_at = original_updated_at + timedelta(seconds=1)
        db_session.commit()
        return fake_result

    publisher = FakeBillingPublisher()

    async def fake_create_billing_publisher(settings):
        return publisher, None

    monkeypatch.setattr(intus_server, "generate_file_edits", fake_generate_file_edits)
    monkeypatch.setattr(intus_server, "create_billing_publisher", fake_create_billing_publisher)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Refactor purlin into helper",
            "files": [original_pointer],
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
    assert status_body["error"] == "Files changed while AI edit was running. Reload and try again."
    assert status_body["user_message"] == "Files changed while AI edit was running. Reload and try again."
    assert status_body["retryable"] is False
    assert db_session.get(ProjectFile, design.id).content == "user changed while llm ran\n"
    assert db_session.scalars(select(SourceSnapshot)).all() == []
    assert db_session.scalars(select(LlmUsageRecord)).all() == []
    assert publisher.published == []


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
    counters, histograms, _up_down_counters = capture_intus_metrics(monkeypatch)
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
    failure_labels = {
        "llm.operation": "files.llm_edit",
        "workflow": "intus",
        "job_status": "failed",
        "failure_category": "truncated",
        "retryable": "true",
    }
    assert ("tertius.llm.job.failed.count", 1, failure_labels) in counters
    assert ("tertius.llm.job.finished.count", 1, failure_labels) in counters
    assert any(
        name == "tertius.llm.job.duration" and attrs == failure_labels and value >= 0
        for name, value, attrs in histograms
    )


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


def test_llm_file_edit_stale_window_covers_retry_attempts():
    settings = make_llm_settings(
        llm_timeout_seconds=1,
        llm_file_edit_max_generation_attempts=2,
        llm_file_edit_max_rate_limit_attempts=4,
        llm_file_edit_rate_limit_backoff_cap_seconds=30.0,
    )

    assert intus_server._llm_edit_stale_after_seconds(settings) == 226


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


def _retry_metric_points(reader, name):
    points = []
    for resource_metrics in reader.get_metrics_data().resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    points.extend(metric.data.data_points)
    return points


def test_record_retry_emits_counter_and_span_event(monkeypatch):
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    import core.telemetry as telemetry

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    monkeypatch.setattr(telemetry, "get_meter", lambda name: meter_provider.get_meter(name))
    monkeypatch.setattr(telemetry, "_METRIC_INSTRUMENTS", {})

    class ListExporter(SpanExporter):
        def __init__(self):
            self.spans = []

        def export(self, spans):
            self.spans.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            return None

        def force_flush(self, timeout_millis=30000):
            return True

    exporter = ListExporter()
    trace_provider = TracerProvider()
    trace_provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        intus_server,
        "get_tracer",
        lambda name: trace_provider.get_tracer(name),
    )

    with intus_server.get_tracer("test").start_as_current_span("llm.file_edit.job"):
        intus_server._record_retry(
            reason="rate_limit",
            attempt=2,
            backoff_seconds=0.5,
            attributes={"llm.operation": "files.llm_edit", "workflow": "intus"},
        )

    assert len(exporter.spans) == 1
    events = [e for e in exporter.spans[0].events if e.name == "llm.retry"]
    assert len(events) == 1
    event_attrs = dict(events[0].attributes or {})
    assert event_attrs["llm.retry.reason"] == "rate_limit"
    assert event_attrs["llm.retry.attempt"] == 2
    assert event_attrs["llm.retry.backoff_seconds"] == 0.5

    retry_points = _retry_metric_points(metric_reader, "tertius.llm.retry.count")
    assert len(retry_points) == 1
    assert retry_points[0].value == 1
    assert dict(retry_points[0].attributes).get("llm.retry_reason") == "rate_limit"
    meter_provider.shutdown()
    trace_provider.shutdown()
