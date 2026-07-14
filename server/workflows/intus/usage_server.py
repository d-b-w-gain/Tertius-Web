from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.auth import get_auth_context, require_tenant_owner
from core.auth_types import AuthContext
from core.config import get_settings
from core.db import get_db
from core.llm_usage import today_llm_usage_summary
from core.repositories import UsageRepository
from core.usage_messages import (
    DailyUsageItem,
    FormatUsageItem,
    LlmModelsResponse,
    LlmTodayUsageResponse,
    MonthlyUsageItem,
    ProjectUsageItem,
    UsageRecordResponse,
    UsageSummaryResponse,
)

router = APIRouter(prefix="/usage", tags=["usage"])
llm_usage_router = APIRouter(prefix="/llm-usage", tags=["llm-usage"])


@router.get("/summary", response_model=UsageSummaryResponse)
def usage_summary(ctx: AuthContext = Depends(require_tenant_owner), db: Session = Depends(get_db)):
    repo = UsageRepository(db, ctx.tenant_id)
    return repo.total_summary()


@router.get("/daily", response_model=list[DailyUsageItem])
def daily_breakdown(
    days: int = Query(default=30, ge=1, le=365),
    ctx: AuthContext = Depends(require_tenant_owner),
    db: Session = Depends(get_db),
):
    repo = UsageRepository(db, ctx.tenant_id)
    return repo.daily_breakdown(days)


@router.get("/monthly", response_model=list[MonthlyUsageItem])
def monthly_breakdown(
    months: int = Query(default=12, ge=1, le=60),
    ctx: AuthContext = Depends(require_tenant_owner),
    db: Session = Depends(get_db),
):
    repo = UsageRepository(db, ctx.tenant_id)
    return repo.monthly_breakdown(months)


@router.get("/by-project", response_model=list[ProjectUsageItem])
def project_breakdown(
    ctx: AuthContext = Depends(require_tenant_owner),
    db: Session = Depends(get_db),
):
    repo = UsageRepository(db, ctx.tenant_id)
    return repo.project_breakdown()


@router.get("/by-format", response_model=list[FormatUsageItem])
def format_breakdown(
    ctx: AuthContext = Depends(require_tenant_owner),
    db: Session = Depends(get_db),
):
    repo = UsageRepository(db, ctx.tenant_id)
    return repo.format_breakdown()


@router.get("/recent", response_model=list[UsageRecordResponse])
def recent_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(require_tenant_owner),
    db: Session = Depends(get_db),
):
    repo = UsageRepository(db, ctx.tenant_id)
    return repo.recent_jobs(limit)


@llm_usage_router.get("/today", response_model=LlmTodayUsageResponse)
def llm_usage_today(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    return today_llm_usage_summary(
        db,
        get_settings(),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )


@llm_usage_router.get("/models", response_model=LlmModelsResponse)
def llm_models(ctx: AuthContext = Depends(get_auth_context)):
    settings = get_settings()
    return {
        "default_model_id": settings.pi_agent_model,
        "models": [
            {
                "id": settings.pi_agent_model,
                "model": settings.pi_agent_model,
                "label": settings.pi_agent_model_label,
                "enabled": settings.pi_agent_enabled,
            }
        ],
    }
