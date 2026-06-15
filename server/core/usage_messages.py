from pydantic import BaseModel


class UsageSummaryResponse(BaseModel):
    total_jobs: int
    total_cost_cents: int
    total_compute_seconds: float
    total_artifact_bytes: int


class DailyUsageItem(BaseModel):
    day: str
    job_count: int
    cost_cents: int
    compute_seconds: float


class MonthlyUsageItem(BaseModel):
    month: str
    job_count: int
    cost_cents: int
    compute_seconds: float


class ProjectUsageItem(BaseModel):
    project_id: str
    project_name: str
    job_count: int
    cost_cents: int
    compute_seconds: float


class FormatUsageItem(BaseModel):
    export_format: str
    job_count: int
    cost_cents: int
    compute_seconds: float


class UsageRecordResponse(BaseModel):
    created_at: str
    export_format: str
    status: str
    compute_duration_seconds: float
    artifact_byte_size: int
    cost_cents: int
    username: str | None
