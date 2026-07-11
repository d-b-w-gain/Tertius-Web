from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from core.llm_file_edit import TokenUsage


MAX_PI_AGENT_PROMPT_BYTES = 32_768
PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS = 8_192
PI_AGENT_PROMPT_PATH = Path(__file__).with_name("pi_agent_system_prompt.md")


class PiAgentPromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class PiAgentPromptSnapshot:
    path: Path
    content: str
    sha256: str


@lru_cache(maxsize=8)
def load_pi_agent_prompt(
    path: Path = PI_AGENT_PROMPT_PATH,
) -> PiAgentPromptSnapshot:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise OSError
        raw = resolved.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PiAgentPromptError("Pi agent system prompt is unavailable") from exc
    if (
        not raw
        or len(raw) > MAX_PI_AGENT_PROMPT_BYTES
        or "\0" in content
        or not content.strip()
    ):
        raise PiAgentPromptError("Pi agent system prompt is invalid")
    return PiAgentPromptSnapshot(
        path=resolved,
        content=content,
        sha256=sha256(raw).hexdigest(),
    )


def estimate_pi_agent_usage(
    *,
    system_prompt: str,
    user_prompt: str,
    source_bytes: int,
    metadata: Mapping[str, str],
    max_output_tokens: int,
) -> TokenUsage:
    metadata_bytes = sum(
        len(key.encode("utf-8")) + len(value.encode("utf-8"))
        for key, value in metadata.items()
    )
    framed_bytes = (
        len(system_prompt.encode("utf-8"))
        + len(user_prompt.encode("utf-8"))
        + source_bytes
        + metadata_bytes
    )
    prompt_tokens = PI_AGENT_BASE_AND_TOOL_RESERVE_TOKENS + (framed_bytes + 3) // 4
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=max_output_tokens,
        total_tokens=prompt_tokens + max_output_tokens,
    )
