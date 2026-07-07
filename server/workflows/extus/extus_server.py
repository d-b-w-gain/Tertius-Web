#!/usr/bin/env python3
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
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
from core.models import Artifact, Project, UserWorkspaceState
from core.procurement_analysis import analyze_design_sources, analyze_gltf_tree, build_procurement_analysis
from core.repositories import ProjectRepository

app = FastAPI(title="Extus STL File Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROCUREMENT_ANALYSIS_CACHE_LIMIT = 32
ProcurementAnalysisCacheKey = tuple[
    str,
    str,
    str,
    str | None,
    str | None,
]
_procurement_analysis_cache: OrderedDict[ProcurementAnalysisCacheKey, dict] = OrderedDict()
_procurement_analysis_cache_lock = RLock()


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


def _procurement_analysis_cache_key(
    ctx: AuthContext,
    project: Project,
    manifest_artifact: Artifact | None,
    model_artifact: Artifact | None,
) -> ProcurementAnalysisCacheKey:
    return (
        str(ctx.tenant_id),
        str(project.id),
        str(project.updated_at.timestamp()),
        _artifact_cache_token(manifest_artifact),
        _artifact_cache_token(model_artifact),
    )


def _get_cached_procurement_analysis(cache_key: ProcurementAnalysisCacheKey) -> dict | None:
    with _procurement_analysis_cache_lock:
        cached = _procurement_analysis_cache.get(cache_key)
        if cached is None:
            return None
        _procurement_analysis_cache.move_to_end(cache_key)
        return deepcopy(cached)


def _set_cached_procurement_analysis(cache_key: ProcurementAnalysisCacheKey, response: dict) -> None:
    with _procurement_analysis_cache_lock:
        _procurement_analysis_cache[cache_key] = deepcopy(response)
        _procurement_analysis_cache.move_to_end(cache_key)
        while len(_procurement_analysis_cache) > PROCUREMENT_ANALYSIS_CACHE_LIMIT:
            _procurement_analysis_cache.popitem(last=False)


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


def get_latest_bom_manifest_artifact(db: Session, ctx: AuthContext) -> Artifact | None:
    project = get_active_project(db, ctx)
    if project is None:
        return None
    return db.scalar(
        select(Artifact)
        .where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind == "bom_manifest",
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


def bom_manifest_counts(manifest: dict) -> dict[str, int]:
    return {
        "scopes": _manifest_list_count(manifest, "scopes"),
        "components": _manifest_list_count(manifest, "components"),
        "requirements": _manifest_list_count(manifest, "requirements"),
        "diagnostics": _manifest_list_count(manifest, "diagnostics"),
    }


def bom_manifest_artifact_state(manifest: dict, matches_model: bool) -> str:
    if not matches_model:
        return "stale_manifest"

    counts = bom_manifest_counts(manifest)
    if counts["requirements"] > 0:
        return "ready"
    if counts["scopes"] > 0 or counts["components"] > 0:
        return "scopes_only"
    return "diagnostic_only"


def gltf_to_scene_tree(gltf: dict) -> dict:
    nodes = gltf.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("GLTF JSON must contain a nodes list.")

    def convert_node(index: int) -> dict:
        node = nodes[index]
        if not isinstance(node, dict):
            raise ValueError(f"GLTF node {index} is not an object.")
        child_value = node.get("children")
        child_indexes = child_value if isinstance(child_value, list) else []
        has_mesh = isinstance(node.get("mesh"), int)
        converted = {
            "id": str(index),
            "name": str(node.get("name") or ("Mesh" if has_mesh else f"node_{index}")),
            "type": "Mesh" if has_mesh else "Object3D",
            "isMesh": has_mesh,
            "children": [convert_node(child_index) for child_index in child_indexes if isinstance(child_index, int)],
        }
        if isinstance(node.get("extras"), dict):
            converted["extras"] = node["extras"]
        for key in ("translation", "rotation", "scale", "matrix"):
            if isinstance(node.get(key), list):
                converted[key] = node[key]
        return converted

    scene_indexes: list[int] = []
    scene_id = gltf.get("scene")
    scenes = gltf.get("scenes")
    if isinstance(scene_id, int) and isinstance(scenes, list) and 0 <= scene_id < len(scenes):
        scene = scenes[scene_id]
        if isinstance(scene, dict) and isinstance(scene.get("nodes"), list):
            scene_indexes = [index for index in scene["nodes"] if isinstance(index, int)]

    if not scene_indexes:
        referenced = {
            child_index
            for node in nodes
            if isinstance(node, dict)
            for child_index in (node.get("children") or [])
            if isinstance(child_index, int)
        }
        scene_indexes = [index for index in range(len(nodes)) if index not in referenced]

    return {
        "name": "Scene",
        "type": "Scene",
        "children": [convert_node(index) for index in scene_indexes],
    }


def glb_to_gltf_json(data: bytes) -> dict:
    if len(data) < 20:
        raise ValueError("GLB payload is too short.")
    magic, version, length = struct.unpack("<4sII", data[:12])
    if magic != b"glTF" or version != 2:
        raise ValueError("GLB header is invalid.")
    if length > len(data):
        raise ValueError("GLB payload is truncated.")

    offset = 12
    while offset + 8 <= length:
        chunk_length, chunk_type = struct.unpack("<I4s", data[offset:offset + 8])
        offset += 8
        chunk_end = offset + chunk_length
        if chunk_end > length:
            raise ValueError("GLB chunk is truncated.")
        chunk = data[offset:chunk_end]
        offset = chunk_end
        if chunk_type == b"JSON":
            parsed = json.loads(chunk.rstrip(b" \t\r\n\x00").decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("GLB JSON chunk must contain an object.")
            return parsed

    raise ValueError("GLB payload does not contain a JSON chunk.")


def model_artifact_to_scene_tree(artifact: Artifact) -> dict:
    if artifact.content is None:
        raise ValueError("Latest model artifact has no stored content.")
    if artifact.kind == "gltf":
        gltf = json.loads(artifact.content.decode("utf-8"))
    elif artifact.kind == "glb":
        gltf = glb_to_gltf_json(artifact.content)
    else:
        raise ValueError(f"Unsupported model artifact kind {artifact.kind!r}.")
    if not isinstance(gltf, dict):
        raise ValueError("GLTF artifact must contain a JSON object.")
    return gltf_to_scene_tree(gltf)


def procurement_analysis_counts(analysis: dict) -> dict[str, int]:
    return {
        "scopes": _manifest_list_count(analysis, "assemblies"),
        "components": _manifest_list_count(analysis, "components"),
        "requirements": _manifest_list_count(analysis, "requirements"),
        "diagnostics": _manifest_list_count(analysis, "diagnostics"),
    }


def procurement_analysis_artifact_state(analysis: dict) -> str:
    counts = procurement_analysis_counts(analysis)
    requirements = analysis.get("requirements")
    orderable_requirements = [
        requirement
        for requirement in requirements
        if isinstance(requirement, dict) and requirement.get("orderable") is not False
    ] if isinstance(requirements, list) else []
    if orderable_requirements:
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
    return {"mtime": artifact.created_at.timestamp()}

@app.get("/model")
def get_model(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    artifact = get_latest_model_artifact(db, ctx)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return Response(content=artifact.content, media_type=artifact.content_type)


@app.get("/bom_manifest")
def get_bom_manifest(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    manifest_artifact = get_latest_bom_manifest_artifact(db, ctx)
    if manifest_artifact is None or manifest_artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "BoM manifest not found"})
    model_artifact = get_latest_model_artifact(db, ctx, include_content=False)
    try:
        manifest = json.loads(manifest_artifact.content.decode("utf-8"))
    except Exception:
        return JSONResponse(status_code=500, content={"error": "BoM manifest artifact is invalid JSON"})
    if not isinstance(manifest, dict):
        return JSONResponse(status_code=500, content={"error": "BoM manifest artifact must be a JSON object"})
    matches_model = bool(
        model_artifact
        and model_artifact.compile_job_id is not None
        and model_artifact.compile_job_id == manifest_artifact.compile_job_id
    )
    counts = bom_manifest_counts(manifest)
    return {
        "manifest": manifest,
        "manifest_artifact_id": str(manifest_artifact.id),
        "manifest_compile_job_id": str(manifest_artifact.compile_job_id) if manifest_artifact.compile_job_id else None,
        "model_artifact_id": str(model_artifact.id) if model_artifact else None,
        "model_compile_job_id": str(model_artifact.compile_job_id) if model_artifact and model_artifact.compile_job_id else None,
        "matches_model": matches_model,
        "is_verified_for_model": matches_model,
        "artifact_state": bom_manifest_artifact_state(manifest, matches_model),
        "manifest_counts": counts,
        "mtime": manifest_artifact.created_at.timestamp(),
    }


@app.get("/procurement_analysis")
def get_procurement_analysis(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project = get_active_project(db, ctx)
    if project is None:
        return JSONResponse(status_code=404, content={"error": "Active project not found"})

    manifest_artifact = get_latest_bom_manifest_artifact(db, ctx)
    model_artifact = get_latest_model_artifact(db, ctx, include_content=False)
    cache_key = _procurement_analysis_cache_key(ctx, project, manifest_artifact, model_artifact)
    cached_response = _get_cached_procurement_analysis(cache_key)
    if cached_response is not None:
        return cached_response

    files = ProjectRepository(db, ctx.tenant_id).files_for_runtime(project.name) or {}
    if "design.py" not in files:
        return JSONResponse(status_code=404, content={"error": "design.py not found"})

    explicit_manifest = None
    diagnostics: list[dict] = []
    if manifest_artifact is not None and manifest_artifact.content is not None:
        try:
            loaded_manifest = json.loads(manifest_artifact.content.decode("utf-8"))
            if isinstance(loaded_manifest, dict):
                explicit_manifest = loaded_manifest
            else:
                diagnostics.append({
                    "code": "invalid_bom_manifest",
                    "severity": "warning",
                    "message": "Stored BoM manifest was not a JSON object, so procurement analysis ignored it.",
                })
        except Exception:
            diagnostics.append({
                "code": "invalid_bom_manifest_json",
                "severity": "warning",
                "message": "Stored BoM manifest could not be parsed, so procurement analysis ignored it.",
            })

    tree_analysis: dict[str, Any] = {"assemblies": [], "components": [], "diagnostics": []}
    visual_analysis_loaded = False
    if model_artifact is not None and model_artifact.kind in {"gltf", "glb"}:
        try:
            model_content_artifact = get_model_artifact_by_id(db, ctx, model_artifact.id)
            if model_content_artifact is None:
                raise ValueError("Latest model artifact could not be loaded.")
            tree_analysis = analyze_gltf_tree(model_artifact_to_scene_tree(model_content_artifact))
            visual_analysis_loaded = True
        except Exception:
            diagnostics.append({
                "code": "invalid_gltf_json",
                "severity": "warning",
                "message": "Latest GLTF/GLB artifact could not be parsed, so procurement analysis used source evidence only.",
            })
    elif model_artifact is not None:
        diagnostics.append({
            "code": "unsupported_visual_artifact_for_analysis",
            "severity": "info",
            "message": "Latest model artifact is not text GLTF, so procurement analysis used source evidence only.",
        })

    try:
        source_analysis = analyze_design_sources(files)
        analysis = build_procurement_analysis(
            source_analysis,
            tree_analysis,
            explicit_manifest=explicit_manifest,
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"Procurement analysis failed: {exc}"})

    analysis.setdefault("diagnostics", [])
    analysis["diagnostics"].extend(diagnostics)
    model_time = model_artifact.created_at.timestamp() if model_artifact else 0
    manifest_time = manifest_artifact.created_at.timestamp() if manifest_artifact else 0
    mtime = max(model_time, manifest_time)
    counts = procurement_analysis_counts(analysis)

    response = {
        "manifest": analysis,
        "manifest_artifact_id": f"procurement-analysis:{project.id}:{mtime}",
        "manifest_compile_job_id": str(manifest_artifact.compile_job_id) if manifest_artifact and manifest_artifact.compile_job_id else None,
        "model_artifact_id": str(model_artifact.id) if model_artifact else None,
        "model_compile_job_id": str(model_artifact.compile_job_id) if model_artifact and model_artifact.compile_job_id else None,
        "matches_model": True,
        "is_verified_for_model": visual_analysis_loaded,
        "artifact_state": procurement_analysis_artifact_state(analysis),
        "manifest_counts": counts,
        "mtime": mtime,
    }
    _set_cached_procurement_analysis(cache_key, response)
    return response


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
