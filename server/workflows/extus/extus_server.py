#!/usr/bin/env python3
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="Extus STL File Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_ROOT = Path(__file__).parent.parent.parent.parent / 'cache' / 'tertius'
ACTIVE_STL = CACHE_ROOT / 'active_output.stl'
ACTIVE_PROJECT = CACHE_ROOT / 'active_project.txt'

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/project_name")
def get_project_name():
    if ACTIVE_PROJECT.exists():
        script_path = ACTIVE_PROJECT.read_text(encoding="utf-8").strip()
        if script_path:
            return {"project_name": Path(script_path).parent.name}
    return {"project_name": ""}

@app.get("/status")
def get_status():
    if not ACTIVE_STL.exists():
        return JSONResponse(status_code=404, content={"error": "File not found"})
    mtime = os.path.getmtime(ACTIVE_STL)
    return {"mtime": mtime}

@app.get("/model")
def get_model():
    if not ACTIVE_STL.exists():
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return FileResponse(ACTIVE_STL, media_type="application/octet-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8892)
