from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

from core.pi_agent_messages import (
    PiAgentChangedFile,
    PiAgentCommand,
    PiAgentConversationContext,
    PiAgentConversationTurn,
    PiAgentResult,
    PiAgentSourceFile,
    PiAgentUsage,
    assert_pi_agent_command_size,
    assert_pi_agent_result_size,
    pi_agent_command_message_id,
    pi_agent_result_message_id,
)


NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def source_file(*, filename="design.py", content="x = 1", file_id=None):
    return PiAgentSourceFile(
        id=file_id or uuid4(),
        filename=filename,
        content=content,
        updated_at=NOW,
        sha256=sha256(content.encode("utf-8")).hexdigest(),
    )


def command(**overrides):
    file = overrides.pop("file", source_file())
    values = {
        "schema_version": 1,
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": uuid4(),
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "thinking": "high",
        "prompt": "Make the requested change",
        "prior_prompts": [],
        "active_file_id": file.id,
        "files": [file],
        "created_at": NOW,
    }
    values.update(overrides)
    return PiAgentCommand(**values)


def test_command_round_trips_and_rejects_extra_fields():
    message = command()
    assert PiAgentCommand.model_validate_json(message.model_dump_json()) == message
    with pytest.raises(ValidationError):
        command(unexpected=True)


@pytest.mark.parametrize(
    (
        "schema_version",
        "prior_prompts",
        "conversation",
        "system_prompt_sha256",
        "valid",
    ),
    [
        (1, ["Earlier request"], None, None, True),
        (2, [], PiAgentConversationContext(), "a" * 64, True),
        (1, [], PiAgentConversationContext(), "a" * 64, False),
        (2, [], PiAgentConversationContext(), None, False),
        (2, [], None, "a" * 64, False),
        (2, ["Earlier request"], PiAgentConversationContext(), "a" * 64, False),
    ],
)
def test_command_version_context_field_matrix(
    schema_version,
    prior_prompts,
    conversation,
    system_prompt_sha256,
    valid,
):
    values = {
        "schema_version": schema_version,
        "prior_prompts": prior_prompts,
        "conversation": conversation,
        "system_prompt_sha256": system_prompt_sha256,
    }
    if valid:
        assert command(**values).schema_version == schema_version
    else:
        with pytest.raises(ValidationError):
            command(**values)


def test_conversation_turn_enforces_state_and_filename_bounds():
    assert PiAgentConversationTurn(
        user_request="Update the design",
        status="succeeded",
        outcome="changed",
        changed_files=["parts/design.py"],
    )
    assert PiAgentConversationTurn(
        user_request="Update the design",
        status="failed",
        error_code="provider_error",
    )
    for values in (
        {"status": "succeeded"},
        {"status": "failed", "outcome": "no_changes", "error_code": "provider_error"},
        {"status": "failed", "error_code": ""},
        {"status": "succeeded", "outcome": "no_changes", "error_code": "stale_error"},
        {"status": "succeeded", "outcome": "no_changes", "changed_files": ["design.py"]},
        {"status": "succeeded", "outcome": "changed", "changed_files": ["../design.py"]},
        {"status": "succeeded", "outcome": "changed", "changed_files": ["x" * 513]},
    ):
        with pytest.raises(ValidationError):
            PiAgentConversationTurn(user_request="Update the design", **values)


def test_command_rejects_duplicate_ids_normalized_names_and_bad_hashes():
    first = source_file(filename="parts/design.py")
    with pytest.raises(ValidationError):
        command(files=[first, source_file(filename="other.py", file_id=first.id)])
    with pytest.raises(ValidationError):
        command(files=[first, source_file(filename="parts//design.py")])
    with pytest.raises(ValidationError):
        source_file(content="x = 1").model_copy(update={"sha256": "0" * 64}, deep=True).__class__.model_validate(
            {**source_file(content="x = 1").model_dump(), "sha256": "0" * 64}
        )


def test_result_cross_field_states():
    identity = {
        "schema_version": 1,
        "execution_id": uuid4(),
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": uuid4(),
    }
    usage = PiAgentUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    changed_file = PiAgentChangedFile(id=uuid4(), filename="design.py", content="x = 2", sha256=sha256(b"x = 2").hexdigest())
    common = {
        **identity,
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "usage": usage,
        "worker_started_at": NOW,
        "worker_finished_at": NOW + timedelta(seconds=1),
    }

    assert PiAgentResult(status="succeeded", outcome="changed", changed_files=[changed_file], assistant_summary="Updated", **common)
    assert PiAgentResult(status="succeeded", outcome="no_changes", assistant_summary="No update needed", **common)
    assert PiAgentResult(status="failed", outcome=None, error_code="provider_auth", error_message="Login required", retryable=False, **common)
    with pytest.raises(ValidationError):
        PiAgentResult(status="succeeded", outcome="changed", assistant_summary="Updated", **common)
    with pytest.raises(ValidationError):
        PiAgentResult(status="succeeded", outcome="no_changes", retryable=True, **common)


def test_result_rejects_duplicate_changed_file_ids_and_normalized_filenames():
    common = {
        "schema_version": 1,
        "execution_id": uuid4(),
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": uuid4(),
        "status": "succeeded",
        "outcome": "changed",
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "worker_started_at": NOW,
        "worker_finished_at": NOW + timedelta(seconds=1),
    }
    first = PiAgentChangedFile(
        id=uuid4(), filename="parts/design.py", content="x = 2", sha256=sha256(b"x = 2").hexdigest()
    )
    duplicate_id = PiAgentChangedFile(
        id=first.id, filename="other.py", content="y = 2", sha256=sha256(b"y = 2").hexdigest()
    )
    duplicate_name = PiAgentChangedFile(
        id=uuid4(), filename="parts//design.py", content="z = 2", sha256=sha256(b"z = 2").hexdigest()
    )

    with pytest.raises(ValidationError, match="changed file IDs must be unique"):
        PiAgentResult(changed_files=[first, duplicate_id], **common)
    with pytest.raises(ValidationError, match="normalized changed filenames must be unique"):
        PiAgentResult(changed_files=[first, duplicate_name], **common)


def test_usage_counters_are_independent_bounded_nonnegative_reports():
    assert PiAgentUsage(
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=20,
        cache_write_tokens=30,
        total_tokens=100,
    )
    for values in (
        {"cache_read_tokens": -1},
        {"cache_write_tokens": -1},
        {"cache_read_tokens": 2**63},
        {"cache_write_tokens": 2**63},
        {"total_tokens": -1},
        {"total_tokens": 2**63},
    ):
        with pytest.raises(ValidationError):
            PiAgentUsage(**values)


def test_message_ids_are_deterministic_and_result_discriminator_is_stable():
    message = command()
    assert pi_agent_command_message_id(message) == f"pi-request:{message.job_id}"
    assert pi_agent_command_message_id(message) == pi_agent_command_message_id(message)

    common = {
        "schema_version": 1,
        "execution_id": uuid4(),
        "job_id": message.job_id,
        "tenant_id": message.tenant_id,
        "project_id": message.project_id,
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "worker_started_at": NOW,
        "worker_finished_at": NOW + timedelta(seconds=1),
    }
    result = PiAgentResult(status="succeeded", outcome="no_changes", **common)
    assert pi_agent_result_message_id(result) == (
        f"pi-result:{message.job_id}:{result.execution_id}"
    )
    failed = PiAgentResult(
        status="failed", error_code="provider_auth", error_message="Login required", **common
    )
    assert pi_agent_result_message_id(failed) == (
        f"pi-result:{message.job_id}:{failed.execution_id}"
    )
    same_execution_failure = PiAgentResult(
        status="failed",
        error_code="provider_rate_limit",
        error_message="Try again",
        retryable=True,
        **common,
    )
    assert pi_agent_result_message_id(same_execution_failure) == pi_agent_result_message_id(
        failed
    )
    other_execution = same_execution_failure.model_copy(
        update={"execution_id": uuid4()}
    )
    assert pi_agent_result_message_id(other_execution) != pi_agent_result_message_id(failed)


def test_message_byte_size_enforcement_uses_serialized_utf8_bytes():
    message = command(prompt="é")
    exact_size = len(message.model_dump_json().encode("utf-8"))
    assert_pi_agent_command_size(message, exact_size)
    with pytest.raises(ValueError, match="above .* byte limit"):
        assert_pi_agent_command_size(message, exact_size - 1)

    result = PiAgentResult(
        schema_version=1,
        execution_id=uuid4(),
        job_id=message.job_id,
        tenant_id=message.tenant_id,
        project_id=message.project_id,
        status="succeeded",
        outcome="no_changes",
        provider="openai-codex",
        model="gpt-5.5",
        worker_started_at=NOW,
        worker_finished_at=NOW + timedelta(seconds=1),
    )
    result_size = len(result.model_dump_json().encode("utf-8"))
    assert_pi_agent_result_size(result, result_size)
    with pytest.raises(ValueError, match="above .* byte limit"):
        assert_pi_agent_result_size(result, result_size - 1)
