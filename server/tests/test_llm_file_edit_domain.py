from uuid import uuid4

import pytest
from pydantic import ValidationError

from core.llm_file_edit import (
    LlmFileEditInput,
    LlmEditableFile,
    llm_edit_context_chars_for_tier,
    select_llm_edit_context_files,
)


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


def test_context_tiers_have_bounded_character_budgets_and_default_to_low():
    assert [llm_edit_context_chars_for_tier(tier) for tier in ("low", "medium", "high", "very_high")] == [80_000, 160_000, 250_000, 350_000]
    request = LlmFileEditInput(
        prompt="make it taller",
        files=[{"id": uuid4(), "filename": "design.py", "updated_at": "2026-07-18T00:00:00Z"}],
    )
    assert request.context_tier == "low"
