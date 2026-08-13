#!/usr/bin/env python3
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import logging
import struct
from threading import RLock
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.gltf_geometry import model_site_dimensions
from core.models import Artifact, Project, UserWorkspaceState

logger = logging.getLogger(__name__)

app = FastAPI(title="Extus STL File Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_GEOMETRY_CACHE_LIMIT = 16
_model_geometry_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_model_geometry_cache_lock = RLock()


def _artifact_cache_token(artifact: Artifact | None) -> str | None:
    if artifact is None:
        return None
    return "|".join(
        [
            str(artifact.id),
            artifact.kind,
            str(artifact.byte_size or 0),
            str(artifact.compile_job_id) if artifact.compile_job_id else "",
            str(artifact.created_at.timestamp()),
        ]
    )


def _cached_model_geometry(artifact: Artifact, db: Session, ctx: AuthContext) -> dict[str, Any] | None:
    cache_key = _artifact_cache_token(artifact)
    if cache_key is None or artifact.kind not in {"gltf", "glb"}:
        return None
    with _model_geometry_cache_lock:
        cached = _model_geometry_cache.get(cache_key)
        if cached is not None:
            _model_geometry_cache.move_to_end(cache_key)
            return deepcopy(cached)

    loaded = get_model_artifact_by_id(db, ctx, artifact.id)
    if loaded is None or loaded.content is None:
        return None
    try:
        dimensions = model_site_dimensions(loaded.kind, loaded.content)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, struct.error):
        logger.exception("Could not read active candidate model bounds")
        return None
    if dimensions is None:
        return None
    dimensions = {**dimensions, "model_artifact_id": str(artifact.id)}
    with _model_geometry_cache_lock:
        _model_geometry_cache[cache_key] = deepcopy(dimensions)
        _model_geometry_cache.move_to_end(cache_key)
        while len(_model_geometry_cache) > MODEL_GEOMETRY_CACHE_LIMIT:
            _model_geometry_cache.popitem(last=False)
    return dimensions


def get_active_project(db: Session, ctx: AuthContext) -> Project | None:
    state = db.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == ctx.user_id,
            UserWorkspaceState.tenant_id == ctx.tenant_id,
        )
    )
    if state is None or state.active_project_id is None:
        return None
    return db.scalar(
        select(Project).where(
            Project.tenant_id == ctx.tenant_id,
            Project.id == state.active_project_id,
        )
    )


def get_latest_model_artifact(db: Session, ctx: AuthContext, *, include_content: bool = True) -> Artifact | None:
    project = get_active_project(db, ctx)
    if project is None:
        return None
    query = (
        select(Artifact)
        .where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind.in_(["gltf", "glb", "stl"]),
        )
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    if not include_content:
        query = query.options(
            load_only(
                Artifact.id,
                Artifact.tenant_id,
                Artifact.project_id,
                Artifact.compile_job_id,
                Artifact.kind,
                Artifact.storage_key,
                Artifact.content_type,
                Artifact.byte_size,
                Artifact.created_at,
            )
        )
    return db.scalar(query)


def get_latest_procurement_artifact(db: Session, ctx: AuthContext) -> Artifact | None:
    project = get_active_project(db, ctx)
    if project is None:
        return None
    return db.scalar(
        select(Artifact)
        .where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind == "procurement",
        )
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )


def get_model_artifact_by_id(db: Session, ctx: AuthContext, artifact_id: UUID) -> Artifact | None:
    project = get_active_project(db, ctx)
    if project is None:
        return None
    return db.scalar(
        select(Artifact)
        .where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.id == artifact_id,
            Artifact.kind.in_(["gltf", "glb", "stl"]),
        )
        .limit(1)
    )


def _manifest_list_count(manifest: dict, key: str) -> int:
    value = manifest.get(key)
    return len(value) if isinstance(value, list) else 0


def procurement_projection_counts(manifest: dict) -> dict[str, int]:
    return {
        "scopes": _manifest_list_count(manifest, "assemblies"),
        "components": _manifest_list_count(manifest, "components"),
        "requirements": _manifest_list_count(manifest, "requirements"),
        "diagnostics": _manifest_list_count(manifest, "diagnostics"),
    }


def procurement_projection_artifact_state(manifest: dict, matches_model: bool) -> str:
    if not matches_model:
        return "stale_manifest"

    counts = procurement_projection_counts(manifest)
    if counts["requirements"] > 0:
        return "ready"
    if counts["scopes"] > 0 or counts["components"] > 0:
        return "scopes_only"
    return "diagnostic_only"


@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/projects/{name}/activate")
def activate_project(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    from core.repositories import ProjectRepository
    from fastapi.responses import JSONResponse
    repo = ProjectRepository(db, ctx.tenant_id)
    project = repo.get_project(name)
    if not project:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    repo.set_active_project(ctx.user_id, project.id)
    db.commit()
    return {"success": True}

@app.get("/project_name")
def get_project_name(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project = get_active_project(db, ctx)
    if project is None:
        return {"project_name": ""}
    return {"project_name": project.name}

@app.get("/status")
def get_status(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    artifact = get_latest_model_artifact(db, ctx, include_content=False)
    if artifact is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    response: dict[str, Any] = {
        "mtime": artifact.created_at.timestamp(),
        "model_artifact_id": str(artifact.id),
    }
    dimensions = _cached_model_geometry(artifact, db, ctx)
    if dimensions is not None:
        response["site_dimensions"] = dimensions
    return response

@app.get("/model")
def get_model(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    artifact = get_latest_model_artifact(db, ctx)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return Response(content=artifact.content, media_type=artifact.content_type)


@app.get("/procurement")
def get_procurement(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    manifest_artifact = get_latest_procurement_artifact(db, ctx)
    if manifest_artifact is None or manifest_artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "Procurement artifact not found"})
    model_artifact = get_latest_model_artifact(db, ctx, include_content=False)
    try:
        manifest = json.loads(manifest_artifact.content.decode("utf-8"))
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Procurement artifact is invalid JSON"})
    if not isinstance(manifest, dict):
        return JSONResponse(status_code=500, content={"error": "Procurement artifact must be a JSON object"})
    if manifest.get("schema_version") != "tertius.procurement.v1":
        return JSONResponse(
            status_code=500,
            content={"error": "Procurement artifact schema is unsupported"},
        )
    matches_model = bool(
        model_artifact
        and model_artifact.compile_job_id is not None
        and model_artifact.compile_job_id == manifest_artifact.compile_job_id
    )
    counts = procurement_projection_counts(manifest)
    return {
        "manifest": manifest,
        "manifest_artifact_id": str(manifest_artifact.id),
        "manifest_compile_job_id": str(manifest_artifact.compile_job_id) if manifest_artifact.compile_job_id else None,
        "model_artifact_id": str(model_artifact.id) if model_artifact else None,
        "model_compile_job_id": str(model_artifact.compile_job_id) if model_artifact and model_artifact.compile_job_id else None,
        "matches_model": matches_model,
        "is_verified_for_model": matches_model,
        "artifact_state": procurement_projection_artifact_state(
            manifest,
            matches_model,
        ),
        "manifest_counts": counts,
        "mtime": manifest_artifact.created_at.timestamp(),
    }


@app.get("/artifacts/{artifact_id}/model")
def get_model_by_artifact_id(
    artifact_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    artifact = get_model_artifact_by_id(db, ctx, artifact_id)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return Response(content=artifact.content, media_type=artifact.content_type)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8892)
