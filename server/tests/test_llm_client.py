import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from core.auth_types import AuthContext
from core.config import Settings
from core.llm_client import (
    BuildScriptGenerationInput,
    LlmBillingError,
    LlmEditableFile,
    LlmFileEditTruncatedError,
    LlmFileEditInput,
    LlmFilePointer,
    LlmInvalidFileEditError,
    LlmNotConfiguredError,
    build_file_edit_messages,
    estimate_file_edit_tokens,
    generate_build_script,
    generate_file_edits,
    parse_llm_file_edit_response,
    select_llm_edit_context_files,
)


FILE_UPDATED_AT = datetime(2026, 6, 17, tzinfo=timezone.utc)


def llm_file_pointer(file_id: UUID | None = None, filename: str = "design.py") -> LlmFilePointer:
    return LlmFilePointer(id=file_id or uuid4(), filename=filename, updated_at=FILE_UPDATED_AT)


class FakeChatCompletions:
    def __init__(self, content=None, prompt_tokens=11, completion_tokens=22, finish_reason="stop"):
        self.calls = []
        self._content = content
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._finish_reason = finish_reason

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = (
            self._content
            if self._content is not None
            else "```python\nimport build123d as bd\npart = bd.Box(1, 2, 3)\n```"
        )
        return SimpleNamespace(
            id="chatcmpl-test",
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=self._finish_reason)],
            usage=SimpleNamespace(
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                total_tokens=self._prompt_tokens + self._completion_tokens,
            ),
        )


class FakeOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


class FakePublisher:
    def __init__(self):
        self.published = []

    async def publish_json(self, subject, message, message_id=None):
        self.published.append((subject, message, message_id))


@pytest.mark.asyncio
async def test_generate_build_script_calls_openai_and_publishes_billing_event():
    client = FakeOpenAIClient()
    publisher = FakePublisher()
    settings = Settings(llm_api_key="secret")
    auth = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="kc-test",
        email="test@example.com",
    )
    project_id = uuid4()

    result = await generate_build_script(
        BuildScriptGenerationInput(
            prompt="make a bracket",
            active_file="design.py",
            current_code="import build123d as bd\n",
            metadata={"source": "compiler_tab"},
        ),
        settings=settings,
        auth=auth,
        project_id=project_id,
        openai_client=client,
        billing_publisher=publisher,
    )

    assert result.script == "import build123d as bd\npart = bd.Box(1, 2, 3)"
    assert result.usage.prompt_tokens == 11
    assert client.chat.completions.calls[0]["model"] == "deepseek-v4-flash"
    assert client.chat.completions.calls[0]["max_tokens"] == 2048
    assert publisher.published[0][0] == "tertius.billing.usage.llm.tokens"
    billing_event = publisher.published[0][1]
    assert billing_event.prompt == "make a bracket"
    assert billing_event.tenant_id == auth.tenant_id
    assert billing_event.user_id == auth.user_id
    assert billing_event.project_id == project_id
    assert billing_event.prompt_tokens == 11
    assert billing_event.completion_tokens == 22
    assert billing_event.total_tokens == 33
    assert billing_event.metadata == {"source": "compiler_tab"}
    assert publisher.published[0][2].startswith("billing-usage:")


@pytest.mark.asyncio
async def test_generate_build_script_raises_when_billing_publish_fails():
    class FailingPublisher(FakePublisher):
        async def publish_json(self, subject, message, message_id=None):
            raise RuntimeError("nats unavailable")

    with pytest.raises(LlmBillingError):
        await generate_build_script(
            BuildScriptGenerationInput(prompt="make a bracket"),
            settings=Settings(llm_api_key="secret"),
            auth=AuthContext(user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc-test", email=None),
            project_id=uuid4(),
            openai_client=FakeOpenAIClient(),
            billing_publisher=FailingPublisher(),
        )


@pytest.mark.asyncio
async def test_generate_build_script_requires_configured_key():
    with pytest.raises(LlmNotConfiguredError):
        await generate_build_script(
            BuildScriptGenerationInput(prompt="make a bracket"),
            settings=Settings(llm_api_key=""),
            auth=AuthContext(user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc-test", email=None),
            project_id=uuid4(),
            openai_client=FakeOpenAIClient(),
            billing_publisher=FakePublisher(),
        )


# ---------------------------------------------------------------------------
# LLM File Edit Provider Parser
# ---------------------------------------------------------------------------


def _allowed_file_ids(*ids: UUID) -> set[UUID]:
    return set(ids)


def test_parse_llm_file_edit_response_accepts_valid_json():
    file_id = uuid4()
    payload = json.dumps(
        {
            "files": [
                {
                    "file_id": str(file_id),
                    "content": "import build123d as bd\n",
                    "summary": "initial scaffold",
                }
            ]
        }
    )

    result = parse_llm_file_edit_response(payload, _allowed_file_ids(file_id))

    assert result.outcome == "changed"
    assert result.message == ""
    assert len(result.files) == 1
    assert result.files[0].file_id == file_id
    assert result.files[0].content == "import build123d as bd\n"
    assert result.files[0].summary == "initial scaffold"


def test_parse_llm_file_edit_response_strips_markdown_code_fence():
    file_id = uuid4()
    inner = json.dumps(
        {
            "files": [
                {
                    "file_id": str(file_id),
                    "content": "x = 1\n",
                    "summary": "x",
                }
            ]
        }
    )
    fenced = f"```json\n{inner}\n```"

    result = parse_llm_file_edit_response(fenced, _allowed_file_ids(file_id))

    assert len(result.files) == 1
    assert result.files[0].file_id == file_id
    assert result.files[0].content == "x = 1\n"


def test_parse_llm_file_edit_response_rejects_unknown_file_id():
    allowed = uuid4()
    unknown = uuid4()
    payload = json.dumps(
        {
            "files": [
                {
                    "file_id": str(unknown),
                    "content": "x = 1\n",
                    "summary": "x",
                }
            ]
        }
    )

    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response(payload, _allowed_file_ids(allowed))


def test_parse_llm_file_edit_response_rejects_duplicate_file_id():
    file_id = uuid4()
    payload = json.dumps(
        {
            "files": [
                {"file_id": str(file_id), "content": "x = 1\n", "summary": "a"},
                {"file_id": str(file_id), "content": "x = 2\n", "summary": "b"},
            ]
        }
    )

    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response(payload, _allowed_file_ids(file_id))


def test_parse_llm_file_edit_response_accepts_no_change_and_cannot_complete():
    file_id = uuid4()

    no_change = parse_llm_file_edit_response(
        json.dumps({"outcome": "no_change", "message": "Already matches", "files": []}),
        _allowed_file_ids(file_id),
    )
    cannot_complete = parse_llm_file_edit_response(
        json.dumps({"outcome": "cannot_complete", "message": "Needs a new file", "files": []}),
        _allowed_file_ids(file_id),
    )

    assert no_change.outcome == "no_change"
    assert no_change.files == []
    assert cannot_complete.outcome == "cannot_complete"
    assert cannot_complete.files == []


def test_parse_llm_file_edit_response_rejects_invalid_outcome_file_combinations():
    file_id = uuid4()

    invalid_payloads = [
        {"outcome": "changed", "message": "", "files": []},
        {
            "outcome": "no_change",
            "message": "Already done",
            "files": [{"file_id": str(file_id), "content": "x = 1\n", "summary": "x"}],
        },
        {
            "outcome": "cannot_complete",
            "message": "Blocked",
            "files": [{"file_id": str(file_id), "content": "x = 1\n", "summary": "x"}],
        },
        {"outcome": "no_change", "message": "", "files": []},
        {"outcome": "cannot_complete", "message": "   ", "files": []},
    ]

    for payload in invalid_payloads:
        with pytest.raises(LlmInvalidFileEditError):
            parse_llm_file_edit_response(json.dumps(payload), _allowed_file_ids(file_id))


def test_parse_llm_file_edit_response_rejects_unknown_top_level_fields():
    file_id = uuid4()
    payload = json.dumps(
        {
            "outcome": "no_change",
            "message": "Already matches",
            "files": [],
            "extra": "not allowed",
        }
    )

    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response(payload, _allowed_file_ids(file_id))


def test_parse_llm_file_edit_response_rejects_non_json():
    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response("not json at all", _allowed_file_ids(uuid4()))


def test_parse_llm_file_edit_response_rejects_missing_files_key():
    file_id = uuid4()
    payload = json.dumps({"not_files": []})

    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response(payload, _allowed_file_ids(file_id))


def test_parse_llm_file_edit_response_rejects_more_files_than_requested():
    allowed_a = uuid4()
    allowed_b = uuid4()
    payload = json.dumps(
        {
            "files": [
                {"file_id": str(allowed_a), "content": "a", "summary": "a"},
                {"file_id": str(allowed_b), "content": "b", "summary": "b"},
            ]
        }
    )

    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response(payload, _allowed_file_ids(allowed_a))


def test_parse_llm_file_edit_response_rejects_oversized_content():
    file_id = uuid4()
    payload = json.dumps(
        {
            "files": [
                {
                    "file_id": str(file_id),
                    "content": "a" * 200_001,
                    "summary": "x",
                }
            ]
        }
    )

    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response(payload, _allowed_file_ids(file_id))


def test_parse_llm_file_edit_response_rejects_oversized_summary():
    file_id = uuid4()
    payload = json.dumps(
        {
            "outcome": "changed",
            "message": "",
            "files": [
                {
                    "file_id": str(file_id),
                    "content": "x = 1\n",
                    "summary": "a" * 501,
                }
            ],
        }
    )

    with pytest.raises(LlmInvalidFileEditError):
        parse_llm_file_edit_response(payload, _allowed_file_ids(file_id))


# ---------------------------------------------------------------------------
# LLM File Edit Message Builder + Token Estimator
# ---------------------------------------------------------------------------


def test_build_file_edit_messages_includes_prompt_files_and_schema_hint():
    file_id = uuid4()
    request = LlmFileEditInput(
        prompt="rename length to span",
        files=[llm_file_pointer(file_id)],
        active_file_id=file_id,
    )
    files = [LlmEditableFile(id=file_id, filename="design.py", content="length = 100\n")]

    messages = build_file_edit_messages(request, files)

    assert len(messages) == 2
    system, user = messages
    assert system["role"] == "system"
    assert "build123d" in system["content"]
    assert "do not use code fences" in system["content"].lower()
    assert "outcome" in system["content"]
    assert user["role"] == "user"
    user_content = user["content"]
    assert "rename length to span" in user_content
    assert "Files available for editing" in user_content
    assert '"outcome": "changed"' in user_content
    assert str(file_id) in user_content
    assert "design.py" in user_content
    assert "length = 100" in user_content


def test_build_file_edit_messages_includes_none_active_file_id():
    request = LlmFileEditInput(
        prompt="refactor",
        files=[llm_file_pointer()],
        active_file_id=None,
    )
    files = [LlmEditableFile(id=request.files[0].id, filename="design.py", content="")]

    messages = build_file_edit_messages(request, files)

    user_content = messages[1]["content"]
    assert "Active file id:" in user_content
    assert "none" in user_content.lower()


def test_build_file_edit_messages_accepts_system_prompt_override():
    request, files = _file_edit_request_and_files()

    messages = build_file_edit_messages(
        request,
        files,
        system_prompt="custom system prompt",
    )

    assert messages[0] == {"role": "system", "content": "custom system prompt"}


def test_estimate_file_edit_tokens_exceeds_max_output_tokens_for_large_prompt():
    file_id = uuid4()
    request = LlmFileEditInput(
        prompt="x" * 12_000,
        files=[llm_file_pointer(file_id)],
    )
    files = [LlmEditableFile(id=file_id, filename="design.py", content="y" * 200_000)]

    estimate = estimate_file_edit_tokens(request, files, max_output_tokens=2048)

    assert estimate > 2048


def test_select_llm_edit_context_files_prioritizes_active_design_mentions_and_imports():
    active_id = uuid4()
    design_id = uuid4()
    helper_id = uuid4()
    caller_id = uuid4()
    mentioned_id = uuid4()
    files = [
        LlmEditableFile(id=caller_id, filename="caller.py", content="import helper\n"),
        LlmEditableFile(id=active_id, filename="active.py", content="from helper import make\n"),
        LlmEditableFile(id=design_id, filename="design.py", content="import active\n"),
        LlmEditableFile(id=helper_id, filename="helper.py", content="def make():\n    return 1\n"),
        LlmEditableFile(id=mentioned_id, filename="bracket.py", content="x = 1\n"),
    ]

    selected = select_llm_edit_context_files(
        prompt="update bracket.py",
        active_file_id=active_id,
        files=files,
        max_files=5,
        max_chars=10000,
    )

    assert [file.filename for file in selected][:3] == ["active.py", "design.py", "bracket.py"]
    assert "helper.py" in [file.filename for file in selected]
    assert "caller.py" in [file.filename for file in selected]


def test_select_llm_edit_context_files_handles_cycles_syntax_errors_and_limits():
    files = [
        LlmEditableFile(id=uuid4(), filename="design.py", content="import a\n"),
        LlmEditableFile(id=uuid4(), filename="a.py", content="import b\n"),
        LlmEditableFile(id=uuid4(), filename="b.py", content="import a\n"),
        LlmEditableFile(id=uuid4(), filename="bad.py", content="def nope(:\n"),
        LlmEditableFile(id=uuid4(), filename="tail.py", content="x = 1\n"),
    ]

    selected = select_llm_edit_context_files(
        prompt="update design",
        active_file_id=None,
        files=files,
        max_files=3,
        max_chars=30,
    )

    assert [file.filename for file in selected] == ["design.py", "a.py", "b.py"]


def test_select_llm_edit_context_files_errors_when_mandatory_file_exceeds_budget():
    files = [
        LlmEditableFile(id=uuid4(), filename="design.py", content="x" * 100),
    ]

    with pytest.raises(ValueError, match="exceeds the AI edit context budget"):
        select_llm_edit_context_files(
            prompt="update design",
            active_file_id=None,
            files=files,
            max_files=5,
            max_chars=50,
        )


def test_estimate_file_edit_tokens_uses_system_prompt_override():
    request, files = _file_edit_request_and_files()

    short_estimate = estimate_file_edit_tokens(
        request,
        files,
        max_output_tokens=2048,
        system_prompt="short",
    )
    long_estimate = estimate_file_edit_tokens(
        request,
        files,
        max_output_tokens=2048,
        system_prompt="x" * 400,
    )

    assert long_estimate > short_estimate


def test_llm_file_edit_input_rejects_more_than_50_metadata_entries():
    with pytest.raises(ValidationError, match="metadata must contain at most 50 entries"):
        LlmFileEditInput(
            prompt="edit",
            files=[llm_file_pointer()],
            metadata={f"k{i}": "v" for i in range(51)},
        )


def test_llm_file_edit_input_rejects_metadata_key_longer_than_200_chars():
    with pytest.raises(ValidationError, match="metadata keys must be at most 200 characters"):
        LlmFileEditInput(
            prompt="edit",
            files=[llm_file_pointer()],
            metadata={"k" * 201: "v"},
        )


def test_llm_file_edit_input_rejects_metadata_value_longer_than_200_chars():
    with pytest.raises(ValidationError, match="metadata values must be at most 200 characters"):
        LlmFileEditInput(
            prompt="edit",
            files=[llm_file_pointer()],
            metadata={"source": "v" * 201},
        )


def test_build_script_generation_input_rejects_oversized_metadata():
    with pytest.raises(ValidationError, match="metadata must contain at most 50 entries"):
        BuildScriptGenerationInput(
            prompt="make a bracket",
            metadata={f"k{i}": "v" for i in range(51)},
        )

    with pytest.raises(ValidationError, match="metadata keys must be at most 200 characters"):
        BuildScriptGenerationInput(prompt="make a bracket", metadata={"k" * 201: "v"})

    with pytest.raises(ValidationError, match="metadata values must be at most 200 characters"):
        BuildScriptGenerationInput(prompt="make a bracket", metadata={"source": "v" * 201})


# ---------------------------------------------------------------------------
# LLM File Edit Provider Call + Billing
# ---------------------------------------------------------------------------


def _file_edit_request_and_files() -> tuple[LlmFileEditInput, list[LlmEditableFile]]:
    file_id = uuid4()
    request = LlmFileEditInput(
        prompt="rename length to span",
        files=[llm_file_pointer(file_id)],
        active_file_id=file_id,
        metadata={"source": "compiler_tab"},
    )
    files = [LlmEditableFile(id=file_id, filename="design.py", content="length = 100\n")]
    return request, files


@pytest.mark.asyncio
async def test_generate_file_edits_returns_provider_result_without_publishing_billing():
    request, files = _file_edit_request_and_files()
    payload = json.dumps(
        {
            "outcome": "changed",
            "message": "",
            "files": [
                {
                    "file_id": str(request.files[0].id),
                    "content": "span = 100\n",
                    "summary": "rename length to span",
                }
            ]
        }
    )
    client = FakeOpenAIClient()
    client.chat = SimpleNamespace(completions=FakeChatCompletions(content=payload))
    publisher = FakePublisher()
    settings = Settings(llm_api_key="secret")
    auth = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="kc-test",
        email="test@example.com",
    )
    project_id = uuid4()

    result = await generate_file_edits(
        request,
        files=files,
        settings=settings,
        auth=auth,
        project_id=project_id,
        openai_client=client,
        billing_publisher=publisher,
    )

    assert result.success is True
    assert result.outcome == "changed"
    assert result.message == ""
    assert len(result.files) == 1
    assert result.files[0].content == "span = 100\n"
    assert result.files[0].summary == "rename length to span"

    call = client.chat.completions.calls[0]
    assert call["model"] == "deepseek-v4-flash"
    assert call["max_tokens"] == 65536
    assert call["response_format"] == {"type": "json_object"}
    assert len(call["messages"]) == 2
    assert call["messages"][0]["content"] == settings.llm_file_edit_system_prompt

    assert result.provider_request_id == "chatcmpl-test"
    assert result.billing_event_id is None
    assert publisher.published == []


@pytest.mark.asyncio
async def test_generate_file_edits_uses_settings_system_prompt():
    request, files = _file_edit_request_and_files()
    payload = json.dumps(
        {
            "outcome": "changed",
            "message": "",
            "files": [
                {
                    "file_id": str(request.files[0].id),
                    "content": "span = 100\n",
                    "summary": "rename length to span",
                }
            ]
        }
    )
    client = FakeOpenAIClient()
    client.chat = SimpleNamespace(completions=FakeChatCompletions(content=payload))
    settings = Settings(
        llm_api_key="secret",
        llm_file_edit_system_prompt="custom provider system prompt",
    )

    await generate_file_edits(
        request,
        files=files,
        settings=settings,
        auth=AuthContext(
            user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc", email=None
        ),
        project_id=uuid4(),
        openai_client=client,
        billing_publisher=FakePublisher(),
    )

    call = client.chat.completions.calls[0]
    assert call["messages"][0]["content"] == "custom provider system prompt"


@pytest.mark.asyncio
async def test_generate_file_edits_rejects_truncated_response():
    request, files = _file_edit_request_and_files()
    payload = json.dumps(
        {
            "outcome": "changed",
            "message": "",
            "files": [
                {
                    "file_id": str(request.files[0].id),
                    "content": "span = 100\n",
                    "summary": "rename length to span",
                }
            ],
        }
    )
    client = FakeOpenAIClient()
    client.chat = SimpleNamespace(
        completions=FakeChatCompletions(content=payload, finish_reason="length")
    )

    with pytest.raises(LlmFileEditTruncatedError):
        await generate_file_edits(
            request,
            files=files,
            settings=Settings(llm_api_key="secret"),
            auth=AuthContext(
                user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc", email=None
            ),
            project_id=uuid4(),
            openai_client=client,
            billing_publisher=FakePublisher(),
        )


@pytest.mark.asyncio
async def test_generate_file_edits_does_not_publish_when_provider_returns_invalid_json():
    request, files = _file_edit_request_and_files()
    client = FakeOpenAIClient()
    client.chat = SimpleNamespace(
        completions=FakeChatCompletions(content="not json at all")
    )
    publisher = FakePublisher()
    settings = Settings(llm_api_key="secret")
    auth = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="kc-test",
        email=None,
    )
    project_id = uuid4()

    with pytest.raises(LlmInvalidFileEditError):
        await generate_file_edits(
            request,
            files=files,
            settings=settings,
            auth=auth,
            project_id=project_id,
            openai_client=client,
            billing_publisher=publisher,
        )

    assert publisher.published == []


@pytest.mark.asyncio
async def test_generate_file_edits_does_not_publish_when_file_id_is_unknown():
    request, files = _file_edit_request_and_files()
    unknown_id = uuid4()
    payload = json.dumps(
        {
            "files": [
                {"file_id": str(unknown_id), "content": "x = 1\n", "summary": "x"}
            ]
        }
    )
    client = FakeOpenAIClient()
    client.chat = SimpleNamespace(completions=FakeChatCompletions(content=payload))
    publisher = FakePublisher()

    with pytest.raises(LlmInvalidFileEditError):
        await generate_file_edits(
            request,
            files=files,
            settings=Settings(llm_api_key="secret"),
            auth=AuthContext(
                user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc", email=None
            ),
            project_id=uuid4(),
            openai_client=client,
            billing_publisher=publisher,
        )

    assert publisher.published == []


@pytest.mark.asyncio
async def test_generate_file_edits_does_not_publish_when_files_duplicate():
    request, files = _file_edit_request_and_files()
    same_id = request.files[0].id
    payload = json.dumps(
        {
            "files": [
                {"file_id": str(same_id), "content": "a", "summary": "a"},
                {"file_id": str(same_id), "content": "b", "summary": "b"},
            ]
        }
    )
    client = FakeOpenAIClient()
    client.chat = SimpleNamespace(completions=FakeChatCompletions(content=payload))
    publisher = FakePublisher()

    with pytest.raises(LlmInvalidFileEditError):
        await generate_file_edits(
            request,
            files=files,
            settings=Settings(llm_api_key="secret"),
            auth=AuthContext(
                user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc", email=None
            ),
            project_id=uuid4(),
            openai_client=client,
            billing_publisher=publisher,
        )

    assert publisher.published == []


@pytest.mark.asyncio
async def test_generate_file_edits_requires_configured_key():
    request, files = _file_edit_request_and_files()
    with pytest.raises(LlmNotConfiguredError):
        await generate_file_edits(
            request,
            files=files,
            settings=Settings(llm_api_key=""),
            auth=AuthContext(
                user_id=uuid4(), tenant_id=uuid4(), keycloak_subject="kc", email=None
            ),
            project_id=uuid4(),
            openai_client=FakeOpenAIClient(),
            billing_publisher=FakePublisher(),
        )
