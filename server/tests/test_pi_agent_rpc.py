from __future__ import annotations

from pathlib import Path

import pytest

from core.pi_agent_rpc import PiAgentRpcError, run_pi_agent


@pytest.fixture
def fake_pi(tmp_path: Path) -> Path:
    path = tmp_path / "fake-pi"
    path.write_text(
        """#!/usr/bin/env python3
import json, os, sys, time
scenario=os.environ.get("FAKE_PI_SCENARIO", "settled")
for raw in sys.stdin:
    request=json.loads(raw)
    kind=request["type"]
    if kind=="get_state":
        print(json.dumps({"id":request["id"],"success":True,"data":{"model":{"provider":"openai-codex","id":"gpt-5.5"}}}), flush=True)
    elif kind=="prompt":
        assert "message" in request
        expected_id=os.environ.get("FAKE_PI_EXPECT_PROMPT_ID")
        if expected_id: assert request["id"]==expected_id
        if scenario=="malformed":
            print("{bad", flush=True); continue
        if scenario=="timeout":
            time.sleep(60); continue
        if scenario=="interleaved":
            print(json.dumps({"type":"message_update","message":{"role":"assistant"}}), flush=True)
        events=[]
        if scenario=="turn-limit": events=[{"type":"turn_end"}]*13
        elif scenario=="overflow": events=[{"type":"turn_end"}]*300
        elif scenario=="noise": events=[{"id":f"unknown-{i}","success":True,"data":"x"*1000} for i in range(1000)]
        elif scenario=="tool-limit": events=[{"type":"tool_execution_start"}]*49
        elif scenario=="guard": events=[{"type":"tool_execution_end","result":{"content":[{"type":"text","text":"TERTIUS_GUARD_FAILURE"}]}}]
        elif scenario=="auth": events=[{"type":"agent_error","error":{"message":"Unauthorized bearer sk-secret"}}]
        elif scenario=="rate": events=[{"type":"auto_retry_end","success":False,"finalError":"429 rate limit exceeded"}]
        elif scenario=="assistant-error": events=[{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"Unauthorized secret-token"}}]
        for event in events: print(json.dumps(event), flush=True)
        print(json.dumps({"id":request["id"],"success":True}), flush=True)
        print(json.dumps({"type":"agent_settled"}), flush=True)
    elif kind=="get_session_stats":
        print(json.dumps({"id":request["id"],"success":True,"data":{"tokens":{"input":11,"output":7,"cacheRead":3,"cacheWrite":2,"total":23},"cost":999}}), flush=True)
    elif kind=="abort":
        print(json.dumps({"id":request["id"],"success":True}), flush=True)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def settings(fake_pi: Path, scenario: str = "settled") -> dict:
    return {
        "executable": str(fake_pi),
        "cwd": fake_pi.parent,
        "system_prompt": "system",
        "timeout_seconds": 1,
        "max_turns": 12,
        "max_tool_calls": 48,
        "environment": {"FAKE_PI_SCENARIO": scenario},
    }


@pytest.mark.asyncio
async def test_u024_prompt_is_stdin_only_and_argv_is_fixed(fake_pi):
    result = await run_pi_agent("private prompt", **settings(fake_pi))
    assert result.usage.input_tokens == 11
    assert "private prompt" not in result.argv
    assert result.argv[1:] == [
        "--mode",
        "rpc",
        "--no-session",
        "--provider",
        "openai-codex",
        "--model",
        "gpt-5.5",
        "--thinking",
        "high",
        "--tools",
        "read,edit,write,grep,find,ls",
        "--no-extensions",
        "--extension",
        "/opt/tertius-pi/workspace-guard.ts",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--append-system-prompt",
        "system",
    ]


@pytest.mark.asyncio
async def test_prompt_uses_supplied_correlation_id(fake_pi):
    correlation_id = "job-correlation-id"
    options = settings(fake_pi)
    options["environment"]["FAKE_PI_EXPECT_PROMPT_ID"] = correlation_id

    result = await run_pi_agent(
        "private prompt", correlation_id=correlation_id, **options
    )

    assert result.usage.total_tokens == 23


@pytest.mark.asyncio
async def test_u025_stats_are_mapped_without_cost(fake_pi):
    result = await run_pi_agent("prompt", **settings(fake_pi))
    assert result.usage.model_dump() == {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_tokens": 3,
        "cache_write_tokens": 2,
        "total_tokens": 23,
    }


@pytest.mark.asyncio
async def test_u026_crlf_is_accepted_but_unicode_separator_is_not(tmp_path):
    path = tmp_path / "fake"
    path.write_text(
        '#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b\'{"id":"state","success":true,"data":{"model":{"provider":"openai-codex","id":"gpt-5.5"}}}\\r\\n\');sys.stdout.flush();sys.stdin.readline();sys.stdout.write(\'{bad\\u2028\');sys.stdout.flush()\n'
    )
    path.chmod(0o755)
    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("x", **settings(path))
    assert caught.value.code == "protocol_error"


@pytest.mark.asyncio
async def test_rpc_rejects_valid_json_eof_record_without_lf(tmp_path):
    path = tmp_path / "fake-no-lf"
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
request=json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"id":request["id"],"success":True,"data":{"model":{"provider":"openai-codex","id":"gpt-5.5"}}}))
sys.stdout.flush()
""",
        encoding="utf-8",
    )
    path.chmod(0o755)

    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("x", **settings(path))

    assert caught.value.code == "protocol_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "code", "retryable"),
    [
        ("malformed", "protocol_error", False),
        ("turn-limit", "agent_limit_exceeded", False),
        ("tool-limit", "agent_limit_exceeded", False),
        ("auth", "provider_auth", False),
        ("rate", "provider_rate_limit", True),
        ("guard", "tool_guard_failure", False),
        ("assistant-error", "provider_auth", False),
    ],
)
async def test_u027_to_u034_failures_are_bounded(fake_pi, scenario, code, retryable):
    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("prompt", **settings(fake_pi, scenario))
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert len(str(caught.value)) <= 500
    assert "sk-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_u028_interleaved_events_do_not_break_correlation(fake_pi):
    result = await run_pi_agent("prompt", **settings(fake_pi, "interleaved"))
    assert result.usage.total_tokens == 23


@pytest.mark.asyncio
async def test_rpc_bounds_event_and_unknown_response_buffers(fake_pi):
    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("prompt", **settings(fake_pi, "overflow"))
    assert caught.value.code == "protocol_error"

    result = await run_pi_agent("prompt", **settings(fake_pi, "noise"))
    assert result.usage.total_tokens == 23


@pytest.mark.asyncio
async def test_u031_timeout_is_bounded(fake_pi):
    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("prompt", **settings(fake_pi, "timeout"))
    assert caught.value.code == "timeout"


@pytest.mark.asyncio
async def test_u036_empty_assistant_text_is_allowed(fake_pi):
    assert (await run_pi_agent("prompt", **settings(fake_pi))).assistant_summary == ""
