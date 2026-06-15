from __future__ import annotations

import asyncio
import base64
import logging
from datetime import timedelta

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
    connect_nats,
    ensure_compile_stream,
    pull_compile_result_subscription,
)
from core.repositories import CompileRepository, UsageRepository
from core.billing import compute_cost_cents, get_format_multiplier


logger = logging.getLogger(__name__)


def _record_usage_if_applicable(db, result: CompileResultPayload, job: CompileJob, settings, artifact_byte_size: int = 0) -> None:
    if result.worker_started_at is None or result.worker_finished_at is None:
        logger.warning("Skipping usage record for job %s: missing timing data", job.id)
        return
    duration = (result.worker_finished_at - result.worker_started_at).total_seconds()
    if duration < 0:
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
    if len(artifact_bytes) > settings.compile_result_max_bytes:
        repo.finish_job(
            job,
            "failed",
            error=f"Compile artifact is {len(artifact_bytes)} bytes, above {settings.compile_result_max_bytes} byte limit",
            error_code="artifact_too_large",
            user_message="Compile succeeded but the artifact is too large to return.",
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
    try:
        result = CompileResultPayload.model_validate_json(msg.data)
    except Exception:
        logger.exception("Invalid compile result JSON")
        await msg.term()
        return

    try:
        apply_compile_result(db, result, settings)
        await msg.ack()
    except Exception:
        db.rollback()
        logger.exception("Failed to apply compile result")
        await msg.nak()


async def republish_stale_queued_jobs(db, publisher: NatsPublisher, settings, older_than_seconds: int = 60) -> int:
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
            logger.warning("Marked stale queued compile job %s failed because its source snapshot is too large", job.id)
            continue

        await publisher.publish_json(settings.compile_request_subject, command, message_id=request_id)
        republished += 1
    db.rollback()
    return republished


def fail_stale_running_jobs(db) -> int:
    tenant_ids = db.scalars(select(CompileJob.tenant_id).where(CompileJob.status == "running").distinct()).all()
    count = 0
    for tenant_id in tenant_ids:
        repo = CompileRepository(db, tenant_id)
        for job in repo.stale_running_jobs():
            repo.finish_job(
                job,
                "failed",
                error="Compile job lease expired before a result was recorded",
                error_code="stale_running",
                user_message="Compile did not finish before the worker stopped. Try again.",
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
    if result.artifact_byte_size is not None and result.artifact_byte_size != len(artifact_bytes):
        raise ValueError("compile result artifact byte size did not match decoded content")
    return artifact_bytes
