from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from core.pi_agent_rpc import PiAgentRpcError, build_pi_argv, run_pi_agent


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
        print(json.dumps({"id":request["id"],"success":True,"data":{"model":{"provider":"openai-codex","id":"gpt-5.6-sol"}}}), flush=True)
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
        elif scenario=="guard": events=[{"type":"tool_execution_end","toolCallId":"call-1","toolName":"read","result":{"content":[{"type":"text","text":"TERTIUS_GUARD_FAILURE"}],"details":{}},"isError":True}]
        elif scenario=="assistant-mentions-guard": events=[{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"The literal TERTIUS_GUARD_FAILURE is documented here."}],"stopReason":"stop"}}]
        elif scenario=="user-mentions-guard": events=[{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"Explain TERTIUS_GUARD_FAILURE"}]}}]
        elif scenario=="successful-tool-mentions-guard": events=[{"type":"tool_execution_end","toolCallId":"call-1","toolName":"read","result":{"content":[{"type":"text","text":"TERTIUS_GUARD_FAILURE"}],"details":{}},"isError":False}]
        elif scenario=="large-tool-output": events=[{"type":"tool_execution_end","toolCallId":"call-1","toolName":"read","result":{"content":[{"type":"text","text":"x"*100000}],"details":{}},"isError":False}]
        elif scenario=="assistant-summary": events=[
            {"type":"message_update","message":{"role":"assistant","content":[{"type":"text","text":"UPDATE_SENTINEL"}]}},
            {"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"EARLIER_SENTINEL"}],"stopReason":"stop"}},
            {"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"USER_SENTINEL"}]}},
            {"type":"tool_execution_end","toolCallId":"call-1","toolName":"read","result":{"content":[{"type":"text","text":"TOOL_SENTINEL"}],"details":{}},"isError":False},
            {"type":"message_end","message":{"role":"assistant","content":[{"type":"thinking","thinking":"THINKING_SENTINEL"*210},{"type":"text","text":"FINAL_FIRST_BLOCK "},{"type":"text","text":"F"*2100},{"type":"text","text":" FINAL_SECOND_BLOCK"}],"stopReason":"stop"}},
        ]
        elif scenario=="progress": events=[
            {"type":"message_update","message":{"role":"assistant"},"assistantMessageEvent":{"type":"thinking_delta","delta":"Checking workspace","tertiusReasoningSummary":True}},
            {"type":"message_update","message":{"role":"assistant","content":[{"type":"text","text":"SOURCE_SENTINEL"}]},"assistantMessageEvent":{"type":"text_delta","delta":"ASSISTANT_SENTINEL"}},
            {"type":"tool_execution_start","toolCallId":"call-read","toolName":"read","args":{"path":"nested/model.py","content":"ARG_SOURCE_SENTINEL"}},
            {"type":"tool_execution_update","toolCallId":"call-read","toolName":"read","args":{"path":"nested/model.py"},"partialResult":{"content":[{"type":"text","text":"UPDATE_RESULT_SENTINEL"}]}},
            {"type":"tool_execution_end","toolCallId":"call-read","toolName":"read","result":{"content":[{"type":"text","text":"RESULT_SENTINEL"}]},"isError":False},
            {"type":"tool_execution_start","toolCallId":"call-edit","toolName":"edit","args":{"file_path":os.path.join(os.getcwd(),"src/model.py"),"replacement":"REPLACEMENT_SENTINEL"}},
            {"type":"tool_execution_end","toolCallId":"call-edit","toolName":"edit","result":{"content":[{"type":"text","text":"EDIT_RESULT_SENTINEL"}]},"isError":True},
            {"type":"tool_execution_start","toolCallId":"call-traversal","toolName":"write","args":{"path":"../outside.py","content":"TRAVERSAL_SOURCE_SENTINEL"}},
            {"type":"tool_execution_end","toolCallId":"call-traversal","toolName":"write","result":{"content":[{"type":"text","text":"TRAVERSAL_RESULT_SENTINEL"}]},"isError":False},
            {"type":"tool_execution_start","toolCallId":"call-outside","toolName":"read","args":{"path":"/etc/passwd"}},
            {"type":"tool_execution_end","toolCallId":"call-outside","toolName":"read","result":{"content":[{"type":"text","text":"OUTSIDE_RESULT_SENTINEL"}]},"isError":False},
            {"type":"tool_execution_start","toolCallId":"call-unknown","toolName":"bash","args":{"command":"UNKNOWN_ARG_SENTINEL"}},
            {"type":"tool_execution_end","toolCallId":"call-unknown","toolName":"bash","result":{"content":[{"type":"text","text":"UNKNOWN_RESULT_SENTINEL"}]},"isError":False},
            {"type":"tool_execution_end","toolCallId":"call-orphan","toolName":"read","result":{"content":[{"type":"text","text":"ORPHAN_RESULT_SENTINEL"}]},"isError":False},
        ]
        elif scenario=="long-reasoning": events=[
            {"type":"message_update","message":{"role":"assistant"},"assistantMessageEvent":{"type":"thinking_delta","delta":"R"*1201,"tertiusReasoningSummary":True}},
        ]
        elif scenario=="mixed-reasoning": events=[
            {"type":"message_update","message":{"role":"assistant"},"assistantMessageEvent":{"type":"thinking_delta","delta":"SAFE_SUMMARY_SENTINEL","tertiusReasoningSummary":True}},
            {"type":"message_update","message":{"role":"assistant"},"assistantMessageEvent":{"type":"thinking_delta","delta":"RAW_REASONING_SENTINEL","tertiusReasoningSummary":False}},
            {"type":"message_update","message":{"role":"assistant"},"assistantMessageEvent":{"type":"thinking_delta","delta":"UNMARKED_REASONING_SENTINEL"}},
        ]
        elif scenario=="unsafe-targets": events=[
            {"type":"tool_execution_start","toolCallId":"call-contained","toolName":"read","args":{"path":"nested/../model.py"}},
            {"type":"tool_execution_end","toolCallId":"call-contained","toolName":"read","result":{"content":[]},"isError":False},
            {"type":"tool_execution_start","toolCallId":"call-symlink","toolName":"read","args":{"path":"escape/secret.py"}},
            {"type":"tool_execution_end","toolCallId":"call-symlink","toolName":"read","result":{"content":[]},"isError":False},
        ]
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
    system_prompt_path = fake_pi.parent / "APPEND_SYSTEM.md"
    system_prompt_path.write_text("system", encoding="utf-8")
    return {
        "executable": str(fake_pi),
        "cwd": fake_pi.parent,
        "system_prompt_path": system_prompt_path,
        "timeout_seconds": 1,
        "max_turns": 12,
        "max_tool_calls": 48,
        "environment": {"FAKE_PI_SCENARIO": scenario},
    }


@pytest.mark.asyncio
async def test_rpc_emits_thinking_and_correlated_safe_tool_milestones(fake_pi):
    progress: list[Any] = []

    async def capture(event: Any) -> None:
        await asyncio.sleep(0)
        progress.append(event)

    result = await run_pi_agent(
        "prompt",
        **settings(fake_pi, "progress"),
        progress_callback=capture,
    )

    assert result.tool_calls == 5
    assert [asdict(event) for event in progress] == [
        {
            "kind": "reasoning_delta",
            "text": "Checking workspace",
            "tool_name": None,
            "target": None,
            "is_error": None,
        },
        {
            "kind": "tool_started",
            "text": None,
            "tool_name": "read",
            "target": "nested/model.py",
            "is_error": None,
        },
        {
            "kind": "tool_finished",
            "text": None,
            "tool_name": "read",
            "target": "nested/model.py",
            "is_error": False,
        },
        {
            "kind": "tool_started",
            "text": None,
            "tool_name": "edit",
            "target": "src/model.py",
            "is_error": None,
        },
        {
            "kind": "tool_finished",
            "text": None,
            "tool_name": "edit",
            "target": "src/model.py",
            "is_error": True,
        },
        {
            "kind": "tool_started",
            "text": None,
            "tool_name": "write",
            "target": None,
            "is_error": None,
        },
        {
            "kind": "tool_finished",
            "text": None,
            "tool_name": "write",
            "target": None,
            "is_error": False,
        },
        {
            "kind": "tool_started",
            "text": None,
            "tool_name": "read",
            "target": None,
            "is_error": None,
        },
        {
            "kind": "tool_finished",
            "text": None,
            "tool_name": "read",
            "target": None,
            "is_error": False,
        },
    ]


@pytest.mark.asyncio
async def test_rpc_progress_excludes_raw_args_results_source_and_tool_ids(fake_pi):
    progress: list[Any] = []

    async def capture(event: Any) -> None:
        progress.append(event)

    await run_pi_agent(
        "prompt",
        **settings(fake_pi, "progress"),
        progress_callback=capture,
    )

    assert all(
        set(asdict(event)) == {"kind", "text", "tool_name", "target", "is_error"}
        for event in progress
    )
    serialized = repr(progress)
    for sentinel in (
        "SOURCE_SENTINEL",
        "ASSISTANT_SENTINEL",
        "ARG_SOURCE_SENTINEL",
        "UPDATE_RESULT_SENTINEL",
        "RESULT_SENTINEL",
        "REPLACEMENT_SENTINEL",
        "EDIT_RESULT_SENTINEL",
        "TRAVERSAL_SOURCE_SENTINEL",
        "TRAVERSAL_RESULT_SENTINEL",
        "OUTSIDE_RESULT_SENTINEL",
        "UNKNOWN_ARG_SENTINEL",
        "UNKNOWN_RESULT_SENTINEL",
        "ORPHAN_RESULT_SENTINEL",
        "call-read",
    ):
        assert sentinel not in serialized


@pytest.mark.asyncio
async def test_rpc_preserves_full_thinking_delta_for_worker_batch_splitting(fake_pi):
    progress: list[Any] = []

    async def capture(event: Any) -> None:
        progress.append(event)

    await run_pi_agent(
        "prompt",
        **settings(fake_pi, "long-reasoning"),
        progress_callback=capture,
    )

    assert len(progress) == 1
    assert progress[0].text == "R" * 1201


@pytest.mark.asyncio
async def test_rpc_emits_only_provenance_marked_reasoning_summaries(fake_pi):
    progress: list[Any] = []

    async def capture(event: Any) -> None:
        progress.append(event)

    await run_pi_agent(
        "prompt",
        **settings(fake_pi, "mixed-reasoning"),
        progress_callback=capture,
    )

    assert [event.text for event in progress] == ["SAFE_SUMMARY_SENTINEL"]
    assert "RAW_REASONING_SENTINEL" not in repr(progress)
    assert "UNMARKED_REASONING_SENTINEL" not in repr(progress)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing_kind",
    ["reasoning_delta", "tool_started", "tool_finished"],
)
async def test_rpc_progress_callback_failure_does_not_fail_terminal_result(
    fake_pi, failing_kind
):
    observed: list[str] = []

    async def capture(event: Any) -> None:
        observed.append(event.kind)
        if event.kind == failing_kind:
            raise RuntimeError("CALLBACK_CONTENT_SENTINEL")

    result = await run_pi_agent(
        "prompt",
        **settings(fake_pi, "progress"),
        progress_callback=capture,
    )

    assert failing_kind in observed
    assert result.tool_calls == 5
    assert result.usage.total_tokens == 23


@pytest.mark.asyncio
async def test_rpc_progress_callback_cancellation_propagates(fake_pi):
    async def cancel(_: Any) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_pi_agent(
            "prompt",
            **settings(fake_pi, "long-reasoning"),
            progress_callback=cancel,
        )


@pytest.mark.asyncio
async def test_rpc_rejects_contained_traversal_and_symlink_escape_targets(
    fake_pi, tmp_path
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (fake_pi.parent / "escape").symlink_to(outside, target_is_directory=True)
    progress: list[Any] = []

    async def capture(event: Any) -> None:
        progress.append(event)

    await run_pi_agent(
        "prompt",
        **settings(fake_pi, "unsafe-targets"),
        progress_callback=capture,
    )

    assert [event.target for event in progress] == [None, None, None, None]


def test_pi_argv_uses_prompt_path_without_prompt_bytes(tmp_path):
    path = tmp_path / "APPEND_SYSTEM.md"
    path.write_text("PROMPT_ARGV_SENTINEL", encoding="utf-8")
    argv = build_pi_argv(
        "pi",
        provider="openai-codex",
        model="gpt-5.5",
        thinking="high",
        system_prompt_path=path,
        extension_path="/opt/tertius-pi/workspace-guard.ts",
    )
    index = argv.index("--append-system-prompt")
    assert argv[index + 1] == str(path)
    assert "PROMPT_ARGV_SENTINEL" not in argv


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
        "gpt-5.6-sol",
        "--thinking",
        "medium",
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
        str(fake_pi.parent / "APPEND_SYSTEM.md"),
    ]


@pytest.mark.asyncio
async def test_rpc_does_not_inject_prompt_bytes_into_child_argv_or_env(
    monkeypatch, fake_pi
):
    sentinel = "PROMPT_CHILD_ENV_SENTINEL"
    options = settings(fake_pi)
    options["system_prompt_path"].write_text(sentinel, encoding="utf-8")
    captured = {}
    original_spawn = asyncio.create_subprocess_exec

    async def capture_spawn(*argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        captured["limit"] = kwargs["limit"]
        return await original_spawn(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_spawn)

    await run_pi_agent("prompt", **options)

    assert all(sentinel not in argument for argument in captured["argv"])
    assert all(sentinel not in value for value in captured["env"].values())
    assert captured["limit"] >= 2_000_000


@pytest.mark.asyncio
async def test_rpc_rejects_missing_prompt_path_before_spawn(
    monkeypatch, fake_pi, tmp_path
):
    async def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    options = settings(fake_pi)
    options["system_prompt_path"] = tmp_path / "missing.md"
    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("prompt", **options)
    assert caught.value.code == "worker_config_error"


@pytest.mark.asyncio
async def test_rpc_rejects_directory_prompt_path_before_spawn(
    monkeypatch, fake_pi, tmp_path
):
    async def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("subprocess must not start")

    prompt_directory = tmp_path / "prompt-directory"
    prompt_directory.mkdir()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    options = settings(fake_pi)
    options["system_prompt_path"] = prompt_directory

    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("prompt", **options)

    assert caught.value.code == "worker_config_error"


@pytest.mark.asyncio
async def test_rpc_rejects_unreadable_prompt_path_before_spawn(
    monkeypatch, fake_pi, tmp_path
):
    async def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("subprocess must not start")

    prompt_path = tmp_path / "unreadable.md"
    prompt_path.write_text("policy", encoding="utf-8")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    monkeypatch.setattr(os, "access", lambda _path, _mode: False)
    options = settings(fake_pi)
    options["system_prompt_path"] = prompt_path

    with pytest.raises(PiAgentRpcError) as caught:
        await run_pi_agent("prompt", **options)

    assert caught.value.code == "worker_config_error"


@pytest.mark.asyncio
async def test_rpc_captures_only_bounded_final_assistant_text(fake_pi, tmp_path):
    path = tmp_path / "APPEND_SYSTEM.md"
    path.write_text("policy", encoding="utf-8")
    options = settings(fake_pi, "assistant-summary")
    options["system_prompt_path"] = path
    result = await run_pi_agent("prompt", **options)
    final_text = "FINAL_FIRST_BLOCK " + "F" * 2100 + " FINAL_SECOND_BLOCK"
    assert result.assistant_summary == final_text[:2000]
    assert len(result.assistant_summary) <= 2000
    assert "UPDATE_SENTINEL" not in result.assistant_summary
    assert "EARLIER_SENTINEL" not in result.assistant_summary
    assert "USER_SENTINEL" not in result.assistant_summary
    assert "TOOL_SENTINEL" not in result.assistant_summary
    assert "THINKING_SENTINEL" not in result.assistant_summary


@pytest.mark.asyncio
async def test_rpc_accepts_large_tool_result_lines(fake_pi):
    result = await run_pi_agent("prompt", **settings(fake_pi, "large-tool-output"))
    assert result.usage.total_tokens == 23


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
        '#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b\'{"id":"state","success":true,"data":{"model":{"provider":"openai-codex","id":"gpt-5.6-sol"}}}\\r\\n\');sys.stdout.flush();sys.stdin.readline();sys.stdout.write(\'{bad\\u2028\');sys.stdout.flush()\n'
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
sys.stdout.write(json.dumps({"id":request["id"],"success":True,"data":{"model":{"provider":"openai-codex","id":"gpt-5.6-sol"}}}))
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
@pytest.mark.parametrize(
    "scenario",
    [
        "assistant-mentions-guard",
        "user-mentions-guard",
        "successful-tool-mentions-guard",
    ],
)
async def test_guard_sentinel_in_ordinary_rpc_content_is_not_a_guard_failure(
    fake_pi, scenario
):
    result = await run_pi_agent("prompt", **settings(fake_pi, scenario))

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
