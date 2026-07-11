from __future__ import annotations

import ast
import json
import logging
import posixpath
import re
from datetime import datetime
from math import ceil
from pathlib import PurePosixPath
from typing import Literal, NamedTuple
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


logger = logging.getLogger(__name__)
LLM_FILE_EDIT_MAX_FILES = 20
MAX_METADATA_ENTRIES = 50
MAX_METADATA_KEY_CHARS = 200
MAX_METADATA_VALUE_CHARS = 200
LlmFileEditOutcome = Literal["changed", "no_change", "cannot_complete"]
BUILD123D_RUNTIME_GUARDRAILS = """\
build123d runtime guardrails:
- Use only build123d APIs known to exist in this runtime; do not invent helpers, classes, or functions.
- Do not use bd.RoundedPolygon; it is not available.
- For rounded rectangular or handle-like geometry, prefer bd.Box, bd.Cylinder, bd.Sphere, bd.Cone, boolean operations, and fillets on resulting solids.
- Always produce code that can run with `import build123d as bd`.
- Avoid advanced builder-mode APIs unless they already appear in the supplied project files.
"""


def validate_filename(filename: str) -> str:
    if "\0" in filename or not filename or PurePosixPath(filename).is_absolute():
        raise ValueError("filename must be a non-empty relative path")
    if ".." in PurePosixPath(filename).parts:
        raise ValueError("filename must not contain parent traversal")
    return filename


def normalize_filename(filename: str) -> str:
    validate_filename(filename)
    return posixpath.normpath(filename)


def validate_llm_metadata(metadata: dict[str, str]) -> dict[str, str]:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise ValueError("metadata must contain at most 50 entries")
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("metadata keys and values must be strings")
        if len(key) > MAX_METADATA_KEY_CHARS:
            raise ValueError("metadata keys must be at most 200 characters")
        if len(value) > MAX_METADATA_VALUE_CHARS:
            raise ValueError("metadata values must be at most 200 characters")
    return metadata


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_creation_prompt_tokens: int = 0


class LlmFilePointer(BaseModel):
    id: UUID
    filename: str
    updated_at: datetime

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return validate_filename(value)


class LlmEditableFile(BaseModel):
    id: UUID
    filename: str
    content: str = Field(max_length=200000)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return validate_filename(value)


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
    file_id: UUID = Field(validation_alias=AliasChoices("file_id", "id"))
    content: str = Field(max_length=200000)
    summary: str = Field(default="", max_length=500)


class LlmFileEditProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: LlmFileEditOutcome
    message: str = Field(default="", max_length=500)
    files: list[LlmReturnedFileEdit] = Field(default_factory=list, max_length=LLM_FILE_EDIT_MAX_FILES)

    @model_validator(mode="after")
    def validate_outcome_contract(self):
        if self.outcome == "changed":
            if not self.files:
                raise ValueError("changed outcome requires at least one file")
        elif self.files or not self.message.strip():
            raise ValueError(f"{self.outcome} outcome requires a message and no file edits")
        return self


class LlmFileEditResult(BaseModel):
    success: bool = True
    outcome: LlmFileEditOutcome
    message: str = ""
    files: list[LlmReturnedFileEdit]
    provider: str
    model: str
    usage: TokenUsage
    provider_request_id: str | None = None
    billing_event_id: UUID | None = None


class FileEditPromptContents(NamedTuple):
    system: str
    user: str


def file_edit_system_content(system_prompt: str) -> str:
    return f"{system_prompt.rstrip()}\n\n{BUILD123D_RUNTIME_GUARDRAILS.strip()}"


def file_edit_prompt_contents(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
    *,
    system_prompt: str,
    prior_prompts: list[str] | tuple[str, ...] = (),
) -> FileEditPromptContents:
    system = file_edit_system_content(system_prompt)
    available = [
        {"file_id": str(file.id), "filename": file.filename, "content": file.content}
        for file in files
    ]
    active_id = str(request.active_file_id) if request.active_file_id is not None else "none"
    history = "\n".join(f"{index + 1}. {prompt}" for index, prompt in enumerate(prior_prompts))
    history_block = f"Conversation history (up to 5 prompts):\n{history}\n\n" if history else ""
    user = (
        f"{history_block}"
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
    return FileEditPromptContents(system=system, user=user)


def estimate_file_edit_usage(request: LlmFileEditInput, files: list[LlmEditableFile], *, system_prompt: str, max_output_tokens: int, prior_prompts: list[str] | tuple[str, ...] = ()) -> TokenUsage:
    contents = file_edit_prompt_contents(
        request, files, system_prompt=system_prompt, prior_prompts=prior_prompts
    )
    prompt_chars = len(contents.system) + len(contents.user)
    prompt_chars += sum(len(key) + len(value) for key, value in request.metadata.items())
    prompt_tokens = ceil(prompt_chars / 4)
    return TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=max_output_tokens, total_tokens=prompt_tokens + max_output_tokens)


def estimate_file_edit_tokens(request: LlmFileEditInput, files: list[LlmEditableFile], *, system_prompt: str, max_output_tokens: int) -> int:
    return estimate_file_edit_usage(request, files, system_prompt=system_prompt, max_output_tokens=max_output_tokens).total_tokens


def _module_stem(filename: str) -> str | None:
    path = PurePosixPath(filename)
    return None if path.suffix != ".py" else path.with_suffix("").as_posix().replace("/", ".")


def _local_imports(file: LlmEditableFile, local_modules: set[str]) -> set[str]:
    try:
        tree = ast.parse(file.content)
    except SyntaxError:
        logger.debug("Skipping import discovery for syntax-invalid file %s", file.filename)
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            for index in range(len(parts), 0, -1):
                candidate = ".".join(parts[:index])
                if candidate in local_modules:
                    found.add(candidate)
                    break
    return found


def select_llm_edit_context_files(*, prompt: str, active_file_id: UUID | None, files: list[LlmEditableFile], max_files: int, max_chars: int) -> list[LlmEditableFile]:
    max_files = max(1, min(max_files, LLM_FILE_EDIT_MAX_FILES))
    max_chars = max(1, max_chars)
    by_id = {file.id: file for file in files}
    by_filename = {file.filename: file for file in files}
    module_to_file = {stem: file for file in files if (stem := _module_stem(file.filename)) is not None}
    imports_by_id = {file.id: _local_imports(file, set(module_to_file)) for file in files}
    selected: list[LlmEditableFile] = []
    selected_ids: set[UUID] = set()
    total_chars = 0
    mandatory: list[LlmEditableFile] = []
    if active_file_id is not None and (active_file := by_id.get(active_file_id)) is not None:
        mandatory.append(active_file)
    if (design_file := by_filename.get("design.py")) is not None and design_file.id != active_file_id:
        mandatory.append(design_file)
    for file in mandatory:
        if len(file.content) > max_chars:
            raise ValueError(f"Required file {file.filename} exceeds the AI edit context budget")

    def add(file: LlmEditableFile) -> None:
        nonlocal total_chars
        if file.id in selected_ids or len(selected) >= max_files or total_chars + len(file.content) > max_chars:
            return
        selected.append(file)
        selected_ids.add(file.id)
        total_chars += len(file.content)

    for file in mandatory:
        add(file)
    prompt_lower = prompt.lower()
    prompt_terms = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]*", prompt_lower))
    for file in files:
        path = PurePosixPath(file.filename)
        if path.name.lower() in prompt_terms or path.with_suffix("").as_posix().lower() in prompt_terms or file.filename.lower() in prompt_lower:
            add(file)
    for selected_file in list(selected):
        for module in imports_by_id[selected_file.id]:
            add(module_to_file[module])
    for selected_file in list(selected):
        stem = _module_stem(selected_file.filename)
        if stem:
            for file in files:
                if stem in imports_by_id[file.id]:
                    add(file)
    for file in files:
        add(file)
    return selected
