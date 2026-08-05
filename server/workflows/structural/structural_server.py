from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.compile_runtime import runtime_files_hash, structural_runtime_files_hash
from core.db import get_db
from core.structural.cantilever_fixture import cantilever_glb, cantilever_snapshot
from core.structural.contracts import (
    CompiledStructuralManifest,
    ProjectStructuralCapture,
    StructuralSnapshot,
)
from core.structural.design_capture import (
    StructuralDeclarationError,
    capture_project_structural_declaration,
    parse_project_structural_capture,
)
from core.structural.project_analysis import (
    StructuralAnalysisError,
    solve_project_structural,
)
from core.structural.site_wind import (
    REGION_SOURCE,
    REGION_VERIFY_AGAINST,
    SiteWindError,
    compute_site_wind,
    lookup_wind_region,
    wind_region_geojson,
)
from core.models import Artifact, Project, UserWorkspaceState
from core.repositories import ProjectRepository
from core.site_definition import (
    SITE_DEFINITION_FILENAME,
    SiteDefinitionError,
    apply_site_definition,
    parse_site_definition,
    validate_design_site_usage,
)
from core.workbench_access import require_structural_workbench

app = FastAPI(
    title="Tertius Structural Design Workbench",
    dependencies=[Depends(require_structural_workbench)],
)


class WindSiteRequest(BaseModel):
    site_address: str
    latitude: float
    longitude: float
    region: str = ""
    terrain_category: str
    importance_level: str = "2"
    annual_probability_uls: str = "1/500"
    reference_height_m: float
    direction_multiplier: float = 1.0
    shielding_multiplier: float = 1.0
    topographic_multiplier: float = 1.0
    climate_change_multiplier: float | None = None


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


def get_latest_structural_manifest_artifact(
    db: Session,
    ctx: AuthContext,
    project: Project,
) -> Artifact | None:
    return db.scalar(
        select(Artifact)
        .where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind == "structural",
        )
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1)
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
    assert files is not None
    site = None
    site_source = files.get(SITE_DEFINITION_FILENAME)
    if site_source is not None:
        try:
            site = parse_site_definition(site_source)
            validate_design_site_usage(design_source)
        except SiteDefinitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    artifact = get_latest_structural_manifest_artifact(db, ctx, project)
    if artifact is not None and artifact.content is not None:
        try:
            compiled = CompiledStructuralManifest.model_validate_json(artifact.content)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Compiled structural manifest is invalid: {exc}",
            ) from exc
        source_is_current = (
            compiled.structural_source_hash == structural_runtime_files_hash(files)
            if compiled.structural_source_hash is not None
            else compiled.source_hash == runtime_files_hash(files)
        )
        if not source_is_current:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Structural metadata is stale. Compile the active project "
                    "to resolve its current design.py imports."
                ),
            )
        try:
            declaration = (
                apply_site_definition(compiled.declaration, site)
                if site is not None
                else compiled.declaration
            )
            return capture_project_structural_declaration(
                declaration,
                project_name=project.name,
                design_hash=compiled.design_hash,
                capture_detail=(
                    "Structural manifest resolved from the compiled design.py "
                    "source closure and validated catalogue imports."
                ),
            )
        except (StructuralDeclarationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        capture = parse_project_structural_capture(
            design_source,
            project_name=project.name,
        )
        if site is None:
            return capture
        return ProjectStructuralCapture.model_validate(
            apply_site_definition(capture.model_dump(mode="python"), site)
        )
    except (StructuralDeclarationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/active/analysis", response_model=StructuralSnapshot)
def get_active_analysis(
    combination_id: str | None = Query(default=None),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> StructuralSnapshot:
    capture = get_active_capture(ctx=ctx, db=db)
    try:
        return solve_project_structural(
            capture,
            combination_id=combination_id,
        )
    except StructuralAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/wind/region")
def get_wind_region(
    latitude: float,
    longitude: float,
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        result = lookup_wind_region(
            latitude=latitude,
            longitude=longitude,
        )
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


@app.post("/wind/site")
def calculate_site_wind(
    request: WindSiteRequest,
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        suggested = lookup_wind_region(
            latitude=request.latitude,
            longitude=request.longitude,
        )
        selected_region = request.region.strip().upper()
        if not selected_region:
            if suggested is None or not suggested.get("region"):
                raise SiteWindError(
                    "site coordinates do not resolve to a wind region; "
                    "select one manually"
                )
            selected_region = str(suggested["region"])
        calculation = compute_site_wind(
            region=selected_region,
            terrain_category=request.terrain_category,
            importance_level=request.importance_level,
            annual_probability_uls=request.annual_probability_uls,
            reference_height_m=request.reference_height_m,
            direction_multiplier=request.direction_multiplier,
            shielding_multiplier=request.shielding_multiplier,
            topographic_multiplier=request.topographic_multiplier,
            climate_change_multiplier=request.climate_change_multiplier,
        )
    except SiteWindError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    suggested_region = (
        str(suggested.get("region"))
        if suggested is not None and suggested.get("region")
        else None
    )
    return {
        "site_address": request.site_address,
        "latitude": request.latitude,
        "longitude": request.longitude,
        "region_area": (
            str(suggested.get("area") or "") if suggested is not None else ""
        ),
        "region_source": REGION_SOURCE,
        "region_approximate": True,
        "region_status": "suggested",
        "suggested_region": suggested_region,
        "selected_region": selected_region,
        "region_conflict": bool(
            suggested_region and suggested_region != selected_region
        ),
        "region_detail": (
            suggested.get("detail")
            if suggested is not None
            else "No overlay suggestion is available."
        ),
        **calculation,
    }


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
