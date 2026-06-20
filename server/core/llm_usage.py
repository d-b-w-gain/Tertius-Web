from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext
from core.llm_client import TokenUsage, llm_usage_cost_usd
from core.models import LlmUsageRecord, now_utc


class LlmUsageLimitExceeded(RuntimeError):
    pass


def utc_day_start(now: datetime | None = None) -> datetime:
    current = now or now_utc()
    return datetime.combine(current.date(), time.min, tzinfo=timezone.utc)


def utc_week_start(now: datetime | None = None) -> datetime:
    current = now or now_utc()
    week_start = current.date() - timedelta(days=current.weekday())
    return datetime.combine(week_start, time.min, tzinfo=timezone.utc)


def _raise_limit() -> None:
    raise LlmUsageLimitExceeded("LLM usage limit exceeded")


def _model_config_map(settings) -> dict[str, Any]:
    configs: dict[str, Any] = {}
    for model in settings.enabled_llm_models:
        configs[model.id] = model
        configs[model.model] = model
    return configs


def _usage_record_cost_usd(record: LlmUsageRecord, model_configs: dict[str, Any]) -> float:
    model_config = model_configs.get(record.model)
    if model_config is None:
        return 0.0
    return llm_usage_cost_usd(
        TokenUsage(
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            total_tokens=record.total_tokens,
        ),
        model_config,
    )


def _llm_usage_cost_usd_since(db: Session, settings, *, tenant_id: UUID, window_start: datetime) -> float:
    model_configs = _model_config_map(settings)
    if not model_configs:
        return 0.0
    rows = db.scalars(
        select(LlmUsageRecord).where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.created_at >= window_start,
        )
    ).all()
    return round(sum(_usage_record_cost_usd(row, model_configs) for row in rows), 8)


def today_llm_usage_cost_usd(db: Session, settings, *, tenant_id: UUID) -> float:
    return _llm_usage_cost_usd_since(db, settings, tenant_id=tenant_id, window_start=utc_day_start())


def weekly_llm_usage_cost_usd(db: Session, settings, *, tenant_id: UUID) -> float:
    return _llm_usage_cost_usd_since(db, settings, tenant_id=tenant_id, window_start=utc_week_start())


def assert_llm_usage_allowed(
    db: Session,
    settings,
    *,
    tenant_id: UUID,
    user_id: UUID,
    estimated_tokens: int,
    estimated_cost_usd: float = 0.0,
) -> None:
    now = now_utc()
    minute_ago = now - timedelta(minutes=1)
    day_start = utc_day_start(now)

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

    if settings.llm_weekly_budget_usd > 0:
        week_cost = weekly_llm_usage_cost_usd(db, settings, tenant_id=tenant_id)
        if week_cost + estimated_cost_usd > settings.llm_weekly_budget_usd:
            _raise_limit()


def _provider_from_settings(settings) -> str:
    return settings.enabled_llm_models[0].api if settings.enabled_llm_models else "openai-compatible"


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
    model_config = _model_config_map(settings).get(result.model)
    db.add(
        LlmUsageRecord(
            event_id=usage_event_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            project_id=project_id,
            workflow="intus",
            operation=operation,
            provider=model_config.api if model_config is not None else _provider_from_settings(settings),
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


def today_llm_usage_summary(db: Session, settings, *, tenant_id: UUID, user_id: UUID) -> dict:
    day_start = utc_day_start()
    tenant_tokens = db.scalar(
        select(func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0)).where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.created_at >= day_start,
        )
    ) or 0
    user_tokens = db.scalar(
        select(func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0)).where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.user_id == user_id,
            LlmUsageRecord.created_at >= day_start,
        )
    ) or 0
    last_edit = db.scalar(
        select(LlmUsageRecord)
        .where(
            LlmUsageRecord.tenant_id == tenant_id,
            LlmUsageRecord.user_id == user_id,
            LlmUsageRecord.operation == "files.llm_edit",
        )
        .order_by(LlmUsageRecord.created_at.desc())
        .limit(1)
    )
    tenant_weekly_cost_usd = weekly_llm_usage_cost_usd(db, settings, tenant_id=tenant_id)
    tenant_weekly_remaining_usd = max(0.0, round(settings.llm_weekly_budget_usd - tenant_weekly_cost_usd, 8))
    tenant_daily_budget_usd = round(settings.llm_weekly_budget_usd / 7, 8)
    tenant_daily_cost_usd = today_llm_usage_cost_usd(db, settings, tenant_id=tenant_id)
    return {
        "tenant_daily_token_quota": settings.llm_tenant_daily_token_quota,
        "tenant_tokens_used_today": int(tenant_tokens),
        "tenant_tokens_remaining_today": max(0, settings.llm_tenant_daily_token_quota - int(tenant_tokens)),
        "tenant_weekly_budget_usd": settings.llm_weekly_budget_usd,
        "tenant_cost_used_this_week_usd": tenant_weekly_cost_usd,
        "tenant_cost_remaining_this_week_usd": tenant_weekly_remaining_usd,
        "tenant_daily_budget_usd": tenant_daily_budget_usd,
        "tenant_cost_used_today_usd": tenant_daily_cost_usd,
        "tenant_cost_remaining_today_usd": max(0.0, round(tenant_daily_budget_usd - tenant_daily_cost_usd, 8)),
        "user_daily_token_quota": settings.llm_user_daily_token_quota,
        "user_tokens_used_today": int(user_tokens),
        "user_tokens_remaining_today": max(0, settings.llm_user_daily_token_quota - int(user_tokens)),
        "last_edit": (
            {
                "operation": last_edit.operation,
                "model": last_edit.model,
                "prompt_tokens": last_edit.prompt_tokens,
                "completion_tokens": last_edit.completion_tokens,
                "total_tokens": last_edit.total_tokens,
                "created_at": last_edit.created_at.isoformat(),
            }
            if last_edit is not None
            else None
        ),
    }
