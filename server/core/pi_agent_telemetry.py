from __future__ import annotations

from typing import Literal


PiTelemetryOperation = Literal["pi_agent.worker", "pi_agent.api"]

FAILURE_CATEGORIES = {
    "agent_limit_exceeded",
    "dispatch_config_error",
    "file_conflict",
    "invalid_workspace",
    "model_mismatch",
    "protocol_error",
    "provider_auth",
    "provider_error",
    "provider_rate_limit",
    "result_too_large",
    "timeout",
    "tool_guard_failure",
    "worker_error",
    "worker_lost",
}


def bounded_failure_category(code: str | None) -> str:
    return code if code in FAILURE_CATEGORIES else "worker_error" if code else "none"


def pi_agent_metric_attributes(
    *,
    operation: PiTelemetryOperation,
    provider: str,
    model: str,
    status: str,
    failure_category: str | None = None,
    retryable: bool = False,
) -> dict[str, str | bool]:
    return {
        "operation": operation,
        "provider": provider,
        "model": model,
        "status": status,
        "failure_category": bounded_failure_category(failure_category),
        "retryable": retryable,
    }
