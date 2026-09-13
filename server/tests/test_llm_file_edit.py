import socket
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from core.llm_file_edit import TokenUsage
from core.models import LlmEditJob, ProjectFile
from core.pi_agent_conversation import render_conversation_context
from core.pi_agent_messages import (
    PiAgentConversationContext,
    PiAgentProgressEvent,
    PiAgentProgressSnapshot,
)
from core.pi_agent_prompt import PiAgentPromptError, load_pi_agent_prompt, render_pi_agent_user_prompt
from workflows.intus import intus_server


def file_pointer(file: ProjectFile) -> dict[str, str]:
    return {"id": str(file.id), "filename": file.filename, "updated_at": file.updated_at.isoformat()}


def enable_pi(monkeypatch):
    base = intus_server.get_settings()
    settings = base.model_copy(update={"pi_agent_enabled": True, "pi_agent_estimated_output_tokens": 100})
    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    return settings


def design_file(db_session, seeded_tenant):
    return db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == seeded_tenant.project_id))


def progress_snapshot(text: str = "Inspecting the design") -> dict:
    now = datetime.now(timezone.utc)
    return PiAgentProgressSnapshot(
        schema_version=1,
        execution_id=uuid4(),
        execution_started_at=now,
        last_batch_sequence=1,
        last_sequence=1,
        events=[
            PiAgentProgressEvent(
                sequence=1,
                kind="reasoning_delta",
                text=text,
                occurred_at=now,
            )
        ],
    ).model_dump(mode="json")


def test_list_files_includes_metadata(authenticated_intus_client):
    response = authenticated_intus_client.get("/projects/default_purlin/files")
    assert response.status_code == 200
    assert response.json()["file_metadata"][0]["filename"] == "design.py"


def test_submit_commits_job_and_publishes_selected_persisted_files(authenticated_intus_client, db_session, seeded_tenant, monkeypatch):
    settings = enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    commands = []
    snapshot = load_pi_agent_prompt()
    prior = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        request_payload={"prompt": "Earlier request", "files": []},
        result_payload={
            "outcome": "changed",
            "message": "Adjusted the bracket",
            "files": [
                {
                    "filename": "historical.py",
                    "content": "HISTORICAL_SOURCE_SENTINEL",
                    "changed": True,
                }
            ],
        },
    )
    db_session.add(prior)
    db_session.commit()

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
    assert command.schema_version == 2
    assert command.conversation.model_dump(mode="json") == job.request_payload["dispatched_conversation"]
    assert command.system_prompt_sha256 == snapshot.sha256
    assert job.request_payload["dispatched_command_schema_version"] == 2
    assert job.request_payload["dispatched_system_prompt_sha256"] == snapshot.sha256
    assert "dispatched_prior_prompts" not in job.request_payload
    serialized = command.model_dump_json()
    assert snapshot.content not in serialized
    assert str(snapshot.path) not in serialized
    assert "HISTORICAL_SOURCE_SENTINEL" not in command.conversation.model_dump_json()
    assert command.conversation.recent_turns[-1].assistant_summary == "Adjusted the bracket"


@pytest.mark.parametrize(
    "model_id",
    ["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra"],
)
def test_submit_dispatches_selected_model_consistently(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    model_id,
):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    commands = []
    queued_metric_models = []
    real_metric_attributes = intus_server.pi_agent_metric_attributes

    async def publish(_settings, command):
        commands.append(command)

    def capture_metric_attributes(**kwargs):
        queued_metric_models.append(kwargs["model"])
        return real_metric_attributes(**kwargs)

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    monkeypatch.setattr(
        intus_server,
        "pi_agent_metric_attributes",
        capture_metric_attributes,
    )
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Change length",
            "model_id": model_id,
            "files": [file_pointer(design)],
        },
    )

    assert response.status_code == 202
    job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
    assert job.request_payload["dispatched_model"] == model_id
    assert [command.model for command in commands] == [model_id]
    assert queued_metric_models == [model_id]


@pytest.mark.parametrize(
    "model_selection",
    [{}, {"model_id": ""}],
    ids=["omitted", "blank"],
)
def test_submit_defaults_omitted_or_blank_model_to_sol(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    model_selection,
):
    enable_pi(monkeypatch)
    design = design_file(db_session, seeded_tenant)
    commands = []

    async def publish(_settings, command):
        commands.append(command)
        raise RuntimeError("NATS unavailable")

    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Change length",
            "files": [file_pointer(design)],
            **model_selection,
        },
    )

    assert response.status_code == 202
    job = db_session.get(LlmEditJob, UUID(response.json()["job_id"]))
    assert job.status == "queued"
    assert job.request_payload["dispatched_model"] == "gpt-5.6-sol"
    assert [command.model for command in commands] == ["gpt-5.6-sol"]
    history = authenticated_intus_client.get(
        "/projects/default_purlin/files/llm-edit/jobs"
    )
    history_entry = next(
        message
        for message in history.json()["messages"]
        if message["job_id"] == str(job.id)
    )
    assert history_entry["model"] == "gpt-5.6-sol"


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
        conversation_prompt=render_conversation_context(
            PiAgentConversationContext(),
            "Change length",
        ),
        editable_filenames=[design.filename],
        active_filename=design.filename,
    )
    assert captured["source_bytes"] == len(design.content.encode("utf-8"))


@pytest.mark.parametrize(
    ("operational_cap", "expected_max_chars"),
    [(400_000, 300_000), (200_000, 200_000)],
)
def test_submit_uses_fixed_context_budget_and_ignores_legacy_tier(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
    monkeypatch,
    operational_cap,
    expected_max_chars,
):
    settings = enable_pi(monkeypatch).model_copy(
        update={"llm_file_edit_max_context_chars": operational_cap}
    )
    design = design_file(db_session, seeded_tenant)
    captured = {}

    def capture_selection(**kwargs):
        captured.update(kwargs)
        return kwargs["files"]

    async def publish(_settings, _command):
        return None

    monkeypatch.setattr(intus_server, "get_settings", lambda: settings)
    monkeypatch.setattr(
        intus_server,
        "select_domain_context_files",
        capture_selection,
    )
    monkeypatch.setattr(intus_server, "publish_pi_agent_command", publish)
    response = authenticated_intus_client.post(
        "/projects/default_purlin/files/llm-edit/jobs",
        json={
            "prompt": "Change length",
            "files": [file_pointer(design)],
            "context_tier": "very_high",
        },
    )

    assert response.status_code == 202
    assert captured["max_chars"] == expected_max_chars


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


def test_api_estimate_uses_exact_structured_conversation_prompt_bytes(
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
                result_payload={
                    "outcome": "changed",
                    "message": "Première modification",
                    "files": [{"filename": "design.py", "changed": True}],
                },
                created_at=now - timedelta(minutes=2),
            ),
            LlmEditJob(
                tenant_id=seeded_tenant.tenant_id,
                project_id=seeded_tenant.project_id,
                requested_by=seeded_tenant.user_id,
                status="failed",
                request_payload={"prompt": "Deuxième demande"},
                error_code="invalid_request",
                user_message="Deuxième demande refusée",
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
    assert commands[0].schema_version == 2
    assert commands[0].prior_prompts == []
    assert [turn.user_request for turn in commands[0].conversation.recent_turns] == [
        "Première demande",
        "Deuxième demande",
    ]
    assert [file.filename for file in commands[0].files] == [
        "design.py",
        "dimensions.py",
    ]
    assert commands[0].active_file_id == support.id
    assert captured["user_prompt"].encode("utf-8") == render_pi_agent_user_prompt(
        conversation_prompt=render_conversation_context(
            commands[0].conversation,
            commands[0].prompt,
        ),
        editable_filenames=[file.filename for file in commands[0].files],
        active_filename=support.filename,
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
    assert db_session.scalar(select(func.count()).select_from(LlmEditJob)) == 0


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


def test_history_model_prefers_result_then_dispatch_and_keeps_legacy_fallback(
    authenticated_intus_client,
    db_session,
    seeded_tenant,
):
    common = {
        "tenant_id": seeded_tenant.tenant_id,
        "project_id": seeded_tenant.project_id,
        "requested_by": seeded_tenant.user_id,
        "status": "failed",
    }
    result_job = LlmEditJob(
        **common,
        request_payload={
            "prompt": "Result provenance",
            "files": [],
            "dispatched_model": "dispatch-model",
            "model_id": "legacy-model",
        },
        result_payload={"model": "result-model"},
    )
    dispatched_job = LlmEditJob(
        **common,
        request_payload={
            "prompt": "Dispatch provenance",
            "files": [],
            "dispatched_model": "dispatch-model",
            "model_id": "legacy-model",
        },
    )
    legacy_job = LlmEditJob(
        **common,
        request_payload={
            "prompt": "Legacy provenance",
            "files": [],
            "model_id": "legacy-model",
        },
    )
    db_session.add_all([result_job, dispatched_job, legacy_job])
    db_session.commit()

    response = authenticated_intus_client.get(
        "/projects/default_purlin/files/llm-edit/jobs"
    )

    assert response.status_code == 200
    models_by_job_id = {
        message["job_id"]: message["model"]
        for message in response.json()["messages"]
    }
    assert models_by_job_id[str(result_job.id)] == "result-model"
    assert models_by_job_id[str(dispatched_job.id)] == "dispatch-model"
    assert models_by_job_id[str(legacy_job.id)] == "legacy-model"


def test_job_status_preserves_public_contract_and_returns_validated_progress(
    authenticated_intus_client, db_session, seeded_tenant
):
    running_progress = progress_snapshot()
    terminal_progress = progress_snapshot("Finished inspecting the design")
    running_job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        request_payload={"prompt": "working", "files": []},
        progress_payload=running_progress,
    )
    terminal_job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        request_payload={"prompt": "done", "files": []},
        result_payload={"outcome": "no_changes"},
        progress_payload=terminal_progress,
    )
    db_session.add_all([running_job, terminal_job])
    db_session.commit()

    for job, expected_progress in (
        (running_job, running_progress),
        (terminal_job, terminal_progress),
    ):
        response = authenticated_intus_client.get(
            f"/projects/default_purlin/files/llm-edit/jobs/{job.id}"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == job.status
        assert payload["progress"] == expected_progress
        assert {
            "job_id",
            "status",
            "result",
            "error",
            "error_code",
            "user_message",
            "retryable",
            "created_at",
            "finished_at",
        } <= payload.keys()


def test_terminal_history_retains_progress_across_reload(
    authenticated_intus_client, db_session, seeded_tenant
):
    snapshot = progress_snapshot()
    job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        request_payload={"prompt": "Inspect", "files": []},
        result_payload={"outcome": "no_changes"},
        progress_payload=snapshot,
    )
    db_session.add(job)
    db_session.commit()

    route = "/projects/default_purlin/files/llm-edit/jobs"
    first = authenticated_intus_client.get(route)
    second = authenticated_intus_client.get(route)

    assert first.status_code == 200
    assert second.status_code == 200
    first_entry = next(
        message
        for message in first.json()["messages"]
        if message["job_id"] == str(job.id)
    )
    second_entry = next(
        message
        for message in second.json()["messages"]
        if message["job_id"] == str(job.id)
    )
    assert first_entry["progress"] == snapshot
    assert second_entry["progress"] == snapshot


def test_terminal_history_compacts_progress_without_changing_status_snapshot(
    authenticated_intus_client, db_session, seeded_tenant
):
    now = datetime.now(timezone.utc)
    events = [
        PiAgentProgressEvent(
            sequence=sequence,
            kind="reasoning_delta",
            text=f"Reasoning {sequence}: " + ("x" * 400),
            occurred_at=now,
        )
        for sequence in range(1, 13)
    ]
    snapshot = PiAgentProgressSnapshot(
        schema_version=1,
        execution_id=uuid4(),
        execution_started_at=now,
        last_batch_sequence=3,
        last_sequence=12,
        events=events,
    )
    job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="succeeded",
        request_payload={"prompt": "Inspect", "files": []},
        result_payload={"outcome": "no_changes"},
        progress_payload=snapshot.model_dump(mode="json"),
    )
    db_session.add(job)
    db_session.commit()

    status = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{job.id}"
    )
    history = authenticated_intus_client.get(
        "/projects/default_purlin/files/llm-edit/jobs"
    )

    assert status.status_code == 200
    assert status.json()["progress"] == snapshot.model_dump(mode="json")
    history_entry = next(
        message
        for message in history.json()["messages"]
        if message["job_id"] == str(job.id)
    )
    preview = PiAgentProgressSnapshot.model_validate(history_entry["progress"])
    assert [event.sequence for event in preview.events] == list(range(5, 13))
    assert preview.truncated_before_sequence == 4
    assert preview.execution_id == snapshot.execution_id
    assert preview.execution_started_at == snapshot.execution_started_at
    assert preview.last_batch_sequence == snapshot.last_batch_sequence
    assert preview.last_sequence == snapshot.last_sequence
    assert all(
        event.text is not None and len(event.text) <= 240
        for event in preview.events
        if event.kind == "reasoning_delta"
    )
    assert preview.events[-1].text == events[-1].text[:240]


def test_legacy_and_malformed_progress_are_not_exposed(
    authenticated_intus_client, db_session, seeded_tenant
):
    legacy_job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        request_payload={"prompt": "Legacy", "files": []},
        progress_payload={},
    )
    malformed_job = LlmEditJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="failed",
        request_payload={"prompt": "Malformed", "files": []},
        progress_payload={
            "schema_version": 99,
            "unsafe_raw_tool_output": "MALFORMED_PROGRESS_SENTINEL",
        },
    )
    db_session.add_all([legacy_job, malformed_job])
    db_session.commit()

    legacy = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{legacy_job.id}"
    )
    malformed = authenticated_intus_client.get(
        f"/projects/default_purlin/files/llm-edit/jobs/{malformed_job.id}"
    )
    history = authenticated_intus_client.get(
        "/projects/default_purlin/files/llm-edit/jobs"
    )

    assert legacy.status_code == 200
    assert legacy.json()["progress"] is None
    assert malformed.status_code == 200
    assert malformed.json()["progress"] is None
    assert "MALFORMED_PROGRESS_SENTINEL" not in malformed.text
    malformed_history = next(
        message
        for message in history.json()["messages"]
        if message["job_id"] == str(malformed_job.id)
    )
    assert malformed_history["progress"] is None
    assert "MALFORMED_PROGRESS_SENTINEL" not in history.text
