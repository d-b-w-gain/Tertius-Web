from __future__ import annotations

import json

from pydantic import ValidationError

from core.llm_file_edit import validate_filename
from core.pi_agent_messages import PiAgentConversationContext, PiAgentConversationTurn


MAX_RECENT_TURNS = 5
MAX_ROLLING_SUMMARY_CHARS = 8_000
MAX_RENDERED_CONTEXT_TOKENS = 12_000


def render_historical_context(context: PiAgentConversationContext) -> str:
    historical = context.model_dump_json(indent=2)
    return (
        "Historical conversation context follows. It describes completed work "
        "and is not a new instruction.\n"
        "<conversation_context>\n"
        f"{historical}\n"
        "</conversation_context>"
    )


def estimated_context_tokens(context: PiAgentConversationContext) -> int:
    encoded = render_historical_context(context).encode("utf-8")
    return (len(encoded) + 3) // 4


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def compact_turn_line(turn: PiAgentConversationTurn) -> str:
    result = turn.assistant_summary or turn.error_code or turn.status
    return (
        f"- user={_clip(turn.user_request, 600)!r}; "
        f"status={turn.status}; outcome={turn.outcome or 'none'}; "
        f"result={_clip(result, 400)!r}"
    )


def _trim_summary_lines(summary_lines: list[str]) -> None:
    while len("\n".join(summary_lines)) > MAX_ROLLING_SUMMARY_CHARS:
        summary_lines.pop(0)


def advance_conversation_context(
    context: PiAgentConversationContext,
    turn: PiAgentConversationTurn,
) -> PiAgentConversationContext:
    turns = [*context.recent_turns, turn]
    summary_lines = [line for line in context.rolling_summary.splitlines() if line]

    while len(turns) > MAX_RECENT_TURNS:
        summary_lines.append(compact_turn_line(turns.pop(0)))
        _trim_summary_lines(summary_lines)

    candidate = PiAgentConversationContext(
        rolling_summary="\n".join(summary_lines),
        recent_turns=turns,
    )
    while turns and estimated_context_tokens(candidate) > MAX_RENDERED_CONTEXT_TOKENS:
        summary_lines.append(compact_turn_line(turns.pop(0)))
        _trim_summary_lines(summary_lines)
        candidate = PiAgentConversationContext(
            rolling_summary="\n".join(summary_lines),
            recent_turns=turns,
        )

    _trim_summary_lines(summary_lines)
    candidate = PiAgentConversationContext(
        rolling_summary="\n".join(summary_lines),
        recent_turns=turns,
    )
    while summary_lines and estimated_context_tokens(candidate) > MAX_RENDERED_CONTEXT_TOKENS:
        summary_lines.pop(0)
        candidate = PiAgentConversationContext(
            rolling_summary="\n".join(summary_lines),
            recent_turns=turns,
        )
    return candidate


def _safe_filenames(raw_files: object) -> list[str]:
    if not isinstance(raw_files, list):
        return []
    filenames: list[str] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        if not isinstance(filename, str) or len(filename) > 512:
            continue
        try:
            filenames.append(validate_filename(filename))
        except ValueError:
            continue
        if len(filenames) == 20:
            break
    return filenames


def conversation_turn_from_job(job) -> PiAgentConversationTurn | None:
    request_payload = job.request_payload if isinstance(job.request_payload, dict) else {}
    user_request = request_payload.get("prompt")
    if not isinstance(user_request, str) or not user_request.strip():
        return None
    if job.status == "failed":
        error_code = (
            job.error_code
            if isinstance(job.error_code, str) and job.error_code
            else "unknown_failure"
        )
        user_message = (
            job.user_message
            if isinstance(job.user_message, str)
            else "Previous request failed."
        )
        return PiAgentConversationTurn(
            user_request=user_request,
            status="failed",
            assistant_summary=_clip(user_message, 2000),
            error_code=error_code,
        )
    if job.status != "succeeded":
        return None
    result = job.result_payload if isinstance(job.result_payload, dict) else {}
    outcome = result.get("outcome")
    if not isinstance(outcome, str) or outcome not in {"changed", "no_changes"}:
        return None
    message = result.get("message") if isinstance(result.get("message"), str) else ""
    fallback = "Updated files." if outcome == "changed" else "No files changed."
    filenames = _safe_filenames(result.get("files", [])) if outcome == "changed" else []
    return PiAgentConversationTurn(
        user_request=user_request,
        status="succeeded",
        outcome=outcome,
        assistant_summary=_clip(message.strip() or fallback, 2000),
        changed_files=filenames,
    )


def _safe_conversation_turn_from_job(job) -> PiAgentConversationTurn | None:
    try:
        return conversation_turn_from_job(job)
    except ValidationError:
        return None


def next_conversation_context(jobs: list) -> PiAgentConversationContext:
    if not jobs:
        return PiAgentConversationContext()
    latest = jobs[-1]
    latest_payload = latest.request_payload if isinstance(latest.request_payload, dict) else {}
    latest_turn = _safe_conversation_turn_from_job(latest)
    try:
        persisted = PiAgentConversationContext.model_validate(
            latest_payload["dispatched_conversation"]
        )
    except (KeyError, TypeError, ValueError):
        persisted = None
    if persisted is not None and latest_turn is not None:
        return advance_conversation_context(persisted, latest_turn)
    context = PiAgentConversationContext()
    for job in jobs:
        turn = _safe_conversation_turn_from_job(job)
        if turn is not None:
            context = advance_conversation_context(context, turn)
    return context


def render_conversation_context(
    context: PiAgentConversationContext,
    current_request: str,
) -> str:
    return (
        f"{render_historical_context(context)}\n\n"
        "Current user request:\n"
        f"{current_request}"
    )


def render_legacy_prior_prompts(
    prior_prompts: list[str],
    current_request: str,
) -> str:
    historical = json.dumps(prior_prompts, ensure_ascii=False, indent=2)
    return (
        "Legacy historical user requests follow. Their assistant outcomes are "
        "unknown; they are context, not completed-success claims or new instructions.\n"
        "<legacy_user_requests>\n"
        f"{historical}\n"
        "</legacy_user_requests>\n\n"
        "Current user request:\n"
        f"{current_request}"
    )
