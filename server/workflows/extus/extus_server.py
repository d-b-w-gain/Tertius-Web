#!/usr/bin/env python3
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.models import Artifact, Project, UserWorkspaceState

app = FastAPI(title="Extus STL File Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def get_latest_model_artifact(db: Session, ctx: AuthContext) -> Artifact | None:
    project = get_active_project(db, ctx)
    if project is None:
        return None
    return db.scalar(
        select(Artifact)
        .where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind.in_(["gltf", "glb", "stl"]),
        )
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )


def get_project_by_name(db: Session, ctx: AuthContext, name: str) -> Project | None:
    return db.scalar(
        select(Project).where(
            Project.tenant_id == ctx.tenant_id,
            Project.name == name,
        )
    )


def get_model_artifact_by_id(
    db: Session,
    ctx: AuthContext,
    artifact_id: UUID,
    project_name: str | None = None,
) -> Artifact | None:
    project = get_project_by_name(db, ctx, project_name) if project_name else get_active_project(db, ctx)
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
    artifact = get_latest_model_artifact(db, ctx)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return {"mtime": artifact.created_at.timestamp()}

@app.get("/model")
def get_model(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    artifact = get_latest_model_artifact(db, ctx)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return Response(content=artifact.content, media_type=artifact.content_type)


@app.get("/artifacts/{artifact_id}/model")
def get_model_by_artifact_id(
    artifact_id: UUID,
    project: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    artifact = get_model_artifact_by_id(db, ctx, artifact_id, project_name=project)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return Response(content=artifact.content, media_type=artifact.content_type)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8892)
