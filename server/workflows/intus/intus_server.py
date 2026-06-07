#!/usr/bin/env python3
import traceback
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.artifacts import ArtifactStore
from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.compile_runtime import hydrate_project_files
from core.compile_sandbox import run_compile_sandbox
from core.config import get_settings
from core.db import get_db
from core.models import CompileJob, ProjectFile
from core.repositories import CompileRepository, ProjectRepository, require_valid_python_filename

app = FastAPI(title="Intus Compiler Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ──────────────────────────────────────────────────────────────────────
CACHE_ROOT = Path(__file__).parent.parent.parent.parent / 'cache' / 'tertius'
PROJECTS_DIR = CACHE_ROOT / 'intus'
ACTIVE_STL = CACHE_ROOT / 'active_output.stl'
ACTIVE_PROJECT = CACHE_ROOT / 'active_project.txt'

CACHE_ROOT.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Default Script ─────────────────────────────────────────────────────────────
TEMPLATE_FILE = Path(__file__).parent / 'templates' / 'default_purlin.py'

def get_default_purlin():
    if TEMPLATE_FILE.exists():
        return TEMPLATE_FILE.read_text(encoding="utf-8")
    return ""

DEFAULT_PURLIN = get_default_purlin()

def init_defaults_if_needed():
    if not list(PROJECTS_DIR.iterdir()):
        default_proj = PROJECTS_DIR / "default_purlin"
        default_proj.mkdir(parents=True, exist_ok=True)
        (default_proj / "design.py").write_text(DEFAULT_PURLIN, encoding="utf-8")

init_defaults_if_needed()

import subprocess

def auto_commit(proj_dir: Path, message: str):
    """Initializes git (if needed) and commits design.py if there are changes."""
    try:
        if not (proj_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=proj_dir, capture_output=True)
            
        subprocess.run(["git", "config", "user.name", "Intus Compiler"], cwd=proj_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "intus@tertius.local"], cwd=proj_dir, capture_output=True)
            
        py_files = [p.name for p in proj_dir.glob("*.py")]
        if py_files:
            subprocess.run(["git", "add"] + py_files, cwd=proj_dir, capture_output=True)
        
        status = subprocess.run(["git", "status", "--porcelain"], cwd=proj_dir, capture_output=True, text=True)
        if status.stdout.strip():
            res = subprocess.run(["git", "commit", "-m", message], cwd=proj_dir, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"Commit failed: {res.stderr}")
    except Exception as e:
        print(f"Git auto-commit failed: {e}")

# ── Models ─────────────────────────────────────────────────────────────────────
class CodeRequest(BaseModel):
    code: str
    file: Optional[str] = "design.py"

class CompileRequest(BaseModel):
    code: str
    export_format: str = "stl"
    file: Optional[str] = "design.py"

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        import build123d
        has_b3d = True
    except ImportError:
        has_b3d = False
    return {"status": "ok", "build123d_installed": has_b3d}

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
def compile_project(
    name: str,
    req: CompileRequest,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    file = req.file or "design.py"
    ext = req.export_format.lower()
    if ext not in ["stl", "step", "gltf"]:
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
    job = None
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

        files = repo.files_for_runtime(name)
        if files is None:
            return JSONResponse(status_code=404, content={"error": "Project not found"})

        job = compile_repo.start_job(project_id, ctx.user_id, ext)
        job_id = job.id
        db.commit()

        with hydrate_project_files(files) as project_dir:
            result = run_compile_sandbox(project_dir, ext)
            if not result.success:
                error = result.error or result.stderr or "Compile failed"
                persisted_job = db.get(CompileJob, job_id)
                compile_repo.finish_job(persisted_job, "failed", error=error)
                db.commit()
                return JSONResponse(status_code=200, content={"success": False, "error": error, "short": error})

            if result.output_path is None:
                raise RuntimeError("Compile succeeded without an output artifact")
            output_bytes = result.output_path.read_bytes()

        artifact_store = ArtifactStore(get_settings().artifact_root)
        stored = artifact_store.write_bytes(ctx.tenant_id, project_id, ext, output_bytes)
        if not artifact_store.path_for(stored.storage_key).exists():
            raise RuntimeError("Artifact write failed")

        artifact = compile_repo.record_artifact(
            project_id,
            job_id,
            ext,
            stored.storage_key,
            stored.content_type,
            stored.byte_size,
        )
        persisted_job = db.get(CompileJob, job_id)
        compile_repo.finish_job(persisted_job, "succeeded")
        db.commit()
        return {"success": True, "format": ext, "artifact_id": str(artifact.id)}

    except Exception as e:
        tb = traceback.format_exc()
        db.rollback()
        if job_id is not None:
            persisted_job = db.get(CompileJob, job_id)
            if persisted_job is not None:
                compile_repo.finish_job(persisted_job, "failed", error=tb)
                db.commit()
        return JSONResponse(status_code=200, content={
            "success": False,
            "error": tb,
            "short": str(e)
        })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8891)
