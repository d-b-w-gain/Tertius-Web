from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.models import Project, UserWorkspaceState
from core.repositories import ProjectRepository
from core.site_definition import (
    SITE_DEFINITION_FILENAME,
    SiteDefinition,
    SiteDefinitionError,
    calculate_site_definition,
    default_site_definition,
    parse_site_definition,
    render_site_definition,
)
from core.structural.site_wind import (
    REGION_SOURCE,
    REGION_VERIFY_AGAINST,
    SiteWindError,
    lookup_wind_region,
    wind_region_geojson,
)
from core.workbench_access import require_site_workbench


app = FastAPI(
    title="Tertius Site and Design Basis Workbench",
    dependencies=[Depends(require_site_workbench)],
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


def _project_or_404(db: Session, ctx: AuthContext) -> Project:
    project = get_active_project(db, ctx)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project")
    return project


def _response(project: Project, site: SiteDefinition, *, exists: bool) -> dict:
    try:
        calculation = calculate_site_definition(site)
    except SiteWindError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "project_name": project.name,
        "filename": SITE_DEFINITION_FILENAME,
        "exists": exists,
        "site_dict": site.model_dump(mode="json"),
        "source": render_site_definition(site),
        "calculation": calculation,
    }


@app.get("/active")
def get_active_site(
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    project = _project_or_404(db, ctx)
    source = ProjectRepository(db, ctx.tenant_id).get_code(
        project.name,
        SITE_DEFINITION_FILENAME,
    )
    if source is None:
        return _response(project, default_site_definition(), exists=False)
    try:
        site = parse_site_definition(source)
    except SiteDefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(project, site, exists=True)


@app.put("/active")
def save_active_site(
    site: SiteDefinition,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    project = _project_or_404(db, ctx)
    # Calculate before saving so an invalid action basis never becomes the
    # project's canonical site definition.
    try:
        calculate_site_definition(site)
    except SiteWindError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = ProjectRepository(db, ctx.tenant_id).save_code(
        project.name,
        SITE_DEFINITION_FILENAME,
        render_site_definition(site),
        ctx.user_id,
        "Update site and design basis",
    )
    if not saved:
        raise HTTPException(status_code=404, detail="Active project no longer exists")
    return _response(project, site, exists=True)


@app.post("/calculate")
def calculate_site(
    site: SiteDefinition,
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        return calculate_site_definition(site)
    except SiteWindError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/wind/region")
def get_wind_region(
    latitude: float,
    longitude: float,
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        result = lookup_wind_region(latitude=latitude, longitude=longitude)
    except SiteWindError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is not None:
        return result
    return {
        "region": None,
        "area": None,
        "approximate": True,
        "source": REGION_SOURCE,
        "verify_against": REGION_VERIFY_AGAINST,
        "detail": "Coordinates are outside the deployed Australian region overlay.",
    }


@app.get("/wind/regions.geojson")
def get_wind_regions_geojson(
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        return JSONResponse(content=wind_region_geojson())
    except SiteWindError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
