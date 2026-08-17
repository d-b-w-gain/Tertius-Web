from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from core.object_store import ObjectRef


class CompileSourceFile(BaseModel):
    filename: str
    content: str


class CompileBinaryAsset(BaseModel):
    logical_filename: Literal["source.3mf"]
    object_ref: ObjectRef


class CompileCommand(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    requested_by: UUID
    export_format: str
    quality: str | None = None
    created_at: datetime
    files: list[CompileSourceFile] = Field(default_factory=list)
    assets: list[CompileBinaryAsset] = Field(default_factory=list, max_length=1)
    request_id: str | None = None
    originating_llm_edit_job_id: UUID | None = None


class CompileResultPayload(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    export_format: str
    status: Literal["succeeded", "failed"]
    artifact_content_base64: str | None = None
    artifact_byte_size: int | None = None
    artifact_content_type: str | None = None
    structural_manifest_json: str | None = None
    bom_manifest_json: str | None = None
    error_code: str | None = None
    user_message: str | None = None
    error: str | None = None
    retryable: bool = False
    is_compressed: bool = False
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
