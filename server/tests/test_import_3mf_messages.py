from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from core.import_3mf_messages import (
    Import3mfCommand,
    Import3mfProgress,
    Import3mfResult,
)
from core.object_store import ObjectRef
from core.project_assets import (
    IMPORT_3MF_CONVERSION_VERSION,
    Import3mfBounds,
    Import3mfManifestSummary,
    Import3mfPartSummary,
)


def object_ref() -> ObjectRef:
    return ObjectRef(
        bucket="TERTIUS_ASSETS", key=f"sha256/{'a' * 64}", sha256="a" * 64, byte_size=10
    )


def command_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "job_id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": uuid4(),
        "user_id": uuid4(),
        "attempt": 1,
        "execution_id": uuid4(),
        "source": object_ref(),
        "conversion_version": IMPORT_3MF_CONVERSION_VERSION,
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
    for traceparent in (
        "not-w3c",
        "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01",
        "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    ):
        with pytest.raises(ValidationError):
            Import3mfCommand.model_validate(
                {**command_payload(), "traceparent": traceparent}
            )
    for tracestate in (
        "bad\nstate",
        "missing-equals",
        "a=b,,c=d",
        "duplicate=one,duplicate=two",
    ):
        with pytest.raises(ValidationError):
            Import3mfCommand.model_validate(
                {**command_payload(), "tracestate": tracestate}
            )


@pytest.mark.parametrize(
    "tracestate",
    [
        "vendor=value, second=other",
        "vendor=value ,\tsecond=other\t",
        "a= leading space",
        f"{'a' * 256}=value",
        f"{'1' + 'a' * 240}@{'s' + 'b' * 13}=value",
        f"a={'v' * 256}",
        ",".join(f"a{index}=v" for index in range(32)),
    ],
)
def test_command_accepts_w3c_tracestate_keys_and_ows(tracestate):
    assert (
        Import3mfCommand.model_validate(
            {**command_payload(), "tracestate": tracestate}
        ).tracestate
        == tracestate
    )


@pytest.mark.parametrize(
    "tracestate",
    [
        "1vendor=value",
        "vendor.name=value",
        "Vendor=value",
        "@system=value",
        "tenant@=value",
        "tenant@1system=value",
        f"{'1' + 'a' * 241}@system=value",
        f"tenant@{'s' + 'b' * 14}=value",
        f"a={'v' * 257}",
        ",".join(f"a{index}=v" for index in range(33)),
        "vendor=value\x0b,second=other",
        "vendor=value,\rsecond=other",
        "vendor=value, vendor=other",
    ],
)
def test_command_rejects_invalid_w3c_tracestate_members(tracestate):
    with pytest.raises(ValidationError):
        Import3mfCommand.model_validate({**command_payload(), "tracestate": tracestate})


@pytest.mark.parametrize(
    "message_type", [Import3mfCommand, Import3mfProgress, Import3mfResult]
)
def test_schema_version_rejects_bool(message_type, manifest_summary):
    command = Import3mfCommand.model_validate(command_payload())
    if message_type is Import3mfCommand:
        payload = command.model_dump()
    elif message_type is Import3mfProgress:
        payload = Import3mfProgress.for_command(
            command, stage="validating", percent=0
        ).model_dump()
    else:
        payload = Import3mfResult.success_for(
            command,
            brep=object_ref(),
            manifest=object_ref(),
            summary=manifest_summary,
            duration_ms=1,
        ).model_dump()
    with pytest.raises(ValidationError):
        message_type.model_validate({**payload, "schema_version": True})


def test_attempt_execution_and_source_reference_are_strict_and_bounded():
    with pytest.raises(ValidationError):
        Import3mfCommand.model_validate({**command_payload(), "attempt": "1"})
    with pytest.raises(ValidationError):
        Import3mfCommand.model_validate({**command_payload(), "attempt": 0})
    with pytest.raises(ValidationError):
        Import3mfCommand.model_validate(
            {**command_payload(), "execution_id": str(uuid4())}
        )
    oversized = object_ref().model_copy(update={"byte_size": 128 * 1024 * 1024 + 1})
    with pytest.raises(ValidationError):
        Import3mfCommand.model_validate({**command_payload(), "source": oversized})
    boundary = object_ref().model_copy(update={"byte_size": 128 * 1024 * 1024})
    assert (
        Import3mfCommand.model_validate(
            {**command_payload(), "source": boundary}
        ).source
        == boundary
    )


def test_progress_is_strict_and_bounded():
    progress = Import3mfProgress(
        schema_version=1,
        job_id=uuid4(),
        attempt=1,
        execution_id=uuid4(),
        stage="converting",
        percent=50,
    )
    assert progress.percent == 50
    with pytest.raises(ValidationError):
        Import3mfProgress(
            schema_version=1,
            job_id=uuid4(),
            attempt=1,
            execution_id=uuid4(),
            stage="converting",
            percent=101,
        )
    command = Import3mfCommand.model_validate(command_payload())
    matching = Import3mfProgress.for_command(command, stage="validating", percent=0)
    matching.assert_matches(command)
    with pytest.raises(ValueError, match="provenance"):
        matching.model_copy(update={"execution_id": uuid4()}).assert_matches(command)
    with pytest.raises(ValidationError):
        Import3mfProgress.model_validate(
            {
                "schema_version": 1,
                "job_id": uuid4(),
                "attempt": 1,
                "execution_id": uuid4(),
                "stage": "converting",
                "percent": "50",
            }
        )


def test_result_success_requires_refs_and_summary(manifest_summary):
    command = Import3mfCommand.model_validate(command_payload())
    result = Import3mfResult.success_for(
        command,
        brep=object_ref(),
        manifest=object_ref(),
        summary=manifest_summary,
        duration_ms=25,
    )
    assert result.status == "succeeded"
    assert result.job_id == command.job_id
    assert result.attempt == command.attempt
    assert result.execution_id == command.execution_id
    assert result.duration_ms == 25
    with pytest.raises(ValidationError):
        Import3mfResult.model_validate({**result.model_dump(), "error_code": "bad"})


def test_result_failure_requires_bounded_error_and_no_outputs():
    command = Import3mfCommand.model_validate(command_payload())
    result = Import3mfResult.failure_for(
        command,
        error_code="invalid_3mf",
        user_message="The 3MF is invalid.",
        duration_ms=1,
    )
    assert result.status == "failed"
    assert result.brep is None
    with pytest.raises(ValidationError):
        Import3mfResult.failure_for(
            command, error_code="e" * 65, user_message="bad", duration_ms=1
        )
    with pytest.raises(ValidationError):
        Import3mfResult.failure_for(
            command, error_code="bad", user_message="x" * 241, duration_ms=1
        )


def test_result_reference_limits_and_provenance_mismatch_are_rejected(manifest_summary):
    command = Import3mfCommand.model_validate(command_payload())
    success = Import3mfResult.success_for(
        command,
        brep=object_ref(),
        manifest=object_ref(),
        summary=manifest_summary,
        duration_ms=1,
    )
    success.assert_matches(command)
    oversized_source = object_ref().model_copy(
        update={"byte_size": 128 * 1024 * 1024 + 1}
    )
    with pytest.raises(ValidationError):
        Import3mfResult.model_validate(
            {**success.model_dump(), "source": oversized_source}
        )
    with pytest.raises(ValueError, match="provenance"):
        success.model_copy(update={"attempt": 2}).assert_matches(command)
    with pytest.raises(ValueError, match="provenance"):
        success.model_copy(update={"traceparent": "different"}).assert_matches(command)
    oversized_brep = object_ref().model_copy(
        update={"byte_size": 512 * 1024 * 1024 + 1}
    )
    with pytest.raises(ValidationError):
        Import3mfResult.success_for(
            command,
            brep=oversized_brep,
            manifest=object_ref(),
            summary=manifest_summary,
            duration_ms=1,
        )
    exact_brep = object_ref().model_copy(update={"byte_size": 512 * 1024 * 1024})
    exact_manifest = object_ref().model_copy(update={"byte_size": 256 * 1024})
    assert (
        Import3mfResult.success_for(
            command,
            brep=exact_brep,
            manifest=exact_manifest,
            summary=manifest_summary,
            duration_ms=1,
        ).brep
        == exact_brep
    )
    zero = object_ref().model_copy(update={"byte_size": 0})
    with pytest.raises(ValidationError):
        Import3mfResult.success_for(
            command,
            brep=zero,
            manifest=object_ref(),
            summary=manifest_summary,
            duration_ms=1,
        )
    other_bucket = object_ref().model_copy(update={"bucket": "OTHER_ASSETS"})
    with pytest.raises(ValidationError):
        Import3mfResult.success_for(
            command,
            brep=other_bucket,
            manifest=object_ref(),
            summary=manifest_summary,
            duration_ms=1,
        )
    oversized_manifest = object_ref().model_copy(update={"byte_size": 256 * 1024 + 1})
    with pytest.raises(ValidationError):
        Import3mfResult.success_for(
            command,
            brep=object_ref(),
            manifest=oversized_manifest,
            summary=manifest_summary,
            duration_ms=1,
        )


def test_messages_round_trip_through_json_ipc(manifest_summary):
    command = Import3mfCommand.model_validate(command_payload())
    assert Import3mfCommand.model_validate_json(command.model_dump_json()) == command
    progress = Import3mfProgress.for_command(command, stage="converting", percent=50)
    assert Import3mfProgress.model_validate_json(progress.model_dump_json()) == progress
    result = Import3mfResult.success_for(
        command,
        brep=object_ref(),
        manifest=object_ref(),
        summary=manifest_summary,
        duration_ms=1,
    )
    assert Import3mfResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        Import3mfResult.model_validate(
            {
                **result.model_dump(),
                "traceparent": "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            }
        )
