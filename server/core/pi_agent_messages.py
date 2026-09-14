from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.llm_file_edit import normalize_filename, validate_filename


class StrictMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PiAgentSourceFile(StrictMessage):
    id: UUID
    filename: str
    content: str = Field(max_length=2_000_000)
    updated_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        return validate_filename(value)

    @field_validator("updated_at")
    @classmethod
    def aware_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def valid_hash(self):
        if sha256(self.content.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("sha256 does not match content")
        return self


class PiAgentFileManifest(StrictMessage):
    id: UUID
    filename: str
    updated_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        return validate_filename(value)

    @field_validator("updated_at")
    @classmethod
    def aware_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        return value


class PiAgentConversationTurn(StrictMessage):
    user_request: str = Field(min_length=1, max_length=12000)
    status: Literal["succeeded", "failed"]
    outcome: Literal["changed", "no_changes"] | None = None
    assistant_summary: str = Field(default="", max_length=2000)
    error_code: str | None = Field(default=None, max_length=100)
    changed_files: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("changed_files")
    @classmethod
    def safe_changed_filenames(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 512:
                raise ValueError("changed filenames must be at most 512 characters")
            validate_filename(value)
        return values

    @model_validator(mode="after")
    def consistent_outcome(self):
        if self.status == "succeeded":
            if self.outcome is None:
                raise ValueError("successful conversation turns require an outcome")
            if self.error_code is not None:
                raise ValueError("successful conversation turns cannot contain errors")
        if self.status == "failed" and (self.outcome is not None or not self.error_code):
            raise ValueError("failed conversation turns require only an error code")
        if self.outcome != "changed" and self.changed_files:
            raise ValueError("only changed turns can contain filenames")
        return self


class PiAgentConversationContext(StrictMessage):
    rolling_summary: str = Field(default="", max_length=8000)
    recent_turns: list[PiAgentConversationTurn] = Field(
        default_factory=list,
        max_length=5,
    )


class PiAgentCommand(StrictMessage):
    schema_version: Literal[1, 2]
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    provider: Literal["openai-codex"]
    model: str = Field(min_length=1, max_length=200)
    thinking: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
    prompt: str = Field(min_length=1, max_length=20000)
    prior_prompts: list[str] = Field(default_factory=list, max_length=5)
    conversation: PiAgentConversationContext | None = None
    system_prompt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    active_file_id: UUID | None = None
    files: list[PiAgentSourceFile] = Field(min_length=1, max_length=20)
    created_at: datetime
    traceparent: str | None = Field(default=None, max_length=512)
    tracestate: str | None = Field(default=None, max_length=512)

    @field_validator("prior_prompts")
    @classmethod
    def bounded_prior_prompts(cls, values: list[str]) -> list[str]:
        if any(len(value) > 20000 for value in values):
            raise ValueError("prior prompts must be at most 20000 characters")
        return values

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def versioned_context(self):
        if self.schema_version == 1:
            if self.conversation is not None or self.system_prompt_sha256 is not None:
                raise ValueError("v1 commands cannot contain v2 prompt context")
        elif self.conversation is None or self.system_prompt_sha256 is None:
            raise ValueError("v2 commands require conversation and prompt hash")
        elif self.prior_prompts:
            raise ValueError("v2 commands cannot contain legacy prior prompts")
        return self

    @model_validator(mode="after")
    def consistent_files(self):
        ids = [file.id for file in self.files]
        names = [normalize_filename(file.filename) for file in self.files]
        if len(ids) != len(set(ids)):
            raise ValueError("file IDs must be unique")
        if len(names) != len(set(names)):
            raise ValueError("normalized filenames must be unique")
        if self.active_file_id is not None and self.active_file_id not in ids:
            raise ValueError("active_file_id must identify a command file")
        return self


class PiAgentChangedFile(StrictMessage):
    id: UUID
    filename: str
    content: str = Field(max_length=2_000_000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        return validate_filename(value)

    @model_validator(mode="after")
    def valid_hash(self):
        if sha256(self.content.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("sha256 does not match content")
        return self


class PiAgentUsage(StrictMessage):
    input_tokens: int = Field(default=0, ge=0, le=2**63 - 1)
    output_tokens: int = Field(default=0, ge=0, le=2**63 - 1)
    cache_read_tokens: int = Field(default=0, ge=0, le=2**63 - 1)
    cache_write_tokens: int = Field(default=0, ge=0, le=2**63 - 1)
    total_tokens: int = Field(default=0, ge=0, le=2**63 - 1)


class PiAgentProgressEvent(StrictMessage):
    sequence: int = Field(ge=1, le=2**63 - 1)
    kind: Literal["reasoning_delta", "tool_started", "tool_finished"]
    text: str | None = Field(default=None, min_length=1, max_length=1000)
    tool_name: Literal["read", "edit", "write", "grep", "find", "ls"] | None = None
    target: str | None = Field(default=None, max_length=512)
    is_error: bool | None = None
    occurred_at: datetime

    @field_validator("target")
    @classmethod
    def safe_target(cls, value: str | None) -> str | None:
        return normalize_filename(value) if value is not None else None

    @field_validator("occurred_at")
    @classmethod
    def utc_occurred_at(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise ValueError("occurred_at must be UTC")
        return value

    @model_validator(mode="after")
    def valid_state(self):
        if self.kind == "reasoning_delta":
            if self.text is None:
                raise ValueError("reasoning events require text")
            if any(
                value is not None
                for value in (self.tool_name, self.target, self.is_error)
            ):
                raise ValueError("reasoning events cannot contain tool fields")
        elif self.kind == "tool_started":
            if self.text is not None or self.tool_name is None or self.is_error is not None:
                raise ValueError(
                    "tool-started events require only tool name and optional target"
                )
        elif self.text is not None or self.tool_name is None or self.is_error is None:
            raise ValueError(
                "tool-finished events require tool name, error state, and optional target"
            )
        return self


class PiAgentProgressBatch(StrictMessage):
    message_type: Literal["progress"]
    schema_version: Literal[1]
    execution_id: UUID
    execution_started_at: datetime
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    batch_sequence: int = Field(ge=1, le=2**63 - 1)
    events: list[PiAgentProgressEvent] = Field(min_length=1, max_length=16)
    traceparent: str | None = Field(default=None, max_length=512)
    tracestate: str | None = Field(default=None, max_length=512)

    @field_validator("execution_started_at")
    @classmethod
    def utc_execution_started_at(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise ValueError("execution_started_at must be UTC")
        return value

    @field_validator("events")
    @classmethod
    def ordered_events(
        cls, values: list[PiAgentProgressEvent]
    ) -> list[PiAgentProgressEvent]:
        sequences = [event.sequence for event in values]
        if any(current <= previous for previous, current in zip(sequences, sequences[1:])):
            raise ValueError("progress event sequences must be strictly increasing")
        return values


class PiAgentProgressSnapshot(StrictMessage):
    schema_version: Literal[1]
    execution_id: UUID
    execution_started_at: datetime
    last_batch_sequence: int = Field(ge=1, le=2**63 - 1)
    last_sequence: int = Field(ge=1, le=2**63 - 1)
    truncated_before_sequence: int | None = Field(
        default=None,
        ge=1,
        le=2**63 - 1,
    )
    events: list[PiAgentProgressEvent] = Field(min_length=1, max_length=128)

    @field_validator("execution_started_at")
    @classmethod
    def utc_execution_started_at(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise ValueError("execution_started_at must be UTC")
        return value

    @model_validator(mode="after")
    def consistent_sequences(self):
        sequences = [event.sequence for event in self.events]
        if any(
            current <= previous
            for previous, current in zip(sequences, sequences[1:])
        ):
            raise ValueError("progress snapshot sequences must be strictly increasing")
        if self.last_sequence != sequences[-1]:
            raise ValueError("last_sequence must match the final progress event")
        if (
            self.truncated_before_sequence is not None
            and self.truncated_before_sequence >= sequences[0]
        ):
            raise ValueError(
                "truncated_before_sequence must precede retained progress events"
            )
        return self


class PiAgentResult(StrictMessage):
    schema_version: Literal[1]
    execution_id: UUID
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    status: Literal["succeeded", "failed"]
    outcome: Literal["changed", "no_changes"] | None = None
    provider: Literal["openai-codex"]
    model: str = Field(min_length=1, max_length=200)
    assistant_summary: str = Field(default="", max_length=2000)
    changed_files: list[PiAgentChangedFile] = Field(default_factory=list, max_length=20)
    usage: PiAgentUsage = Field(default_factory=PiAgentUsage)
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)
    retryable: bool = False
    worker_started_at: datetime
    worker_finished_at: datetime
    traceparent: str | None = Field(default=None, max_length=512)
    tracestate: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def valid_state(self):
        if any(value.tzinfo is None or value.utcoffset() is None for value in (self.worker_started_at, self.worker_finished_at)):
            raise ValueError("worker timestamps must be timezone-aware")
        if self.worker_finished_at < self.worker_started_at:
            raise ValueError("worker_finished_at must not precede worker_started_at")
        if self.status == "succeeded":
            if self.retryable:
                raise ValueError("successful results must not be retryable")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("successful results must not include errors")
            if self.outcome == "changed" and not self.changed_files:
                raise ValueError("changed outcome requires changed files")
            if self.outcome == "no_changes" and self.changed_files:
                raise ValueError("no_changes outcome must not include changed files")
            if self.outcome is None:
                raise ValueError("successful results require an outcome")
        elif self.outcome is not None or self.changed_files or not self.error_code or not self.error_message:
            raise ValueError("failed results require error fields and no outcome or changed files")
        file_ids = [file.id for file in self.changed_files]
        filenames = [normalize_filename(file.filename) for file in self.changed_files]
        if len(file_ids) != len(set(file_ids)):
            raise ValueError("changed file IDs must be unique")
        if len(filenames) != len(set(filenames)):
            raise ValueError("normalized changed filenames must be unique")
        return self


def serialized_pi_agent_message_size(message: BaseModel) -> int:
    return len(message.model_dump_json().encode("utf-8"))


def assert_pi_agent_message_size(message: BaseModel, max_bytes: int, label: str) -> None:
    size = serialized_pi_agent_message_size(message)
    if size > max_bytes:
        raise ValueError(f"{label} message is {size} bytes, above {max_bytes} byte limit")


def assert_pi_agent_command_size(command: PiAgentCommand, max_bytes: int) -> None:
    assert_pi_agent_message_size(command, max_bytes, "Pi agent command")


def assert_pi_agent_result_size(result: PiAgentResult, max_bytes: int) -> None:
    assert_pi_agent_message_size(result, max_bytes, "Pi agent result")


def assert_pi_agent_progress_size(
    progress: PiAgentProgressBatch, max_bytes: int
) -> None:
    assert_pi_agent_message_size(progress, max_bytes, "Pi agent progress")


def pi_agent_command_message_id(command: PiAgentCommand) -> str:
    return f"pi-request:{command.job_id}"


def pi_agent_result_message_id(result: PiAgentResult) -> str:
    return f"pi-result:{result.job_id}:{result.execution_id}"


def pi_agent_progress_message_id(progress: PiAgentProgressBatch) -> str:
    return (
        f"pi-progress:{progress.job_id}:{progress.execution_id}:"
        f"{progress.batch_sequence}"
    )
