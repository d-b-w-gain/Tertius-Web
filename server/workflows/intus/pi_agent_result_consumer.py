import asyncio
from hashlib import sha256
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from core.billing_messages import LlmTokenUsageEvent, assert_billing_message_size, billing_usage_message_id
from core.config import get_settings
from core.db import SessionLocal
from core.models import LlmEditJob, LlmUsageRecord, Project
from core.nats_client import NatsPublisher, connect_nats, ensure_billing_stream, ensure_pi_agent_stream, extract_nats_context, pull_pi_agent_result_subscription
from core.pi_agent_messages import (
    PiAgentCommand,
    PiAgentConversationContext,
    PiAgentFileManifest,
    PiAgentResult,
    PiAgentSourceFile,
    assert_pi_agent_command_size,
    assert_pi_agent_result_size,
    pi_agent_command_message_id,
)
from core.pi_agent_telemetry import pi_agent_metric_attributes
from core.repositories import (
    FileVersionConflictError,
    LlmEditRepository,
    ProjectRepository,
    normalize_file_version,
)
from core.telemetry import counter_add, up_down_counter_add

logger = logging.getLogger(__name__)
_ACTIVE_JOBS_OBSERVED = 0
_RESULT_CONSUMER_HEARTBEAT_SECONDS = 30.0
def _metric_attributes(
    *, provider: str, model: str, status: str, failure_category: str | None, retryable: bool
) -> dict[str, str | bool]:
    return pi_agent_metric_attributes(
        operation="pi_agent.api",
        provider=provider,
        model=model,
        status=status,
        failure_category=failure_category,
        retryable=retryable,
    )


def _result_provenance(
    db: Session, result: PiAgentResult, settings
) -> tuple[str, str] | None:
    job = db.get(LlmEditJob, result.job_id)
    if job is None:
        return None
    if job.tenant_id != result.tenant_id or job.project_id != result.project_id:
        return None
    payload = job.request_payload if isinstance(job.request_payload, dict) else {}
    provider = payload.get("dispatched_provider", "openai-codex")
    model = payload.get("dispatched_model", getattr(settings, "pi_agent_model", "gpt-5.5"))
    if not isinstance(provider, str) or not isinstance(model, str):
        return None
    if result.provider != provider or result.model != model:
        return None
    return provider, model


def _record_api_terminal(
    *, provider: str, model: str, status: str, failure_category: str | None, retryable: bool
) -> None:
    counter_add(
        "tertius.pi_agent.job.terminal.count",
        1,
        pi_agent_metric_attributes(
            operation="pi_agent.api",
            provider=provider,
            model=model,
            status=status,
            failure_category=failure_category,
            retryable=retryable,
        ),
    )


def _record_result_consumer_heartbeat(settings) -> None:
    counter_add(
        "tertius.pi_agent.result_consumer.heartbeat.count",
        1,
        pi_agent_metric_attributes(
            operation="pi_agent.api",
            provider=getattr(settings, "pi_agent_provider", "openai-codex"),
            model=getattr(settings, "pi_agent_model", "gpt-5.5"),
            status="healthy",
        ),
    )


def pi_agent_billing_event_id(execution_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"pi-agent-billing:{execution_id}")


def _usage_record(
    job: LlmEditJob,
    result: PiAgentResult,
    payload: dict,
    event_id: UUID,
) -> LlmUsageRecord:
    return LlmUsageRecord(
        event_id=event_id,
        tenant_id=job.tenant_id,
        user_id=job.requested_by,
        project_id=job.project_id,
        workflow="intus",
        operation="files.llm_edit",
        provider=result.provider,
        model=result.model,
        prompt=str(payload.get("prompt", "")),
        prompt_tokens=result.usage.input_tokens,
        completion_tokens=result.usage.output_tokens,
        total_tokens=result.usage.total_tokens,
        provider_request_id=None,
        metadata_json=payload.get("metadata", {}),
        status="completed" if result.status == "succeeded" else "failed",
    )


def _result_payload(result: PiAgentResult, snapshot, changed_rows) -> dict:
    rows = {row.id: row for row in changed_rows}
    return {
        "success": result.status == "succeeded",
        "outcome": result.outcome,
        "message": result.assistant_summary,
        "provider": result.provider,
        "model": result.model,
        "usage": {
            "prompt_tokens": result.usage.input_tokens,
            "completion_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
        },
        "snapshot": ({"id": str(snapshot.id), "message": snapshot.message, "content_hash": snapshot.content_hash} if snapshot else None),
        "files": [
            {
                "id": str(edit.id),
                "filename": edit.filename,
                "content": edit.content,
                "updated_at": rows[edit.id].updated_at.isoformat(),
                "changed": True,
                "summary": result.assistant_summary,
            }
            for edit in result.changed_files
            if edit.id in rows
        ],
    }


async def apply_pi_agent_result(db: Session, result: PiAgentResult, settings, billing_publisher) -> str:
    def validate_locked_job(job: LlmEditJob | None):
        if job is None:
            return "invalid", None, None
        if job.tenant_id != result.tenant_id or job.project_id != result.project_id:
            return "invalid", None, None
        if job.status not in {"queued", "running", "succeeded", "failed"}:
            return "invalid", None, None
        payload = job.request_payload if isinstance(job.request_payload, dict) else {}
        if (
            result.provider != payload.get("dispatched_provider", "openai-codex")
            or result.model
            != payload.get(
                "dispatched_model", getattr(settings, "pi_agent_model", "gpt-5.5")
            )
        ):
            return "invalid", None, None
        try:
            manifest = [
                PiAgentFileManifest.model_validate(item)
                for item in payload.get("dispatched_manifest", [])
            ]
        except (TypeError, ValueError):
            return "invalid", None, None
        pointers = {item.id: item for item in manifest}
        if (
            not pointers
            or len(pointers) != len(manifest)
            or any(
                edit.id not in pointers
                or pointers[edit.id].filename != edit.filename
                for edit in result.changed_files
            )
        ):
            return "invalid", None, None
        state = "duplicate" if job.status in {"succeeded", "failed"} else "valid"
        return state, payload, pointers

    job = db.scalar(
        select(LlmEditJob).where(LlmEditJob.id == result.job_id).with_for_update()
    )
    state, payload, _ = validate_locked_job(job)
    if state == "invalid":
        return state
    assert job is not None and payload is not None
    event_id = pi_agent_billing_event_id(result.execution_id)
    already_recorded = db.scalar(
        select(LlmUsageRecord.id).where(LlmUsageRecord.event_id == event_id)
    )
    if already_recorded is not None:
        db.rollback()
        return "duplicate"
    event = LlmTokenUsageEvent(
        event_id=event_id,
        tenant_id=job.tenant_id,
        user_id=job.requested_by,
        project_id=job.project_id,
        workflow="intus",
        operation="files.llm_edit",
        provider=result.provider,
        model=result.model,
        prompt=str(payload.get("prompt", "")),
        prompt_tokens=result.usage.input_tokens,
        completion_tokens=result.usage.output_tokens,
        total_tokens=result.usage.total_tokens,
        occurred_at=datetime.now(timezone.utc),
        metadata=payload.get("metadata", {}),
    )
    assert_billing_message_size(event, settings.billing_max_bytes)
    db.rollback()

    await billing_publisher.publish_json(
        settings.billing_llm_usage_subject,
        event,
        message_id=billing_usage_message_id(event),
    )

    job = db.scalar(
        select(LlmEditJob).where(LlmEditJob.id == result.job_id).with_for_update()
    )
    state, payload, pointers = validate_locked_job(job)
    if state == "invalid":
        return state
    assert job is not None and payload is not None and pointers is not None
    already_recorded = db.scalar(
        select(LlmUsageRecord.id).where(LlmUsageRecord.event_id == event_id)
    )
    if already_recorded is not None:
        return "duplicate"
    if state == "duplicate":
        db.add(_usage_record(job, result, payload, event_id))
        return "duplicate"
    project = db.scalar(
        select(Project).where(
            Project.id == job.project_id, Project.tenant_id == job.tenant_id
        )
    )
    if project is None:
        return "invalid"

    snapshot = None
    changed_rows: list = []
    try:
        if result.status == "succeeded" and result.outcome == "changed":
            stage = ProjectRepository(db, job.tenant_id).stage_file_updates(
                project.name,
                {edit.id: edit.content for edit in result.changed_files},
                job.requested_by,
                f"LLM edit: {str(payload.get('prompt', ''))[:480]}",
                expected_updated_at={
                    edit.id: pointers[edit.id].updated_at
                    for edit in result.changed_files
                },
            )
            if stage is None:
                return "invalid"
            snapshot, changed_rows = stage
    except FileVersionConflictError:
        db.rollback()
        job = db.scalar(
            select(LlmEditJob)
            .where(LlmEditJob.id == result.job_id)
            .with_for_update()
        )
        state, payload, _ = validate_locked_job(job)
        if state != "valid":
            return state
        assert job is not None and payload is not None
        usage_record = _usage_record(job, result, payload, event_id)
        usage_record.status = "failed"
        db.add(usage_record)
        LlmEditRepository(db, job.tenant_id).finish_job(
            job,
            "failed",
            error="Files changed while AI edit was running. Reload and try again.",
            error_code="file_conflict",
            user_message="Files changed while AI edit was running. Reload and try again.",
            retryable=False,
        )
        return "applied"

    db.add(_usage_record(job, result, payload, event_id))
    repo = LlmEditRepository(db, job.tenant_id)
    if result.status == "failed":
        repo.finish_job(
            job,
            "failed",
            error=result.error_message,
            error_code=result.error_code,
            user_message=result.error_message,
            retryable=result.retryable,
        )
    else:
        repo.finish_job(
            job,
            "succeeded",
            result_payload=_result_payload(result, snapshot, changed_rows),
        )
    return "applied"


async def republish_queued_pi_agent_jobs(
    db: Session,
    publisher,
    settings,
    *,
    backoff_seconds: int = 30,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=backoff_seconds)
    jobs = list(db.scalars(select(LlmEditJob).where(LlmEditJob.status == "queued")))
    published = 0
    for job in jobs:
        payload = job.request_payload if isinstance(job.request_payload, dict) else {}
        raw_attempted_at = payload.get("dispatch_attempted_at")
        if not isinstance(raw_attempted_at, str):
            continue
        try:
            attempted_at = datetime.fromisoformat(raw_attempted_at)
        except ValueError:
            continue
        if attempted_at.tzinfo is None:
            attempted_at = attempted_at.replace(tzinfo=timezone.utc)
        if attempted_at > cutoff:
            continue

        repo = LlmEditRepository(db, job.tenant_id)
        error_code = "dispatch_config_error"
        try:
            if (
                payload.get("dispatched_provider") != settings.pi_agent_provider
                or payload.get("dispatched_model") != settings.pi_agent_model
                or payload.get("dispatched_thinking") != settings.pi_agent_thinking
            ):
                raise ValueError("Pi agent runtime configuration changed")
            manifest = [
                PiAgentFileManifest.model_validate(item)
                for item in payload.get("dispatched_manifest", [])
            ]
            if not manifest or len({item.id for item in manifest}) != len(manifest):
                raise ValueError("Invalid dispatched manifest")
            project = db.scalar(
                select(Project).where(
                    Project.id == job.project_id,
                    Project.tenant_id == job.tenant_id,
                )
            )
            if project is None:
                raise ValueError("Project not found")
            rows = ProjectRepository(db, job.tenant_id).files_by_ids(
                project.name, [item.id for item in manifest]
            )
            if set(rows) != {item.id for item in manifest}:
                error_code = "file_conflict"
                raise ValueError("Dispatched files no longer exist")
            command_files = []
            for item in manifest:
                row = rows[item.id]
                if (
                    row.filename != item.filename
                    or normalize_file_version(row.updated_at)
                    != normalize_file_version(item.updated_at)
                    or sha256(row.content.encode("utf-8")).hexdigest() != item.sha256
                ):
                    error_code = "file_conflict"
                    raise ValueError("Dispatched files changed before retry")
                command_files.append(
                    PiAgentSourceFile(
                        id=row.id,
                        filename=row.filename,
                        content=row.content,
                        updated_at=row.updated_at,
                        sha256=item.sha256,
                    )
                )
            has_v2_context = (
                "dispatched_conversation" in payload
                or "dispatched_system_prompt_sha256" in payload
            )
            schema_version: Literal[1, 2]
            if "dispatched_command_schema_version" in payload:
                raw_schema_version = payload["dispatched_command_schema_version"]
                if type(raw_schema_version) is not int or raw_schema_version not in {1, 2}:
                    raise ValueError("Invalid dispatched command schema version")
                if raw_schema_version == 1:
                    schema_version = 1
                else:
                    schema_version = 2
            else:
                if has_v2_context:
                    raise ValueError("Missing dispatched command schema version")
                schema_version = 1
            if schema_version == 1 and has_v2_context:
                raise ValueError("Legacy command contains v2 context")
            if schema_version == 2:
                conversation = PiAgentConversationContext.model_validate(
                    payload["dispatched_conversation"]
                )
                prompt_hash = payload["dispatched_system_prompt_sha256"]
                prior_prompts = []
            else:
                conversation = None
                prompt_hash = None
                prior_prompts = payload.get("dispatched_prior_prompts", [])
            command = PiAgentCommand(
                schema_version=schema_version,
                job_id=job.id,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                provider=payload["dispatched_provider"],
                model=payload["dispatched_model"],
                thinking=payload["dispatched_thinking"],
                prompt=payload["prompt"],
                prior_prompts=prior_prompts,
                conversation=conversation,
                system_prompt_sha256=prompt_hash,
                active_file_id=payload.get("active_file_id"),
                files=command_files,
                created_at=datetime.fromisoformat(payload["dispatch_created_at"]),
                traceparent=payload.get("dispatch_traceparent"),
                tracestate=payload.get("dispatch_tracestate"),
            )
            assert_pi_agent_command_size(command, settings.pi_agent_request_max_bytes)
        except (KeyError, TypeError, ValueError) as exc:
            repo.finish_job(
                job,
                "failed",
                error=str(exc),
                error_code=error_code,
                user_message="AI edit could not be safely retried.",
                retryable=False,
            )
            db.commit()
            _record_api_terminal(
                provider=getattr(settings, "pi_agent_provider", "openai-codex"),
                model=getattr(settings, "pi_agent_model", "gpt-5.5"),
                status="failed",
                failure_category=error_code,
                retryable=False,
            )
            continue

        updated_payload = dict(payload)
        updated_payload["dispatch_attempted_at"] = datetime.now(timezone.utc).isoformat()
        job.request_payload = updated_payload
        flag_modified(job, "request_payload")
        db.commit()
        try:
            await publisher.publish_json(
                settings.pi_agent_request_subject,
                command,
                message_id=pi_agent_command_message_id(command),
            )
        except Exception:
            logger.warning("Queued Pi agent command republish remained ambiguous")
            continue
        refreshed = repo.get_job(job.project_id, job.id)
        if refreshed is not None and repo.mark_job_dispatched(refreshed):
            db.commit()
            published += 1
    return published


async def handle_pi_agent_result_message(msg, db: Session, settings, billing_publisher) -> None:
    try:
        result = PiAgentResult.model_validate_json(msg.data)
        assert_pi_agent_result_size(result, settings.pi_agent_result_max_bytes)
    except (ValidationError, ValueError):
        logger.warning("Discarding invalid or oversize Pi agent result envelope")
        await msg.ack()
        return
    provenance = _result_provenance(db, result, settings)
    if provenance is None:
        logger.warning("Discarding Pi agent result with invalid provenance")
        await msg.ack()
        return
    provider, model = provenance
    headers = getattr(msg, "headers", None)
    if headers is not None:
        parent_context = extract_nats_context(headers)
    else:
        carrier = {
            key: value
            for key, value in {
                "traceparent": result.traceparent,
                "tracestate": result.tracestate,
            }.items()
            if value is not None
        }
        parent_context = propagate.extract(carrier)
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "pi_agent.result.consume",
        context=parent_context,
        kind=SpanKind.CONSUMER,
        attributes=_metric_attributes(
            provider=provider,
            model=model,
            status="processing",
            failure_category=result.error_code,
            retryable=result.retryable,
        ),
    ):
        try:
            outcome = await apply_pi_agent_result(db, result, settings, billing_publisher)
            db.commit()
        except Exception:
            db.rollback()
            await msg.nak()
            return
        if outcome == "applied":
            persisted = db.get(LlmEditJob, result.job_id)
            if persisted is not None:
                _record_api_terminal(
                    provider=provider,
                    model=model,
                    status=persisted.status,
                    failure_category=persisted.error_code,
                    retryable=bool(persisted.retryable),
                )
        counter_add(
            "tertius.pi_agent.result.processed.count",
            1,
            _metric_attributes(
                provider=provider,
                model=model,
                status=outcome,
                failure_category=result.error_code,
                retryable=result.retryable,
            ),
        )
    await msg.ack()


def pi_agent_job_stale(job: LlmEditJob, settings, *, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    if job.status == "queued":
        deadline_seconds = settings.pi_agent_stream_max_age_seconds + 60
        origin = job.created_at
    elif job.status == "running":
        deadline_seconds = (
            (settings.pi_agent_timeout_seconds + settings.pi_agent_ack_wait_seconds)
            * settings.pi_agent_max_deliver
            + 60
        )
        payload = job.request_payload if isinstance(job.request_payload, dict) else {}
        raw_dispatched_at = payload.get("dispatched_at")
        try:
            origin = datetime.fromisoformat(raw_dispatched_at) if isinstance(raw_dispatched_at, str) else job.created_at
        except ValueError:
            origin = job.created_at
    else:
        return False
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=timezone.utc)
    return origin < current - timedelta(seconds=deadline_seconds)


def reconcile_stale_pi_agent_job(db: Session, job: LlmEditJob, settings) -> bool:
    if not pi_agent_job_stale(job, settings):
        return False
    locked = db.scalar(
        select(LlmEditJob).where(LlmEditJob.id == job.id).with_for_update()
    )
    if locked is None or not pi_agent_job_stale(locked, settings):
        return False
    LlmEditRepository(db, locked.tenant_id).finish_job(
        locked,
        "failed",
        error="LLM edit worker stopped before reporting a result",
        error_code="worker_lost",
        user_message="AI generation stopped unexpectedly. Try again.",
        retryable=True,
    )
    return True


def reconcile_stale_pi_agent_jobs(db: Session, settings) -> int:
    jobs = list(
        db.scalars(
            select(LlmEditJob).where(LlmEditJob.status.in_(["queued", "running"]))
        )
    )
    count = sum(
        int(reconcile_stale_pi_agent_job(db, job, settings))
        for job in jobs
    )
    db.commit()
    if count:
        terminal_attributes = pi_agent_metric_attributes(
            operation="pi_agent.api",
            provider=getattr(settings, "pi_agent_provider", "openai-codex"),
            model=getattr(settings, "pi_agent_model", "gpt-5.5"),
            status="failed",
            failure_category="worker_lost",
            retryable=True,
        )
        counter_add(
            "tertius.pi_agent.job.stale.count",
            count,
            terminal_attributes,
        )
        counter_add("tertius.pi_agent.job.terminal.count", count, terminal_attributes)
    return count


def observe_pi_agent_active_jobs(db: Session, settings) -> int:
    global _ACTIVE_JOBS_OBSERVED
    active_count = len(
        list(
            db.scalars(
                select(LlmEditJob.id).where(
                    LlmEditJob.status.in_(["queued", "running"])
                )
            )
        )
    )
    active_attributes = pi_agent_metric_attributes(
        operation="pi_agent.api",
        provider=getattr(settings, "pi_agent_provider", "openai-codex"),
        model=getattr(settings, "pi_agent_model", "gpt-5.5"),
        status="active",
    )
    up_down_counter_add(
        "tertius.pi_agent.jobs.active",
        active_count - _ACTIVE_JOBS_OBSERVED,
        active_attributes,
    )
    _ACTIVE_JOBS_OBSERVED = active_count
    return active_count


async def run_pi_agent_active_observer(
    stop_event: asyncio.Event | None = None, *, interval_seconds: float = 30.0
) -> None:
    settings = get_settings()
    while stop_event is None or not stop_event.is_set():
        try:
            with SessionLocal() as db:
                observe_pi_agent_active_jobs(db, settings)
        except Exception:
            logger.exception("Pi agent active-job observation failed; retrying")
        if stop_event is None:
            await asyncio.sleep(interval_seconds)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def run_pi_agent_result_consumer(
    stop_event: asyncio.Event | None = None,
    *,
    heartbeat_interval_seconds: float = _RESULT_CONSUMER_HEARTBEAT_SECONDS,
) -> None:
    settings = get_settings()
    while stop_event is None or not stop_event.is_set():
        nc = None
        try:
            nc = await connect_nats(settings.nats_url)
            js = await ensure_pi_agent_stream(nc, settings)
            await ensure_billing_stream(nc, settings)
            publisher = NatsPublisher(js)
            subscription = await pull_pi_agent_result_subscription(js, settings)
            last_reconcile = asyncio.get_running_loop().time()
            last_heartbeat = last_reconcile
            _record_result_consumer_heartbeat(settings)
            while stop_event is None or not stop_event.is_set():
                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= heartbeat_interval_seconds:
                    _record_result_consumer_heartbeat(settings)
                    last_heartbeat = now
                if now - last_reconcile >= 60:
                    with SessionLocal() as db:
                        await republish_queued_pi_agent_jobs(db, publisher, settings)
                        reconcile_stale_pi_agent_jobs(db, settings)
                    last_reconcile = now
                try:
                    messages = await subscription.fetch(batch=1, timeout=5)
                except TimeoutError:
                    continue
                for msg in messages:
                    with SessionLocal() as db:
                        await handle_pi_agent_result_message(msg, db, settings, publisher)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Pi agent result consumer failed; retrying")
            await asyncio.sleep(2)
        finally:
            if nc is not None:
                await nc.close()
