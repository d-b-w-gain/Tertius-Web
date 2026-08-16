from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import logging
from datetime import datetime, timezone
from time import perf_counter
from uuid import UUID

from opentelemetry.trace import SpanKind

from core.compile_messages import (
    CompileCommand,
    CompileResultPayload,
    assert_message_size,
    compile_result_message_id,
    serialized_message_size,
)
from core.compile_runtime import (
    hydrate_project_files,
    runtime_files_hash,
    structural_runtime_files_hash,
)
from core.compile_sandbox import run_compile_sandbox
from core.config import get_settings
from core.nats_client import (
    NatsPublisher,
    Publisher,
    connect_nats,
    ensure_compile_stream,
    extract_nats_context,
    pull_compile_subscription,
)
from core.object_store import (
    ObjectIntegrityError,
    ObjectNotFoundError,
    open_compile_sidecar_store,
)
from core.telemetry import (
    configure_telemetry,
    counter_add,
    elapsed_seconds,
    get_tracer,
    histogram_record,
    record_exception,
)
from core.structural.contracts import CompiledStructuralManifest
from core.structural.design_capture import capture_project_structural_declaration


logger = logging.getLogger(__name__)


def _hash_llm_edit_job_id(job_id: UUID) -> str:
    return hashlib.sha256(str(job_id).encode("ascii")).hexdigest()[:16]


async def handle_compile_request_message(
    msg, publisher: Publisher, settings, object_store=None
) -> None:
    context = extract_nats_context(getattr(msg, "headers", None))
    subject = getattr(msg, "subject", "tertius.compile.request")
    attributes = {
        "messaging.system": "nats",
        "messaging.destination.name": subject,
        "messaging.operation.name": "process",
        "nats_subject": subject,
    }
    with get_tracer(__name__).start_as_current_span(
        "NATS consume tertius.compile.request",
        context=context,
        kind=SpanKind.CONSUMER,
        attributes=attributes,
    ) as span:
        try:
            command = CompileCommand.model_validate_json(msg.data)
        except Exception as exc:
            logger.exception("Invalid compile command JSON")
            record_exception(span, exc)
            span.set_attribute("messaging.nats.ack_action", "term")
            await msg.term()
            return

        span.set_attribute("tertius.export_format", command.export_format)
        if command.originating_llm_edit_job_id is not None:
            span.set_attribute(
                "tertius.originating_llm_edit_job_hash",
                _hash_llm_edit_job_id(command.originating_llm_edit_job_id),
            )
        queue_latency = (now_utc() - command.created_at).total_seconds()
        if queue_latency >= 0:
            histogram_record(
                "tertius.compile.queue.latency",
                queue_latency,
                {"export_format": command.export_format},
            )

        counter_add("tertius.compile.job.started.count", 1, {"export_format": command.export_format})
        start = perf_counter()
        try:
            binary_files: dict[str, bytes] = {}
            if command.assets:
                if object_store is None:
                    result = _failed_result(
                        command,
                        now_utc(),
                        error="Compile binary asset transport is unavailable",
                        error_code="invalid_binary_asset",
                        user_message="Compile input could not be loaded. Try again.",
                        retryable=True,
                    )
                else:
                    try:
                        for asset in command.assets:
                            binary_files[asset.logical_filename] = await object_store.get(
                                asset.object_ref
                            )
                    except (ObjectIntegrityError, ObjectNotFoundError) as exc:
                        result = _failed_result(
                            command,
                            now_utc(),
                            error=str(exc),
                            error_code="invalid_binary_asset",
                            user_message="Compile input failed its integrity check.",
                            retryable=False,
                        )
                    else:
                        result = await asyncio.to_thread(
                            execute_compile_command,
                            command,
                            settings,
                            binary_files,
                        )
            else:
                # Keep the NATS event loop responsive while CAD runs in its bounded
                # subprocess. Otherwise a long Build123D compile prevents client
                # keepalives and the result publish loses its connection.
                result = await asyncio.to_thread(execute_compile_command, command, settings)
            assert_message_size(result, settings.compile_result_max_bytes, "result")
            await publisher.publish_json(
                settings.compile_result_subject,
                result,
                message_id=compile_result_message_id(result),
            )
            await msg.ack()
            span.set_attribute("messaging.nats.ack_action", "ack")
            labels = {"export_format": command.export_format, "job_status": result.status}
            counter_add("tertius.compile.job.finished.count", 1, labels)
            if result.status == "failed":
                counter_add("tertius.compile.job.failed.count", 1, labels)
            histogram_record("tertius.compile.job.duration", elapsed_seconds(start), labels)
        except Exception as exc:
            logger.exception("Compile job failed before request ack")
            record_exception(span, exc)
            span.set_attribute("messaging.nats.ack_action", "nak")
            counter_add(
                "tertius.compile.job.failed.count",
                1,
                {"export_format": command.export_format, "job_status": "worker_error"},
            )
            histogram_record(
                "tertius.compile.job.duration",
                elapsed_seconds(start),
                {"export_format": command.export_format, "job_status": "worker_error"},
            )
            await msg.nak()


def execute_compile_command(
    command: CompileCommand,
    settings,
    binary_files: dict[str, bytes] | None = None,
) -> CompileResultPayload:
    started_at = now_utc()
    if not command.files:
        return _failed_result(
            command,
            started_at,
            error="Compile command source bundle is empty",
            error_code="missing_snapshot",
            user_message="Compile failed because the submitted source snapshot is missing. Try again.",
            retryable=True,
        )

    files = {file.filename: file.content for file in command.files}
    with hydrate_project_files(files, binary_files) as project_dir:
        result = run_compile_sandbox(
            project_dir,
            command.export_format,
            quality=command.quality,
            timeout_seconds=settings.compile_timeout_seconds,
        )
        if not result.success:
            error = result.error or result.stderr or "Compile failed"
            return _failed_result(
                command,
                started_at,
                error=error,
                error_code=_error_code(error),
                user_message=_user_message(error),
                retryable=True,
                max_bytes=settings.compile_result_max_bytes,
            )

        if result.output_path is None:
            return _failed_result(
                command,
                started_at,
                error="Compile succeeded without an output artifact",
                error_code="missing_artifact",
                user_message="Compile failed before an artifact was produced. Try again.",
                retryable=True,
            )
        output_bytes = result.output_path.read_bytes()
        structural_manifest_json = None
        bom_manifest_json = None
        manifest_path = getattr(result, "structural_manifest_path", None)
        if manifest_path is not None:
            try:
                declaration = json.loads(manifest_path.read_text(encoding="utf-8"))
                design_hash = hashlib.sha256(
                    files["design.py"].encode("utf-8")
                ).hexdigest()
                capture_project_structural_declaration(
                    declaration,
                    project_name="compiled-project",
                    design_hash=design_hash,
                    capture_detail="Compile-time structural validation.",
                )
                compiled_manifest = CompiledStructuralManifest(
                    source_hash=runtime_files_hash(files),
                    structural_source_hash=structural_runtime_files_hash(files),
                    design_hash=design_hash,
                    declaration=declaration,
                )
                structural_manifest_json = compiled_manifest.model_dump_json()
            except (KeyError, OSError, TypeError, ValueError) as exc:
                return _failed_result(
                    command,
                    started_at,
                    error=f"Invalid compiled structural manifest: {exc}",
                    error_code="invalid_structural_manifest",
                    user_message=(
                        "Compile produced invalid structural metadata. "
                        "Fix the structural catalogue or design declaration."
                    ),
                    retryable=False,
                )
        bom_manifest_path = getattr(result, "bom_manifest_path", None)
        if bom_manifest_path is not None:
            try:
                bom_manifest = json.loads(
                    bom_manifest_path.read_text(encoding="utf-8")
                )
                if not isinstance(bom_manifest, dict):
                    raise ValueError("manifest root must be a JSON object")
                bom_manifest["source_snapshot_hash"] = runtime_files_hash(files)
                bom_manifest_json = json.dumps(
                    bom_manifest,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            except (OSError, TypeError, ValueError) as exc:
                return _failed_result(
                    command,
                    started_at,
                    error=f"Invalid compiled BoM manifest: {exc}",
                    error_code="invalid_bom_manifest",
                    user_message=(
                        "Compile produced invalid Procurement metadata. "
                        "Fix the BOM declarations in the design or component library."
                    ),
                    retryable=False,
                )

    is_compressed = False
    payload_bytes = output_bytes

    # Compress the artifact if it might reduce payload size over NATS
    compressed_bytes = gzip.compress(output_bytes)
    if len(compressed_bytes) < len(output_bytes):
        payload_bytes = compressed_bytes
        is_compressed = True

    success = CompileResultPayload(
        job_id=command.job_id,
        tenant_id=command.tenant_id,
        project_id=command.project_id,
        export_format=command.export_format,
        status="succeeded",
        artifact_content_base64=base64.b64encode(payload_bytes).decode("ascii"),
        artifact_byte_size=len(output_bytes),  # original uncompressed size
        artifact_content_type=None,
        structural_manifest_json=structural_manifest_json,
        bom_manifest_json=bom_manifest_json,
        is_compressed=is_compressed,
        worker_started_at=started_at,
        worker_finished_at=now_utc(),
    )

    try:
        assert_message_size(success, settings.compile_result_max_bytes, "result")
        return success
    except ValueError as exc:
        return _failed_result(
            command,
            started_at,
            error=str(exc),
            error_code="artifact_too_large",
            user_message="Compile succeeded but the artifact is too large to return.",
            retryable=False,
        )


async def run_once() -> int:
    settings = get_settings()
    configure_telemetry(settings, "tertius-compile-job")
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_compile_stream(nc, settings)
        publisher = NatsPublisher(js)
        subscription = await pull_compile_subscription(js, settings)
        try:
            messages = await subscription.fetch(batch=1, timeout=5)
        except TimeoutError:
            return 0

        object_store = None
        for msg in messages:
            try:
                command = CompileCommand.model_validate_json(msg.data)
            except Exception:
                command = None
            if command is not None and command.assets and object_store is None:
                object_store = await open_compile_sidecar_store(js, settings)
            await handle_compile_request_message(msg, publisher, settings, object_store)
        return 0
    finally:
        await nc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run_once()))


def _failed_result(
    command: CompileCommand,
    started_at,
    error: str,
    error_code: str,
    user_message: str,
    retryable: bool,
    max_bytes: int | None = None,
) -> CompileResultPayload:
    result = CompileResultPayload(
        job_id=command.job_id,
        tenant_id=command.tenant_id,
        project_id=command.project_id,
        export_format=command.export_format,
        status="failed",
        error=error,
        error_code=error_code,
        user_message=user_message,
        retryable=retryable,
        worker_started_at=started_at,
        worker_finished_at=now_utc(),
    )
    if max_bytes is None or serialized_message_size(result) <= max_bytes:
        return result

    suffix = "[truncated]"
    low = 0
    high = len(error)
    best = suffix
    while low <= high:
        mid = (low + high) // 2
        candidate_error = f"{error[:mid]}{suffix}"
        candidate = result.model_copy(update={"error": candidate_error})
        if serialized_message_size(candidate) <= max_bytes:
            best = candidate_error
            low = mid + 1
        else:
            high = mid - 1
    return result.model_copy(update={"error": best})


def _error_code(error: str) -> str:
    if "killed" in error.lower() and "memory" in error.lower():
        return "worker_oom"
    if "timed out" in error.lower():
        return "timeout"
    return "sandbox_error"


def _user_message(error: str) -> str:
    if "killed" in error.lower() and "memory" in error.lower():
        return "Compile ran out of memory while building the model. Try simplifying the model or exporting a smaller format."
    if "timed out" in error.lower():
        return "Compile timed out after 10 minutes. Try again."
    return "Compile failed. Fix the model source and try again."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
