from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, contextmanager
from typing import Any
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import UUID

from opentelemetry.trace import SpanKind

from core.compile_messages import (
    CompileCommand,
    CompileResultPayload,
    assert_message_size,
    compile_result_message_id,
    serialized_message_size,
)
from core.compile_runtime import (
    hydrate_project_files,
    runtime_files_hash,
)
from core.compile_artifacts import (
    WORKBENCH_ARTIFACT_KINDS,
    compile_bundle_digest,
    encode_compile_artifact,
    validate_compile_bundle,
)
from core.compile_sandbox import run_compile_sandbox
from core.scene_manifest import ASSET_ID, scene_assets
from core.component_cache import (
    CacheScope,
    ComponentCacheStore,
    load_json_file,
    open_component_cache_store,
    prefetch_entries,
    publish_entries,
    record_compile_phases,
    record_component_report,
)
from core.config import get_settings
from core.nats_client import (
    NatsPublisher,
    Publisher,
    connect_nats,
    ensure_compile_stream,
    extract_nats_context,
    pull_compile_subscription,
)
from core.object_store import (
    ObjectIntegrityError,
    ObjectNotFoundError,
    ObjectStoreUnavailableError,
    open_compile_sidecar_store,
)
from core.telemetry import (
    configure_telemetry,
    counter_add,
    elapsed_seconds,
    get_tracer,
    histogram_record,
    record_exception,
)


logger = logging.getLogger(__name__)

COMPONENT_RUN_REPORT = "run-report.json"
SCENE_DIRNAME = "tertius-scene"
# {asset id: GLB path} -> {asset id: ObjectRef as a dict}, run on the
# worker's event loop from the compile thread.
SceneUpload = Callable[[dict[str, Path]], dict[str, dict]]


def _scene_artifact(scene_dir: Path, upload: SceneUpload) -> bytes | None:
    """The scene manifest with its assets uploaded, or None: without it the
    result still carries the monolithic GLB, which the viewer falls back to."""
    try:
        manifest = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
        assets = {
            asset_id: scene_dir / "assets" / f"{asset_id}.glb"
            for asset_id in manifest.get("assets") or []
            if isinstance(asset_id, str) and ASSET_ID.match(asset_id)
        }
        manifest["asset_refs"] = upload(assets)
        scene_assets(manifest)
    except Exception:
        logger.warning("Scene manifest could not be published; using the monolithic GLB", exc_info=True)
        counter_add("tertius.compile.scene.publish", 1, {"outcome": "failed"})
        return None
    counter_add("tertius.compile.scene.publish", 1, {"outcome": "ok"})
    return json.dumps(manifest, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _scene_uploader(object_store) -> SceneUpload:
    """Upload scene assets through the sidecar store from a compile thread."""
    loop = asyncio.get_running_loop()

    def upload(assets: dict[str, Path]) -> dict[str, dict]:
        async def put_all() -> dict[str, dict]:
            refs = {}
            for asset_id, path in assets.items():
                ref = await object_store.put_if_absent(path.read_bytes())
                refs[asset_id] = ref.model_dump()
            return refs

        return asyncio.run_coroutine_threadsafe(put_all(), loop).result(timeout=600)

    return upload


def _hash_llm_edit_job_id(job_id: UUID) -> str:
    return hashlib.sha256(str(job_id).encode("ascii")).hexdigest()[:16]


def _component_cache_enabled(settings, component_cache) -> bool:
    return component_cache is not None and bool(
        getattr(settings, "component_cache_enabled", False)
    )


async def _execute_with_component_cache(
    command: CompileCommand,
    settings,
    binary_files: dict[str, bytes] | None,
    component_cache: ComponentCacheStore | None,
    span,
    object_store=None,
) -> tuple[CompileResultPayload, Callable[[], Awaitable[None]] | None]:
    """Run one compile, restoring the project's cached components before it.
    Returns the result as soon as the sandbox's outputs are written, with a
    step to run after the result is published: it waits for the sandbox to
    write the components it built and stores them for the next compile.
    Store trouble only costs speed: the compile runs cold and still
    succeeds."""
    args = (command, settings) if binary_files is None else (command, settings, binary_files)
    if not _component_cache_enabled(settings, component_cache):
        return await asyncio.to_thread(execute_compile_command, *args), None
    assert component_cache is not None
    scope = CacheScope.for_project(command.tenant_id, command.project_id)
    cache_dir = Path(tempfile.mkdtemp(prefix="tertius-component-cache-"))
    try:
        prefetch = await prefetch_entries(
            component_cache,
            scope,
            cache_dir,
            max_bytes=settings.component_cache_prefetch_max_bytes,
        )
        span.set_attribute("tertius.component_cache.prefetch_outcome", prefetch.outcome)
        span.set_attribute("tertius.component_cache.prefetched_entries", prefetch.entries)
        scene_upload = (
            _scene_uploader(object_store)
            if object_store is not None and getattr(settings, "scene_export_enabled", False)
            else None
        )
        result, finish = await asyncio.to_thread(
            _execute_compile_command,
            command,
            settings,
            binary_files,
            cache_dir,
            finish_later=True,
            scene_upload=scene_upload,
        )
    except BaseException:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise

    async def store_new_entries() -> None:
        try:
            if finish is not None:
                stored = await asyncio.to_thread(finish)
                span.set_attribute("tertius.component_cache.entries_written", stored)
            report = load_json_file(cache_dir / COMPONENT_RUN_REPORT)
            if result.status == "succeeded" and report is not None:
                counts = report.get("counts") or {}
                for status in ("hit", "built", "uncacheable"):
                    if isinstance(counts.get(status), int):
                        span.set_attribute(f"tertius.component_cache.{status}", counts[status])
                published = await publish_entries(component_cache, scope, cache_dir, report)
                span.set_attribute("tertius.component_cache.publish_outcome", published.outcome)
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)

    return result, store_new_entries


async def handle_compile_request_message(
    msg, publisher: Publisher, settings, object_store=None, component_cache=None
) -> None:
    context = extract_nats_context(getattr(msg, "headers", None))
    subject = getattr(msg, "subject", "tertius.compile.request")
    attributes = {
        "messaging.system": "nats",
        "messaging.destination.name": subject,
        "messaging.operation.name": "process",
        "nats_subject": subject,
    }
    with get_tracer(__name__).start_as_current_span(
        "NATS consume tertius.compile.request",
        context=context,
        kind=SpanKind.CONSUMER,
        attributes=attributes,
    ) as span:
        try:
            command = CompileCommand.model_validate_json(msg.data)
        except Exception as exc:
            logger.exception("Invalid compile command JSON")
            record_exception(span, exc)
            span.set_attribute("messaging.nats.ack_action", "term")
            await msg.term()
            return

        span.set_attribute("tertius.export_format", command.export_format)
        if command.originating_llm_edit_job_id is not None:
            span.set_attribute(
                "tertius.originating_llm_edit_job_hash",
                _hash_llm_edit_job_id(command.originating_llm_edit_job_id),
            )
        queue_latency = (now_utc() - command.created_at).total_seconds()
        if queue_latency >= 0:
            histogram_record(
                "tertius.compile.queue.latency",
                queue_latency,
                {"export_format": command.export_format},
            )

        counter_add(
            "tertius.compile.job.started.count",
            1,
            {"export_format": command.export_format},
        )
        start = perf_counter()
        after_publish: Callable[[], Awaitable[None]] | None = None
        try:
            binary_files: dict[str, bytes] = {}
            if command.assets:
                if object_store is None:
                    result = _failed_result(
                        command,
                        now_utc(),
                        error="Compile binary asset transport is unavailable",
                        error_code="binary_asset_unavailable",
                        user_message="Compile input storage is temporarily unavailable. Try again.",
                        retryable=True,
                    )
                else:
                    try:
                        for asset in command.assets:
                            binary_files[
                                asset.logical_filename
                            ] = await object_store.get(asset.object_ref)
                    except ObjectStoreUnavailableError as exc:
                        result = _failed_result(
                            command,
                            now_utc(),
                            error=str(exc),
                            error_code="binary_asset_unavailable",
                            user_message=(
                                "Compile input storage is temporarily unavailable. Try again."
                            ),
                            retryable=True,
                        )
                    except (ObjectIntegrityError, ObjectNotFoundError) as exc:
                        result = _failed_result(
                            command,
                            now_utc(),
                            error=str(exc),
                            error_code="invalid_binary_asset",
                            user_message="Compile input failed its integrity check.",
                            retryable=False,
                        )
                    else:
                        result, after_publish = await _execute_with_component_cache(
                            command, settings, binary_files, component_cache, span, object_store
                        )
            else:
                # Keep the NATS event loop responsive while CAD runs in its bounded
                # subprocess. Otherwise a long Build123D compile prevents client
                # keepalives and the result publish loses its connection.
                result, after_publish = await _execute_with_component_cache(
                    command, settings, None, component_cache, span, object_store
                )
            assert_message_size(
                result,
                settings.compile_result_max_bytes,
                "result",
                compress=True,
            )
            await publisher.publish_json(
                settings.compile_result_subject,
                result,
                message_id=compile_result_message_id(result),
                compress=True,
            )
            await msg.ack()
            span.set_attribute("messaging.nats.ack_action", "ack")
            labels = {
                "export_format": command.export_format,
                "job_status": result.status,
            }
            counter_add("tertius.compile.job.finished.count", 1, labels)
            if result.status == "failed":
                counter_add("tertius.compile.job.failed.count", 1, labels)
            histogram_record(
                "tertius.compile.job.duration", elapsed_seconds(start), labels
            )
            if after_publish is not None:
                # The user has the result; storing new cached components only
                # speeds up the next compile, so it can't fail this one.
                store_entries, after_publish = after_publish, None
                try:
                    await store_entries()
                except Exception:
                    logger.warning("Storing new component cache entries failed", exc_info=True)
        except Exception as exc:
            if after_publish is not None:
                # Still wait for the sandbox and remove its folders.
                try:
                    await after_publish()
                except Exception:
                    logger.warning("Component cache clean-up failed", exc_info=True)
            logger.exception("Compile job failed before request ack")
            record_exception(span, exc)
            span.set_attribute("messaging.nats.ack_action", "nak")
            counter_add(
                "tertius.compile.job.failed.count",
                1,
                {"export_format": command.export_format, "job_status": "worker_error"},
            )
            histogram_record(
                "tertius.compile.job.duration",
                elapsed_seconds(start),
                {"export_format": command.export_format, "job_status": "worker_error"},
            )
            await msg.nak()


def execute_compile_command(
    command: CompileCommand,
    settings,
    binary_files: dict[str, bytes] | None = None,
    component_cache_dir: Path | None = None,
) -> CompileResultPayload:
    result, finish = _execute_compile_command(
        command, settings, binary_files, component_cache_dir, finish_later=False
    )
    return result


def _execute_compile_command(
    command: CompileCommand,
    settings,
    binary_files: dict[str, bytes] | None,
    component_cache_dir: Path | None,
    *,
    finish_later: bool,
    scene_upload: SceneUpload | None = None,
) -> tuple[CompileResultPayload, Callable[[], bool] | None]:
    """Run one compile. With finish_later, return as soon as the sandbox's
    outputs are written, with a callable that waits for it to finish
    storing new component cache entries (and then removes the project
    folder); otherwise wait for everything."""
    stack = ExitStack()
    sandbox_finish: list[Callable[[], bool]] = []
    try:
        result = _run_compile(
            command, settings, binary_files, component_cache_dir, stack, finish_later, sandbox_finish,
            scene_upload,
        )
    except BaseException:
        for finish in sandbox_finish:
            finish()
        stack.close()
        raise
    if not sandbox_finish:
        stack.close()
        return result, None

    def finish_and_clean_up() -> bool:
        try:
            return sandbox_finish[0]()
        finally:
            stack.close()

    return result, finish_and_clean_up


@contextmanager
def _kept(stack: ExitStack, context):
    """Enter a context into stack, so it outlives the with block using it."""
    yield stack.enter_context(context)


def _run_compile(
    command: CompileCommand,
    settings,
    binary_files: dict[str, bytes] | None,
    component_cache_dir: Path | None,
    stack: ExitStack,
    finish_later: bool,
    sandbox_finish: list[Callable[[], bool]],
    scene_upload: SceneUpload | None = None,
) -> CompileResultPayload:
    started_at = now_utc()
    if not command.files:
        return _failed_result(
            command,
            started_at,
            error="Compile command source bundle is empty",
            error_code="missing_snapshot",
            user_message="Compile failed because the submitted source snapshot is missing. Try again.",
            retryable=True,
        )

    files = {file.filename: file.content for file in command.files}
    with _kept(stack, hydrate_project_files(files, binary_files)) as project_dir:
        sandbox_options: dict[str, Any] = {}
        if component_cache_dir is not None:
            sandbox_options["component_cache_dir"] = component_cache_dir
        if finish_later:
            sandbox_options["early_result"] = True
        export_scene = (
            scene_upload is not None
            and component_cache_dir is not None
            and command.export_format == "glb"
        )
        if export_scene:
            sandbox_options["scene_export"] = True
        result = run_compile_sandbox(
            project_dir,
            command.export_format,
            quality=command.quality,
            timeout_seconds=settings.compile_timeout_seconds,
            **sandbox_options,
        )
        finish = getattr(result, "finish", None)
        if finish is not None:
            sandbox_finish.append(finish)
        if not result.success:
            error = result.error or result.stderr or "Compile failed"
            return _failed_result(
                command,
                started_at,
                error=error,
                error_code=_error_code(error),
                user_message=_user_message(error),
                retryable=True,
                max_bytes=settings.compile_result_max_bytes,
            )

        if result.output_path is None:
            return _failed_result(
                command,
                started_at,
                error="Compile succeeded without an output artifact",
                error_code="missing_artifact",
                user_message="Compile failed before an artifact was produced. Try again.",
                retryable=True,
            )
        artifact_paths = getattr(result, "artifact_paths", {})
        missing_paths = sorted(WORKBENCH_ARTIFACT_KINDS - set(artifact_paths))
        if missing_paths:
            return _failed_result(
                command,
                started_at,
                error=f"Compile completed without required artifacts: {missing_paths}",
                error_code="missing_artifact_bundle",
                user_message=(
                    "Compile failed because Tertius could not finalize every workbench "
                    "from the mechanical design."
                ),
                retryable=False,
            )
        artifact_contents = {
            command.export_format: result.output_path.read_bytes(),
            **{
                kind: path.read_bytes()
                for kind, path in artifact_paths.items()
                if kind in WORKBENCH_ARTIFACT_KINDS
            },
        }
        phases = load_json_file(getattr(result, "phases_path", None))
        if phases is not None:
            record_compile_phases(phases, command.export_format)
        component_report_path = getattr(result, "component_report_path", None)
        component_report = load_json_file(component_report_path)
        if component_report_path is not None and component_report is not None:
            record_component_report(component_report, command.export_format)
            artifact_contents["components"] = component_report_path.read_bytes()
            if component_cache_dir is not None:
                shutil.copyfile(
                    component_report_path, component_cache_dir / COMPONENT_RUN_REPORT
                )
        if export_scene:
            assert scene_upload is not None
            scene = _scene_artifact(project_dir / SCENE_DIRNAME, scene_upload)
            if scene is not None:
                artifact_contents["scene"] = scene
        try:
            compiled_design = json.loads(artifact_contents["compiled_design"])
            if not isinstance(compiled_design, dict):
                raise ValueError("compiled-design root must be a JSON object")
            if compiled_design.get("schema_version") != "1.0":
                raise ValueError("compiled-design schema_version must be '1.0'")
            if not str(compiled_design.get("compiled_design_digest") or ""):
                raise ValueError("compiled-design digest is missing")
            compiled_design["source_snapshot_hash"] = runtime_files_hash(files)
            artifact_contents["compiled_design"] = json.dumps(
                compiled_design,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (OSError, TypeError, ValueError) as exc:
            return _failed_result(
                command,
                started_at,
                error=f"Invalid compiled-design graph: {exc}",
                error_code="invalid_compiled_design",
                user_message=(
                    "Compile produced inconsistent mechanical/workbench data. "
                    "Fix the product library or physical design definition."
                ),
                retryable=False,
            )

    artifacts = [
        encode_compile_artifact(kind, content)
        for kind, content in sorted(artifact_contents.items())
    ]
    bundle_digest = compile_bundle_digest(artifacts)
    try:
        validate_compile_bundle(
            artifacts,
            export_format=command.export_format,
            expected_bundle_digest=bundle_digest,
        )
    except ValueError as exc:
        return _failed_result(
            command,
            started_at,
            error=f"Invalid compile artifact bundle: {exc}",
            error_code="invalid_artifact_bundle",
            user_message=(
                "Compile produced inconsistent mechanical/workbench data. "
                "Fix the product library or physical design definition."
            ),
            retryable=False,
        )

    success = CompileResultPayload(
        job_id=command.job_id,
        tenant_id=command.tenant_id,
        project_id=command.project_id,
        export_format=command.export_format,
        status="succeeded",
        artifacts=artifacts,
        bundle_digest=bundle_digest,
        worker_started_at=started_at,
        worker_finished_at=now_utc(),
    )

    try:
        assert_message_size(
            success,
            settings.compile_result_max_bytes,
            "result",
            compress=True,
        )
        return success
    except ValueError as exc:
        return _failed_result(
            command,
            started_at,
            error=str(exc),
            error_code="artifact_too_large",
            user_message="Compile succeeded but the artifact is too large to return.",
            retryable=False,
        )


async def run_once() -> int:
    settings = get_settings()
    configure_telemetry(settings, "tertius-compile-job")
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_compile_stream(nc, settings)
        publisher = NatsPublisher(js)
        subscription = await pull_compile_subscription(js, settings)
        try:
            messages = await subscription.fetch(batch=1, timeout=5)
        except TimeoutError:
            return 0

        object_store = None
        component_cache = None
        for msg in messages:
            try:
                command = CompileCommand.model_validate_json(msg.data)
            except Exception:
                command = None
            wants_sidecars = command is not None and (
                command.assets or getattr(settings, "scene_export_enabled", False)
            )
            if wants_sidecars and object_store is None:
                try:
                    object_store = await open_compile_sidecar_store(js, settings)
                except ObjectStoreUnavailableError:
                    logger.exception("Compile sidecar Object Store is unavailable")
            if (
                command is not None
                and getattr(settings, "component_cache_enabled", False)
                and component_cache is None
            ):
                try:
                    component_cache = await open_component_cache_store(js, settings)
                except ObjectStoreUnavailableError:
                    # A cold compile is slower, not wrong.
                    logger.warning("Component cache Object Store is unavailable")
                    counter_add(
                        "tertius.compile.component_cache.store.operations",
                        1,
                        {"operation": "open", "outcome": "unavailable"},
                    )
            if component_cache is not None:
                await handle_compile_request_message(
                    msg, publisher, settings, object_store, component_cache
                )
            else:
                await handle_compile_request_message(msg, publisher, settings, object_store)
        return 0
    finally:
        await nc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run_once()))


def _failed_result(
    command: CompileCommand,
    started_at,
    error: str,
    error_code: str,
    user_message: str,
    retryable: bool,
    max_bytes: int | None = None,
) -> CompileResultPayload:
    result = CompileResultPayload(
        job_id=command.job_id,
        tenant_id=command.tenant_id,
        project_id=command.project_id,
        export_format=command.export_format,
        status="failed",
        error=error,
        error_code=error_code,
        user_message=user_message,
        retryable=retryable,
        worker_started_at=started_at,
        worker_finished_at=now_utc(),
    )
    if max_bytes is None or serialized_message_size(result) <= max_bytes:
        return result

    suffix = "[truncated]"
    low = 0
    high = len(error)
    best = suffix
    while low <= high:
        mid = (low + high) // 2
        candidate_error = f"{error[:mid]}{suffix}"
        candidate = result.model_copy(update={"error": candidate_error})
        if serialized_message_size(candidate) <= max_bytes:
            best = candidate_error
            low = mid + 1
        else:
            high = mid - 1
    return result.model_copy(update={"error": best})


def _error_code(error: str) -> str:
    if "killed" in error.lower() and "memory" in error.lower():
        return "worker_oom"
    if "timed out" in error.lower():
        return "timeout"
    return "sandbox_error"


def _user_message(error: str) -> str:
    if "killed" in error.lower() and "memory" in error.lower():
        return "Compile ran out of memory while building the model. Try simplifying the model or exporting a smaller format."
    if "timed out" in error.lower():
        return "Compile timed out after 10 minutes. Try again."
    return "Compile failed. Fix the model source and try again."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
