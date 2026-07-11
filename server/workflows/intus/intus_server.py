#!/usr/bin/env python3
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import logging
from pathlib import Path
from typing import Any, Optional, cast
from uuid import UUID
from pydantic import BaseModel
from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import propagate
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.compile_messages import CompileCommand, CompileSourceFile, assert_message_size
from core.config import get_settings
from core.db import get_db
from core.llm_usage import LlmUsageLimitExceeded, assert_llm_usage_allowed
from core.models import CompileJob, LlmEditJob, Project, ProjectFile, UserWorkspaceState
from core.nats_client import NatsPublisher, connect_nats, ensure_compile_stream, ensure_pi_agent_stream
from core.pi_agent_messages import PiAgentCommand, PiAgentSourceFile, assert_pi_agent_command_size, pi_agent_command_message_id
from core.pi_agent_prompt import estimate_pi_agent_usage, load_pi_agent_prompt
from core.pi_agent_telemetry import pi_agent_metric_attributes
from core.llm_file_edit import (
    LlmEditableFile as DomainEditableFile,
    LlmFileEditInput,
    select_llm_edit_context_files as select_domain_context_files,
)
from core.telemetry import (
    counter_add,
)
from core.repositories import (
    CompileRepository,
    ProjectRepository,
    normalize_file_version,
    require_valid_python_filename,
    LlmEditRepository,
)
from workflows.intus.usage_server import llm_usage_router, router as usage_router
from workflows.intus.pi_agent_result_consumer import reconcile_stale_pi_agent_job

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
    originating_llm_edit_job_id: Optional[UUID] = None


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





# â”€â”€ Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€



@app.get("/health")
def health():
    has_b3d = importlib.util.find_spec("build123d") is not None
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

        job = compile_repo.start_job(
            project_id,
            ctx.user_id,
            ext,
            status="queued",
            originating_llm_edit_job_id=req.originating_llm_edit_job_id,
        )
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
            originating_llm_edit_job_id=req.originating_llm_edit_job_id,
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





def _llm_edit_job_content(job: LlmEditJob) -> str:
    if job.status in {"queued", "running"}:
        return ""
    if job.result_payload:
        message = str(job.result_payload.get("message") or "").strip()
        if message:
            return message

        files = _llm_edit_job_files(job)
        changed_files = [file for file in files if file.get("changed") is not False]
        file_summary = " ".join(str(file.get("summary")) for file in files if file.get("summary"))
        outcome = job.result_payload.get("outcome")
        parts = [
            f"Updated {len(changed_files) or len(files)} file(s)." if outcome == "changed" else f"AI returned {outcome}.",
            f"Model: {job.result_payload.get('model')}." if job.result_payload.get("model") else "",
            file_summary,
        ]
        return " ".join(part for part in parts if part)
    return job.user_message or job.error or ""


def _llm_edit_job_files(job: LlmEditJob) -> list[dict[str, Any]]:
    if not job.result_payload:
        return []
    files = job.result_payload.get("files")
    if not isinstance(files, list):
        return []
    projected = []
    for file in files:
        if not isinstance(file, dict):
            continue
        projected.append({key: value for key, value in file.items() if key != "content"})
    return projected


def _llm_edit_job_model(job: LlmEditJob) -> str | None:
    result_payload = job.result_payload or {}
    result_model = result_payload.get("model")
    if isinstance(result_model, str) and result_model:
        return result_model

    request_payload = job.request_payload or {}
    request_model = request_payload.get("model_id")
    if isinstance(request_model, str) and request_model:
        return request_model
    return None


def _serialize_llm_edit_history_message(
    job: LlmEditJob,
    compile_job: CompileJob | None,
    compile_repo: CompileRepository,
) -> dict[str, Any]:
    request_payload = job.request_payload or {}
    result_payload = job.result_payload or {}
    request_files = request_payload.get("files")
    artifact = compile_repo.artifact_for_job(compile_job.id) if compile_job is not None else None
    return {
        "job_id": str(job.id),
        "prompt": str(request_payload.get("prompt") or ""),
        "content": _llm_edit_job_content(job),
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "status": job.status,
        "model": _llm_edit_job_model(job),
        "metadata": request_payload.get("metadata") if isinstance(request_payload.get("metadata"), dict) else {},
        "usage": result_payload.get("usage"),
        "files": _llm_edit_job_files(job),
        "requested_file_count": len(request_files) if isinstance(request_files, list) else 0,
        "compile": (
            {
                "job_id": str(compile_job.id),
                "status": compile_job.status,
                "artifact_id": str(artifact.id) if artifact else None,
                "export_format": compile_job.export_format,
            }
            if compile_job is not None
            else None
        ),
    }











def _llm_file_edit_job_attributes() -> dict[str, str]:
    return {"llm.operation": "files.llm_edit", "workflow": "intus"}











async def publish_pi_agent_command(settings, command: PiAgentCommand) -> None:
    nc = await asyncio.wait_for(connect_nats(settings.nats_url), timeout=2)
    try:
        js = await ensure_pi_agent_stream(nc, settings)
        await NatsPublisher(js).publish_json(
            settings.pi_agent_request_subject,
            command,
            message_id=pi_agent_command_message_id(command),
        )
        await nc.flush()
    finally:
        await nc.close()


@app.post("/projects/{name}/files/llm-edit/jobs")
async def start_llm_file_edit_job(
    name: str,
    req: LlmFileEditInput,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    job_repo = LlmEditRepository(db, ctx.tenant_id)
    settings = get_settings()
    job = None
    publication_attempted = False
    try:
        if not settings.pi_agent_enabled:
            return JSONResponse(status_code=503, content={"success": False, "error": "AI editing is not configured", "retryable": False})
        project = repo.get_project(name)
        if project is None:
            return JSONResponse(status_code=404, content={"success": False, "error": "Project not found"})
        locked_project = db.scalar(
            select(Project).where(Project.id == project.id).with_for_update()
        )
        if locked_project is None:
            return JSONResponse(status_code=404, content={"success": False, "error": "Project not found"})
        project = locked_project
        active_job = db.scalar(
            select(LlmEditJob)
            .where(
                LlmEditJob.tenant_id == ctx.tenant_id,
                LlmEditJob.project_id == project.id,
                LlmEditJob.status.in_(["queued", "running"]),
            )
            .limit(1)
        )
        if active_job is not None:
            db.rollback()
            return JSONResponse(status_code=409, content={"success": False, "error": "An AI edit is already running for this project", "retryable": True})
        if req.model_id not in (None, "", settings.pi_agent_model):
            return JSONResponse(status_code=400, content={"success": False, "error": "unsupported_model"})

        seen_ids: set[UUID] = set()
        for pointer in req.files:
            require_valid_python_filename(pointer.filename)
            if pointer.id in seen_ids:
                raise ValueError("Duplicate file id in request")
            seen_ids.add(pointer.id)
        if req.active_file_id is not None and req.active_file_id not in seen_ids:
            raise ValueError("Active file id is not in the request")
        rows = repo.files_by_ids(name, [pointer.id for pointer in req.files])
        if set(rows) != seen_ids:
            return JSONResponse(status_code=404, content={"success": False, "error": "File not found"})
        for pointer in req.files:
            row = rows[pointer.id]
            if row.filename != pointer.filename:
                raise ValueError("File pointer does not match filename")
            if normalize_file_version(row.updated_at) != normalize_file_version(pointer.updated_at):
                return JSONResponse(status_code=409, content={"success": False, "error": "Files changed while AI edit was running. Reload and try again.", "retryable": False})

        editable = [
            DomainEditableFile(id=rows[p.id].id, filename=rows[p.id].filename, content=rows[p.id].content)
            for p in req.files
        ]
        selected = select_domain_context_files(
            prompt=req.prompt,
            active_file_id=req.active_file_id,
            files=editable,
            max_files=settings.llm_file_edit_max_context_files,
            max_chars=settings.llm_file_edit_max_context_chars,
        )
        prior_prompts = job_repo.list_recent_prompts(name, limit=5)
        if prior_prompts:
            history = "\n".join(
                f"{index}. {prompt}"
                for index, prompt in enumerate(prior_prompts, start=1)
            )
            user_prompt = (
                "Previous user requests, oldest first:\n"
                f"{history}\n\n"
                "Current user request:\n"
                f"{req.prompt}"
            )
        else:
            user_prompt = req.prompt
        estimate = estimate_pi_agent_usage(
            system_prompt=load_pi_agent_prompt().content,
            user_prompt=user_prompt,
            source_bytes=sum(
                len(file.content.encode("utf-8")) for file in selected
            ),
            metadata=req.metadata,
            max_output_tokens=settings.pi_agent_estimated_output_tokens,
        )
        assert_llm_usage_allowed(
            db,
            settings,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            estimated_tokens=estimate.total_tokens,
        )
        selected_ids = {file.id for file in selected}
        command_files = [
            PiAgentSourceFile(
                id=row.id,
                filename=row.filename,
                content=row.content,
                updated_at=row.updated_at,
                sha256=sha256(row.content.encode("utf-8")).hexdigest(),
            )
            for pointer in req.files
            if pointer.id in selected_ids
            for row in [rows[pointer.id]]
        ]
        dispatch_created_at = datetime.now(timezone.utc)
        request_payload = req.model_dump(mode="json")
        request_payload.update(
            {
                "dispatched_provider": settings.pi_agent_provider,
                "dispatched_model": settings.pi_agent_model,
                "dispatched_thinking": settings.pi_agent_thinking,
                "dispatched_prior_prompts": prior_prompts,
                "dispatch_created_at": dispatch_created_at.isoformat(),
                "dispatch_attempted_at": dispatch_created_at.isoformat(),
                "dispatched_manifest": [
                    {
                        "id": str(file.id),
                        "filename": file.filename,
                        "updated_at": file.updated_at.isoformat(),
                        "sha256": file.sha256,
                    }
                    for file in command_files
                ],
            }
        )
        trace_headers: dict[str, str] = {}
        propagate.inject(trace_headers)
        request_payload["dispatch_traceparent"] = trace_headers.get("traceparent")
        request_payload["dispatch_tracestate"] = trace_headers.get("tracestate")
        job = job_repo.start_job(project.id, ctx.user_id, request_payload, status="queued")
        command = PiAgentCommand(
            schema_version=1,
            job_id=job.id,
            tenant_id=ctx.tenant_id,
            project_id=project.id,
            provider=settings.pi_agent_provider,
            model=settings.pi_agent_model,
            thinking=settings.pi_agent_thinking,
            prompt=req.prompt,
            prior_prompts=prior_prompts,
            active_file_id=req.active_file_id,
            files=command_files,
            created_at=dispatch_created_at,
            traceparent=trace_headers.get("traceparent"),
            tracestate=trace_headers.get("tracestate"),
        )
        assert_pi_agent_command_size(command, settings.pi_agent_request_max_bytes)
        db.commit()
        counter_add("tertius.llm.job.queued.count", 1, _llm_file_edit_job_attributes())
        counter_add(
            "tertius.pi_agent.job.queued.count",
            1,
            pi_agent_metric_attributes(
                operation="pi_agent.api",
                provider=settings.pi_agent_provider,
                model=settings.pi_agent_model,
                status="queued",
            ),
        )

        publication_attempted = True
        await publish_pi_agent_command(settings, command)
        job = job_repo.get_job(project.id, job.id)
        if job is not None:
            job_repo.mark_job_dispatched(job)
            db.commit()
    except LlmUsageLimitExceeded:
        db.rollback()
        return JSONResponse(status_code=429, content={"success": False, "error": "LLM generation limit exceeded.", "retryable": True})
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
    except Exception:
        logger.exception("Failed to dispatch Pi agent job")
        db.rollback()
        if publication_attempted and job is not None:
            return JSONResponse(status_code=202, content={"success": True, "job_id": str(job.id), "status": "queued"})
        return JSONResponse(status_code=503, content={"success": False, "error": "Could not prepare AI edit job", "retryable": True})

    assert job is not None
    return JSONResponse(status_code=202, content={"success": True, "job_id": str(job.id), "status": "queued"})


@app.get("/projects/{name}/files/llm-edit/jobs")
def list_llm_file_edit_jobs(
    name: str,
    limit: int = 200,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    llm_edit_repo = LlmEditRepository(db, ctx.tenant_id)
    compile_repo = CompileRepository(db, ctx.tenant_id)
    try:
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})

    settings = get_settings()
    jobs = llm_edit_repo.list_jobs_for_project(project.id, limit=limit)
    for candidate in jobs:
        reconcile_stale_pi_agent_job(db, candidate, settings)
    db.commit()

    jobs = llm_edit_repo.list_jobs_for_project(project.id, limit=limit)
    messages = [
        _serialize_llm_edit_history_message(
            job,
            llm_edit_repo.get_compile_job_for_llm_edit(project.id, job.id),
            compile_repo,
        )
        for job in jobs
    ]
    return {"messages": messages}


@app.get("/projects/{name}/files/llm-edit/jobs/{job_id}")
def get_llm_file_edit_job_status(
    name: str,
    job_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    repo = ProjectRepository(db, ctx.tenant_id)
    llm_edit_repo = LlmEditRepository(db, ctx.tenant_id)
    try:
        project = repo.get_project(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if project is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})

    job = llm_edit_repo.get_job(project.id, job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "LLM edit job not found"})

    settings = get_settings()
    reconcile_stale_pi_agent_job(db, job, settings)
    db.commit()
    db.refresh(job)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "LLM edit job not found"})

    return {
        "job_id": str(job.id),
        "status": job.status,
        "result": job.result_payload,
        "error": job.error,
        "error_code": job.error_code,
        "user_message": job.user_message,
        "retryable": job.retryable,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
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
