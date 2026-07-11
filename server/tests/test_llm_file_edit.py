import socket
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select

from core.llm_file_edit import TokenUsage
from core.models import LlmEditJob, ProjectFile
from core.pi_agent_prompt import PiAgentPromptError, render_pi_agent_user_prompt
from workflows.intus import intus_server
from workflows.intus.pi_agent_job import build_coding_agent_prompt


def file_pointer(file: ProjectFile) -> dict[str, str]:
    return {"id": str(file.id), "filename": file.filename, "updated_at": file.updated_at.isoformat()}


def enable_pi(monkeypatch):
    base = intus_server.get_settings()
    settings = base.model_copy(update={"pi_agent_enabled": True, "pi_agent_estimated_output_tokens": 100})
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    return settings


def design_file(db_session, seeded_tenant):
    return db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))


def test_list_files_includes_metadata(authenticated_intus_client):
    response = authenticated_intus_client.get("/projects/default_purlin/files")
    assert response.status_code == 200
    assert response.json()["file_metadata"][0]["filename"] == "design.py"


def test_submit_commits_job_and_publishes_selected_persisted_files(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    settings = enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    commands = []

    async def publish(_settings, command):
        commands.append(command)

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Change length", "files": [file_pointer(design)], "active_file_id": str(design.id)},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    job_id = UUID(response.json()["job_id"])
    db_session.expire_all()
    job = db_session.get(LlmEditJob, job_id)
    assert job.status == "running"
    assert job.attempt_count == 1
    assert len(commands) == 1
    command = commands[0]
    assert command.job_id == job_id
    assert command.tenant_id == seeded_tenant.tenant_id
    assert command.project_id == seeded_tenant.project_id
    assert command.provider == settings.pi_agent_provider
    assert command.model == settings.pi_agent_model
    assert command.files[0].content == design.content


def test_submit_estimates_the_complete_shared_worker_prompt(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    captured = {}

    def capture_estimate(**kwargs):
        captured.update(kwargs)
        return TokenUsage(prompt_tokens=1, completion_tokens=100, total_tokens=101)

    async def publish(_settings, _command):
        return None

    monkeypatch.setattr(intus_server, "estimate_pi_agent_usage", capture_estimate)
    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Change length",
            "files": [file_pointer(design)],
            "active_file_id": str(design.id),
        },
    )

    assert response.status_code == 202
    assert captured["user_prompt"] == render_pi_agent_user_prompt(
        conversation_prompt="Change length",
        editable_filenames=[design.filename],
        active_filename=design.filename,
    )
    assert captured["source_bytes"] == len(design.content.encode("utf-8"))


def test_submit_returns_fixed_unavailable_response_when_policy_cannot_load(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    caplog,
):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)

    def unavailable_prompt():
        raise PiAgentPromptError("secret prompt path /tmp/policy")

    async def forbidden_publish(*_args):
        raise AssertionError("unavailable policy must not publish")

    monkeypatch.setattr(intus_server, "load_pi_agent_prompt", unavailable_prompt)
    monkeypatch.setattr(intus_server, "publish_pi_agent_command", forbidden_publish)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Inspect", "files": [file_pointer(design)]},
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "AI editing is not configured",
        "retryable": False,
    }
    assert "secret prompt path" not in caplog.text
    assert db_session.scalar(select(func.count()).select_from(LlmEditJob)) == 0


def test_api_estimate_and_worker_execution_share_exact_legacy_prompt_bytes(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    support = ProjectFile(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        filename="dimensions.py",
        content="largeur = 'café'\n",
    )
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            support,
            LlmEditJob(
                tenant_id=seeded_tenant.tenant_id,
                project_id=seeded_tenant.project_id,
                requested_by=seeded_tenant.user_id,
                status="succeeded",
                request_payload={"prompt": "Première demande"},
                created_at=now - timedelta(minutes=2),
            ),
            LlmEditJob(
                tenant_id=seeded_tenant.tenant_id,
                project_id=seeded_tenant.project_id,
                requested_by=seeded_tenant.user_id,
                status="failed",
                request_payload={"prompt": "Deuxième demande"},
                created_at=now - timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()
    db_session.refresh(support)
    captured = {}
    commands = []
    real_estimate = intus_server.estimate_pi_agent_usage

    def capture_estimate(**kwargs):
        captured.update(kwargs)
        return real_estimate(**kwargs)

    async def publish(_settings, command):
        commands.append(command)

    monkeypatch.setattr(intus_server, "estimate_pi_agent_usage", capture_estimate)
    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)

    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Agrandir la pièce",
            "files": [file_pointer(design), file_pointer(support)],
            "active_file_id": str(support.id),
            "metadata": {"source": "éditeur"},
        },
    )

    assert response.status_code == 202, response.json()
    assert len(commands) == 1
    assert commands[0].prior_prompts == ["Première demande", "Deuxième demande"]
    assert [file.filename for file in commands[0].files] == [
        "design.py",
        "dimensions.py",
    ]
    assert commands[0].active_file_id == support.id
    assert captured["user_prompt"].encode("utf-8") == build_coding_agent_prompt(
        commands[0]
    ).encode("utf-8")
    assert captured["source_bytes"] == sum(
        len(file.content.encode("utf-8")) for file in commands[0].files
    )
    assert captured["metadata"] == {"source": "éditeur"}


def test_submit_rejects_unsupported_model_before_publish(
    authenticated_intus_client, db_session, seeded_tenant, monkeypatch
):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)

    async def publish(*_args):
        raise AssertionError("unsupported model must not publish")

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Change length",
            "model_id": "unsupported-model",
            "files": [file_pointer(design)],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_model"


def test_ambiguous_publish_failure_stays_queued_and_returns_accepted(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)

    async def fail_publish(_settings, _command):
        raise RuntimeError("NATS unavailable")

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", fail_publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Change length", "files": [file_pointer(design)]},
    )

    assert response.status_code == 202
    job = db_session.scalar(select(LlmEditJob))
    db_session.refresh(job)
    assert job.status == "queued"
    assert job.error_code is None
    assert job.request_payload["dispatch_attempted_at"]


def test_result_that_finishes_during_publish_is_not_overwritten_running(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)

    async def publish(_settings, command):
        job = db_session.get(LlmEditJob, command.job_id)
        job.status = "succeeded"
        job.result_payload = {"success": True, "outcome": "no_changes"}
        db_session.commit()

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Inspect", "files": [file_pointer(design)]},
    )
    assert response.status_code == 202
    job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
    db_session.refresh(job)
    assert job.status == "succeeded"


def test_job_persists_exact_dispatched_manifest(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    commands = []

    async def publish(_settings, command):
        commands.append(command)

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Inspect", "files": [file_pointer(design)]},
    )
    job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
    manifest = job.request_payload["dispatched_manifest"]
    assert manifest == [
        {
            "id": str(commands[0].files[0].id),
            "filename": commands[0].files[0].filename,
            "updated_at": commands[0].files[0].updated_at.isoformat(),
            "sha256": commands[0].files[0].sha256,
        }
    ]
    assert "content" not in manifest[0]
    assert job.request_payload["files"][0]["id"] == str(design.id)
    assert job.request_payload["files"][0]["filename"] == design.filename
    assert job.request_payload["dispatched_at"]


def test_post_publish_running_commit_failure_returns_accepted_and_does_not_fail_job(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    published = []

    async def publish(_settings, command):
        published.append(command)

    original_commit = db_session.commit
    commits = 0

    def fail_second_commit():
        nonlocal commits
        commits += 1
        if commits == 2:
            raise RuntimeError("ambiguous commit")
        original_commit()

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    monkeypatch.setattr(db_session, "commit", fail_second_commit)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Inspect", "files": [file_pointer(design)]},
    )
    assert response.status_code == 202
    assert len(published) == 1
    db_session.expire_all()
    job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
    assert job.status == "queued"
    assert job.error_code is None


def test_oversize_command_rolls_back_job_before_publish(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    settings = enable_pi(monkeypatch)
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings.model_copy(update={"pi_agent_request_max_bytes": 10}))
    design = design_file(db_session, seeded_tenant)

    async def publish(*_args):
        raise AssertionError("oversize command must not publish")

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Inspect", "files": [file_pointer(design)]},
    )
    assert response.status_code == 400
    assert db_session.scalar(select(func.count()).select_from(LlmEditJob)) == 0


def test_actual_unavailable_nats_keeps_attempted_job_queued(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    settings = enable_pi(monkeypatch)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        unavailable_port = probe.getsockname()[1]
    monkeypatch.setattr(
        intus_server,
        "get_settings",
        lambda: settings.model_copy(update={"nats_url": f"nats://127.0.0.1:{unavailable_port}"}),
    )
    design = design_file(db_session, seeded_tenant)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "Inspect", "files": [file_pointer(design)]},
    )
    assert response.status_code == 202
    job = db_session.scalar(select(LlmEditJob))
    db_session.refresh(job)
    assert job.status == "queued"
    assert job.request_payload["dispatch_attempted_at"]


def test_project_with_active_job_rejects_second_submit(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    db_session.add(LlmEditJob(tenant_id=seeded_tenant.tenant_id, project_id=seeded_tenant.project_id, requested_by=seeded_tenant.user_id, status="running", request_payload={"prompt": "first"}))
    db_session.commit()

    async def publish(*_args):
        raise AssertionError("must not publish")

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={"prompt": "second", "files": [file_pointer(design)]},
    )
    assert response.status_code == 409


def test_job_status_preserves_public_contract(authenticated_intus_client, db_session, seeded_tenant):
    job = LlmEditJob(tenant_id=seeded_tenant.tenant_id, project_id=seeded_tenant.project_id, requested_by=seeded_tenant.user_id, status="running", request_payload={"prompt": "working", "files": []})
    db_session.add(job)
    db_session.commit()
    response = authenticated_intus_client.get(f"/projects/default_purlin/files/llm-edit/jobs/{job.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
