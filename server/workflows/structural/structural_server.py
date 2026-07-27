from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.structural.cantilever_fixture import cantilever_glb, cantilever_snapshot
from core.structural.contracts import ProjectStructuralCapture, StructuralSnapshot
from core.structural.design_capture import (
    StructuralDeclarationError,
    parse_project_structural_capture,
)
from core.models import Project, UserWorkspaceState
from core.repositories import ProjectRepository

app = FastAPI(title="Tertius Structural Design Workbench")


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


@app.get("/active/capture", response_model=ProjectStructuralCapture)
def get_active_capture(
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ProjectStructuralCapture:
    project = get_active_project(db, ctx)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project")
    files = ProjectRepository(db, ctx.tenant_id).files_for_runtime(project.name)
    design_source = files.get("design.py") if files else None
    if design_source is None:
        raise HTTPException(status_code=404, detail="Active project has no design.py")
    try:
        return parse_project_structural_capture(
            design_source,
            project_name=project.name,
        )
    except StructuralDeclarationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/fixture/cantilever", response_model=StructuralSnapshot)
def get_cantilever_fixture(
    _ctx: AuthContext = Depends(get_auth_context),
) -> StructuralSnapshot:
    return cantilever_snapshot()


@app.get("/fixture/cantilever/model")
def get_cantilever_model(
    _ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    return Response(content=cantilever_glb(), media_type="model/gltf-binary")
