import json
from types import SimpleNamespace

import pytest

from core.pi_agent_conversation import (
    MAX_RENDERED_CONTEXT_TOKENS,
    advance_conversation_context,
    conversation_turn_from_job,
    estimated_context_tokens,
    next_conversation_context,
    render_conversation_context,
    render_legacy_prior_prompts,
)
from core.pi_agent_messages import PiAgentConversationContext, PiAgentConversationTurn


def successful_turn(index: int, *, request_size: int = 10):
    return PiAgentConversationTurn(
        user_request=f"request-{index}-" + "u" * request_size,
        status="succeeded",
        outcome="changed",
        assistant_summary=f"changed-{index}",
        changed_files=["design.py"],
    )


def test_context_rolls_oldest_turns_and_obeys_token_budget():
    context = PiAgentConversationContext()
    for index in range(12):
        context = advance_conversation_context(
            context,
            successful_turn(index, request_size=11_500),
        )

    assert len(context.recent_turns) <= 5
    assert context.recent_turns[-1].user_request.startswith("request-11-")
    assert len(context.rolling_summary) <= 8_000
    assert estimated_context_tokens(context) <= MAX_RENDERED_CONTEXT_TOKENS


def test_max_length_multibyte_request_compacts_within_byte_estimated_budget():
    turn = PiAgentConversationTurn(
        user_request="😀" * 12_000,
        status="succeeded",
        outcome="no_changes",
        assistant_summary="No files changed.",
    )

    context = advance_conversation_context(PiAgentConversationContext(), turn)

    assert context.recent_turns == []
    assert context.rolling_summary
    assert len(context.rolling_summary) <= 8_000
    assert estimated_context_tokens(context) <= MAX_RENDERED_CONTEXT_TOKENS


def test_failed_job_uses_user_message_and_excludes_internal_payload():
    job = SimpleNamespace(
        status="failed",
        request_payload={
            "prompt": "try the change",
            "dispatched_conversation": {"rolling_summary": "secret recursion"},
        },
        result_payload={
            "files": [{"filename": "design.py", "content": "SOURCE_SENTINEL"}],
            "snapshot": {"id": "SNAPSHOT_SENTINEL"},
        },
        user_message="Provider was unavailable",
        error="RAW_INTERNAL_SENTINEL",
        error_code="provider_error",
    )

    turn = conversation_turn_from_job(job)

    assert turn is not None
    assert turn.status == "failed"
    assert turn.user_request == "try the change"
    assert turn.error_code == "provider_error"
    assert turn.assistant_summary == "Provider was unavailable"
    serialized = turn.model_dump_json()
    assert "SOURCE_SENTINEL" not in serialized
    assert "SNAPSHOT_SENTINEL" not in serialized
    assert "RAW_INTERNAL_SENTINEL" not in serialized
    assert "secret recursion" not in serialized


def test_successful_job_uses_bounded_safe_result_fields_only():
    job = SimpleNamespace(
        status="succeeded",
        request_payload={"prompt": "change the files"},
        result_payload={
            "outcome": "changed",
            "message": "  Updated the requested files.  ",
            "files": [
                {"filename": "parts/design.py", "content": "SOURCE_SENTINEL"},
                {"filename": "../unsafe.py"},
                {"filename": "x" * 513},
                "not-a-file",
            ],
            "snapshot": {"id": "SNAPSHOT_SENTINEL"},
        },
        user_message=None,
        error="RAW_INTERNAL_SENTINEL",
        error_code=None,
    )

    turn = conversation_turn_from_job(job)

    assert turn is not None
    assert turn.assistant_summary == "Updated the requested files."
    assert turn.changed_files == ["parts/design.py"]
    serialized = turn.model_dump_json()
    assert "SOURCE_SENTINEL" not in serialized
    assert "SNAPSHOT_SENTINEL" not in serialized
    assert "RAW_INTERNAL_SENTINEL" not in serialized


def test_successful_job_with_unhashable_outcome_is_skipped():
    job = SimpleNamespace(
        status="succeeded",
        request_payload={"prompt": "change the files"},
        result_payload={"outcome": ["changed"], "message": "", "files": []},
        user_message=None,
        error=None,
        error_code=None,
    )

    assert conversation_turn_from_job(job) is None


def test_latest_persisted_context_advances_only_the_latest_job():
    persisted = PiAgentConversationContext(recent_turns=[successful_turn(1)])
    latest = SimpleNamespace(
        status="succeeded",
        request_payload={
            "prompt": "latest request",
            "dispatched_conversation": persisted.model_dump(mode="json"),
        },
        result_payload={
            "outcome": "no_changes",
            "message": "No update needed",
            "files": [],
        },
        user_message=None,
        error=None,
        error_code=None,
    )

    context = next_conversation_context([latest])

    assert [turn.user_request for turn in context.recent_turns] == [
        persisted.recent_turns[0].user_request,
        "latest request",
    ]


def test_invalid_persisted_context_bootstraps_valid_jobs_oldest_first():
    jobs = [
        SimpleNamespace(
            status="succeeded",
            request_payload={"prompt": "first request"},
            result_payload={"outcome": "no_changes", "message": "", "files": []},
            user_message=None,
            error=None,
            error_code=None,
        ),
        SimpleNamespace(
            status="running",
            request_payload={"prompt": "skip running"},
            result_payload=None,
            user_message=None,
            error=None,
            error_code=None,
        ),
        SimpleNamespace(
            status="failed",
            request_payload={
                "prompt": "last request",
                "dispatched_conversation": {"recent_turns": "malformed"},
            },
            result_payload=None,
            user_message=None,
            error="do not include",
            error_code=None,
        ),
    ]

    context = next_conversation_context(jobs)

    assert [turn.user_request for turn in context.recent_turns] == [
        "first request",
        "last request",
    ]
    assert context.recent_turns[0].assistant_summary == "No files changed."
    assert context.recent_turns[1].assistant_summary == "Previous request failed."
    assert context.recent_turns[1].error_code == "unknown_failure"


def test_malformed_terminal_rows_do_not_abort_valid_history_reconstruction():
    def succeeded(prompt):
        return SimpleNamespace(
            status="succeeded",
            request_payload={"prompt": prompt},
            result_payload={"outcome": "no_changes", "message": "", "files": []},
            user_message=None,
            error=None,
            error_code=None,
        )

    jobs = [
        succeeded("valid first"),
        succeeded("x" * 12_001),
        succeeded("valid second"),
        SimpleNamespace(
            status="failed",
            request_payload={"prompt": "invalid error code"},
            result_payload=None,
            user_message="Failed safely",
            error="do not include",
            error_code="e" * 101,
        ),
    ]

    context = next_conversation_context(jobs)

    assert [turn.user_request for turn in context.recent_turns] == [
        "valid first",
        "valid second",
    ]


def test_history_reconstruction_propagates_job_property_type_errors():
    class BrokenJob:
        request_payload = {"prompt": "valid request"}

        @property
        def status(self):
            raise TypeError("broken status property")

    with pytest.raises(TypeError, match="broken status property"):
        next_conversation_context([BrokenJob()])


def test_renderer_keeps_current_request_outside_historical_json():
    context = PiAgentConversationContext(recent_turns=[successful_turn(1)])

    rendered = render_conversation_context(context, "CURRENT_REQUEST_SENTINEL")

    history, current = rendered.split("Current user request:\n", maxsplit=1)
    assert "<conversation_context>\n" in history
    assert "request-1" in history
    assert "CURRENT_REQUEST_SENTINEL" not in history
    assert current == "CURRENT_REQUEST_SENTINEL"


def test_legacy_renderer_labels_unknown_outcomes_without_fabricating_turns():
    prior_prompts = ["LEGACY_REQUEST_SENTINEL", "Première demande"]

    rendered = render_legacy_prior_prompts(
        prior_prompts,
        "CURRENT_REQUEST_SENTINEL",
    )

    history, current = rendered.split("Current user request:\n", maxsplit=1)
    legacy_json = history.split("<legacy_user_requests>\n", maxsplit=1)[1].split(
        "\n</legacy_user_requests>", maxsplit=1
    )[0]
    assert json.loads(legacy_json) == prior_prompts
    assert "outcome" not in legacy_json
    assert "succeeded" not in legacy_json
    assert "CURRENT_REQUEST_SENTINEL" not in history
    assert current == "CURRENT_REQUEST_SENTINEL"
