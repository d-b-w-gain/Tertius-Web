from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated, Self

from core.object_store import ObjectRef
from core.project_assets import (
    MAX_3MF_DERIVED_BREP_BYTES,
    MAX_3MF_MANIFEST_BYTES,
    MAX_3MF_UPLOAD_BYTES,
    Import3mfManifestSummary,
)


TraceParent = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"),
]
TraceState = Annotated[
    str,
    StringConstraints(min_length=3, max_length=512),
]
ErrorCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_]*$"),
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

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def schema_version_is_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("traceparent", check_fields=False)
    @classmethod
    def valid_traceparent(cls, value: str | None) -> str | None:
        if value is not None:
            version, trace_id, parent_id, _ = value.split("-")
            if version == "ff" or trace_id == "0" * 32 or parent_id == "0" * 16:
                raise ValueError("traceparent identifiers must be non-zero")
        return value

    @field_validator("tracestate", check_fields=False)
    @classmethod
    def valid_tracestate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        members = value.split(",")
        keys: list[str] = []
        if len(members) > 32:
            raise ValueError("tracestate must contain valid W3C list members")
        for index, raw_member in enumerate(members):
            if index == 0 and raw_member.startswith((" ", "\t")):
                raise ValueError("tracestate must contain valid W3C list members")
            member = raw_member.rstrip(" \t")
            if index:
                member = member.lstrip(" \t")
            key = _tracestate_member_key(member)
            if key is None or key in keys:
                raise ValueError("tracestate must contain valid W3C list members")
            keys.append(key)
        return value


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
    traceparent: TraceParent | None = None
    tracestate: TraceState | None = None

    @model_validator(mode="after")
    def validate_source_size(self) -> Self:
        if not 0 < self.source.byte_size <= MAX_3MF_UPLOAD_BYTES:
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
    traceparent: TraceParent | None = None
    tracestate: TraceState | None = None
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
        if not 0 < self.source.byte_size <= MAX_3MF_UPLOAD_BYTES:
            raise ValueError("source reference exceeds the 3MF upload limit")
        if self.status == "succeeded":
            if any(value is None for value in outputs) or any(
                value is not None for value in errors
            ):
                raise ValueError(
                    "successful results require only BREP, manifest, and summary"
                )
            assert self.brep is not None and self.manifest is not None
            if not 0 < self.brep.byte_size <= MAX_3MF_DERIVED_BREP_BYTES:
                raise ValueError("BREP reference exceeds the derived asset limit")
            if not 0 < self.manifest.byte_size <= MAX_3MF_MANIFEST_BYTES:
                raise ValueError("manifest reference exceeds the manifest limit")
            if not (self.source.bucket == self.brep.bucket == self.manifest.bucket):
                raise ValueError("asset references must use the same configured bucket")
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


_SIMPLE_TRACESTATE_KEY = re.compile(r"^[a-z][a-z0-9_\-*/]{0,255}$")
_TENANT_TRACESTATE_KEY = re.compile(
    r"^[a-z0-9][a-z0-9_\-*/]{0,240}@[a-z][a-z0-9_\-*/]{0,13}$"
)


def _tracestate_member_key(member: str) -> str | None:
    if member.count("=") != 1:
        return None
    key, value = member.split("=", 1)
    valid_key = (
        _SIMPLE_TRACESTATE_KEY.fullmatch(key) is not None
        or _TENANT_TRACESTATE_KEY.fullmatch(key) is not None
    )
    valid_value = (
        1 <= len(value) <= 256
        and value[-1] != " "
        and all(
            " " <= character <= "~" and character not in ",=" for character in value
        )
    )
    return key if valid_key and valid_value else None
