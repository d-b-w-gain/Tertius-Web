from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import UUID

from pydantic import ValidationError
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind

from core.config import get_settings
from core.nats_client import (
    NatsPublisher,
    Publisher,
    connect_nats,
    ensure_pi_agent_stream,
    extract_nats_context,
    pull_pi_agent_request_subscription,
)
from core.pi_agent_telemetry import pi_agent_metric_attributes
from core.pi_agent_messages import (
    PiAgentChangedFile,
    PiAgentCommand,
    PiAgentResult,
    assert_pi_agent_command_size,
    assert_pi_agent_result_size,
    pi_agent_result_message_id,
)
from core.pi_agent_rpc import PiAgentRpcError, run_pi_agent
from core.telemetry import (
    configure_telemetry,
    counter_add,
    elapsed_seconds,
    histogram_record,
)


logger = logging.getLogger(__name__)
_MAX_FILE_BYTES = 200_000
def _metric_attributes(
    command: PiAgentCommand,
    *,
    status: str,
    failure_category: str | None = None,
    retryable: bool = False,
) -> dict[str, str | bool]:
    return pi_agent_metric_attributes(
        operation="pi_agent.worker",
        provider=command.provider,
        model=command.model,
        status=status,
        failure_category=failure_category,
        retryable=retryable,
    )


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManifestEntry:
    id: UUID
    filename: str
    sha256: str
    content: str


def build_conversation_prompt(prompt: str, prior_prompts: list[str]) -> str:
    if not prior_prompts:
        return prompt
    history = "\n".join(
        f"{index}. {prior_prompt}"
        for index, prior_prompt in enumerate(prior_prompts, start=1)
    )
    return (
        "Previous user requests, oldest first:\n"
        f"{history}\n\n"
        "Current user request:\n"
        f"{prompt}"
    )


def _secure_mkdir(path: Path) -> None:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)


def hydrate_workspace(command: PiAgentCommand, root: Path) -> dict[str, ManifestEntry]:
    if root.exists():
        raise WorkspaceError("workspace must be fresh")
    _secure_mkdir(root)
    manifest: dict[str, ManifestEntry] = {}
    canonical_root = root.resolve()
    for source in command.files:
        relative = Path(source.filename)
        destination = root / relative
        parent = root
        for component in relative.parts[:-1]:
            parent /= component
            if not parent.exists():
                _secure_mkdir(parent)
            elif not parent.is_dir() or parent.is_symlink():
                raise WorkspaceError("invalid workspace directory")
        if destination.exists() or destination.is_symlink():
            raise WorkspaceError("duplicate workspace path")
        if (
            canonical_root not in destination.parent.resolve().parents
            and destination.parent.resolve() != canonical_root
        ):
            raise WorkspaceError("workspace path escaped root")
        data = source.content.encode("utf-8")
        with destination.open("xb") as file:
            file.write(data)
        os.chmod(destination, 0o600)
        manifest[source.filename] = ManifestEntry(
            source.id, source.filename, source.sha256, source.content
        )
    return manifest


def scan_workspace(
    root: Path, manifest: dict[str, ManifestEntry]
) -> list[PiAgentChangedFile]:
    canonical_root = root.resolve()
    actual: set[str] = set()
    expected_directories = {
        parent.as_posix()
        for filename in manifest
        for parent in Path(filename).parents
        if parent.as_posix() != "."
    }
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_dir() and not path.is_symlink():
            if relative not in expected_directories:
                raise WorkspaceError("workspace directory set changed")
            continue
        actual.add(relative)
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise WorkspaceError("workspace contains a non-regular file")
        if canonical_root not in path.resolve().parents:
            raise WorkspaceError("workspace path escaped root")
    if actual != set(manifest):
        raise WorkspaceError("workspace file set changed")
    changed: list[PiAgentChangedFile] = []
    for filename, entry in manifest.items():
        path = root / filename
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise WorkspaceError("workspace file is oversized")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("workspace file is not UTF-8") from exc
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != entry.sha256:
            changed.append(
                PiAgentChangedFile(
                    id=entry.id, filename=filename, content=content, sha256=digest
                )
            )
    return changed


def _failure(
    command: PiAgentCommand,
    started_at: datetime,
    code: str,
    message: str,
    retryable: bool,
) -> PiAgentResult:
    return PiAgentResult(
        schema_version=1,
        job_id=command.job_id,
        tenant_id=command.tenant_id,
        project_id=command.project_id,
        status="failed",
        provider=command.provider,
        model=command.model,
        error_code=code,
        error_message=message[:500],
        retryable=retryable,
        worker_started_at=started_at,
        worker_finished_at=datetime.now(timezone.utc),
    )


async def execute_pi_agent_command(command: PiAgentCommand, settings) -> PiAgentResult:
    started_at = datetime.now(timezone.utc)
    workspace_base = Path(os.environ.get("TERTIUS_PI_WORKSPACE", "/workspace"))
    root: Path | None = None
    try:
        workspace_base.mkdir(mode=0o700, parents=True, exist_ok=True)
        if workspace_base.is_symlink() or not workspace_base.is_dir():
            raise WorkspaceError("invalid workspace base")
        root = Path(tempfile.mkdtemp(prefix="repo-", dir=workspace_base))
        root.rmdir()
        manifest = hydrate_workspace(command, root)
        rpc = await run_pi_agent(
            build_conversation_prompt(command.prompt, command.prior_prompts),
            correlation_id=str(command.job_id),
            cwd=root,
            provider=command.provider,
            model=command.model,
            thinking=command.thinking,
            system_prompt=settings.pi_agent_system_prompt,
            timeout_seconds=settings.pi_agent_timeout_seconds,
            max_turns=settings.pi_agent_max_turns,
            max_tool_calls=settings.pi_agent_max_tool_calls,
        )
        success_attributes = _metric_attributes(command, status="succeeded")
        histogram_record("tertius.pi_agent.turns", rpc.turns, success_attributes)
        histogram_record(
            "tertius.pi_agent.tool_calls",
            rpc.tool_calls,
            success_attributes,
        )
        changed = scan_workspace(root, manifest)
        result = PiAgentResult(
            schema_version=1,
            job_id=command.job_id,
            tenant_id=command.tenant_id,
            project_id=command.project_id,
            status="succeeded",
            outcome="changed" if changed else "no_changes",
            provider=command.provider,
            model=command.model,
            assistant_summary=rpc.assistant_summary if changed else "",
            changed_files=changed,
            usage=rpc.usage,
            worker_started_at=started_at,
            worker_finished_at=datetime.now(timezone.utc),
        )
        try:
            assert_pi_agent_result_size(result, settings.pi_agent_result_max_bytes)
        except ValueError:
            return _failure(
                command,
                started_at,
                "result_too_large",
                "Edited files exceed the worker result size limit",
                False,
            )
        return result
    except PiAgentRpcError as exc:
        return _failure(command, started_at, exc.code, str(exc), exc.retryable)
    except WorkspaceError:
        return _failure(
            command,
            started_at,
            "invalid_workspace",
            "Worker workspace validation failed",
            False,
        )
    except Exception:
        return _failure(
            command, started_at, "worker_error", "Pi agent worker failed", True
        )
    finally:
        if root is not None and root.exists():
            import shutil

            shutil.rmtree(root)


async def _heartbeat(msg, ack_wait_seconds: float = 90) -> None:
    interval = max(0.1, min(30.0, ack_wait_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        await msg.in_progress()


async def handle_pi_agent_request_message(msg, publisher: Publisher, settings) -> None:
    try:
        command = PiAgentCommand.model_validate_json(msg.data)
        assert_pi_agent_command_size(command, settings.pi_agent_request_max_bytes)
    except ValidationError, ValueError:
        logger.warning("Rejected invalid Pi agent command")
        await msg.term()
        return

    if (
        command.model != settings.pi_agent_model
        or command.thinking != settings.pi_agent_thinking
    ):
        logger.warning("Rejected Pi agent command with unsupported runtime selection")
        await msg.term()
        return

    headers = getattr(msg, "headers", None)
    if headers is not None:
        parent_context = extract_nats_context(headers)
    else:
        carrier = {
            key: value
            for key, value in {
                "traceparent": command.traceparent,
                "tracestate": command.tracestate,
            }.items()
            if value is not None
        }
        parent_context = propagate.extract(carrier)
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "pi_agent.command.consume",
        context=parent_context,
        kind=SpanKind.CONSUMER,
        attributes=_metric_attributes(command, status="started"),
    ):
        await _process_pi_agent_request_message(msg, publisher, settings, command)


async def _process_pi_agent_request_message(
    msg, publisher: Publisher, settings, command: PiAgentCommand
) -> None:
    counter_add(
        "tertius.pi_agent.job.started.count",
        1,
        _metric_attributes(command, status="started"),
    )
    start = perf_counter()
    heartbeat = asyncio.create_task(_heartbeat(msg, settings.pi_agent_ack_wait_seconds))
    operation = asyncio.create_task(execute_pi_agent_command(command, settings))
    publish: asyncio.Task[None] | None = None
    try:
        done, _ = await asyncio.wait(
            {operation, heartbeat}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            await heartbeat
            raise RuntimeError("heartbeat ended unexpectedly")
        result = await operation

        trace_headers: dict[str, str] = {}
        propagate.inject(trace_headers)
        result = result.model_copy(
            update={
                "traceparent": trace_headers.get("traceparent"),
                "tracestate": trace_headers.get("tracestate"),
            }
        )
        publish = asyncio.create_task(
            publisher.publish_json(
                settings.pi_agent_result_subject,
                result,
                message_id=pi_agent_result_message_id(result),
                telemetry_message_id=(
                    "pi-result:"
                    + hashlib.sha256(str(command.job_id).encode("ascii")).hexdigest()[
                        :16
                    ]
                ),
            )
        )
        done, _ = await asyncio.wait(
            {publish, heartbeat}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            publish.cancel()
            await asyncio.gather(publish, return_exceptions=True)
            await heartbeat
            raise RuntimeError("heartbeat ended unexpectedly")
        await publish
    except BaseException as exc:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.warning("Pi agent request processing failed before ACK")
        await msg.nak()
        return
    finally:
        if publish is not None and not publish.done():
            publish.cancel()
            await asyncio.gather(publish, return_exceptions=True)
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)

    await msg.ack()
    labels = _metric_attributes(
        command,
        status=result.status,
        failure_category=result.error_code,
        retryable=result.retryable,
    )
    counter_add("tertius.pi_agent.worker.completed.count", 1, labels)
    histogram_record("tertius.pi_agent.job.duration", elapsed_seconds(start), labels)
    for token_class, value in (
        ("input", result.usage.input_tokens),
        ("output", result.usage.output_tokens),
        ("cache_read", result.usage.cache_read_tokens),
        ("cache_write", result.usage.cache_write_tokens),
        ("total", result.usage.total_tokens),
    ):
        histogram_record(f"tertius.pi_agent.tokens.{token_class}", value, labels)


async def run_once() -> int:
    settings = get_settings()
    configure_telemetry(settings, "tertius-pi-agent-job")
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_pi_agent_stream(nc, settings)
        subscription = await pull_pi_agent_request_subscription(js, settings)
        try:
            messages = await subscription.fetch(batch=1, timeout=5)
        except TimeoutError:
            return 0
        publisher = NatsPublisher(js)
        for msg in messages:
            await handle_pi_agent_request_message(msg, publisher, settings)
        return 0
    finally:
        await nc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run_once()))


if __name__ == "__main__":
    main()
