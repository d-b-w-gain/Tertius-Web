from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.pi_agent_messages import PiAgentUsage


_MAX_DIAGNOSTIC = 400
_GUARD_SENTINEL = "TERTIUS_GUARD_FAILURE"


class PiAgentRpcError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message[:_MAX_DIAGNOSTIC])


@dataclass(frozen=True)
class PiAgentRpcResult:
    usage: PiAgentUsage
    assistant_summary: str
    turns: int
    tool_calls: int
    argv: list[str]


def build_pi_argv(
    executable: str,
    *,
    provider: str,
    model: str,
    thinking: str,
    system_prompt: str,
    extension_path: str,
) -> list[str]:
    return [
        executable,
        "--mode",
        "rpc",
        "--no-session",
        "--provider",
        provider,
        "--model",
        model,
        "--thinking",
        thinking,
        "--tools",
        "read,edit,write,grep,find,ls",
        "--no-extensions",
        "--extension",
        extension_path,
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--append-system-prompt",
        system_prompt,
    ]


def _resolved_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get("id") or value.get("name")
        return candidate if isinstance(candidate, str) else None
    return None


def _contains(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains(item, needle) for item in value)
    return False


def _normalized_event(value: dict[str, Any]) -> dict[str, Any] | None:
    event_type = value.get("type")
    if _contains(value, _GUARD_SENTINEL):
        return {"type": "_guard_failure"}
    if event_type == "turn_end":
        return {"type": "turn_end"}
    if event_type == "tool_execution_start":
        return {"type": "tool_execution_start"}
    if event_type == "agent_settled":
        return {"type": "agent_settled"}
    if event_type in {"agent_error", "error"}:
        failure = _classify_provider_failure(value)
        return {
            "type": "_provider_failure",
            "code": failure.code,
            "retryable": failure.retryable,
        }
    if event_type == "auto_retry_end" and value.get("success") is False:
        failure = _classify_provider_failure({"error": value.get("finalError")})
        return {
            "type": "_provider_failure",
            "code": failure.code,
            "retryable": failure.retryable,
        }
    if event_type in {"message_end", "message_update"}:
        message = value.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            stop_reason = message.get("stopReason")
            if stop_reason in {"error", "abort", "aborted"}:
                failure = _classify_provider_failure(
                    {"error": message.get("errorMessage"), "stopReason": stop_reason}
                )
                return {
                    "type": "_provider_failure",
                    "code": failure.code,
                    "retryable": failure.retryable,
                }
    return None


class _RpcProtocol:
    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self.responses: dict[str, dict[str, Any]] = {}
        self.waiting_ids: set[str] = set()
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self.reader = asyncio.create_task(self._read())

    def _signal(self, event: dict[str, Any]) -> None:
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            while not self.events.empty():
                self.events.get_nowait()
            self.events.put_nowait({"type": "_protocol_overflow"})

    async def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                if not line.endswith(b"\n"):
                    self._signal({"type": "_protocol_error"})
                    return
                try:
                    value = json.loads(line.decode("utf-8"))
                except UnicodeDecodeError, json.JSONDecodeError:
                    self._signal({"type": "_protocol_error"})
                    return
                if not isinstance(value, dict):
                    self._signal({"type": "_protocol_error"})
                elif isinstance(value.get("id"), str):
                    if value["id"] in self.waiting_ids:
                        self.responses[value["id"]] = value
                else:
                    event = _normalized_event(value)
                    if event is not None:
                        self._signal(event)
        except ValueError, asyncio.LimitOverrunError:
            self._signal({"type": "_protocol_overflow"})

    async def request(
        self, kind: str, *, request_id: str | None = None, **fields: Any
    ) -> dict[str, Any]:
        request_id = request_id or uuid4().hex
        if request_id in self.waiting_ids:
            raise PiAgentRpcError("protocol_error", "Duplicate Pi RPC request ID")
        payload = {"id": request_id, "type": kind, **fields}
        self.waiting_ids.add(request_id)
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(
                json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
            )
            await self.process.stdin.drain()
            while request_id not in self.responses:
                if self.reader.done() and request_id not in self.responses:
                    raise PiAgentRpcError(
                        "protocol_error", "Pi RPC exited before responding"
                    )
                await asyncio.sleep(0.001)
            response = self.responses.pop(request_id)
            if response.get("success") is False:
                raise _classify_provider_failure(response)
            return response
        finally:
            self.waiting_ids.discard(request_id)
            self.responses.pop(request_id, None)


def _classify_provider_failure(event: dict[str, Any]) -> PiAgentRpcError:
    lowered = json.dumps(event, ensure_ascii=True).lower()
    if any(
        term in lowered
        for term in ("unauthorized", "authentication", "invalid_grant", "401")
    ):
        return PiAgentRpcError(
            "provider_auth", "Provider authentication failed", retryable=False
        )
    if any(term in lowered for term in ("rate limit", "rate_limit", "429")):
        return PiAgentRpcError(
            "provider_rate_limit", "Provider rate limit exhausted", retryable=True
        )
    return PiAgentRpcError(
        "provider_error", "Provider execution failed", retryable=True
    )


async def _cleanup(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None and not process.stdin.is_closing():
        process.stdin.close()
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def run_pi_agent(
    prompt: str,
    *,
    correlation_id: str | None = None,
    executable: str = "pi",
    cwd: str | Path = "/workspace/repo",
    provider: str = "openai-codex",
    model: str = "gpt-5.5",
    thinking: str = "high",
    system_prompt: str = "",
    extension_path: str = "/opt/tertius-pi/workspace-guard.ts",
    timeout_seconds: float = 480,
    max_turns: int = 12,
    max_tool_calls: int = 48,
    environment: Mapping[str, str] | None = None,
) -> PiAgentRpcResult:
    argv = build_pi_argv(
        executable,
        provider=provider,
        model=model,
        thinking=thinking,
        system_prompt=system_prompt,
        extension_path=extension_path,
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PI_CODING_AGENT_DIR": "/var/lib/pi-agent",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
        "HOME": "/tmp/home",
    }
    if environment:
        env.update(environment)
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    protocol = _RpcProtocol(process)
    turns = tool_calls = 0
    try:
        async with asyncio.timeout(timeout_seconds):
            state_response = await protocol.request("get_state")
            state = state_response.get("data") or state_response.get("state") or {}
            resolved_model = state.get("model") if isinstance(state, dict) else None
            resolved_provider = (
                resolved_model.get("provider")
                if isinstance(resolved_model, dict)
                else None
            )
            if (
                _resolved_id(resolved_provider) != provider
                or _resolved_id(resolved_model) != model
            ):
                raise PiAgentRpcError(
                    "model_mismatch", "Pi resolved an unexpected provider or model"
                )
            await protocol.request("prompt", request_id=correlation_id, message=prompt)
            while True:
                event = await protocol.events.get()
                event_type = event.get("type")
                if event_type in {"_protocol_error", "_protocol_overflow"}:
                    raise PiAgentRpcError(
                        "protocol_error", "Malformed or excessive Pi RPC output"
                    )
                if event_type == "_guard_failure":
                    raise PiAgentRpcError(
                        "tool_guard_failure", "Workspace tool guard blocked execution"
                    )
                if event_type == "_provider_failure":
                    raise PiAgentRpcError(
                        event.get("code", "provider_error"),
                        "Provider execution failed",
                        retryable=bool(event.get("retryable")),
                    )
                if event_type == "turn_end":
                    turns += 1
                    if turns > max_turns:
                        await protocol.request("abort")
                        raise PiAgentRpcError(
                            "agent_limit_exceeded", "Agent turn limit exceeded"
                        )
                if event_type == "tool_execution_start":
                    tool_calls += 1
                    if tool_calls > max_tool_calls:
                        await protocol.request("abort")
                        raise PiAgentRpcError(
                            "agent_limit_exceeded", "Agent tool-call limit exceeded"
                        )
                if event_type == "agent_settled":
                    break
            stats_response = await protocol.request("get_session_stats")
            stats = stats_response.get("data") or {}
            tokens = stats.get("tokens") or {}
            usage = PiAgentUsage(
                input_tokens=tokens.get("input", 0),
                output_tokens=tokens.get("output", 0),
                cache_read_tokens=tokens.get("cacheRead", 0),
                cache_write_tokens=tokens.get("cacheWrite", 0),
                total_tokens=tokens.get("total", 0),
            )
            return PiAgentRpcResult(
                usage=usage,
                assistant_summary="",
                turns=turns,
                tool_calls=tool_calls,
                argv=argv,
            )
    except TimeoutError as exc:
        raise PiAgentRpcError("timeout", "Pi agent timed out", retryable=True) from exc
    finally:
        protocol.reader.cancel()
        await _cleanup(process)
