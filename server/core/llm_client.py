from __future__ import annotations

import ast
import json
import logging
import re
from datetime import datetime, timezone
from math import ceil
from pathlib import PurePosixPath
from time import perf_counter
from types import SimpleNamespace
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.auth_types import AuthContext
from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
)
from core.config import LlmModelConfig
from core.nats_client import NatsPublisher, Publisher
from core.telemetry import counter_add, elapsed_seconds, get_tracer, histogram_record, record_exception


logger = logging.getLogger(__name__)
MAX_PROVIDER_ERROR_MESSAGE_CHARS = 500


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


def require_file_edit_system_prompt(system_prompt: str) -> str:
    if not system_prompt.strip():
        raise LlmNotConfiguredError("LLM file edit system prompt is not configured")
    return system_prompt


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
    model_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, metadata):
        return validate_llm_metadata({} if metadata is None else metadata)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_creation_prompt_tokens: int = 0


class BuildScriptGenerationResult(BaseModel):
    success: bool = True
    script: str
    provider: str
    model: str
    usage: TokenUsage
    cost_usd: float = 0.0
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
    model_id: str | None = Field(default=None, max_length=200)
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
    provider: str
    model: str
    usage: TokenUsage
    cost_usd: float = 0.0
    provider_request_id: str | None = None
    billing_event_id: UUID | None = None


def _base_url_from_endpoint(endpoint: str, suffix: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith(suffix):
        return normalized[: -len(suffix)]
    return normalized


def create_openai_client(settings, model_config: LlmModelConfig | None = None):
    from openai import AsyncOpenAI

    if model_config is None or not model_config.endpoint:
        raise LlmNotConfiguredError("LLM model endpoint is not configured")
    kwargs = {
        "api_key": settings.llm_api_key,
        "timeout": settings.llm_timeout_seconds,
    }
    base_url = _base_url_from_endpoint(model_config.endpoint, "/chat/completions")
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


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
    return estimate_build_script_usage(request, max_output_tokens=max_output_tokens).total_tokens


def estimate_build_script_usage(request: BuildScriptGenerationInput, *, max_output_tokens: int) -> TokenUsage:
    prompt_chars = sum(len(message["content"]) for message in build_script_messages(request))
    metadata_chars = sum(len(key) + len(value) for key, value in request.metadata.items())
    prompt_tokens = ceil((prompt_chars + metadata_chars) / 4)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=max_output_tokens,
        total_tokens=prompt_tokens + max_output_tokens,
    )


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
    prompt_tokens = (
        getattr(usage, "prompt_tokens", None)
        if getattr(usage, "prompt_tokens", None) is not None
        else getattr(usage, "input_tokens", 0)
    ) or 0
    completion_tokens = (
        getattr(usage, "completion_tokens", None)
        if getattr(usage, "completion_tokens", None) is not None
        else getattr(usage, "output_tokens", 0)
    ) or 0
    total_tokens = getattr(usage, "total_tokens", None) or prompt_tokens + completion_tokens
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached_prompt_tokens = getattr(prompt_details, "cached_tokens", 0) if prompt_details is not None else 0
    if isinstance(prompt_details, dict):
        cached_prompt_tokens = prompt_details.get("cached_tokens", 0) or 0
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_prompt_tokens=cached_prompt_tokens or getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_prompt_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def llm_usage_cost_usd(usage: TokenUsage, model_config: LlmModelConfig) -> float:
    cached_read_tokens = max(0, usage.cached_prompt_tokens)
    cache_creation_tokens = max(0, usage.cache_creation_prompt_tokens)
    standard_input_tokens = max(0, usage.prompt_tokens - cached_read_tokens - cache_creation_tokens)

    cost = standard_input_tokens * model_config.input_price_per_million / 1_000_000
    cost += usage.completion_tokens * model_config.output_price_per_million / 1_000_000
    if model_config.cached_read_price_per_million is not None:
        cost += cached_read_tokens * model_config.cached_read_price_per_million / 1_000_000
    else:
        cost += cached_read_tokens * model_config.input_price_per_million / 1_000_000
    if model_config.cached_write_price_per_million is not None:
        cost += cache_creation_tokens * model_config.cached_write_price_per_million / 1_000_000
    else:
        cost += cache_creation_tokens * model_config.input_price_per_million / 1_000_000
    return round(cost, 8)


def _provider_from_model(model_config: LlmModelConfig) -> str:
    return model_config.api


def select_llm_model(settings, model_id: str | None) -> LlmModelConfig:
    try:
        return settings.get_llm_model(model_id)
    except ValueError as exc:
        raise LlmNotConfiguredError(str(exc)) from exc


def _provider_exception_status(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def _provider_exception_message(exc: Exception) -> str:
    exc_name = type(exc).__name__
    status_code = _provider_exception_status(exc)
    detail = " ".join(str(exc).strip().split())
    if len(detail) > MAX_PROVIDER_ERROR_MESSAGE_CHARS:
        detail = f"{detail[:MAX_PROVIDER_ERROR_MESSAGE_CHARS]}..."

    parts = [f"LLM provider request failed ({exc_name}"]
    if status_code is not None:
        parts[0] += f", HTTP {status_code}"
    parts[0] += ")"
    if detail:
        parts.append(detail)
    return ": ".join(parts)


def _classify_provider_exception(exc: Exception) -> RuntimeError:
    status_code = _provider_exception_status(exc)
    exc_name = type(exc).__name__
    if status_code in {401, 403} or exc_name in {"AuthenticationError", "PermissionDeniedError"}:
        return LlmProviderAuthenticationError("LLM provider authentication failed")
    if status_code == 429 or exc_name == "RateLimitError":
        return LlmProviderRateLimitError("LLM provider rate limit exceeded")
    return LlmGenerationError(_provider_exception_message(exc))


def _anthropic_parts_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


async def create_anthropic_message(
    *,
    settings,
    model_config: LlmModelConfig,
    messages: list[dict[str, str]],
    max_tokens: int,
):
    import httpx

    endpoint = model_config.endpoint.strip()
    if not endpoint:
        raise LlmNotConfiguredError("LLM model endpoint is not configured")

    system_messages = [message["content"] for message in messages if message["role"] == "system"]
    request_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if message["role"] != "system"
    ]
    payload = {
        "model": model_config.model,
        "max_tokens": max_tokens,
        "messages": request_messages,
    }
    if system_messages:
        payload["system"] = "\n\n".join(system_messages)

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "x-api-key": settings.llm_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()

    data = response.json()
    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    return SimpleNamespace(
        id=data.get("id") if isinstance(data, dict) else None,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=_anthropic_parts_to_text(data.get("content") if isinstance(data, dict) else "")),
                finish_reason=data.get("stop_reason") if isinstance(data, dict) else None,
            )
        ],
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
        ),
    )


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
    model_config = select_llm_model(settings, request.model_id)
    if not model_config.model:
        raise LlmNotConfiguredError("LLM model is not configured")

    provider = _provider_from_model(model_config)
    attributes = {
        "llm.provider": provider,
        "llm.model_id": model_config.id,
        "llm.operation": "build_script.generate",
        "provider": provider,
        "model_id": model_config.id,
    }
    start = perf_counter()
    try:
        messages = build_script_messages(request)
        with get_tracer(__name__).start_as_current_span("llm.build_script.generate", attributes=attributes) as span:
            if model_config.api == "anthropic-messages":
                response = await create_anthropic_message(
                    settings=settings,
                    model_config=model_config,
                    messages=messages,
                    max_tokens=settings.llm_max_output_tokens,
                )
            else:
                client = openai_client or create_openai_client(settings, model_config)
                response = await client.chat.completions.create(
                    model=model_config.model,
                    messages=messages,
                    max_tokens=settings.llm_max_output_tokens,
                )
    except Exception as exc:
        duration = elapsed_seconds(start)
        counter_add("tertius.llm.request.error.count", 1, attributes)
        histogram_record("tertius.llm.request.duration", duration, {**attributes, "status_category": "error"})
        with get_tracer(__name__).start_as_current_span("llm.build_script.error", attributes=attributes) as span:
            record_exception(span, exc)
        logger.exception(
            "LLM provider build-script request failed model_id=%s api=%s",
            model_config.id,
            model_config.api,
        )
        raise _classify_provider_exception(exc) from exc
    duration = elapsed_seconds(start)
    content = response.choices[0].message.content or ""
    usage = extract_usage(response)
    metric_attributes = {**attributes, "status_category": "ok"}
    counter_add("tertius.llm.request.count", 1, metric_attributes)
    histogram_record("tertius.llm.request.duration", duration, metric_attributes)
    histogram_record("tertius.llm.tokens.input", usage.prompt_tokens, attributes)
    histogram_record("tertius.llm.tokens.output", usage.completion_tokens, attributes)
    provider_request_id = getattr(response, "id", None)
    result = BuildScriptGenerationResult(
        script=strip_markdown_code_fence(content),
        provider=provider,
        model=model_config.model,
        usage=usage,
        cost_usd=llm_usage_cost_usd(usage, model_config),
        provider_request_id=provider_request_id,
    )
    histogram_record("tertius.llm.cost.usd", result.cost_usd, attributes)

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
            provider=_provider_from_model(model_config),
            model=model_config.model,
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
            counter_add("tertius.billing.publish.error.count", 1, {"provider": provider, "model_id": model_config.id})
            logger.exception("Failed to publish LLM billing usage event")
            raise LlmBillingError("LLM billing failed") from exc

    return result


def build_file_edit_messages(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
    *,
    system_prompt: str,
) -> list[dict[str, str]]:
    system_prompt = require_file_edit_system_prompt(system_prompt)
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
    system_prompt: str,
    max_output_tokens: int,
) -> int:
    return estimate_file_edit_usage(
        request,
        files,
        system_prompt=system_prompt,
        max_output_tokens=max_output_tokens,
    ).total_tokens


def estimate_file_edit_usage(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
    *,
    system_prompt: str,
    max_output_tokens: int,
) -> TokenUsage:
    prompt_chars = sum(
        len(message["content"])
        for message in build_file_edit_messages(
            request, files, system_prompt=system_prompt
        )
    )
    metadata_chars = sum(len(key) + len(value) for key, value in request.metadata.items())
    prompt_tokens = ceil((prompt_chars + metadata_chars) / 4)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=max_output_tokens,
        total_tokens=prompt_tokens + max_output_tokens,
    )


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
    model_config = select_llm_model(settings, request.model_id)
    if not model_config.model:
        raise LlmNotConfiguredError("LLM model is not configured")
    system_prompt = require_file_edit_system_prompt(settings.llm_file_edit_system_prompt)

    allowed_file_ids = {file.id for file in files}
    provider = _provider_from_model(model_config)
    attributes = {
        "llm.provider": provider,
        "llm.model_id": model_config.id,
        "llm.operation": "files.llm_edit",
        "provider": provider,
        "model_id": model_config.id,
    }
    start = perf_counter()
    try:
        messages = build_file_edit_messages(
            request,
            files,
            system_prompt=system_prompt,
        )
        with get_tracer(__name__).start_as_current_span("llm.files.edit", attributes=attributes) as span:
            if model_config.api == "anthropic-messages":
                response = await create_anthropic_message(
                    settings=settings,
                    model_config=model_config,
                    messages=messages,
                    max_tokens=settings.llm_file_edit_max_output_tokens,
                )
            else:
                client = openai_client or create_openai_client(settings, model_config)
                response = await client.chat.completions.create(
                    model=model_config.model,
                    messages=messages,
                    max_tokens=settings.llm_file_edit_max_output_tokens,
                    response_format={"type": "json_object"},
                )
    except Exception as exc:
        duration = elapsed_seconds(start)
        counter_add("tertius.llm.request.error.count", 1, attributes)
        histogram_record("tertius.llm.request.duration", duration, {**attributes, "status_category": "error"})
        with get_tracer(__name__).start_as_current_span("llm.files.edit.error", attributes=attributes) as span:
            record_exception(span, exc)
        logger.exception(
            "LLM provider file-edit request failed model_id=%s api=%s",
            model_config.id,
            model_config.api,
        )
        raise _classify_provider_exception(exc) from exc
    duration = elapsed_seconds(start)
    usage = extract_usage(response)
    metric_attributes = {**attributes, "status_category": "ok"}
    counter_add("tertius.llm.request.count", 1, metric_attributes)
    histogram_record("tertius.llm.request.duration", duration, metric_attributes)
    histogram_record("tertius.llm.tokens.input", usage.prompt_tokens, attributes)
    histogram_record("tertius.llm.tokens.output", usage.completion_tokens, attributes)
    provider_request_id = getattr(response, "id", None)
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason in {"length", "max_tokens"}:
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
        provider=provider,
        model=model_config.model,
        usage=usage,
        cost_usd=llm_usage_cost_usd(usage, model_config),
        provider_request_id=provider_request_id,
    )
    histogram_record("tertius.llm.cost.usd", result.cost_usd, attributes)

    return result
