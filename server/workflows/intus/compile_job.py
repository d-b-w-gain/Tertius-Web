from __future__ import annotations

import asyncio
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
)
from core.compile_artifacts import (
    WORKBENCH_ARTIFACT_KINDS,
    compile_bundle_digest,
    encode_compile_artifact,
    validate_compile_bundle,
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
    ObjectStoreUnavailableError,
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

        counter_add(
            "tertius.compile.job.started.count",
            1,
            {"export_format": command.export_format},
        )
        start = perf_counter()
        try:
            binary_files: dict[str, bytes] = {}
            if command.assets:
                if object_store is None:
                    result = _failed_result(
                        command,
                        now_utc(),
                        error="Compile binary asset transport is unavailable",
                        error_code="binary_asset_unavailable",
                        user_message="Compile input storage is temporarily unavailable. Try again.",
                        retryable=True,
                    )
                else:
                    try:
                        for asset in command.assets:
                            binary_files[
                                asset.logical_filename
                            ] = await object_store.get(asset.object_ref)
                    except ObjectStoreUnavailableError as exc:
                        result = _failed_result(
                            command,
                            now_utc(),
                            error=str(exc),
                            error_code="binary_asset_unavailable",
                            user_message=(
                                "Compile input storage is temporarily unavailable. Try again."
                            ),
                            retryable=True,
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
                result = await asyncio.to_thread(
                    execute_compile_command, command, settings
                )
            assert_message_size(
                result,
                settings.compile_result_max_bytes,
                "result",
                compress=True,
            )
            await publisher.publish_json(
                settings.compile_result_subject,
                result,
                message_id=compile_result_message_id(result),
                compress=True,
            )
            await msg.ack()
            span.set_attribute("messaging.nats.ack_action", "ack")
            labels = {
                "export_format": command.export_format,
                "job_status": result.status,
            }
            counter_add("tertius.compile.job.finished.count", 1, labels)
            if result.status == "failed":
                counter_add("tertius.compile.job.failed.count", 1, labels)
            histogram_record(
                "tertius.compile.job.duration", elapsed_seconds(start), labels
            )
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
        artifact_paths = getattr(result, "artifact_paths", {})
        missing_paths = sorted(WORKBENCH_ARTIFACT_KINDS - set(artifact_paths))
        if missing_paths:
            return _failed_result(
                command,
                started_at,
                error=f"Compile completed without required artifacts: {missing_paths}",
                error_code="missing_artifact_bundle",
                user_message=(
                    "Compile failed because Tertius could not finalize every workbench "
                    "from the mechanical design."
                ),
                retryable=False,
            )
        artifact_contents = {
            command.export_format: result.output_path.read_bytes(),
            **{
                kind: path.read_bytes()
                for kind, path in artifact_paths.items()
                if kind in WORKBENCH_ARTIFACT_KINDS
            },
        }
        try:
            compiled_design = json.loads(artifact_contents["compiled_design"])
            if not isinstance(compiled_design, dict):
                raise ValueError("compiled-design root must be a JSON object")
            if compiled_design.get("schema_version") != "1.0":
                raise ValueError("compiled-design schema_version must be '1.0'")
            if not str(compiled_design.get("compiled_design_digest") or ""):
                raise ValueError("compiled-design digest is missing")
            compiled_design["source_snapshot_hash"] = runtime_files_hash(files)
            artifact_contents["compiled_design"] = json.dumps(
                compiled_design,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (OSError, TypeError, ValueError) as exc:
            return _failed_result(
                command,
                started_at,
                error=f"Invalid compiled-design graph: {exc}",
                error_code="invalid_compiled_design",
                user_message=(
                    "Compile produced inconsistent mechanical/workbench data. "
                    "Fix the product library or physical design definition."
                ),
                retryable=False,
            )

    artifacts = [
        encode_compile_artifact(kind, content)
        for kind, content in sorted(artifact_contents.items())
    ]
    bundle_digest = compile_bundle_digest(artifacts)
    try:
        validate_compile_bundle(
            artifacts,
            export_format=command.export_format,
            expected_bundle_digest=bundle_digest,
        )
    except ValueError as exc:
        return _failed_result(
            command,
            started_at,
            error=f"Invalid compile artifact bundle: {exc}",
            error_code="invalid_artifact_bundle",
            user_message=(
                "Compile produced inconsistent mechanical/workbench data. "
                "Fix the product library or physical design definition."
            ),
            retryable=False,
        )

    success = CompileResultPayload(
        job_id=command.job_id,
        tenant_id=command.tenant_id,
        project_id=command.project_id,
        export_format=command.export_format,
        status="succeeded",
        artifacts=artifacts,
        bundle_digest=bundle_digest,
        worker_started_at=started_at,
        worker_finished_at=now_utc(),
    )

    try:
        assert_message_size(
            success,
            settings.compile_result_max_bytes,
            "result",
            compress=True,
        )
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
                try:
                    object_store = await open_compile_sidecar_store(js, settings)
                except ObjectStoreUnavailableError:
                    logger.exception("Compile sidecar Object Store is unavailable")
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
