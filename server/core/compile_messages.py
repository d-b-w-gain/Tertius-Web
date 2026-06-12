from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CompileCommand(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    requested_by: UUID
    export_format: str
    created_at: datetime


class CompileResultEvent(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    status: str
    export_format: str
    artifact_id: UUID | None = None
    error_code: str | None = None
    user_message: str | None = None
    error: str | None = None
    retryable: bool = False
    finished_at: datetime
