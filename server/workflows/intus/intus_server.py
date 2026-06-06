#!/usr/bin/env python3
import sys
import json
import traceback
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
def list_projects():
    projects = [p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()]
    return {"projects": projects}

@app.post("/projects/{name}/new")
def new_project(name: str):
    proj_dir = PROJECTS_DIR / name
    if proj_dir.exists():
        return JSONResponse(status_code=400, content={"error": "Project already exists"})
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "design.py").write_text(DEFAULT_PURLIN, encoding="utf-8")
    auto_commit(proj_dir, "Initial project creation")
    return {"success": True, "project": name}

@app.get("/projects/{name}/files")
def list_files(name: str):
    proj_dir = PROJECTS_DIR / name
    if not proj_dir.exists():
        return JSONResponse(status_code=404, content={"error": "Project not found"})
    files = [p.name for p in proj_dir.glob("*.py") if p.is_file()]
    # Ensure design.py is always first if it exists
    if "design.py" in files:
        files.remove("design.py")
        files.insert(0, "design.py")
    return {"files": files}

@app.get("/projects/{name}/code")
def get_code(name: str, file: str = "design.py"):
    # Security: prevent directory traversal
    if "/" in file or "\\" in file or not file.endswith(".py"):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
        
    script_file = PROJECTS_DIR / name / file
    if script_file.exists():
        if file == "design.py":
            ACTIVE_PROJECT.write_text(str(script_file), encoding="utf-8")
        return {"code": script_file.read_text(encoding="utf-8")}
    return JSONResponse(status_code=404, content={"error": "File not found"})

@app.get("/projects/{name}/status")
def get_status(name: str, file: str = "design.py"):
    if "/" in file or "\\" in file or not file.endswith(".py"):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    script_file = PROJECTS_DIR / name / file
    if script_file.exists():
        return {"mtime": script_file.stat().st_mtime}
    return JSONResponse(status_code=404, content={"error": "Project not found"})

@app.get("/projects/{name}/git_status")
def get_git_status(name: str):
    proj_dir = PROJECTS_DIR / name
    if not proj_dir.exists():
        return JSONResponse(status_code=404, content={"error": "Project not found"})
        
    if not (proj_dir / ".git").exists():
        return {"is_git": False}
        
    try:
        commit_res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=proj_dir, capture_output=True, text=True)
        commit_hash = commit_res.stdout.strip()
        
        log_res = subprocess.run(["git", "log", "--oneline", "-n", "50"], cwd=proj_dir, capture_output=True, text=True)
        history = log_res.stdout.strip().splitlines()
        
        return {
            "is_git": True,
            "commit": commit_hash,
            "history": history
        }
    except Exception as e:
        return {"is_git": False, "error": str(e)}

@app.post("/projects/{name}/save")
def save_code(name: str, req: CodeRequest):
    file = req.file or "design.py"
    if "/" in file or "\\" in file or not file.endswith(".py"):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
        
    script_file = PROJECTS_DIR / name / file
    if script_file.parent.exists():
        script_file.write_text(req.code, encoding="utf-8")
        auto_commit(PROJECTS_DIR / name, f"Manual save {file} via Intus")
        return {"success": True}
    return JSONResponse(status_code=404, content={"error": "Project not found"})

@app.delete("/projects/{name}/file")
def delete_file(name: str, file: str):
    if "/" in file or "\\" in file or not file.endswith(".py"):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    if file == "design.py":
        return JSONResponse(status_code=400, content={"error": "Cannot delete design.py"})
        
    script_file = PROJECTS_DIR / name / file
    if script_file.exists():
        script_file.unlink()
        
        proj_dir = PROJECTS_DIR / name
        import subprocess
        subprocess.run(["git", "rm", file], cwd=proj_dir, capture_output=True)
        auto_commit(proj_dir, f"Deleted {file} via Intus")
        
        return {"success": True}
    return JSONResponse(status_code=404, content={"error": "File not found"})

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
