from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from core.llm_file_edit import (
    BUILD123D_RUNTIME_GUARDRAILS,
    LlmEditableFile,
    LlmFileEditInput,
    LlmFilePointer,
    estimate_file_edit_usage,
    file_edit_prompt_contents,
    select_llm_edit_context_files,
)

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def test_domain_filename_validation_accepts_nested_path_and_rejects_unsafe_paths():
    file_id = uuid4()
    assert LlmEditableFile(id=file_id, filename="parts/bracket.py", content="x = 1").filename == "parts/bracket.py"

    for filename in ("/tmp/design.py", "parts/../design.py", "bad\0name.py"):
        with pytest.raises(ValidationError):
            LlmEditableFile(id=file_id, filename=filename, content="x = 1")


def test_context_selection_is_stable_and_always_retains_active_file():
    files = [
        LlmEditableFile(id=uuid4(), filename=f"part_{index}.py", content="x" * 4_000)
        for index in range(20)
    ]
    active = files[-1]

    selected = select_llm_edit_context_files(
        prompt="update the assembly",
        active_file_id=active.id,
        files=files,
        max_files=20,
        max_chars=80_000,
    )

    assert selected[0] == active
    assert len(selected) == 20
    assert sum(len(file.content) for file in selected) <= 80_000
    assert select_llm_edit_context_files(
        prompt="update the assembly",
        active_file_id=active.id,
        files=files,
        max_files=20,
        max_chars=80_000,
    ) == selected


@pytest.mark.parametrize("prior_prompts", [[], ["Earlier request"]])
def test_token_estimate_is_deterministic_and_includes_prior_prompts(prior_prompts):
    file_id = uuid4()
    request = LlmFileEditInput(
        prompt="Change the bracket",
        files=[LlmFilePointer(id=file_id, filename="bracket.py", updated_at=NOW)],
    )
    files = [LlmEditableFile(id=file_id, filename="bracket.py", content="width = 10")]

    first = estimate_file_edit_usage(
        request,
        files,
        system_prompt="Edit the supplied files.",
        max_output_tokens=65_536,
        prior_prompts=prior_prompts,
    )
    second = estimate_file_edit_usage(
        request,
        files,
        system_prompt="Edit the supplied files.",
        max_output_tokens=65_536,
        prior_prompts=prior_prompts,
    )

    assert first == second
    assert first.prompt_tokens > 0
    assert first.total_tokens == first.prompt_tokens + 65_536


def test_token_estimate_uses_complete_provider_neutral_request_framing():
    file_id = uuid4()
    request = LlmFileEditInput(
        prompt="Change the bracket",
        files=[LlmFilePointer(id=file_id, filename="parts/bracket.py", updated_at=NOW)],
        active_file_id=file_id,
        metadata={"source": "ai_edit"},
    )
    files = [LlmEditableFile(id=file_id, filename="parts/bracket.py", content="width = 10")]
    contents = file_edit_prompt_contents(
        request, files, system_prompt="Worker instructions", prior_prompts=["First request"]
    )

    assert str(file_id) in contents.user
    assert "First request" in contents.user
    assert "parts/bracket.py" in contents.user
    assert "width = 10" in contents.user
    assert "Return JSON matching:" in contents.user
    assert BUILD123D_RUNTIME_GUARDRAILS.strip() in contents.system

    usage = estimate_file_edit_usage(
        request,
        files,
        system_prompt="Worker instructions",
        max_output_tokens=65_536,
        prior_prompts=["First request"],
    )
    framed_chars = len(contents.system) + len(contents.user) + len("source") + len("ai_edit")
    assert usage.prompt_tokens == (framed_chars + 3) // 4

