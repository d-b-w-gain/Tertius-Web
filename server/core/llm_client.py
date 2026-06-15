from __future__ import annotations

import logging
from datetime import datetime, timezone
from math import ceil
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from core.auth_types import AuthContext
from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
)
from core.nats_client import NatsPublisher


logger = logging.getLogger(__name__)


class LlmNotConfiguredError(RuntimeError):
    pass


class LlmGenerationError(RuntimeError):
    pass


class LlmBillingError(RuntimeError):
    pass


class BuildScriptGenerationInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    active_file: str = Field(default="design.py")
    current_code: str = Field(default="", max_length=200000)
    metadata: dict[str, str] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class BuildScriptGenerationResult(BaseModel):
    success: bool = True
    script: str
    model: str
    usage: TokenUsage
    provider_request_id: str | None = None
    billing_event_id: UUID | None = None


def create_openai_client(settings):
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
    )


def build_script_messages(request: BuildScriptGenerationInput) -> list[dict[str, str]]:
    system_prompt = (
        "You generate Python build scripts for Tertius Intus. "
        "Return only executable Python source code. "
        "Use build123d idioms when geometry is needed. "
        "Do not include markdown fences or explanation."
    )
    user_prompt = (
        f"Active file: {request.active_file}\n\n"
        f"Current code:\n{request.current_code}\n\n"
        f"Requested build script:\n{request.prompt}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def estimate_build_script_tokens(request: BuildScriptGenerationInput, *, max_output_tokens: int) -> int:
    prompt_chars = sum(len(message["content"]) for message in build_script_messages(request))
    metadata_chars = sum(len(key) + len(value) for key, value in request.metadata.items())
    return max_output_tokens + ceil((prompt_chars + metadata_chars) / 4)


def strip_markdown_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def extract_usage(response) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )


def _provider_from_settings(settings) -> str:
    if "deepseek" in settings.llm_base_url.lower():
        return "deepseek"
    return "openai-compatible"


async def generate_build_script(
    request: BuildScriptGenerationInput,
    *,
    settings,
    auth: AuthContext,
    project_id: UUID | None,
    openai_client=None,
    billing_publisher: NatsPublisher | None = None,
) -> BuildScriptGenerationResult:
    if not settings.llm_api_key:
        raise LlmNotConfiguredError("LLM provider is not configured")

    client = openai_client or create_openai_client(settings)
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=build_script_messages(request),
        max_tokens=settings.llm_max_output_tokens,
    )
    content = response.choices[0].message.content or ""
    usage = extract_usage(response)
    provider_request_id = getattr(response, "id", None)
    result = BuildScriptGenerationResult(
        script=strip_markdown_code_fence(content),
        model=settings.llm_model,
        usage=usage,
        provider_request_id=provider_request_id,
    )

    if billing_publisher is not None:
        billing_event_id = uuid4()
        result.billing_event_id = billing_event_id
        event = LlmTokenUsageEvent(
            event_id=billing_event_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            project_id=project_id,
            workflow="intus",
            operation="build_script.generate",
            provider=_provider_from_settings(settings),
            model=settings.llm_model,
            prompt=request.prompt,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            occurred_at=datetime.now(timezone.utc),
            provider_request_id=provider_request_id,
            metadata=request.metadata,
        )
        try:
            assert_billing_message_size(event, settings.billing_max_bytes)
            await billing_publisher.publish_json(
                settings.billing_llm_usage_subject,
                event,
                message_id=billing_usage_message_id(event),
            )
        except Exception as exc:
            logger.exception("Failed to publish LLM billing usage event")
            raise LlmBillingError("LLM billing failed") from exc

    return result
