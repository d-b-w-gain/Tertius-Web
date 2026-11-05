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

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.config import get_settings
from core.db import get_db
from core.gltf_geometry import model_site_dimensions
from core.models import Artifact, Project, ProjectShare, UserWorkspaceState, now_utc
from core.repositories import CompileRepository
from core.scene_manifest import ASSET_ID
from core.share_links import (
    EXTUS_WORKBENCH,
    SHARE_TOKEN_HEADER,
    ShareLinksUnavailable,
    share_id_from_token,
    share_token,
)

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
    return latest_project_model(db, project, include_content=include_content)


def latest_project_model(db: Session, project: Project, *, include_content: bool = True) -> Artifact | None:
    """The project's latest compiled model, signed-in or shared."""
    query = (
        select(Artifact)
        .where(
            Artifact.tenant_id == project.tenant_id,
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


@app.get("/procurement_analysis")
def get_procurement_analysis(
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    """Serve the deterministic compile projection under the newer UI route.

    The structural-enabled runtime emits ``tertius.procurement.v1`` directly
    from the compile.  Newer clients request ``/procurement_analysis``; keep
    that route compatible without replacing the authoritative compile artifact
    with a GLTF-derived approximation.
    """
    return get_procurement(ctx=ctx, db=db)


def _latest_scene(db: Session, model_artifact: Artifact | None) -> dict[str, Any] | None:
    """The per-component scene (#314) of a model, if its compile published one."""
    if model_artifact is None or model_artifact.compile_job_id is None:
        return None
    scene_artifact = db.scalar(
        select(Artifact).where(
            Artifact.tenant_id == model_artifact.tenant_id,
            Artifact.project_id == model_artifact.project_id,
            Artifact.compile_job_id == model_artifact.compile_job_id,
            Artifact.kind == "scene",
        )
    )
    if scene_artifact is None or scene_artifact.content is None:
        return None
    manifest = json.loads(scene_artifact.content.decode("utf-8"))
    manifest.pop("asset_refs", None)        # transport detail
    return {
        "manifest": manifest,
        "model_artifact_id": str(model_artifact.id),
        "scene_artifact_id": str(scene_artifact.id),
    }


def _scene_asset_response(db: Session, tenant_id: UUID, asset_id: str, cache: str) -> Response:
    if not ASSET_ID.match(asset_id):
        return JSONResponse(status_code=404, content={"error": "Scene asset not found"})
    asset = CompileRepository(db, tenant_id).get_scene_asset(asset_id)
    if asset is None:
        return JSONResponse(status_code=404, content={"error": "Scene asset not found"})
    return Response(
        content=asset.content,
        media_type="model/gltf-binary",
        headers={"Cache-Control": f"{cache}, max-age=31536000, immutable", "ETag": f'"{asset.sha256}"'},
    )


@app.get("/scene")
def get_scene(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    """The per-component scene manifest (#314) of the latest model, if that
    compile published one; 404 means load the monolithic model instead."""
    scene = _latest_scene(db, get_latest_model_artifact(db, ctx, include_content=False))
    if scene is None:
        return JSONResponse(status_code=404, content={"error": "Scene not found"})
    return scene


@app.get("/scene-assets/{asset_id}")
def get_scene_asset(
    asset_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    """One immutable scene asset: its ID names its content, so browsers keep it."""
    return _scene_asset_response(db, ctx.tenant_id, asset_id, "private")


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

# --- Share links (#417) --------------------------------------------------
# The owner makes read-only links to the active project; anyone holding one
# reads that project's latest model through the /shared routes, which take
# the token from SHARE_TOKEN_HEADER and answer 404 for anything else.

_NO_STORE = {"Cache-Control": "no-store"}


def _share_payload(share: ProjectShare) -> dict[str, Any]:
    return {
        "id": str(share.id),
        "token": share_token(share.id, get_settings()),
        "workbench": share.workbench,
        "created_at": share.created_at.isoformat(),
    }


def _share_links_unavailable() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "Share links are not configured"})


@app.get("/shares")
def list_shares(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    """The active project's live share links, newest first."""
    project = get_active_project(db, ctx)
    if project is None:
        return {"project_name": "", "shares": []}
    shares = db.scalars(
        select(ProjectShare)
        .where(
            ProjectShare.tenant_id == ctx.tenant_id,
            ProjectShare.project_id == project.id,
            ProjectShare.workbench == EXTUS_WORKBENCH,
            ProjectShare.revoked_at.is_(None),
        )
        .order_by(ProjectShare.created_at.desc())
    ).all()
    try:
        return {"project_name": project.name, "shares": [_share_payload(share) for share in shares]}
    except ShareLinksUnavailable:
        return _share_links_unavailable()


@app.post("/shares", status_code=201)
def create_share(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    """A new read-only Extus link to the active project."""
    project = get_active_project(db, ctx)
    if project is None:
        return JSONResponse(status_code=404, content={"error": "No active project to share"})
    share = ProjectShare(
        tenant_id=ctx.tenant_id,
        project_id=project.id,
        workbench=EXTUS_WORKBENCH,
        created_by=ctx.user_id,
    )
    db.add(share)
    db.flush()
    try:
        payload = _share_payload(share)
    except ShareLinksUnavailable:
        db.rollback()
        return _share_links_unavailable()
    db.commit()
    return payload


@app.delete("/shares/{share_id}")
def revoke_share(share_id: UUID, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    share = db.scalar(
        select(ProjectShare).where(
            ProjectShare.id == share_id,
            ProjectShare.tenant_id == ctx.tenant_id,
            ProjectShare.revoked_at.is_(None),
        )
    )
    if share is None:
        return JSONResponse(status_code=404, content={"error": "Share link not found"})
    share.revoked_at = now_utc()
    db.commit()
    return {"revoked": True}


def get_shared_project(request: Request, db: Session = Depends(get_db)) -> Project:
    """The project a share token opens, or 404: an unknown, forged or revoked
    token looks the same as a deleted project."""
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")
    try:
        share_id = share_id_from_token(request.headers.get(SHARE_TOKEN_HEADER), get_settings())
    except ShareLinksUnavailable:
        raise not_found from None
    if share_id is None:
        raise not_found
    share = db.get(ProjectShare, share_id)
    if share is None or share.revoked_at is not None or share.workbench != EXTUS_WORKBENCH:
        raise not_found
    project = db.scalar(
        select(Project).where(Project.id == share.project_id, Project.tenant_id == share.tenant_id)
    )
    if project is None:
        raise not_found
    return project


@app.get("/shared/project_name")
def get_shared_project_name(project: Project = Depends(get_shared_project)):
    return JSONResponse(content={"project_name": project.name}, headers=_NO_STORE)


@app.get("/shared/status")
def get_shared_status(project: Project = Depends(get_shared_project), db: Session = Depends(get_db)):
    artifact = latest_project_model(db, project, include_content=False)
    if artifact is None:
        return JSONResponse(status_code=404, content={"error": "File not found"}, headers=_NO_STORE)
    return JSONResponse(
        content={"mtime": artifact.created_at.timestamp(), "model_artifact_id": str(artifact.id)},
        headers=_NO_STORE,
    )


@app.get("/shared/model")
def get_shared_model(project: Project = Depends(get_shared_project), db: Session = Depends(get_db)):
    artifact = latest_project_model(db, project)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"}, headers=_NO_STORE)
    return Response(content=artifact.content, media_type=artifact.content_type, headers=_NO_STORE)


@app.get("/shared/scene")
def get_shared_scene(project: Project = Depends(get_shared_project), db: Session = Depends(get_db)):
    scene = _latest_scene(db, latest_project_model(db, project, include_content=False))
    if scene is None:
        return JSONResponse(status_code=404, content={"error": "Scene not found"}, headers=_NO_STORE)
    return JSONResponse(content=scene, headers=_NO_STORE)


@app.get("/shared/scene-assets/{asset_id}")
def get_shared_scene_asset(
    asset_id: str,
    project: Project = Depends(get_shared_project),
    db: Session = Depends(get_db),
):
    """A scene asset, only if the shared project's current scene uses it:
    asset ids are tenant-wide, and a link must not open other projects'."""
    scene = _latest_scene(db, latest_project_model(db, project, include_content=False))
    if scene is None or asset_id not in (scene["manifest"].get("assets") or []):
        return JSONResponse(status_code=404, content={"error": "Scene asset not found"})
    return _scene_asset_response(db, project.tenant_id, asset_id, "private")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8892)
