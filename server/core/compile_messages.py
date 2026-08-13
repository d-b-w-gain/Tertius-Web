from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CompileSourceFile(BaseModel):
    filename: str
    content: str


class CompileCommand(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    requested_by: UUID
    export_format: str
    quality: str | None = None
    created_at: datetime
    files: list[CompileSourceFile] = []
    request_id: str | None = None
    originating_llm_edit_job_id: UUID | None = None


class CompileArtifactPayload(BaseModel):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    content_type: str = Field(min_length=1, max_length=100)
    content_base64: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_compressed: bool = False


class CompileResultPayload(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    export_format: str
    status: Literal["succeeded", "failed"]
    artifacts: list[CompileArtifactPayload] = Field(default_factory=list, max_length=8)
    bundle_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = None
    user_message: str | None = None
    error: str | None = None
    retryable: bool = False
    worker_started_at: datetime
    worker_finished_at: datetime


def serialized_message_size(message: BaseModel) -> int:
    return len(message.model_dump_json().encode("utf-8"))


def assert_message_size(message: BaseModel, max_bytes: int, label: str) -> None:
    size = serialized_message_size(message)
    if size > max_bytes:
        raise ValueError(f"{label} message is {size} bytes, above {max_bytes} byte limit")


def compile_result_message_id(result: CompileResultPayload) -> str:
    return f"compile-result:{result.job_id}:{result.status}"
