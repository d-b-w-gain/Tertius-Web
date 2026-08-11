from __future__ import annotations

import asyncio
import hashlib
import logging
from time import perf_counter

from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind
from pydantic import ValidationError

from core.config import get_settings
from core.import_3mf_messages import (
    Import3mfCommand,
    Import3mfProgress,
    Import3mfResult,
)
from core.nats_client import (
    NatsPublisher,
    Publisher,
    connect_nats,
    ensure_import_stream,
    ensure_project_object_store,
    extract_nats_context,
    pull_import_request_subscription,
)
from core.object_store import (
    ObjectIntegrityError,
    ObjectNotFoundError,
    ObjectStoreUnavailableError,
    ProjectObjectStore,
)
from core.project_assets import public_manifest_summary
from core.telemetry import (
    configure_telemetry,
    counter_add,
    elapsed_seconds,
    histogram_record,
)
from workflows.intus.import_3mf_converter import (
    Import3mfError,
    run_converter_subprocess,
)


logger = logging.getLogger(__name__)
_PUBLISH_ATTEMPTS = 3
_SAFE_FAILURES: dict[str, str] = {
    "invalid_3mf_archive": "The file is not a safe 3MF archive.",
    "invalid_3mf_geometry": "The 3MF geometry is invalid or unsupported.",
    "3mf_resource_limit": "The 3MF exceeds an import resource limit.",
    "3mf_conversion_timeout": "The 3MF conversion timed out.",
    "conversion_failed": "The 3MF conversion failed safely.",
    "source_integrity": "The uploaded 3MF could not be verified.",
    "object_integrity": "The converted 3MF objects could not be verified.",
}


def _bounded_attributes(
    *, status: str, error_code: str | None = None
) -> dict[str, str]:
    attributes = {"operation": "import_3mf.worker", "status": status}
    if error_code is not None:
        attributes["failure_category"] = error_code
    return attributes


def _hashed_message_id(prefix: str, command: Import3mfCommand, suffix: str = "") -> str:
    identity = f"{command.job_id}:{command.attempt}:{command.execution_id}:{suffix}"
    digest = hashlib.sha256(identity.encode("ascii")).hexdigest()[:32]
    return f"{prefix}:{digest}"


async def execute_import_command(
    command: Import3mfCommand,
    object_store: ProjectObjectStore,
    settings,
    report_progress=None,
) -> Import3mfResult:
    started = perf_counter()

    async def report(stage, percent: int) -> None:
        if report_progress is not None:
            await report_progress(
                Import3mfProgress.for_command(command, stage=stage, percent=percent)
            )

    await report("validating", 5)
    try:
        source = await object_store.get(command.source)
    except ObjectIntegrityError as exc:
        raise Import3mfError(
            "source_integrity", _SAFE_FAILURES["source_integrity"]
        ) from exc
    await report("converting", 20)
    output = await asyncio.to_thread(
        run_converter_subprocess,
        source,
        settings.import_3mf_timeout_seconds,
    )
    await report("persisting", 90)
    brep_ref = await object_store.put(output.brep_bytes)
    manifest_bytes = output.manifest.model_dump_json().encode("utf-8")
    manifest_ref = await object_store.put(manifest_bytes)
    return Import3mfResult.success_for(
        command,
        brep=brep_ref,
        manifest=manifest_ref,
        summary=public_manifest_summary(output.manifest),
        duration_ms=min(300_000, round(elapsed_seconds(started) * 1000)),
    )


async def _publish_with_retry(
    publisher: Publisher,
    subject: str,
    message,
    *,
    message_id: str,
) -> None:
    for attempt in range(_PUBLISH_ATTEMPTS):
        try:
            await publisher.publish_json(subject, message, message_id=message_id)
            return
        except Exception:
            if attempt + 1 == _PUBLISH_ATTEMPTS:
                raise
            await asyncio.sleep(0.1 * (2**attempt))


async def _heartbeat(msg, ack_wait_seconds: float) -> None:
    interval = max(0.1, min(30.0, ack_wait_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        await msg.in_progress()


async def handle_import_request_message(
    msg,
    object_store: ProjectObjectStore,
    publisher: Publisher,
    settings,
) -> None:
    try:
        if len(msg.data) > settings.import_3mf_message_max_bytes:
            raise ValueError("oversize")
        command = Import3mfCommand.model_validate_json(msg.data)
    except ValidationError, ValueError:
        logger.warning("Rejected invalid 3MF import command")
        await msg.term()
        return

    headers = getattr(msg, "headers", None)
    if headers is not None:
        context = extract_nats_context(headers)
    else:
        carrier = {
            key: value
            for key, value in {
                "traceparent": command.traceparent,
                "tracestate": command.tracestate,
            }.items()
            if value is not None
        }
        context = propagate.extract(carrier)

    with trace.get_tracer(__name__).start_as_current_span(
        "import_3mf.command.consume",
        context=context,
        kind=SpanKind.CONSUMER,
        attributes=_bounded_attributes(status="started"),
    ):
        heartbeat = asyncio.create_task(
            _heartbeat(msg, settings.import_3mf_ack_wait_seconds)
        )
        started = perf_counter()

        async def report(progress: Import3mfProgress) -> None:
            await _publish_with_retry(
                publisher,
                settings.import_3mf_result_subject,
                progress,
                message_id=_hashed_message_id(
                    "import-progress", command, f"{progress.stage}:{progress.percent}"
                ),
            )

        async def process_and_publish() -> Import3mfResult:
            try:
                produced = await execute_import_command(
                    command, object_store, settings, report
                )
            except Import3mfError as exc:
                code = exc.code if exc.code in _SAFE_FAILURES else "conversion_failed"
                produced = Import3mfResult.failure_for(
                    command,
                    error_code=code,
                    user_message=_SAFE_FAILURES[code],
                    duration_ms=min(300_000, round(elapsed_seconds(started) * 1000)),
                )
            except ObjectIntegrityError:
                produced = Import3mfResult.failure_for(
                    command,
                    error_code="object_integrity",
                    user_message=_SAFE_FAILURES["object_integrity"],
                    duration_ms=min(300_000, round(elapsed_seconds(started) * 1000)),
                )
            if (
                len(produced.model_dump_json().encode("utf-8"))
                > settings.import_3mf_message_max_bytes
            ):
                raise ValueError("result envelope exceeds configured limit")
            await _publish_with_retry(
                publisher,
                settings.import_3mf_result_subject,
                produced,
                message_id=_hashed_message_id("import-result", command),
            )
            return produced

        operation = asyncio.create_task(process_and_publish())
        try:
            done, _ = await asyncio.wait(
                {operation, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                await heartbeat
                raise RuntimeError("heartbeat ended unexpectedly")
            result = await operation
        except asyncio.CancelledError:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            try:
                await msg.nak()
            finally:
                raise
        except ObjectNotFoundError, ObjectStoreUnavailableError:
            logger.warning("3MF import object transport unavailable before result")
            await msg.nak()
            return
        except Exception:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            logger.warning("3MF import request failed before result publication")
            await msg.nak()
            return
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

        await msg.ack()
        labels = _bounded_attributes(
            status=result.status,
            error_code=result.error_code,
        )
        counter_add("tertius.import_3mf.worker.completed.count", 1, labels)
        histogram_record(
            "tertius.import_3mf.worker.duration",
            elapsed_seconds(started),
            labels,
        )


async def run_once() -> int:
    settings = get_settings()
    configure_telemetry(settings, "tertius-import-3mf-job")
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_import_stream(nc, settings)
        raw_store = await ensure_project_object_store(nc, settings)
        object_store = ProjectObjectStore(
            raw_store,
            settings.project_asset_object_bucket,
            max_object_bytes=settings.project_asset_object_max_bytes,
        )
        subscription = await pull_import_request_subscription(js, settings)
        try:
            messages = await subscription.fetch(batch=1, timeout=5)
        except TimeoutError:
            return 0
        publisher = NatsPublisher(js)
        for msg in messages:
            await handle_import_request_message(msg, object_store, publisher, settings)
        return 0
    finally:
        await nc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run_once()))


if __name__ == "__main__":
    main()
