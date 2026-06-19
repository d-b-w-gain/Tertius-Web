#!/usr/bin/env python3
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Optional, cast
from uuid import UUID, uuid4
from pydantic import BaseModel
from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.compile_messages import CompileCommand, CompileSourceFile, assert_message_size
from core.config import get_settings
from core.db import get_db
from core.billing_messages import (
    LlmTokenUsageEvent,
    assert_billing_message_size,
    billing_usage_message_id,
)
from core.llm_client import (
    BuildScriptGenerationInput,
    LlmBillingError,
    LlmEditableFile,
    LlmFileEditTruncatedError,
    LlmFileEditInput,
    LlmGenerationError,
    LlmInvalidFileEditError,
    LlmNotConfiguredError,
    LlmProviderAuthenticationError,
    LlmProviderRateLimitError,
    estimate_build_script_tokens,
    estimate_file_edit_tokens,
    generate_build_script,
    generate_file_edits,
    select_llm_edit_context_files,
)
from core.llm_usage import LlmUsageLimitExceeded, assert_llm_usage_allowed, record_llm_usage
from core.models import CompileJob, ProjectFile, UserWorkspaceState, Project
from core.nats_client import NatsPublisher, connect_nats, ensure_billing_stream, ensure_compile_stream
from core.repositories import (
    CompileRepository,
    FileVersionConflictError,
    ProjectRepository,
    normalize_file_version,
    require_valid_python_filename,
)
from workflows.intus.usage_server import llm_usage_router, router as usage_router

app = FastAPI(title="Intus Compiler Server")
app.include_router(usage_router)
app.include_router(llm_usage_router)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# â”€â”€ Paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
TEMPLATE_FILE = Path(__file__).parent / 'templates' / 'default_purlin.py'

def get_default_purlin():
    if TEMPLATE_FILE.exists():
        return TEMPLATE_FILE.read_text(encoding="utf-8")
    return ""

DEFAULT_PURLIN = get_default_purlin()

# ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class CodeRequest(BaseModel):
    code: str
    file: Optional[str] = "design.py"

class CompileRequest(BaseModel):
    code: str
    export_format: str = "stl"
    quality: Optional[str] = None
    file: Optional[str] = "design.py"


async def publish_compile_command(command: CompileCommand) -> None:
    settings = get_settings()
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_compile_stream(nc, settings)
        await NatsPublisher(js).publish_json(
            settings.compile_request_subject,
            command,
            message_id=command.request_id,
        )
        await nc.flush()
    finally:
        await nc.close()


async def create_billing_publisher(settings):
    nc = None
    try:
        nc = await connect_nats(settings.nats_url)
        js = await ensure_billing_stream(nc, settings)
        return NatsPublisher(js), nc
    except Exception:
        if nc is not None:
            try:
                await nc.close()
            except Exception:
                logger.exception("Failed to close LLM billing NATS connection after setup failure")
        logger.exception("Failed to create LLM billing publisher")
        raise LlmBillingError("LLM billing failed")


# â”€â”€ Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _llm_provider_from_settings(settings) -> str:
    return "openai-compatible"


async def publish_file_edit_billing_event(
    *,
    billing_publisher,
    settings,
    auth: AuthContext,
    project_id: UUID,
    request: LlmFileEditInput,
    result,
    event_id: UUID,
) -> None:
    usage = result.usage
    event = LlmTokenUsageEvent(
        event_id=event_id,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        project_id=project_id,
        workflow="intus",
        operation="files.llm_edit",
        provider=_llm_provider_from_settings(settings),
        model=result.model,
        prompt=request.prompt,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        occurred_at=datetime.now(timezone.utc),
        provider_request_id=getattr(result, "provider_request_id", None),
        metadata=request.metadata,
    )
    try:
        assert_billing_message_size(event, settings.billing_max_bytes)
        await billing_publisher.publish_json(
            settings.billing_llm_usage_subject,
            event,
            message_id=billing_usage_message_id(event),
        )
    except Exception as exc:
        logger.exception("Failed to publish LLM billing usage event")
        raise LlmBillingError("LLM billing failed") from exc


@app.get("/health")
def health():
    try:
        import build123d
        has_b3d = True
    except ImportError:
        has_b3d = False
    return {"status": "ok", "build123d_installed": has_b3d}

@app.get("/project_name")
def get_project_name(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    state = db.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == ctx.user_id,
            UserWorkspaceState.tenant_id == ctx.tenant_id,
        )
    )
    if state is None or state.active_project_id is None:
        return {"project_name": ""}
    project = db.scalar(
        select(Project).where(
            Project.tenant_id == ctx.tenant_id,
            Project.id == state.active_project_id,
        )
    )
    if project is None:
        return {"project_name": ""}
    return {"project_name": project.name}

@app.get("/projects")
def list_projects(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    return {"projects": ProjectRepository(db, ctx.tenant_id).list_projects()}

@app.post("/projects/{name}/new")
def new_project(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    repo = ProjectRepository(db, ctx.tenant_id)
    try:
        existing = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if existing:
        return JSONResponse(status_code=400, content={"error": "Project already exists"})
    repo.create_project(name, ctx.user_id, DEFAULT_PURLIN)
    return {"success": True, "project": name}

@app.post("/projects/{name}/activate")
def activate_project(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    repo = ProjectRepository(db, ctx.tenant_id)
    try:
        success = repo.activate_project(name, ctx.user_id)
        if not success:
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        return {"success": True}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

@app.get("/projects/{name}/files")
def list_files(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    repo = ProjectRepository(db, ctx.tenant_id)
    try:
        if repo.get_project(name) is None:
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        files = repo.list_files(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    metadata = repo.list_file_metadata(name)
    return {
        "files": files,
        "file_metadata": [
            {
                "id": str(row["id"]),
                "filename": row["filename"],
                "updated_at": cast(datetime, row["updated_at"]).isoformat(),
            }
            for row in metadata
        ],
    }

@app.get("/projects/{name}/code")
def get_code(
    name: str,
    file: str = "design.py",
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    try:
        code = ProjectRepository(db, ctx.tenant_id).get_code(name, file)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if code is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return {"code": code}

@app.get("/projects/{name}/status")
def get_status(
    name: str,
    file: str = "design.py",
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    try:
        filename = require_valid_python_filename(file)
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})
    project_file = db.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == ctx.tenant_id,
            ProjectFile.project_id == project.id,
            ProjectFile.filename == filename,
        )
    )
    if project_file is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return {"mtime": project_file.updated_at.timestamp()}

@app.get("/projects/{name}/git_status")
def get_git_status(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    try:
        history = ProjectRepository(db, ctx.tenant_id).snapshot_history(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if history is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})
    commit = history[0].split(" ", 1)[0] if history else ""
    return {"is_git": True, "commit": commit, "history": history}

@app.post("/projects/{name}/save")
def save_code(name: str, req: CodeRequest, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    file = req.file or "design.py"
    try:
        saved = ProjectRepository(db, ctx.tenant_id).save_code(
            name,
            file,
            req.code,
            ctx.user_id,
            f"Manual save {file} via Intus",
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if not saved:
        return JSONResponse(status_code=404, content={"error": "Project not found"})
    return {"success": True}

@app.delete("/projects/{name}/file")
def delete_file(name: str, file: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    try:
        deleted = ProjectRepository(db, ctx.tenant_id).delete_file(name, file)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return {"success": True}

@app.post("/projects/{name}/compile")
async def compile_project(
    name: str,
    req: CompileRequest,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    file = req.file or "design.py"
    ext = req.export_format.lower()
    if ext not in ["stl", "step", "gltf", "glb"]:
        ext = "stl"

    repo = ProjectRepository(db, ctx.tenant_id)
    compile_repo = CompileRepository(db, ctx.tenant_id)
    try:
        filename = require_valid_python_filename(file)
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})

    project_id = project.id
    job_id = None
    committed = False
    try:
        saved = repo.stage_code_update(
            name,
            filename,
            req.code,
            ctx.user_id,
            f"Compile update ({filename}) via Intus",
        )
        if not saved:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        job = compile_repo.start_job(project_id, ctx.user_id, ext, status="queued")
        job_id = job.id
        files = repo.files_for_runtime(name)
        if files is None:
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        compile_repo.snapshot_job_files(job, files)

        request_id = f"compile-request:{job.id}"
        command = CompileCommand(
            job_id=job.id,
            tenant_id=ctx.tenant_id,
            project_id=project_id,
            requested_by=ctx.user_id,
            export_format=ext,
            quality=req.quality,
            created_at=job.created_at,
            files=[CompileSourceFile(filename=filename, content=content) for filename, content in files.items()],
            request_id=request_id,
        )
        try:
            assert_message_size(command, get_settings().compile_request_max_bytes, "request")
        except ValueError as exc:
            compile_repo.finish_job(
                job,
                "failed",
                error=str(exc),
                error_code="source_bundle_too_large",
                user_message="Compile source is too large to queue. Split the model into smaller files.",
                retryable=False,
            )
            db.commit()
            committed = True
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "success": False,
                    "job_id": str(job.id),
                    "error": str(exc),
                    "error_code": "source_bundle_too_large",
                    "user_message": "Compile source is too large to queue. Split the model into smaller files.",
                    "retryable": False,
                },
            )
        compile_repo.mark_job_dispatched(job, lease_seconds=get_settings().compile_ack_wait_seconds)
        db.commit()
        committed = True
        await publish_compile_command(command)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"success": True, "job_id": str(job.id), "status": "queued", "format": ext},
        )

    except Exception as exc:
        if job_id is not None:
            db.rollback()
            if committed:
                persisted_job = db.get(CompileJob, job_id)
                if persisted_job is not None:
                    compile_repo = CompileRepository(db, persisted_job.tenant_id)
                    compile_repo.mark_job_publish_pending(
                        persisted_job,
                        error=f"Compile command publish failed: {exc}",
                    )
                    db.commit()
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "success": False,
                        "job_id": str(job_id),
                        "error": str(exc),
                        "short": "Compile command publish failed",
                        "user_message": "Compile queued but could not be published immediately. It will be retried.",
                        "retryable": True,
                    },
                )
            persisted_job = db.get(CompileJob, job_id)
            if persisted_job is not None:
                compile_repo.finish_job(
                    persisted_job,
                    "failed",
                    error=f"Failed to enqueue compile job: {exc}",
                    error_code="enqueue_failed",
                    user_message="Compile could not be started. Try again.",
                    retryable=True,
                )
                db.commit()
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "success": False,
                    "job_id": str(job_id),
                    "error": str(exc),
                    "short": "Failed to enqueue compile job",
                    "user_message": "Compile could not be started. Try again.",
                    "retryable": True,
                },
            )
        return JSONResponse(status_code=200, content={
            "success": False,
            "error": str(exc),
            "short": str(exc)
        })


@app.post("/projects/{name}/build-script/generate")
async def generate_project_build_script(
    name: str,
    req: BuildScriptGenerationInput,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    try:
        require_valid_python_filename(req.active_file)
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "Project not found"})

    settings = get_settings()
    billing_publisher = None
    billing_nc = None
    try:
        if not settings.llm_api_key:
            raise LlmNotConfiguredError("LLM provider is not configured")
        assert_llm_usage_allowed(
            db,
            settings,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            estimated_tokens=estimate_build_script_tokens(
                req,
                max_output_tokens=settings.llm_max_output_tokens,
            ),
        )
        billing_publisher, billing_nc = await create_billing_publisher(settings)
        result = await generate_build_script(
            req,
            settings=settings,
            auth=ctx,
            project_id=project.id,
            billing_publisher=billing_publisher,
        )
        record_llm_usage(
            db,
            auth=ctx,
            project_id=project.id,
            request=req,
            result=result,
            provider_request_id=getattr(result, "provider_request_id", None),
            event_id=getattr(result, "billing_event_id", None),
            settings=settings,
        )
        db.commit()
    except LlmUsageLimitExceeded as exc:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"success": False, "error": str(exc), "retryable": True},
        )
    except LlmNotConfiguredError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": str(exc), "retryable": False},
        )
    except LlmProviderAuthenticationError as exc:
        logger.warning("LLM provider authentication failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": str(exc), "retryable": False},
        )
    except LlmProviderRateLimitError as exc:
        logger.warning("LLM provider rate limit exceeded")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": str(exc), "retryable": True},
        )
    except LlmGenerationError:
        logger.exception("LLM build script generation failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM generation failed", "retryable": True},
        )
    except LlmBillingError:
        logger.exception("LLM billing failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM billing failed", "retryable": True},
        )
    except Exception:
        logger.exception("LLM build script generation failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM generation failed", "retryable": True},
        )
    finally:
        if billing_nc is not None:
            try:
                await billing_nc.flush()
            except Exception:
                logger.exception("Failed to flush LLM billing NATS connection")
            finally:
                try:
                    await billing_nc.close()
                except Exception:
                    logger.exception("Failed to close LLM billing NATS connection")

    return {
        "success": result.success,
        "script": result.script,
        "model": result.model,
        "usage": result.usage.model_dump(),
    }


@app.post("/projects/{name}/files/llm-edit")
async def llm_edit_files(
    name: str,
    req: LlmFileEditInput,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    try:
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "Project not found"})

    try:
        for pointer in req.files:
            require_valid_python_filename(pointer.filename)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid Python filename"})

    seen_ids: set[UUID] = set()
    for pointer in req.files:
        if pointer.id in seen_ids:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Duplicate file id in request"},
            )
        seen_ids.add(pointer.id)

    if req.active_file_id is not None and req.active_file_id not in seen_ids:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Active file id is not in the request"},
        )

    file_rows = repo.files_by_ids(name, [pointer.id for pointer in req.files])
    if set(file_rows) != seen_ids:
        return JSONResponse(status_code=404, content={"success": False, "error": "File not found"})

    for pointer in req.files:
        if file_rows[pointer.id].filename != pointer.filename:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "File pointer does not match filename"},
            )
        if normalize_file_version(file_rows[pointer.id].updated_at) != normalize_file_version(pointer.updated_at):
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "success": False,
                    "error": "Files changed while AI edit was running. Reload and try again.",
                    "retryable": False,
                },
            )

    editable_files = [
        LlmEditableFile(
            id=row.id, filename=row.filename, content=row.content
        )
        for row in [file_rows[pointer.id] for pointer in req.files]
    ]

    settings = get_settings()
    billing_publisher = None
    billing_nc = None
    snapshot = None
    changed_files: list[ProjectFile] = []
    result = None
    try:
        if not settings.llm_api_key:
            raise LlmNotConfiguredError("LLM provider is not configured")
        selected_files = select_llm_edit_context_files(
            prompt=req.prompt,
            active_file_id=req.active_file_id,
            files=editable_files,
            max_files=settings.llm_file_edit_max_context_files,
            max_chars=settings.llm_file_edit_max_context_chars,
        )
        assert_llm_usage_allowed(
            db,
            settings,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            estimated_tokens=estimate_file_edit_tokens(
                req,
                selected_files,
                max_output_tokens=settings.llm_file_edit_max_output_tokens,
                system_prompt=settings.llm_file_edit_system_prompt,
            ),
        )
        result = await generate_file_edits(
            req,
            files=selected_files,
            settings=settings,
            auth=ctx,
            project_id=project.id,
        )
        if result.outcome == "changed":
            requested_versions = {pointer.id: pointer.updated_at for pointer in req.files}
            changed_ids = {edit.file_id for edit in result.files}
            requested_files = repo.files_by_ids(name, list(changed_ids))
            if set(requested_files) != changed_ids:
                return JSONResponse(status_code=404, content={"success": False, "error": "File not found"})
            for file_id in changed_ids:
                expected_updated_at = requested_versions[file_id]
                if normalize_file_version(requested_files[file_id].updated_at) != normalize_file_version(expected_updated_at):
                    db.rollback()
                    return JSONResponse(
                        status_code=status.HTTP_409_CONFLICT,
                        content={
                            "success": False,
                            "error": "Files changed while AI edit was running. Reload and try again.",
                            "retryable": False,
                        },
                    )
            updates = {edit.file_id: edit.content for edit in result.files}
            changed_versions = {file_id: requested_versions[file_id] for file_id in updates}
            try:
                stage_result = repo.stage_file_updates(
                    name,
                    updates,
                    ctx.user_id,
                    f"LLM edit: {req.prompt[:480]}",
                    expected_updated_at=changed_versions,
                )
            except ValueError as exc:
                if str(exc) == "LLM returned no file changes":
                    raise LlmInvalidFileEditError("changed outcome did not change any files") from exc
                raise
            except FileVersionConflictError:
                db.rollback()
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={
                        "success": False,
                        "error": "Files changed while AI edit was running. Reload and try again.",
                        "retryable": False,
                    },
                )
            if stage_result is None:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"success": False, "error": "LLM file update failed", "retryable": True},
                )
            snapshot, changed_files = stage_result
            if not changed_files:
                raise LlmInvalidFileEditError("changed outcome did not change any files")
        billing_publisher, billing_nc = await create_billing_publisher(settings)
        billing_event_id = uuid4()
        result.billing_event_id = billing_event_id
        await publish_file_edit_billing_event(
            billing_publisher=billing_publisher,
            settings=settings,
            auth=ctx,
            project_id=project.id,
            request=req,
            result=result,
            event_id=billing_event_id,
        )
        record_llm_usage(
            db,
            auth=ctx,
            project_id=project.id,
            request=req,
            result=result,
            provider_request_id=getattr(result, "provider_request_id", None),
            event_id=getattr(result, "billing_event_id", None),
            settings=settings,
            operation="files.llm_edit",
        )
        db.commit()
    except LlmUsageLimitExceeded as exc:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"success": False, "error": str(exc), "retryable": True},
        )
    except LlmNotConfiguredError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": str(exc), "retryable": False},
        )
    except LlmProviderAuthenticationError as exc:
        logger.warning("LLM provider authentication failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": str(exc), "retryable": False},
        )
    except LlmProviderRateLimitError as exc:
        logger.warning("LLM provider rate limit exceeded")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": str(exc), "retryable": True},
        )
    except LlmFileEditTruncatedError:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"success": False, "error": "LLM response was truncated", "retryable": True},
        )
    except LlmGenerationError:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM generation failed", "retryable": True},
        )
    except LlmInvalidFileEditError as exc:
        logger.debug("LLM file edit response rejected: %s", exc)
        logger.exception("LLM file edit returned invalid response")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"success": False, "error": "LLM returned invalid file edits", "retryable": True},
        )
    except ValueError as exc:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"success": False, "error": str(exc), "retryable": False},
        )
    except LlmBillingError:
        logger.exception("LLM billing failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM billing failed", "retryable": True},
        )
    except Exception:
        logger.exception("LLM file edit failed")
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM generation failed", "retryable": True},
        )
    finally:
        if billing_nc is not None:
            try:
                await billing_nc.flush()
            except Exception:
                logger.exception("Failed to flush LLM billing NATS connection")
            finally:
                try:
                    await billing_nc.close()
                except Exception:
                    logger.exception("Failed to close LLM billing NATS connection")

    if result is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"success": False, "error": "LLM generation failed", "retryable": True},
        )
    by_id = {row.id: row for row in changed_files}
    return {
        "success": True,
        "outcome": result.outcome,
        "message": result.message,
        "model": result.model,
        "usage": result.usage.model_dump(),
        "snapshot": (
            {
                "id": str(snapshot.id),
                "message": snapshot.message,
                "content_hash": snapshot.content_hash,
            }
            if snapshot is not None
            else None
        ),
        "files": [
            {
                "id": str(edit.file_id),
                "filename": by_id[edit.file_id].filename,
                "content": edit.content,
                "updated_at": by_id[edit.file_id].updated_at.isoformat(),
                "changed": True,
                "summary": edit.summary,
            }
            for edit in result.files
            if edit.file_id in by_id
        ],
    }


@app.get("/projects/{name}/compile/jobs/{job_id}")
def get_compile_job_status(
    name: str,
    job_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    compile_repo = CompileRepository(db, ctx.tenant_id)
    try:
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})

    job = compile_repo.get_job(project.id, job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Compile job not found"})
    settings = get_settings()
    job = compile_repo.reconcile_stale_job(
        project.id,
        job.id,
        queued_older_than_seconds=settings.compile_ack_wait_seconds,
        running_older_than_seconds=settings.compile_timeout_seconds + 30,
    )
    db.commit()
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Compile job not found"})

    artifact = compile_repo.artifact_for_job(job.id) if job.status == "succeeded" else None
    return {
        "job_id": str(job.id),
        "status": job.status,
        "format": job.export_format,
        "error": job.error,
        "error_code": job.error_code,
        "user_message": job.user_message,
        "retryable": job.retryable,
        "artifact_id": str(artifact.id) if artifact else None,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8891)
