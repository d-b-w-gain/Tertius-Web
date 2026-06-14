from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class CompileSourceFile(BaseModel):
    filename: str
    content: str


class CompileCommand(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    requested_by: UUID
    export_format: str
    created_at: datetime
    files: list[CompileSourceFile] = []
    request_id: str | None = None


class CompileResultPayload(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    export_format: str
    status: Literal["succeeded", "failed"]
    artifact_content_base64: str | None = None
    artifact_byte_size: int | None = None
    artifact_content_type: str | None = None
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
