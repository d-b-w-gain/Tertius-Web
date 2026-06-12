#!/usr/bin/env python3
from pathlib import Path
from typing import Optional
from uuid import UUID
from pydantic import BaseModel
from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.compile_messages import CompileCommand
from core.config import get_settings
from core.db import get_db
from core.models import CompileJob, ProjectFile, UserWorkspaceState, Project
from core.nats_client import NatsPublisher, connect_nats, ensure_compile_stream
from core.repositories import CompileRepository, ProjectRepository, require_valid_python_filename

app = FastAPI(title="Intus Compiler Server")

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

# â”€â”€ Models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class CodeRequest(BaseModel):
    code: str
    file: Optional[str] = "design.py"

class CompileRequest(BaseModel):
    code: str
    export_format: str = "stl"
    file: Optional[str] = "design.py"


async def publish_compile_command(command: CompileCommand) -> None:
    settings = get_settings()
    nc = await connect_nats(settings.nats_url)
    try:
        js = await ensure_compile_stream(nc, settings)
        await NatsPublisher(js).publish_json(settings.compile_request_subject, command)
        await nc.flush()
    finally:
        await nc.close()

# â”€â”€ Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    return {"files": files}

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
    try:
        saved = repo.save_code(
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
        db.commit()

        command = CompileCommand(
            job_id=job.id,
            tenant_id=ctx.tenant_id,
            project_id=project_id,
            requested_by=ctx.user_id,
            export_format=ext,
            created_at=job.created_at,
        )
        await publish_compile_command(command)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"success": True, "job_id": str(job.id), "status": "queued", "format": ext},
        )

    except Exception as exc:
        if job_id is not None:
            db.rollback()
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
