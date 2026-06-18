from __future__ import annotations

import ast
import json
import logging
import re
from datetime import datetime, timezone
from math import ceil
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.auth_types import AuthContext
from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
)
from core.llm_prompts import FILE_EDIT_SYSTEM_PROMPT
from core.nats_client import NatsPublisher, Publisher


logger = logging.getLogger(__name__)


class LlmNotConfiguredError(RuntimeError):
    pass


class LlmGenerationError(RuntimeError):
    pass


class LlmProviderAuthenticationError(RuntimeError):
    pass


class LlmProviderRateLimitError(LlmGenerationError):
    pass


class LlmBillingError(RuntimeError):
    pass


class LlmInvalidFileEditError(RuntimeError):
    pass


class LlmFileEditTruncatedError(LlmGenerationError):
    pass


MAX_METADATA_ENTRIES = 50
MAX_METADATA_KEY_CHARS = 200
MAX_METADATA_VALUE_CHARS = 200
LLM_FILE_EDIT_MAX_FILES = 20
LlmFileEditOutcome = Literal["changed", "no_change", "cannot_complete"]


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
    model_config = ConfigDict(extra="forbid")

    file_id: UUID
    content: str = Field(max_length=200000)
    summary: str = Field(default="", max_length=500)


class LlmFileEditProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: LlmFileEditOutcome
    message: str = Field(default="", max_length=500)
    files: list[LlmReturnedFileEdit] = Field(default_factory=list, max_length=LLM_FILE_EDIT_MAX_FILES)

    @model_validator(mode="after")
    def validate_outcome_contract(self):
        message = self.message.strip()
        if self.outcome == "changed":
            if not self.files:
                raise ValueError("changed outcome requires at least one file")
            return self
        if self.files:
            raise ValueError(f"{self.outcome} outcome must not include file edits")
        if not message:
            raise ValueError(f"{self.outcome} outcome requires a message")
        return self


class LlmFileEditResult(BaseModel):
    success: bool = True
    outcome: LlmFileEditOutcome
    message: str = ""
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


def _provider_exception_status(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def _classify_provider_exception(exc: Exception) -> RuntimeError:
    status_code = _provider_exception_status(exc)
    exc_name = type(exc).__name__
    if status_code in {401, 403} or exc_name in {"AuthenticationError", "PermissionDeniedError"}:
        return LlmProviderAuthenticationError("LLM provider authentication failed")
    if status_code == 429 or exc_name == "RateLimitError":
        return LlmProviderRateLimitError("LLM provider rate limit exceeded")
    return LlmGenerationError("LLM provider request failed")


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
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=build_script_messages(request),
            max_tokens=settings.llm_max_output_tokens,
        )
    except Exception as exc:
        raise _classify_provider_exception(exc) from exc
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


def build_file_edit_messages(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
    *,
    system_prompt: str = FILE_EDIT_SYSTEM_PROMPT,
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
        '  "outcome": "changed",\n'
        '  "message": "",\n'
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
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def estimate_file_edit_tokens(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
    *,
    max_output_tokens: int,
    system_prompt: str = FILE_EDIT_SYSTEM_PROMPT,
) -> int:
    prompt_chars = sum(
        len(message["content"])
        for message in build_file_edit_messages(
            request, files, system_prompt=system_prompt
        )
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

    if "outcome" not in payload:
        payload = {"outcome": "changed", "message": "", **payload}

    try:
        parsed = LlmFileEditProviderResult.model_validate(payload)
    except ValidationError as exc:
        raise LlmInvalidFileEditError(f"provider response is invalid: {exc}") from exc

    if len(parsed.files) > len(allowed_file_ids):
        raise LlmInvalidFileEditError("provider returned more files than were requested")

    seen_ids: set[UUID] = set()
    for edit in parsed.files:
        if edit.file_id not in allowed_file_ids:
            raise LlmInvalidFileEditError(
                f"provider returned unauthorized file_id {edit.file_id}"
            )
        if edit.file_id in seen_ids:
            raise LlmInvalidFileEditError(
                f"provider returned duplicate file_id {edit.file_id}"
            )
        seen_ids.add(edit.file_id)

    return parsed


def _module_stem(filename: str) -> str | None:
    path = PurePosixPath(filename)
    if path.suffix != ".py":
        return None
    return path.with_suffix("").as_posix().replace("/", ".")


def _local_imports(file: LlmEditableFile, local_modules: set[str]) -> set[str]:
    try:
        tree = ast.parse(file.content)
    except SyntaxError:
        logger.debug("Skipping import discovery for syntax-invalid file %s", file.filename)
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                for index in range(len(parts), 0, -1):
                    candidate = ".".join(parts[:index])
                    if candidate in local_modules:
                        found.add(candidate)
                        break
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            parts = node.module.split(".")
            for index in range(len(parts), 0, -1):
                candidate = ".".join(parts[:index])
                if candidate in local_modules:
                    found.add(candidate)
                    break
    return found


def select_llm_edit_context_files(
    *,
    prompt: str,
    active_file_id: UUID | None,
    files: list[LlmEditableFile],
    max_files: int,
    max_chars: int,
) -> list[LlmEditableFile]:
    max_files = max(1, min(max_files, LLM_FILE_EDIT_MAX_FILES))
    max_chars = max(1, max_chars)
    by_id = {file.id: file for file in files}
    by_filename = {file.filename: file for file in files}
    module_to_file = {
        stem: file
        for file in files
        if (stem := _module_stem(file.filename)) is not None
    }
    imports_by_id = {
        file.id: _local_imports(file, set(module_to_file))
        for file in files
    }

    selected: list[LlmEditableFile] = []
    selected_ids: set[UUID] = set()
    total_chars = 0
    mandatory_ids: set[UUID] = set()
    if active_file_id in by_id:
        mandatory_ids.add(active_file_id)
    if "design.py" in by_filename:
        mandatory_ids.add(by_filename["design.py"].id)

    for file_id in mandatory_ids:
        file = by_id[file_id]
        if len(file.content) > max_chars:
            raise ValueError(
                f"Required file {file.filename} ({len(file.content)} chars) exceeds "
                f"the AI edit context budget ({max_chars} chars). Reduce the file or "
                f"raise LLM_FILE_EDIT_MAX_CONTEXT_CHARS."
            )

    def add(file: LlmEditableFile, *, mandatory: bool = False) -> bool:
        nonlocal total_chars
        if file.id in selected_ids:
            return True
        if len(selected) >= max_files:
            return False
        if total_chars + len(file.content) > max_chars:
            return False
        selected.append(file)
        selected_ids.add(file.id)
        total_chars += len(file.content)
        return True

    if active_file_id in by_id:
        add(by_id[active_file_id], mandatory=True)
    if "design.py" in by_filename:
        add(by_filename["design.py"], mandatory=True)

    prompt_lower = prompt.lower()
    prompt_terms = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]*", prompt_lower))
    for file in files:
        stem = PurePosixPath(file.filename).with_suffix("").as_posix().lower()
        basename = PurePosixPath(file.filename).name.lower()
        if basename in prompt_terms or stem in prompt_terms or file.filename.lower() in prompt_lower:
            add(file)

    def add_import_neighbors(reverse: bool) -> None:
        current_ids = [file.id for file in selected]
        for selected_id in current_ids:
            selected_file = by_id[selected_id]
            selected_stem = _module_stem(selected_file.filename)
            if reverse:
                if selected_stem is None:
                    continue
                for file in files:
                    if selected_stem in imports_by_id[file.id]:
                        add(file)
            else:
                for module in imports_by_id[selected_id]:
                    imported = module_to_file.get(module)
                    if imported is not None:
                        add(imported)

    add_import_neighbors(reverse=False)
    add_import_neighbors(reverse=True)

    for file in files:
        add(file, mandatory=file.id in mandatory_ids)

    return selected


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
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=build_file_edit_messages(
                request,
                files,
                system_prompt=settings.llm_file_edit_system_prompt,
            ),
            max_tokens=settings.llm_file_edit_max_output_tokens,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        raise _classify_provider_exception(exc) from exc
    usage = extract_usage(response)
    provider_request_id = getattr(response, "id", None)
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        logger.warning(
            "LLM file edit response truncated provider_request_id=%s finish_reason=%s",
            provider_request_id,
            finish_reason,
        )
        raise LlmFileEditTruncatedError("LLM file edit response was truncated")

    content = choice.message.content or ""

    parsed = parse_llm_file_edit_response(content, allowed_file_ids)

    result = LlmFileEditResult(
        outcome=parsed.outcome,
        message=parsed.message,
        files=parsed.files,
        model=settings.llm_model,
        usage=usage,
        provider_request_id=provider_request_id,
    )

    return result
