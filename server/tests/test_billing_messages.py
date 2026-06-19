from datetime import datetime, timezone
from uuid import uuid4

import pytest

from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
    serialized_billing_message_size,
)


def test_llm_token_usage_event_serializes_trusted_billing_fields():
    event = LlmTokenUsageEvent(
        event_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        project_id=uuid4(),
        workflow="intus",
        operation="build_script.generate",
        provider="openai-compatible",
        model="test-openai-compatible-model",
        prompt="make a bracket",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        provider_request_id="chatcmpl-123",
        metadata={"source": "compiler_tab"},
    )

    payload = event.model_dump_json()

    assert '"workflow":"intus"' in payload
    assert '"operation":"build_script.generate"' in payload
    assert '"prompt":"make a bracket"' in payload
    assert '"total_tokens":30' in payload
    assert billing_usage_message_id(event).startswith("billing-usage:")


def test_billing_message_size_limit_rejects_oversized_payload():
    event = LlmTokenUsageEvent(
        event_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        workflow="intus",
        operation="build_script.generate",
        provider="openai-compatible",
        model="test-openai-compatible-model",
        prompt="x" * 128,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert serialized_billing_message_size(event) > 0
    with pytest.raises(ValueError, match="billing event is"):
        assert_billing_message_size(event, 20)
