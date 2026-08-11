from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from core.import_3mf_messages import Import3mfCommand, Import3mfProgress, Import3mfResult
from core.object_store import ObjectRef
from core.project_assets import (
    IMPORT_3MF_CONVERSION_VERSION,
    Import3mfBounds,
    Import3mfManifestSummary,
    Import3mfPartSummary,
)


def object_ref() -> ObjectRef:
    return ObjectRef(bucket="TERTIUS_ASSETS", key=f"sha256/{'a' * 64}", sha256="a" * 64, byte_size=10)


def command_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "job_id": uuid4(), "tenant_id": uuid4(), "project_id": uuid4(), "user_id": uuid4(),
        "source": object_ref(), "conversion_version": IMPORT_3MF_CONVERSION_VERSION,
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "vendor=value",
    }


@pytest.fixture
def manifest_summary() -> Import3mfManifestSummary:
    return Import3mfManifestSummary(
        conversion_version=IMPORT_3MF_CONVERSION_VERSION,
        source_unit="MM",
        scale_to_mm=1.0,
        object_count=1,
        total_vertices=8,
        total_triangles=12,
        warnings=(),
        parts=(
            Import3mfPartSummary(
                index=0,
                name="box",
                shape_type="solid",
                boolean_capable=True,
                is_valid=True,
                bounds_mm=Import3mfBounds(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0)),
            ),
        ),
    )


def test_command_is_strict_frozen_and_contains_only_reference():
    command = Import3mfCommand.model_validate(command_payload())
    assert command.source.sha256 == "a" * 64
    with pytest.raises(ValidationError):
        command.job_id = uuid4()
    for forbidden in ("content", "bytes", "content_base64"):
        with pytest.raises(ValidationError):
            Import3mfCommand.model_validate({**command_payload(), forbidden: "YWJj"})


def test_command_bounds_trace_state():
    with pytest.raises(ValidationError):
        Import3mfCommand.model_validate({**command_payload(), "traceparent": "x" * 513})
    with pytest.raises(ValidationError):
        Import3mfCommand.model_validate({**command_payload(), "tracestate": "x" * 513})


def test_progress_is_strict_and_bounded():
    progress = Import3mfProgress(
        schema_version=1, job_id=uuid4(), stage="converting", percent=50
    )
    assert progress.percent == 50
    with pytest.raises(ValidationError):
        Import3mfProgress(
            schema_version=1, job_id=uuid4(), stage="converting", percent=101
        )
    with pytest.raises(ValidationError):
        Import3mfProgress.model_validate(
            {"schema_version": 1, "job_id": uuid4(), "stage": "converting", "percent": "50"}
        )


def test_result_success_requires_refs_and_summary(manifest_summary):
    command = Import3mfCommand.model_validate(command_payload())
    result = Import3mfResult.success_for(
        command, brep=object_ref(), manifest=object_ref(), summary=manifest_summary, duration_ms=25
    )
    assert result.status == "succeeded"
    assert result.job_id == command.job_id
    assert result.duration_ms == 25
    with pytest.raises(ValidationError):
        Import3mfResult.model_validate({**result.model_dump(), "error_code": "bad"})


def test_result_failure_requires_bounded_error_and_no_outputs():
    command = Import3mfCommand.model_validate(command_payload())
    result = Import3mfResult.failure_for(
        command, error_code="invalid_3mf", user_message="The 3MF is invalid.", duration_ms=1
    )
    assert result.status == "failed"
    assert result.brep is None
    with pytest.raises(ValidationError):
        Import3mfResult.failure_for(command, error_code="e" * 65, user_message="bad", duration_ms=1)
    with pytest.raises(ValidationError):
        Import3mfResult.failure_for(command, error_code="bad", user_message="x" * 241, duration_ms=1)
