from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from math import ceil
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator

from core.auth_types import AuthContext
from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
)
from core.nats_client import NatsPublisher, Publisher


logger = logging.getLogger(__name__)


class LlmNotConfiguredError(RuntimeError):
    pass


class LlmGenerationError(RuntimeError):
    pass


class LlmBillingError(RuntimeError):
    pass


class LlmInvalidFileEditError(RuntimeError):
    pass


class LlmNoFileChangesError(RuntimeError):
    pass


MAX_METADATA_ENTRIES = 50
MAX_METADATA_KEY_CHARS = 200
MAX_METADATA_VALUE_CHARS = 200


def validate_llm_metadata(metadata: dict[str, str]) -> dict[str, str]:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise ValueError("metadata must contain at most 50 entries")
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        if not isinstance(value, str):
            raise ValueError("metadata values must be strings")
        if len(key) > MAX_METADATA_KEY_CHARS:
            raise ValueError("metadata keys must be at most 200 characters")
        if len(value) > MAX_METADATA_VALUE_CHARS:
            raise ValueError("metadata values must be at most 200 characters")
    return metadata


class BuildScriptGenerationInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    active_file: str = Field(default="design.py")
    current_code: str = Field(default="", max_length=200000)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, metadata):
        return validate_llm_metadata({} if metadata is None else metadata)


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


class LlmFilePointer(BaseModel):
    id: UUID
    filename: str
    updated_at: datetime


class LlmEditableFile(BaseModel):
    id: UUID
    filename: str
    content: str = Field(max_length=200000)


class LlmFileEditInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    files: list[LlmFilePointer] = Field(min_length=1, max_length=20)
    active_file_id: UUID | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, metadata):
        return validate_llm_metadata({} if metadata is None else metadata)


class LlmReturnedFileEdit(BaseModel):
    file_id: UUID
    content: str = Field(max_length=200000)
    summary: str = Field(default="", max_length=500)


class LlmFileEditProviderResult(BaseModel):
    files: list[LlmReturnedFileEdit] = Field(min_length=1, max_length=20)


class LlmFileEditResult(BaseModel):
    success: bool = True
    files: list[LlmReturnedFileEdit]
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
    billing_publisher: Publisher | None = None,
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


FILE_EDIT_SYSTEM_PROMPT = (
    "You edit Python source files for Tertius Intus. "
    "Return only valid JSON. Do not include markdown fences or explanation. "
    "You may modify only files listed in the user message. "
    "Do not create, delete, or rename files. "
    "Each returned file must use the exact file_id supplied by the user. "
    "Return the full final content for every changed file. "
    "If a file does not need changes, omit it from the files array. "
    "All code must be executable Python source suitable for build123d when geometry is involved."
)


def build_file_edit_messages(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
) -> list[dict[str, str]]:
    available = [
        {"file_id": str(file.id), "filename": file.filename, "content": file.content}
        for file in files
    ]
    active_id = str(request.active_file_id) if request.active_file_id is not None else "none"
    user_prompt = (
        f"User request:\n{request.prompt}\n\n"
        f"Active file id:\n{active_id}\n\n"
        f"Files available for editing:\n{json.dumps(available, indent=2)}\n\n"
        "Return JSON matching:\n"
        "{\n"
        '  "files": [\n'
        "    {\n"
        '      "file_id": "<uuid from files available for editing>",\n'
        '      "content": "<full final Python source>",\n'
        '      "summary": "<short human-readable summary>"\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    return [
        {"role": "system", "content": FILE_EDIT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def estimate_file_edit_tokens(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
    *,
    max_output_tokens: int,
) -> int:
    prompt_chars = sum(
        len(message["content"]) for message in build_file_edit_messages(request, files)
    )
    metadata_chars = sum(len(key) + len(value) for key, value in request.metadata.items())
    return max_output_tokens + ceil((prompt_chars + metadata_chars) / 4)


def parse_llm_file_edit_response(
    content: str,
    allowed_file_ids: set[UUID],
) -> LlmFileEditProviderResult:
    if len(allowed_file_ids) > 20:
        raise LlmInvalidFileEditError("allowed file id set is too large")

    stripped = strip_markdown_code_fence(content)
    try:
        payload = json.loads(stripped) if stripped else None
    except json.JSONDecodeError as exc:
        raise LlmInvalidFileEditError(f"provider returned non-JSON response: {exc}") from exc

    if not isinstance(payload, dict):
        raise LlmInvalidFileEditError("provider response must be a JSON object")

    if "files" not in payload:
        raise LlmInvalidFileEditError("provider response missing 'files' key")

    raw_files = payload["files"]
    if not isinstance(raw_files, list):
        raise LlmInvalidFileEditError("provider response 'files' must be a list")

    if len(raw_files) == 0:
        raise LlmNoFileChangesError("LLM returned no file changes")

    if len(raw_files) > len(allowed_file_ids):
        raise LlmInvalidFileEditError("provider returned more files than were requested")

    if len(raw_files) > 20:
        raise LlmInvalidFileEditError("provider returned too many files")

    seen_ids: set[UUID] = set()
    parsed: list[LlmReturnedFileEdit] = []
    for index, entry in enumerate(raw_files):
        if not isinstance(entry, dict):
            raise LlmInvalidFileEditError(f"file edit entry {index} must be an object")
        try:
            edit = LlmReturnedFileEdit.model_validate(entry)
        except ValidationError as exc:
            raise LlmInvalidFileEditError(f"file edit entry {index} is invalid: {exc}") from exc
        if edit.file_id not in allowed_file_ids:
            raise LlmInvalidFileEditError(
                f"provider returned unauthorized file_id {edit.file_id}"
            )
        if edit.file_id in seen_ids:
            raise LlmInvalidFileEditError(
                f"provider returned duplicate file_id {edit.file_id}"
            )
        seen_ids.add(edit.file_id)
        parsed.append(edit)

    return LlmFileEditProviderResult(files=parsed)


async def generate_file_edits(
    request: LlmFileEditInput,
    *,
    files: list[LlmEditableFile],
    settings,
    auth: AuthContext,
    project_id: UUID | None,
    openai_client=None,
    billing_publisher: Publisher | None = None,
) -> LlmFileEditResult:
    if not settings.llm_api_key:
        raise LlmNotConfiguredError("LLM provider is not configured")

    allowed_file_ids = {file.id for file in files}
    client = openai_client or create_openai_client(settings)
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=build_file_edit_messages(request, files),
        max_tokens=settings.llm_max_output_tokens,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    usage = extract_usage(response)
    provider_request_id = getattr(response, "id", None)

    parsed = parse_llm_file_edit_response(content, allowed_file_ids)

    result = LlmFileEditResult(
        files=parsed.files,
        model=settings.llm_model,
        usage=usage,
        provider_request_id=provider_request_id,
    )

    return result
