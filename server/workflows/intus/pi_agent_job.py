from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

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
from core.pi_agent_conversation import (
    render_conversation_context,
    render_legacy_prior_prompts,
)
from core.pi_agent_messages import (
    PiAgentChangedFile,
    PiAgentCommand,
    PiAgentProgressBatch,
    PiAgentProgressEvent,
    PiAgentResult,
    assert_pi_agent_command_size,
    assert_pi_agent_progress_size,
    assert_pi_agent_result_size,
    pi_agent_progress_message_id,
    pi_agent_result_message_id,
)
from core.pi_agent_telemetry import pi_agent_metric_attributes
from core.pi_agent_prompt import (
    PiAgentPromptError,
    load_pi_agent_prompt,
    render_pi_agent_user_prompt,
)
from core.pi_agent_rpc import PiAgentRpcError, PiAgentRpcProgressEvent, run_pi_agent
from core.telemetry import (
    configure_telemetry,
    counter_add,
    elapsed_seconds,
    histogram_record,
)


logger = logging.getLogger(__name__)
_MAX_FILE_BYTES = 2_000_000
_PROGRESS_FLUSH_SECONDS = 0.5
_PROGRESS_PUBLISH_TIMEOUT_SECONDS = 1.0
_PROGRESS_DRAIN_TIMEOUT_SECONDS = 4.0
_PROGRESS_RETRY_DELAYS = (0.1, 0.2)
_MAX_PROGRESS_EVENTS = 16
_MAX_PROGRESS_QUEUE_BATCHES = 64
_MAX_PROGRESS_TEXT = 1000


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


def build_coding_agent_prompt(command: PiAgentCommand) -> str:
    active_filename = next(
        (file.filename for file in command.files if file.id == command.active_file_id),
        None,
    )
    if command.schema_version == 2:
        assert command.conversation is not None
        conversation = render_conversation_context(command.conversation, command.prompt)
    else:
        conversation = render_legacy_prior_prompts(
            command.prior_prompts, command.prompt
        )
    return render_pi_agent_user_prompt(
        conversation_prompt=conversation,
        editable_filenames=[file.filename for file in command.files],
        active_filename=active_filename,
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
    *,
    execution_id: UUID | None = None,
) -> PiAgentResult:
    return PiAgentResult(
        schema_version=1,
        execution_id=execution_id or uuid4(),
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


class _PiAgentProgressBatcher:
    def __init__(
        self,
        command: PiAgentCommand,
        execution_id: UUID,
        publisher: Publisher,
        settings,
        *,
        flush_seconds: float = _PROGRESS_FLUSH_SECONDS,
        publish_timeout_seconds: float | None = None,
        drain_timeout_seconds: float | None = None,
    ) -> None:
        self._command = command
        self._execution_id = execution_id
        self._publisher = publisher
        self._subject = settings.pi_agent_result_subject
        self._max_bytes = settings.pi_agent_result_max_bytes
        self._flush_seconds = flush_seconds
        self._publish_timeout_seconds = (
            _PROGRESS_PUBLISH_TIMEOUT_SECONDS
            if publish_timeout_seconds is None
            else publish_timeout_seconds
        )
        self._drain_timeout_seconds = (
            _PROGRESS_DRAIN_TIMEOUT_SECONDS
            if drain_timeout_seconds is None
            else drain_timeout_seconds
        )
        self._events: list[PiAgentProgressEvent] = []
        self._next_event_sequence = 1
        self._next_batch_sequence = 1
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task[None] | None = None
        self._timer_tasks: set[asyncio.Task[None]] = set()
        self._publish_queue: asyncio.Queue[list[PiAgentProgressEvent] | None] = (
            asyncio.Queue(maxsize=_MAX_PROGRESS_QUEUE_BATCHES)
        )
        self._publisher_task = asyncio.create_task(self._publish_loop())
        self._closed = False
        self._disabled = False
        self._warning_emitted = False

    async def add(self, event: PiAgentRpcProgressEvent) -> None:
        async with self._lock:
            if self._closed or self._disabled:
                return
            if event.kind == "reasoning_delta":
                self._add_reasoning_locked(event.text or "")
            else:
                if len(self._events) >= _MAX_PROGRESS_EVENTS:
                    self._flush_locked()
                self._events.append(self._new_event(event))
                self._flush_locked()
            if self._events and self._timer is None:
                self._schedule_timer_locked()

    async def flush(self) -> None:
        async with self._lock:
            if not self._closed and not self._disabled:
                self._flush_locked()

    async def close(self) -> None:
        async with self._lock:
            if not self._closed:
                self._closed = True
                self._cancel_timer_locked()
                self._flush_locked()
                self._enqueue_stop_locked()
            timers = tuple(self._timer_tasks)
        await self._wait_for_tasks(timers)
        try:
            async with asyncio.timeout(self._drain_timeout_seconds):
                await asyncio.gather(self._publisher_task, return_exceptions=True)
        except TimeoutError:
            self._disable_progress()
            self._publisher_task.cancel()
            await asyncio.gather(self._publisher_task, return_exceptions=True)
        except asyncio.CancelledError:
            self._publisher_task.cancel()
            await asyncio.gather(self._publisher_task, return_exceptions=True)
            raise

    async def cancel(self) -> None:
        async with self._lock:
            self._closed = True
            self._events.clear()
            self._cancel_timer_locked()
            self._drop_queued_progress()
            self._publisher_task.cancel()
            tasks = (*self._timer_tasks, self._publisher_task)
        await self._wait_for_tasks(tasks)

    def _add_reasoning_locked(self, text: str) -> None:
        remaining = text
        while remaining:
            if self._events and self._events[-1].kind == "reasoning_delta":
                previous = self._events[-1]
                previous_text = previous.text or ""
                capacity = _MAX_PROGRESS_TEXT - len(previous_text)
                if capacity:
                    suffix = remaining[:capacity]
                    self._events[-1] = previous.model_copy(
                        update={"text": previous_text + suffix}
                    )
                    remaining = remaining[len(suffix) :]
                    if not remaining:
                        return
            if len(self._events) >= _MAX_PROGRESS_EVENTS:
                self._flush_locked()
                if self._disabled:
                    return
            chunk = remaining[:_MAX_PROGRESS_TEXT]
            remaining = remaining[len(chunk) :]
            self._events.append(
                self._new_event(
                    PiAgentRpcProgressEvent(kind="reasoning_delta", text=chunk)
                )
            )

    def _new_event(self, event: PiAgentRpcProgressEvent) -> PiAgentProgressEvent:
        progress = PiAgentProgressEvent(
            sequence=self._next_event_sequence,
            kind=event.kind,
            text=event.text,
            tool_name=event.tool_name,
            target=event.target,
            is_error=event.is_error,
            occurred_at=datetime.now(timezone.utc),
        )
        self._next_event_sequence += 1
        return progress

    def _schedule_timer_locked(self) -> None:
        timer = asyncio.create_task(self._flush_after_delay())
        self._timer = timer
        self._timer_tasks.add(timer)
        timer.add_done_callback(self._timer_tasks.discard)

    def _cancel_timer_locked(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    async def _flush_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._flush_seconds)
            async with self._lock:
                if self._timer is asyncio.current_task():
                    self._timer = None
                if not self._closed and not self._disabled:
                    self._flush_locked()
        except asyncio.CancelledError:
            return

    def _flush_locked(self) -> None:
        if not self._events:
            self._cancel_timer_locked()
            return
        self._cancel_timer_locked()
        events = self._events
        self._events = []
        try:
            self._publish_queue.put_nowait(events)
        except asyncio.QueueFull:
            self._disable_progress()

    def _enqueue_stop_locked(self) -> None:
        if self._disabled or self._publisher_task.done():
            return
        try:
            self._publish_queue.put_nowait(None)
        except asyncio.QueueFull:
            self._disable_progress()

    async def _publish_loop(self) -> None:
        try:
            while not self._disabled:
                events = await self._publish_queue.get()
                if events is None:
                    return
                if not await self._publish_events(events):
                    self._disable_progress()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._disable_progress()

    async def _publish_events(self, events: list[PiAgentProgressEvent]) -> bool:
        batch_sequence = self._next_batch_sequence
        trace_headers: dict[str, str] = {}
        propagate.inject(trace_headers)
        batch = PiAgentProgressBatch(
            message_type="progress",
            schema_version=1,
            execution_id=self._execution_id,
            job_id=self._command.job_id,
            tenant_id=self._command.tenant_id,
            project_id=self._command.project_id,
            batch_sequence=batch_sequence,
            events=events,
            traceparent=trace_headers.get("traceparent"),
            tracestate=trace_headers.get("tracestate"),
        )
        try:
            assert_pi_agent_progress_size(batch, self._max_bytes)
        except ValueError:
            if len(events) > 1:
                midpoint = len(events) // 2
                if not await self._publish_events(events[:midpoint]):
                    return False
                return await self._publish_events(events[midpoint:])
            self._warn_publish_failure()
            return True

        self._next_batch_sequence += 1
        telemetry_digest = hashlib.sha256(
            (
                f"{self._command.job_id}:{self._execution_id}:"
                f"{batch.batch_sequence}"
            ).encode("ascii")
        ).hexdigest()[:16]
        for attempt in range(3):
            try:
                async with asyncio.timeout(self._publish_timeout_seconds):
                    await self._publisher.publish_json(
                        self._subject,
                        batch,
                        message_id=pi_agent_progress_message_id(batch),
                        telemetry_message_id=f"pi-progress:{telemetry_digest}",
                    )
                return True
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(_PROGRESS_RETRY_DELAYS[attempt])
        return False

    def _disable_progress(self) -> None:
        if not self._disabled:
            self._disabled = True
            self._warn_publish_failure()
        self._events.clear()
        self._drop_queued_progress()
        if (
            self._publisher_task is not asyncio.current_task()
            and not self._publisher_task.done()
        ):
            self._publisher_task.cancel()

    def _warn_publish_failure(self) -> None:
        if not self._warning_emitted:
            self._warning_emitted = True
            logger.warning("Pi agent progress publish failed")

    def _drop_queued_progress(self) -> None:
        while True:
            try:
                self._publish_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    @staticmethod
    async def _wait_for_tasks(tasks) -> None:
        current = asyncio.current_task()
        pending = [task for task in tasks if task is not current]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def execute_pi_agent_command(
    command: PiAgentCommand,
    settings,
    publisher: Publisher | None = None,
) -> PiAgentResult:
    started_at = datetime.now(timezone.utc)
    execution_id = uuid4()
    batcher = (
        _PiAgentProgressBatcher(command, execution_id, publisher, settings)
        if publisher is not None
        else None
    )
    workspace_base = Path(os.environ.get("TERTIUS_PI_WORKSPACE", "/workspace"))
    root: Path | None = None
    try:
        snapshot = load_pi_agent_prompt()
        if (
            command.schema_version == 2
            and command.system_prompt_sha256 != snapshot.sha256
        ):
            return _failure(
                command,
                started_at,
                "worker_config_mismatch",
                "AI worker configuration changed; retry after deployment completes.",
                True,
                execution_id=execution_id,
            )
        workspace_base.mkdir(mode=0o700, parents=True, exist_ok=True)
        if workspace_base.is_symlink() or not workspace_base.is_dir():
            raise WorkspaceError("invalid workspace base")
        root = Path(tempfile.mkdtemp(prefix="repo-", dir=workspace_base))
        root.rmdir()
        manifest = hydrate_workspace(command, root)
        rpc = await run_pi_agent(
            build_coding_agent_prompt(command),
            correlation_id=str(command.job_id),
            cwd=root,
            provider=command.provider,
            model=command.model,
            thinking=command.thinking,
            system_prompt_path=snapshot.path,
            timeout_seconds=settings.pi_agent_timeout_seconds,
            max_turns=settings.pi_agent_max_turns,
            max_tool_calls=settings.pi_agent_max_tool_calls,
            progress_callback=batcher.add if batcher is not None else None,
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
            execution_id=execution_id,
            job_id=command.job_id,
            tenant_id=command.tenant_id,
            project_id=command.project_id,
            status="succeeded",
            outcome="changed" if changed else "no_changes",
            provider=command.provider,
            model=command.model,
            assistant_summary=rpc.assistant_summary
            or ("Updated files." if changed else "No files changed."),
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
                execution_id=execution_id,
            )
        return result
    except asyncio.CancelledError:
        if batcher is not None:
            await batcher.cancel()
        raise
    except PiAgentPromptError:
        return _failure(
            command,
            started_at,
            "worker_config_error",
            "Pi agent policy is unavailable.",
            False,
            execution_id=execution_id,
        )
    except PiAgentRpcError as exc:
        return _failure(
            command,
            started_at,
            exc.code,
            str(exc),
            exc.retryable,
            execution_id=execution_id,
        )
    except WorkspaceError:
        return _failure(
            command,
            started_at,
            "invalid_workspace",
            "Worker workspace validation failed",
            False,
            execution_id=execution_id,
        )
    except Exception:
        return _failure(
            command,
            started_at,
            "worker_error",
            "Pi agent worker failed",
            True,
            execution_id=execution_id,
        )
    finally:
        if batcher is not None:
            await batcher.close()
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
    operation = asyncio.create_task(
        execute_pi_agent_command(command, settings, publisher)
    )
    publish: asyncio.Task[None] | None = None
    nak_task: asyncio.Task[None] | None = None

    async def nak_once() -> None:
        nonlocal nak_task
        if nak_task is None:
            nak_task = asyncio.create_task(msg.nak())
        await asyncio.shield(nak_task)

    async def wait_for_cleanup(*tasks: asyncio.Task) -> None:
        await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

    async def process_until_published() -> PiAgentResult:
        nonlocal publish
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
        for attempt in range(3):
            publish = asyncio.create_task(
                publisher.publish_json(
                    settings.pi_agent_result_subject,
                    result,
                    message_id=pi_agent_result_message_id(result),
                    telemetry_message_id=(
                        "pi-result:"
                        + hashlib.sha256(
                            str(command.job_id).encode("ascii")
                        ).hexdigest()[:16]
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
            try:
                await publish
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.1 * (2**attempt))
        return result

    try:
        try:
            result = await process_until_published()
        except asyncio.CancelledError:
            raise
        except BaseException:
            operation.cancel()
            await wait_for_cleanup(operation)
            if publish is not None and not publish.done():
                publish.cancel()
                await wait_for_cleanup(publish)
            heartbeat.cancel()
            await wait_for_cleanup(heartbeat)
            logger.warning("Pi agent request processing failed before ACK")
            await nak_once()
            return

        heartbeat.cancel()
        await wait_for_cleanup(heartbeat)
    except asyncio.CancelledError:
        if publish is not None and not publish.done():
            publish.cancel()
            await asyncio.gather(publish, return_exceptions=True)
        operation.cancel()
        heartbeat.cancel()
        try:
            await nak_once()
        except Exception:
            logger.warning("Failed to NAK cancelled Pi agent request")
        await asyncio.gather(operation, heartbeat, return_exceptions=True)
        raise

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


async def _run_once_with_sigterm() -> int:
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(run_once())
    shutdown_requested = False

    def cancel_run_once() -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        task.cancel()

    loop.add_signal_handler(signal.SIGTERM, cancel_run_once)
    try:
        try:
            return await task
        except asyncio.CancelledError:
            if not shutdown_requested:
                raise
            return 0
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(_run_once_with_sigterm()))


if __name__ == "__main__":
    main()
