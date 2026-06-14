from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone

from core.compile_messages import CompileCommand, CompileResultPayload, assert_message_size, serialized_message_size
from core.compile_runtime import hydrate_project_files
from core.compile_sandbox import run_compile_sandbox
from core.config import get_settings
from core.nats_client import NatsPublisher, connect_nats, ensure_compile_stream, pull_compile_subscription


logger = logging.getLogger(__name__)


async def handle_compile_request_message(msg, publisher: NatsPublisher, settings) -> None:
    try:
        command = CompileCommand.model_validate_json(msg.data)
    except Exception:
        logger.exception("Invalid compile command JSON")
        await msg.term()
        return

    try:
        result = execute_compile_command(command, settings)
        assert_message_size(result, settings.compile_result_max_bytes, "result")
        await publisher.publish_json(
            settings.compile_result_subject,
            result,
            message_id=_result_message_id(result),
        )
        await msg.ack()
    except Exception:
        logger.exception("Compile job failed before request ack")
        await msg.nak()


def execute_compile_command(command: CompileCommand, settings) -> CompileResultPayload:
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
    with hydrate_project_files(files) as project_dir:
        result = run_compile_sandbox(
            project_dir,
            command.export_format,
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

    success = CompileResultPayload(
        job_id=command.job_id,
        tenant_id=command.tenant_id,
        project_id=command.project_id,
        export_format=command.export_format,
        status="succeeded",
        artifact_content_base64=base64.b64encode(output_bytes).decode("ascii"),
        artifact_byte_size=len(output_bytes),
        artifact_content_type=None,
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
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_compile_stream(nc, settings)
        publisher = NatsPublisher(js)
        subscription = await pull_compile_subscription(js, settings)
        try:
            messages = await subscription.fetch(batch=1, timeout=5)
        except TimeoutError:
            return 0

        for msg in messages:
            await handle_compile_request_message(msg, publisher, settings)
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
    if "timed out" in error.lower():
        return "timeout"
    return "sandbox_error"


def _user_message(error: str) -> str:
    if "timed out" in error.lower():
        return "Compile timed out after 10 minutes. Try again."
    return "Compile failed. Fix the model source and try again."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _result_message_id(result: CompileResultPayload) -> str:
    return f"compile-result:{result.job_id}:{result.status}"


if __name__ == "__main__":
    main()
