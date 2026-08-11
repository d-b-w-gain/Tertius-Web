from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import perf_counter

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from pydantic import ValidationError
from sqlalchemy import select

from core.config import get_settings
from core.db import SessionLocal
from core.import_3mf_messages import Import3mfProgress, Import3mfResult
from core.models import ProjectAsset, ProjectImportJob, now_utc
from core.nats_client import (
    connect_nats,
    ensure_import_stream,
    ensure_project_object_store,
    extract_nats_context,
    pull_import_result_subscription,
)
from core.object_store import (
    ObjectIntegrityError,
    ObjectNotFoundError,
    ObjectStoreUnavailableError,
    ProjectObjectStore,
)
from core.project_assets import Import3mfManifest, public_manifest_summary
from core.repositories import (
    AssetIntegrityError,
    ProjectImportRepository,
    StaleImportExecutionError,
)
from core.telemetry import counter_add, elapsed_seconds, histogram_record


logger = logging.getLogger(__name__)
_SAFE_FAILURES: dict[str, tuple[str, bool]] = {
    "invalid_3mf_archive": ("The file is not a safe 3MF archive.", False),
    "invalid_3mf_geometry": ("The 3MF geometry is invalid or unsupported.", False),
    "3mf_resource_limit": ("The 3MF exceeds an import resource limit.", False),
    "3mf_conversion_timeout": ("The 3MF conversion timed out.", True),
    "conversion_failed": ("The 3MF conversion failed safely.", True),
    "asset_integrity_error": ("The imported 3MF assets could not be verified.", True),
    "worker_lost": ("The 3MF import worker stopped unexpectedly. Try again.", True),
}


def _attributes(*, envelope: str, status: str) -> dict[str, str]:
    return {
        "operation": "import_3mf.result_consumer",
        "envelope": envelope,
        "status": status,
    }


def _parse_envelope(data: bytes, max_bytes: int):
    if len(data) > max_bytes:
        raise ValueError("oversize import envelope")
    try:
        return Import3mfProgress.model_validate_json(data)
    except ValidationError:
        return Import3mfResult.model_validate_json(data)


def _progress_tenant_id(db, progress: Import3mfProgress):
    row = db.execute(
        select(
            ProjectImportJob.tenant_id,
            ProjectImportJob.attempt,
            ProjectImportJob.execution_id,
            ProjectImportJob.status,
        ).where(ProjectImportJob.id == progress.job_id)
    ).one_or_none()
    if (
        row is None
        or row.attempt != progress.attempt
        or row.execution_id != progress.execution_id
        or row.status not in {"queued", "running"}
    ):
        return None
    return row.tenant_id


def _load_result_job(db, result: Import3mfResult):
    row = db.execute(
        select(ProjectImportJob, ProjectAsset)
        .join(
            ProjectAsset,
            (ProjectAsset.id == ProjectImportJob.source_asset_id)
            & (ProjectAsset.project_id == ProjectImportJob.project_id)
            & (ProjectAsset.tenant_id == ProjectImportJob.tenant_id),
        )
        .where(
            ProjectImportJob.id == result.job_id,
            ProjectImportJob.tenant_id == result.tenant_id,
            ProjectImportJob.project_id == result.project_id,
            ProjectImportJob.requested_by == result.user_id,
            ProjectImportJob.attempt == result.attempt,
            ProjectImportJob.execution_id == result.execution_id,
        )
    ).one_or_none()
    if row is None:
        return None
    job, source = row
    return job, source


def _mark_safe_failure(repo, result: Import3mfResult, code: str) -> None:
    message, retryable = _SAFE_FAILURES[code]
    repo.mark_failed(
        result.job_id,
        result.execution_id,
        error=code,
        error_code=code,
        user_message=message,
        retryable=retryable,
    )


async def _apply_progress(db, progress: Import3mfProgress) -> str:
    tenant_id = _progress_tenant_id(db, progress)
    if tenant_id is None:
        db.rollback()
        return "stale"
    repo = ProjectImportRepository(db, tenant_id)
    try:
        repo.mark_running(progress.job_id, progress.execution_id)
        repo.mark_progress(
            progress.job_id,
            progress.execution_id,
            progress.model_dump(mode="json"),
        )
        db.commit()
        return "applied"
    except StaleImportExecutionError, ValueError:
        db.rollback()
        return "stale"


async def _apply_result(db, result: Import3mfResult, object_store, settings) -> str:
    loaded = _load_result_job(db, result)
    if loaded is None:
        db.rollback()
        return "stale"
    job, source = loaded
    if job.status in {"succeeded", "failed"}:
        db.rollback()
        return "duplicate"

    repo = ProjectImportRepository(db, result.tenant_id)
    if (
        result.source.bucket != settings.project_asset_object_bucket
        or result.source.key != f"sha256/{result.source.sha256}"
        or source.sha256 != result.source.sha256
        or source.byte_size != result.source.byte_size
        or source.kind != "source_3mf"
    ):
        _mark_safe_failure(repo, result, "asset_integrity_error")
        db.commit()
        return "failed"

    if result.status == "failed":
        code = (
            result.error_code
            if result.error_code in _SAFE_FAILURES
            else "conversion_failed"
        )
        _mark_safe_failure(repo, result, code)
        db.commit()
        return "failed"

    assert (
        result.brep is not None
        and result.manifest is not None
        and result.summary is not None
    )
    if (
        result.brep.bucket != settings.project_asset_object_bucket
        or result.manifest.bucket != settings.project_asset_object_bucket
    ):
        _mark_safe_failure(repo, result, "asset_integrity_error")
        db.commit()
        return "failed"

    try:
        brep_content = await object_store.get(result.brep)
        manifest_content = await object_store.get(result.manifest)
    except ObjectNotFoundError, ObjectStoreUnavailableError:
        raise
    except ObjectIntegrityError:
        _mark_safe_failure(repo, result, "asset_integrity_error")
        db.commit()
        return "failed"

    try:
        manifest = Import3mfManifest.model_validate_json(manifest_content)
        if (
            public_manifest_summary(manifest) != result.summary
            or manifest.source_sha256 != result.source.sha256
            or manifest.brep_sha256 != result.brep.sha256
            or manifest.brep_byte_size != result.brep.byte_size
        ):
            raise ValueError("manifest integrity mismatch")
    except ValidationError, ValueError:
        _mark_safe_failure(repo, result, "asset_integrity_error")
        db.commit()
        return "failed"

    try:
        repo.apply_success(
            job_id=result.job_id,
            execution_id=result.execution_id,
            source_sha256=result.source.sha256,
            brep_content=brep_content,
            manifest_content=manifest_content,
            user_id=job.requested_by,
        )
    except StaleImportExecutionError, ValueError:
        db.rollback()
        return "stale"
    except AssetIntegrityError:
        _mark_safe_failure(repo, result, "asset_integrity_error")
        db.commit()
        return "failed"
    db.commit()
    return "succeeded"


async def handle_import_result_message(msg, db, object_store, settings) -> None:
    try:
        envelope = _parse_envelope(msg.data, settings.import_3mf_message_max_bytes)
    except ValidationError, ValueError:
        logger.warning("Rejected invalid 3MF import result envelope")
        await msg.term()
        return

    envelope_type = "progress" if isinstance(envelope, Import3mfProgress) else "result"
    headers = getattr(msg, "headers", None)
    context = extract_nats_context(headers) if headers is not None else None
    started = perf_counter()
    with trace.get_tracer(__name__).start_as_current_span(
        "import_3mf.result.consume",
        context=context,
        kind=SpanKind.CONSUMER,
        attributes=_attributes(envelope=envelope_type, status="processing"),
    ):
        try:
            if isinstance(envelope, Import3mfProgress):
                outcome = await _apply_progress(db, envelope)
            else:
                outcome = await _apply_result(db, envelope, object_store, settings)
        except ObjectNotFoundError, ObjectStoreUnavailableError:
            db.rollback()
            logger.warning(
                "3MF import result object transport is temporarily unavailable"
            )
            await msg.nak()
            return
        except Exception:
            db.rollback()
            logger.warning("3MF import result could not be applied")
            await msg.nak()
            return

        await msg.ack()
        labels = _attributes(envelope=envelope_type, status=outcome)
        counter_add("tertius.import_3mf.result.processed.count", 1, labels)
        histogram_record(
            "tertius.import_3mf.result.processing.duration",
            elapsed_seconds(started),
            labels,
        )


def _running_jobs(db):
    return db.scalars(
        select(ProjectImportJob).where(ProjectImportJob.status == "running")
    ).all()


def reconcile_stale_import_jobs(db, settings) -> int:
    cutoff = now_utc() - timedelta(seconds=settings.import_3mf_running_lease_seconds)
    count = 0
    for job in _running_jobs(db):
        heartbeat_at = (
            getattr(job, "heartbeat_at", None) or job.started_at or job.created_at
        )
        if heartbeat_at >= cutoff:
            continue
        try:
            failed = ProjectImportRepository(db, job.tenant_id).fail_if_stale(
                job.id,
                job.execution_id,
                cutoff=cutoff,
                error_code="worker_lost",
                user_message=_SAFE_FAILURES["worker_lost"][0],
            )
            db.commit()
            count += int(failed)
        except StaleImportExecutionError, ValueError:
            db.rollback()
    if not count:
        db.rollback()
    return count


async def run_import_result_consumer(stop_event: asyncio.Event | None = None) -> None:
    settings = get_settings()
    while stop_event is None or not stop_event.is_set():
        nc = None
        try:
            nc = await connect_nats(settings.nats_url)
            js = await ensure_import_stream(nc, settings)
            raw_store = await ensure_project_object_store(nc, settings)
            object_store = ProjectObjectStore(
                raw_store,
                settings.project_asset_object_bucket,
                max_object_bytes=settings.project_asset_object_max_bytes,
            )
            subscription = await pull_import_result_subscription(js, settings)
            while stop_event is None or not stop_event.is_set():
                try:
                    messages = await subscription.fetch(batch=1, timeout=5)
                except TimeoutError:
                    continue
                for msg in messages:
                    with SessionLocal() as db:
                        await handle_import_result_message(
                            msg, db, object_store, settings
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("3MF import result consumer setup failed; retrying")
            if stop_event is not None and stop_event.is_set():
                break
            await asyncio.sleep(2)
        finally:
            if nc is not None:
                await nc.close()


async def run_import_reconciler(
    stop_event: asyncio.Event | None = None, *, interval_seconds: float = 60
) -> None:
    while stop_event is None or not stop_event.is_set():
        settings = get_settings()
        with SessionLocal() as db:
            try:
                reconcile_stale_import_jobs(db, settings)
            except Exception:
                db.rollback()
                logger.warning("3MF stale import reconciliation failed")
        if stop_event is None:
            await asyncio.sleep(interval_seconds)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
