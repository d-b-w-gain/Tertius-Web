from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from core.compile_messages import CompileCommand, CompileResultEvent
from core.config import get_settings
from core.db import SessionLocal
from core.models import CompileJob, now_utc
from core.nats_client import NatsPublisher, connect_nats, ensure_compile_stream, pull_compile_subscription
from core.repositories import CompileRepository
from workflows.intus.compile_executor import execute_compile_job


logger = logging.getLogger(__name__)


async def handle_compile_message(msg, db, publisher: NatsPublisher, settings) -> None:
    try:
        command = CompileCommand.model_validate_json(msg.data)
    except Exception:
        logger.exception("Invalid compile command JSON")
        await msg.term()
        return

    repo = CompileRepository(db, command.tenant_id)
    job = repo.get_job_for_command(command)
    if job is None:
        job = db.get(CompileJob, command.job_id)
        if job is not None:
            repo = CompileRepository(db, job.tenant_id)
            repo.finish_job(
                job,
                "failed",
                error="Compile command did not match the persisted job identity",
                error_code="invalid_command",
                user_message="Compile failed before it could start. Try again.",
                retryable=False,
            )
            db.commit()
            event = CompileResultEvent(
                job_id=job.id,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                status="failed",
                export_format=job.export_format,
                error_code=job.error_code,
                user_message=job.user_message,
                error=job.error,
                retryable=job.retryable,
                finished_at=job.finished_at or now_utc(),
            )
            await publisher.publish_json(settings.compile_failed_subject, event)
        await msg.ack()
        return

    claimed = repo.claim_job_for_command(command, lease_seconds=settings.compile_ack_wait_seconds)
    if claimed is None:
        await msg.ack()
        return
    db.commit()

    event = execute_compile_job(
        db,
        claimed.id,
        claim_token=claimed.claim_token,
        timeout_seconds=settings.compile_timeout_seconds,
        artifact_retention_limit=settings.artifact_retention_limit,
    )
    if event is None:
        await msg.ack()
        return

    subject = settings.compile_succeeded_subject if event.status == "succeeded" else settings.compile_failed_subject
    await publisher.publish_json(subject, event)
    await msg.ack()


async def republish_stale_queued_jobs(db, publisher: NatsPublisher, settings, older_than_seconds: int = 60) -> int:
    tenant_ids = db.scalars(select(CompileJob.tenant_id).where(CompileJob.status == "queued").distinct()).all()
    count = 0
    for tenant_id in tenant_ids:
        repo = CompileRepository(db, tenant_id)
        for job in repo.stale_queued_jobs(older_than_seconds=older_than_seconds):
            command = CompileCommand(
                job_id=job.id,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                requested_by=job.requested_by,
                export_format=job.export_format,
                created_at=job.created_at,
            )
            await publisher.publish_json(settings.compile_request_subject, command)
            count += 1
    return count


async def run_worker() -> None:
    settings = get_settings()
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_compile_stream(nc, settings)
        publisher = NatsPublisher(js)
        subscription = await pull_compile_subscription(js, settings)
        logger.info(
            "Compile worker subscribed to %s via durable consumer %s",
            settings.compile_request_subject,
            settings.compile_worker_queue,
        )
        last_recovery = asyncio.get_running_loop().time()

        while True:
            try:
                now = asyncio.get_running_loop().time()
                if now - last_recovery >= 60:
                    with SessionLocal() as db:
                        try:
                            republished = await republish_stale_queued_jobs(db, publisher, settings, older_than_seconds=60)
                            if republished:
                                logger.warning("Republished %s stale queued compile jobs", republished)
                        except Exception:
                            logger.exception("Compile worker stale queued recovery failed")
                    last_recovery = now

                messages = await subscription.fetch(batch=1, timeout=5)
            except TimeoutError:
                continue
            except Exception:
                logger.exception("Compile worker fetch failed")
                await asyncio.sleep(2)
                continue

            for msg in messages:
                with SessionLocal() as db:
                    try:
                        await handle_compile_message(msg, db, publisher, settings)
                    except Exception:
                        logger.exception("Compile worker failed to handle message")
                        await msg.nak()
    finally:
        await nc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
