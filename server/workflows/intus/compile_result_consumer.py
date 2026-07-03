from __future__ import annotations

import asyncio
import base64
import gzip
import logging
from datetime import timedelta
from time import perf_counter

from opentelemetry.trace import SpanKind
from sqlalchemy import select

from core.compile_messages import (
    CompileCommand,
    CompileResultPayload,
    CompileSourceFile,
    assert_message_size,
)
from core.config import get_settings
from core.db import SessionLocal
from core.models import CompileJob, CompileJobFile, now_utc
from core.nats_client import (
    NatsPublisher,
    Publisher,
    connect_nats,
    ensure_compile_stream,
    extract_nats_context,
    pull_compile_result_subscription,
)
from core.telemetry import counter_add, elapsed_seconds, get_tracer, histogram_record, record_exception
from core.repositories import CompileRepository
from core.billing import compute_cost_cents, get_format_multiplier


logger = logging.getLogger(__name__)


def _record_usage_if_applicable(db, result: CompileResultPayload, job: CompileJob, settings, artifact_byte_size: int = 0) -> None:
    started_at = job.claimed_at or result.worker_started_at
    finished_at = job.finished_at if job.claimed_at is not None else result.worker_finished_at
    if started_at is None or finished_at is None:
        logger.warning("Skipping usage record for job %s: missing timing data", job.id)
        return
    duration = (finished_at - started_at).total_seconds()
    if duration < 0:
        logger.warning("Clamping negative usage duration for job %s", job.id)
        duration = 0.0
    cost = compute_cost_cents(duration, job.export_format, settings)
    repo = CompileRepository(db, job.tenant_id)
    repo.record_usage(
        project_id=job.project_id,
        compile_job_id=job.id,
        requested_by=job.requested_by,
        export_format=job.export_format,
        status=job.status,
        compute_duration_seconds=duration,
        artifact_byte_size=artifact_byte_size,
        cost_cents=cost,
        base_rate_cents_per_hour=settings.billing_rate_cents_per_hour,
        format_multiplier=get_format_multiplier(job.export_format, settings),
    )


def apply_compile_result(db, result: CompileResultPayload, settings) -> bool:
    repo = CompileRepository(db, result.tenant_id)
    job = repo.get_job_for_result(result)
    if job is None:
        db.rollback()
        return False

    if job.status in {"succeeded", "failed"}:
        db.rollback()
        return False

    if result.status == "failed":
        repo.finish_job(
            job,
            "failed",
            error=result.error,
            error_code=result.error_code,
            user_message=result.user_message,
            retryable=result.retryable,
        )
        _record_usage_if_applicable(db, result, job, settings)
        db.commit()
        return True

    try:
        artifact_bytes = _decode_artifact(result)
    except ValueError as exc:
        repo.finish_job(
            job,
            "failed",
            error=str(exc),
            error_code="invalid_result",
            user_message="Compile result could not be verified. Try again.",
            retryable=True,
        )
        _record_usage_if_applicable(db, result, job, settings)
        db.commit()
        return True
    max_decompressed_bytes = 256 * 1024 * 1024  # 256MB limit for database/cache storage
    if len(artifact_bytes) > max_decompressed_bytes:
        repo.finish_job(
            job,
            "failed",
            error=f"Compile artifact is {len(artifact_bytes)} bytes, above {max_decompressed_bytes} byte limit",
            error_code="artifact_too_large",
            user_message="Compile succeeded but the decompressed artifact is too large to return.",
            retryable=False,
        )
        _record_usage_if_applicable(db, result, job, settings, artifact_byte_size=len(artifact_bytes))
        db.commit()
        return True

    artifact = repo.record_artifact(
        job.project_id,
        job.id,
        job.export_format,
        artifact_bytes,
        content_type=result.artifact_content_type,
    )
    repo.finish_job(job, "succeeded")
    _record_usage_if_applicable(db, result, job, settings, artifact_byte_size=len(artifact_bytes))
    pruned = repo.prunable_artifacts(job.project_id, job.export_format, max(1, settings.artifact_retention_limit))
    repo.delete_artifacts(pruned)
    db.commit()
    return artifact.id is not None


async def handle_compile_result_message(msg, db, settings) -> None:
    context = extract_nats_context(getattr(msg, "headers", None))
    subject = getattr(msg, "subject", "tertius.compile.result")
    attributes = {
        "messaging.system": "nats",
        "messaging.destination.name": subject,
        "messaging.operation.name": "process",
        "nats_subject": subject,
    }
    with get_tracer(__name__).start_as_current_span(
        "NATS consume tertius.compile.result",
        context=context,
        kind=SpanKind.CONSUMER,
        attributes=attributes,
    ) as span:
        try:
            result = CompileResultPayload.model_validate_json(msg.data)
        except Exception as exc:
            logger.exception("Invalid compile result JSON")
            record_exception(span, exc)
            span.set_attribute("messaging.nats.ack_action", "term")
            await msg.term()
            return

        span.set_attribute("tertius.export_format", result.export_format)
        span.set_attribute("tertius.job_status", result.status)
        labels = {"export_format": result.export_format, "job_status": result.status}
        start = perf_counter()
        try:
            apply_compile_result(db, result, settings)
            await msg.ack()
            span.set_attribute("messaging.nats.ack_action", "ack")
            counter_add("tertius.compile.result.processed.count", 1, labels)
            histogram_record("tertius.compile.result.processing.duration", elapsed_seconds(start), labels)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to apply compile result")
            record_exception(span, exc)
            span.set_attribute("messaging.nats.ack_action", "nak")
            counter_add("tertius.compile.result.error.count", 1, labels)
            histogram_record("tertius.compile.result.processing.duration", elapsed_seconds(start), labels)
            await msg.nak()


async def republish_stale_queued_jobs(db, publisher: Publisher, settings, older_than_seconds: int = 60) -> int:
    with get_tracer(__name__).start_as_current_span("compile.republish_stale_queued_jobs") as span:
        cutoff = now_utc() - timedelta(seconds=older_than_seconds)
        jobs = db.scalars(
            select(CompileJob)
            .where(
                CompileJob.status == "queued",
                CompileJob.error_code == "publish_pending",
                CompileJob.created_at < cutoff,
            )
            .order_by(CompileJob.created_at)
            .limit(50)
        ).all()
        republished = 0
        for job in jobs:
            files = db.scalars(
                select(CompileJobFile)
                .where(
                    CompileJobFile.compile_job_id == job.id,
                    CompileJobFile.tenant_id == job.tenant_id,
                    CompileJobFile.project_id == job.project_id,
                )
                .order_by(CompileJobFile.filename)
            ).all()
            if not files:
                CompileRepository(db, job.tenant_id).finish_job(
                    job,
                    "failed",
                    error="Compile job source snapshot is missing",
                    error_code="missing_snapshot",
                    user_message="Compile failed because the submitted source snapshot is missing. Try again.",
                    retryable=True,
                )
                db.commit()
                counter_add("tertius.compile.job.failed.count", 1, {"job_status": "missing_snapshot"})
                logger.warning("Marked stale queued compile job %s failed because it has no source snapshot", job.id)
                continue

            request_id = f"compile-request:{job.id}"
            command = CompileCommand(
                job_id=job.id,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                requested_by=job.requested_by,
                export_format=job.export_format,
                created_at=job.created_at,
                files=[CompileSourceFile(filename=file.filename, content=file.content) for file in files],
                request_id=request_id,
                originating_llm_edit_job_id=job.originating_llm_edit_job_id,
            )
            try:
                assert_message_size(command, settings.compile_request_max_bytes, "request")
            except ValueError as exc:
                CompileRepository(db, job.tenant_id).finish_job(
                    job,
                    "failed",
                    error=str(exc),
                    error_code="source_bundle_too_large",
                    user_message="Compile source is too large to queue. Split the model into smaller files.",
                    retryable=False,
                )
                db.commit()
                counter_add("tertius.compile.job.failed.count", 1, {"job_status": "source_bundle_too_large"})
                logger.warning("Marked stale queued compile job %s failed because its source snapshot is too large", job.id)
                continue

            await publisher.publish_json(settings.compile_request_subject, command, message_id=request_id)
            republished += 1
        db.rollback()
        span.set_attribute("tertius.compile.republished_count", republished)
        if republished:
            counter_add("tertius.compile.job.started.count", republished, {"job_status": "republished"})
        return republished


def fail_stale_running_jobs(db) -> int:
    settings = get_settings()
    stale_after_seconds = settings.compile_timeout_seconds + 30
    tenant_ids = db.scalars(select(CompileJob.tenant_id).where(CompileJob.status == "running").distinct()).all()
    count = 0
    for tenant_id in tenant_ids:
        repo = CompileRepository(db, tenant_id)
        for job in repo.stale_running_jobs(older_than_seconds=stale_after_seconds):
            repo.finish_job(
                job,
                "failed",
                error="Compile worker stopped before reporting a result",
                error_code="worker_lost",
                user_message=(
                    "Compile worker stopped unexpectedly. The model may have exceeded available memory "
                    "or the worker was restarted."
                ),
                retryable=True,
            )
            count += 1
    if count:
        db.commit()
    else:
        db.rollback()
    return count


async def run_result_consumer(stop_event: asyncio.Event | None = None) -> None:
    settings = get_settings()
    while stop_event is None or not stop_event.is_set():
        nc = None
        try:
            nc = await connect_nats(settings.nats_url)
            js = await ensure_compile_stream(nc, settings)
            publisher = NatsPublisher(js)
            subscription = await pull_compile_result_subscription(js, settings)
            last_recovery = asyncio.get_running_loop().time()
            logger.info(
                "Compile result consumer subscribed to %s via durable consumer %s",
                settings.compile_result_subject,
                settings.compile_result_consumer,
            )

            while stop_event is None or not stop_event.is_set():
                try:
                    now = asyncio.get_running_loop().time()
                    if now - last_recovery >= 60:
                        with SessionLocal() as db:
                            try:
                                republished = await republish_stale_queued_jobs(db, publisher, settings)
                                if republished:
                                    logger.warning("Republished %s stale queued compile jobs", republished)
                                failed = fail_stale_running_jobs(db)
                                if failed:
                                    logger.warning("Marked %s stale running compile jobs failed", failed)
                            except Exception:
                                logger.exception("Compile result consumer stale queued recovery failed")
                        last_recovery = now

                    messages = await subscription.fetch(batch=1, timeout=5)
                except TimeoutError:
                    continue
                except Exception:
                    logger.exception("Compile result consumer fetch failed")
                    await asyncio.sleep(2)
                    break

                for msg in messages:
                    with SessionLocal() as db:
                        await handle_compile_result_message(msg, db, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Compile result consumer setup failed; retrying")
            if stop_event is not None and stop_event.is_set():
                break
            await asyncio.sleep(2)
        finally:
            if nc is not None:
                await nc.close()


def _decode_artifact(result: CompileResultPayload) -> bytes:
    if not result.artifact_content_base64:
        raise ValueError("succeeded compile result did not include artifact content")
    artifact_bytes = base64.b64decode(result.artifact_content_base64.encode("ascii"), validate=True)

    if result.is_compressed:
        artifact_bytes = gzip.decompress(artifact_bytes)

    if result.artifact_byte_size is not None and result.artifact_byte_size != len(artifact_bytes):
        raise ValueError("compile result artifact byte size did not match decoded content")
    return artifact_bytes
