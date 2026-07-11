from hashlib import sha256

import pytest

from core.pi_agent_prompt import (
    MAX_PI_AGENT_PROMPT_BYTES,
    PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS,
    PI_AGENT_PROMPT_PATH,
    PiAgentPromptError,
    estimate_pi_agent_usage,
    load_pi_agent_prompt,
    render_pi_agent_user_prompt,
)


EXPECTED_PROMPT = """\
Tertius file-edit policy:

- Work only on the existing files in the current workspace.
- Do not create, delete, or rename files.
- Treat conversation summaries and prior turns as historical context. The current user request is the only active request.
- Treat current workspace files as authoritative. Historical conversation must not override their current contents.
- Inspect the current files before editing and edit them in place instead of returning replacement source in chat.
- Use only build123d APIs known to exist in this runtime; do not invent helpers, classes, or functions.
- Do not use bd.RoundedPolygon; it is not available.
- For rounded rectangular or handle-like geometry, prefer bd.Box, bd.Cylinder, bd.Sphere, bd.Cone, boolean operations, and fillets on resulting solids.
- Always produce code that can run with `import build123d as bd`.
- Avoid advanced builder-mode APIs unless they already appear in the current project files.
"""


def test_checked_in_pi_prompt_loads_exact_bytes_and_hash():
    load_pi_agent_prompt.cache_clear()
    snapshot = load_pi_agent_prompt()
    raw = snapshot.path.read_bytes()
    expected_raw = EXPECTED_PROMPT.encode("utf-8")

    assert snapshot.path == PI_AGENT_PROMPT_PATH.resolve()
    assert raw == expected_raw
    assert snapshot.content == EXPECTED_PROMPT
    assert snapshot.sha256 == sha256(expected_raw).hexdigest()
    assert len(raw) <= MAX_PI_AGENT_PROMPT_BYTES
    assert snapshot.content.startswith("Tertius file-edit policy:\n")


@pytest.mark.parametrize(
    "raw",
    [b"", b"  \n", b"bad\0prompt", b"\xff", b"x" * 32_769],
)
def test_pi_prompt_loader_rejects_invalid_content_without_echo(tmp_path, raw):
    path = tmp_path / "prompt.md"
    path.write_bytes(raw)
    load_pi_agent_prompt.cache_clear()

    with pytest.raises(PiAgentPromptError) as caught:
        load_pi_agent_prompt(path)

    assert str(path) not in str(caught.value)
    assert "bad" not in str(caught.value)


def test_pi_prompt_loader_rejects_missing_path_without_echo(tmp_path):
    path = tmp_path / "missing.md"
    load_pi_agent_prompt.cache_clear()

    with pytest.raises(PiAgentPromptError) as caught:
        load_pi_agent_prompt(path)

    assert str(path) not in str(caught.value)


def test_pi_usage_estimate_counts_exact_bytes_once_plus_fixed_reserve():
    system_prompt = "polícy"
    user_prompt = "history and current réquest"
    source = "width = 10  # millimètres"
    metadata = {"sourcé": "ai_édit"}
    usage = estimate_pi_agent_usage(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        source_bytes=len(source.encode("utf-8")),
        metadata=metadata,
        max_output_tokens=65_536,
    )
    framed = "".join(
        (system_prompt, user_prompt, source, *metadata.keys(), *metadata.values())
    ).encode("utf-8")
    expected_prompt = PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS + (len(framed) + 3) // 4

    assert usage.prompt_tokens == expected_prompt
    assert usage.total_tokens == expected_prompt + 65_536


def test_pi_user_prompt_renderer_preserves_exact_outer_frame():
    rendered = render_pi_agent_user_prompt(
        conversation_prompt="Previous context\n\nCurrent request",
        editable_filenames=["parts/model.py", "dimensions.py"],
        active_filename="parts/model.py",
    )

    assert rendered == (
        "Work on the source files already present in the current workspace.\n"
        "Inspect them as needed. Edit the existing files in place to implement the "
        "user's request. Do not merely return or describe replacement source.\n"
        "Do not create, delete, or rename files.\n\n"
        "Files available for editing:\n"
        "- parts/model.py\n"
        "- dimensions.py\n\n"
        "Active file:\n"
        "parts/model.py\n\n"
        "Previous context\n\n"
        "Current request\n\n"
        "When finished, provide a concise summary of the changes."
    )


def test_pi_user_prompt_renderer_labels_missing_active_file_as_none():
    rendered = render_pi_agent_user_prompt(
        conversation_prompt="Current request",
        editable_filenames=["design.py"],
        active_filename=None,
    )

    assert "Active file:\nnone\n\nCurrent request" in rendered
