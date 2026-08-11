from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated, Self

from core.object_store import ObjectRef
from core.project_assets import Import3mfManifestSummary


TraceValue = Annotated[str, StringConstraints(max_length=512)]
ErrorCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
UserMessage = Annotated[
    str,
    StringConstraints(min_length=1, max_length=240, pattern=r"^[^\x00-\x1f\x7f-\x9f]*$"),
]


class StrictImportMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Import3mfCommand(StrictImportMessage):
    schema_version: Literal[1]
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    user_id: UUID
    source: ObjectRef
    conversion_version: Literal["tertius-3mf-brep-v1-build123d-0.8.0"]
    traceparent: TraceValue | None = None
    tracestate: TraceValue | None = None


class Import3mfProgress(StrictImportMessage):
    schema_version: Literal[1]
    job_id: UUID
    stage: Literal["validating", "converting", "persisting"]
    percent: int = Field(strict=True, ge=0, le=100)


class Import3mfResult(StrictImportMessage):
    schema_version: Literal[1]
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    user_id: UUID
    source: ObjectRef
    conversion_version: Literal["tertius-3mf-brep-v1-build123d-0.8.0"]
    traceparent: TraceValue | None = None
    tracestate: TraceValue | None = None
    status: Literal["succeeded", "failed"]
    brep: ObjectRef | None = None
    manifest: ObjectRef | None = None
    summary: Import3mfManifestSummary | None = None
    duration_ms: int = Field(strict=True, ge=0, le=300_000)
    error_code: ErrorCode | None = None
    user_message: UserMessage | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        outputs = (self.brep, self.manifest, self.summary)
        errors = (self.error_code, self.user_message)
        if self.status == "succeeded":
            if any(value is None for value in outputs) or any(value is not None for value in errors):
                raise ValueError("successful results require only BREP, manifest, and summary")
        elif any(value is not None for value in outputs) or any(value is None for value in errors):
            raise ValueError("failed results require only an error code and user message")
        return self

    @classmethod
    def success_for(
        cls,
        command: Import3mfCommand,
        *,
        brep: ObjectRef,
        manifest: ObjectRef,
        summary: Import3mfManifestSummary,
        duration_ms: int,
    ) -> Self:
        return cls(
            schema_version=command.schema_version,
            job_id=command.job_id,
            tenant_id=command.tenant_id,
            project_id=command.project_id,
            user_id=command.user_id,
            source=command.source,
            conversion_version=command.conversion_version,
            traceparent=command.traceparent,
            tracestate=command.tracestate,
            status="succeeded",
            brep=brep,
            manifest=manifest,
            summary=summary,
            duration_ms=duration_ms,
        )

    @classmethod
    def failure_for(
        cls,
        command: Import3mfCommand,
        *,
        error_code: str,
        user_message: str,
        duration_ms: int,
    ) -> Self:
        return cls(
            schema_version=command.schema_version,
            job_id=command.job_id,
            tenant_id=command.tenant_id,
            project_id=command.project_id,
            user_id=command.user_id,
            source=command.source,
            conversion_version=command.conversion_version,
            traceparent=command.traceparent,
            tracestate=command.tracestate,
            status="failed",
            error_code=error_code,
            user_message=user_message,
            duration_ms=duration_ms,
        )
