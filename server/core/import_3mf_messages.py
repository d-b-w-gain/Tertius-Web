from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated, Self

from core.object_store import ObjectRef
from core.project_assets import (
    MAX_3MF_DERIVED_BREP_BYTES,
    MAX_3MF_MANIFEST_BYTES,
    MAX_3MF_UPLOAD_BYTES,
    Import3mfManifestSummary,
)


TraceValue = Annotated[str, StringConstraints(max_length=512)]
ErrorCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
UserMessage = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=240, pattern=r"^[^\x00-\x1f\x7f-\x9f]*$"
    ),
]
Import3mfProgressStage = Literal["validating", "converting", "persisting"]


class StrictImportMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Import3mfCommand(StrictImportMessage):
    schema_version: Literal[1]
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    user_id: UUID
    attempt: int = Field(strict=True, ge=1)
    execution_id: UUID
    source: ObjectRef
    conversion_version: Literal["tertius-3mf-brep-v1-build123d-0.8.0"]
    traceparent: TraceValue | None = None
    tracestate: TraceValue | None = None

    @model_validator(mode="after")
    def validate_source_size(self) -> Self:
        if self.source.byte_size > MAX_3MF_UPLOAD_BYTES:
            raise ValueError("source reference exceeds the 3MF upload limit")
        return self


class Import3mfProgress(StrictImportMessage):
    schema_version: Literal[1]
    job_id: UUID
    attempt: int = Field(strict=True, ge=1)
    execution_id: UUID
    stage: Import3mfProgressStage
    percent: int = Field(strict=True, ge=0, le=100)

    @classmethod
    def for_command(
        cls,
        command: Import3mfCommand,
        *,
        stage: Import3mfProgressStage,
        percent: int,
    ) -> Self:
        return cls(
            schema_version=command.schema_version,
            job_id=command.job_id,
            attempt=command.attempt,
            execution_id=command.execution_id,
            stage=stage,
            percent=percent,
        )

    def assert_matches(self, command: Import3mfCommand) -> None:
        if (
            self.job_id != command.job_id
            or self.attempt != command.attempt
            or self.execution_id != command.execution_id
        ):
            raise ValueError("import progress provenance does not match command")


class Import3mfResult(StrictImportMessage):
    schema_version: Literal[1]
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    user_id: UUID
    attempt: int = Field(strict=True, ge=1)
    execution_id: UUID
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
        if self.source.byte_size > MAX_3MF_UPLOAD_BYTES:
            raise ValueError("source reference exceeds the 3MF upload limit")
        if self.status == "succeeded":
            if any(value is None for value in outputs) or any(
                value is not None for value in errors
            ):
                raise ValueError(
                    "successful results require only BREP, manifest, and summary"
                )
            assert self.brep is not None and self.manifest is not None
            if self.brep.byte_size > MAX_3MF_DERIVED_BREP_BYTES:
                raise ValueError("BREP reference exceeds the derived asset limit")
            if self.manifest.byte_size > MAX_3MF_MANIFEST_BYTES:
                raise ValueError("manifest reference exceeds the manifest limit")
        elif any(value is not None for value in outputs) or any(
            value is None for value in errors
        ):
            raise ValueError(
                "failed results require only an error code and user message"
            )
        return self

    def assert_matches(self, command: Import3mfCommand) -> None:
        if (
            self.job_id != command.job_id
            or self.tenant_id != command.tenant_id
            or self.project_id != command.project_id
            or self.user_id != command.user_id
            or self.attempt != command.attempt
            or self.execution_id != command.execution_id
            or self.source != command.source
            or self.conversion_version != command.conversion_version
            or self.traceparent != command.traceparent
            or self.tracestate != command.tracestate
        ):
            raise ValueError("import result provenance does not match command")

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
            attempt=command.attempt,
            execution_id=command.execution_id,
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
            attempt=command.attempt,
            execution_id=command.execution_id,
            source=command.source,
            conversion_version=command.conversion_version,
            traceparent=command.traceparent,
            tracestate=command.tracestate,
            status="failed",
            error_code=error_code,
            user_message=user_message,
            duration_ms=duration_ms,
        )
