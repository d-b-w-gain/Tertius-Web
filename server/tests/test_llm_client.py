from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.auth_types import AuthContext
from core.config import Settings
from core.llm_client import (
    BuildScriptGenerationInput,
    LlmBillingError,
    LlmNotConfiguredError,
    generate_build_script,
)


class FakeChatCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="chatcmpl-test",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="```python\nimport build123d as bd\npart = bd.Box(1, 2, 3)\n```"
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22, total_tokens=33),
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
