from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext
from core.models import LlmUsageRecord, now_utc


class LlmUsageLimitExceeded(RuntimeError):
    pass


def _raise_limit() -> None:
    raise LlmUsageLimitExceeded("LLM usage limit exceeded")


def assert_llm_usage_allowed(
    db: Session,
    settings,
    *,
    tenant_id: UUID,
    user_id: UUID,
    estimated_tokens: int,
) -> None:
    now = now_utc()
    minute_ago = now - timedelta(minutes=1)
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)

    user_recent = db.scalar(
        select(func.count())
        .select_from(LlmUsageRecord)
        .where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.user_id == user_id,
            LlmUsageRecord.created_at >= minute_ago,
        )
    )
    if user_recent >= settings.llm_user_rate_limit_per_minute:
        _raise_limit()

    tenant_recent = db.scalar(
        select(func.count())
        .select_from(LlmUsageRecord)
        .where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.created_at >= minute_ago,
        )
    )
    if tenant_recent >= settings.llm_tenant_rate_limit_per_minute:
        _raise_limit()

    tenant_tokens = db.scalar(
        select(func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0)).where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.created_at >= day_start,
        )
    ) or 0
    if tenant_tokens + estimated_tokens > settings.llm_tenant_daily_token_quota:
        _raise_limit()

    user_tokens = db.scalar(
        select(func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0)).where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.user_id == user_id,
            LlmUsageRecord.created_at >= day_start,
        )
    ) or 0
    if user_tokens + estimated_tokens > settings.llm_user_daily_token_quota:
        _raise_limit()


def _provider_from_settings(settings) -> str:
    return "openai-compatible"


def record_llm_usage(
    db: Session,
    *,
    auth: AuthContext,
    project_id: UUID | None,
    request,
    result,
    settings,
    provider_request_id: str | None = None,
    event_id: UUID | None = None,
    operation: str = "build_script.generate",
) -> UUID:
    usage_event_id = event_id or uuid4()
    usage = result.usage
    db.add(
        LlmUsageRecord(
            event_id=usage_event_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            project_id=project_id,
            workflow="intus",
            operation=operation,
            provider=_provider_from_settings(settings),
            model=result.model,
            prompt=request.prompt,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            provider_request_id=provider_request_id,
            metadata_json=dict(getattr(request, "metadata", {}) or {}),
            status="completed",
        )
    )
    db.flush()
    return usage_event_id
