#!/usr/bin/env python3
import sys
import traceback
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.models import ProjectFile
from core.repositories import ProjectRepository, require_valid_python_filename

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
def compile_project(name: str, req: CompileRequest):
    proj_dir = PROJECTS_DIR / name
    file = req.file or "design.py"
    
    if "/" in file or "\\" in file or not file.endswith(".py"):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
        
    script_file = proj_dir / file
    design_file = proj_dir / "design.py"
    
    if not proj_dir.exists():
        return JSONResponse(status_code=404, content={"error": "Project not found"})

    # Save code first for the file being edited
    script_file.write_text(req.code, encoding="utf-8")
    
    # Auto-commit the compiled changes
    auto_commit(proj_dir, f"Compile update ({file}) via Intus")
    
    # Update active project pointer for Artus (always points to design.py for parsing)
    if design_file.exists():
        ACTIVE_PROJECT.write_text(str(design_file), encoding="utf-8")

    try:
        import build123d as bd
        env = {"bd": bd, "build123d": bd}
        
        proj_dir_str = str(proj_dir.absolute())
        
        # Cache busting for local imports
        for mod_name in list(sys.modules.keys()):
            mod = sys.modules[mod_name]
            if hasattr(mod, '__file__') and mod.__file__:
                # Use os.path.abspath to safely match paths on Windows
                import os
                mod_file = os.path.abspath(mod.__file__)
                if mod_file.startswith(proj_dir_str):
                    del sys.modules[mod_name]

        added_to_path = False
        if proj_dir_str not in sys.path:
            sys.path.insert(0, proj_dir_str)
            added_to_path = True
            
        try:
            if design_file.exists():
                design_code = design_file.read_text(encoding="utf-8")
                exec(design_code, env)
            else:
                return {"success": False, "error": "design.py not found in project. Cannot compile."}
        finally:
            if added_to_path:
                sys.path.remove(proj_dir_str)

        # Extract shapes
        shapes = []
        for val in env.values():
            if isinstance(val, bd.Shape) and hasattr(val, "volume"):
                # Avoid catching simple 2D profiles unless necessary, prefer solids
                g_type = val.geom_type() if callable(val.geom_type) else val.geom_type
                geom_name = getattr(g_type, "name", str(g_type)).upper()
                if geom_name in ("SOLID", "COMPOUND", "OTHER"):
                    shapes.append(val)
            elif hasattr(val, "part") and isinstance(getattr(val, "part"), bd.Shape):
                shapes.append(val.part)

        if not shapes:
             # Try grabbing active builders
             if hasattr(bd.BuildPart, "_get_context") and bd.BuildPart._get_context():
                 shapes.append(bd.BuildPart._get_context().part)

        if not shapes:
             return {"success": False, "error": "No 3D shapes (Solid/Part) were generated by the script."}

        # Deduplicate and combine
        final_shapes = []
        seen = set()
        for s in shapes:
            if id(s) not in seen:
                seen.add(id(s))
                final_shapes.append(s)

        if len(final_shapes) > 1:
            compound = bd.Compound(final_shapes)
        else:
            compound = final_shapes[0]

        # Export
        ext = req.export_format.lower()
        if ext not in ["stl", "step", "gltf"]:
            ext = "stl"
            
        output_file = proj_dir / f"output.{ext}"
        if ext == "stl":
            bd.export_stl(compound, str(output_file))
            # Also write to shared active output for Extus
            bd.export_stl(compound, str(ACTIVE_STL))
        elif ext == "step":
            bd.export_step(compound, str(output_file))

        return {"success": True, "format": ext, "file": str(output_file)}

    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse(status_code=200, content={
            "success": False,
            "error": tb,
            "short": str(e)
        })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8891)
