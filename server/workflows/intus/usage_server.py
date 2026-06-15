from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.auth import require_tenant_owner
from core.auth_types import AuthContext
from core.db import get_db
from core.repositories import UsageRepository
from core.usage_messages import (
    DailyUsageItem,
    FormatUsageItem,
    MonthlyUsageItem,
    ProjectUsageItem,
    UsageRecordResponse,
    UsageSummaryResponse,
)

router = APIRouter(prefix="/usage", tags=["usage"])


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
