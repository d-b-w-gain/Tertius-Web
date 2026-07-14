from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LlmTokenUsageEvent(BaseModel):
    event_id: UUID
    tenant_id: UUID
    user_id: UUID
    project_id: UUID | None = None
    workflow: str
    operation: str
    provider: str
    model: str
    prompt: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0, le=0.0)
    occurred_at: datetime
    provider_request_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def serialized_billing_message_size(message: BaseModel) -> int:
    return len(message.model_dump_json().encode("utf-8"))


def assert_billing_message_size(message: BaseModel, max_bytes: int) -> None:
    size = serialized_billing_message_size(message)
    if size > max_bytes:
        raise ValueError(f"billing event is {size} bytes, above {max_bytes} byte limit")


def billing_usage_message_id(event: LlmTokenUsageEvent) -> str:
    return f"billing-usage:{event.event_id}"
