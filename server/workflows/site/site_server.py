from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Path, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.config import get_settings
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
from core.structural.wind_standard_tables import (
    WindStandardTableError,
    load_wind_standard_dataset,
    site_report_evidence,
    site_table_evidence,
)
from core.workbench_access import require_site_workbench


app = FastAPI(
    title="Tertius Site and Design Basis Workbench",
    dependencies=[Depends(require_site_workbench)],
)

GIS_EVIDENCE_PATTERN = r"^gisv1-[0-9a-f]{32}$"
GIS_UPSTREAM_TIMEOUT_SECONDS = 120.0


def _gis_cache_url() -> str:
    url = get_settings().gis_cache_url.strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=503, detail="GIS cache is not enabled")
    return url


def _gis_detail(response: httpx.Response, fallback: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        return fallback
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return detail if isinstance(detail, str) else fallback


def _checked_gis_response(response: httpx.Response, fallback: str) -> httpx.Response:
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=_gis_detail(response, fallback),
        )
    return response


def _gis_request(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        with httpx.Client(
            timeout=GIS_UPSTREAM_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            return client.request(method, f"{_gis_cache_url()}{path}", **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="GIS cache is unavailable") from exc


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


@app.get("/standards/as-nzs-1170-2-2021/site-values")
def get_site_standard_values(
    region: str = Query(min_length=1, max_length=8),
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        return site_table_evidence(region)
    except WindStandardTableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/standards/as-nzs-1170-2-2021/tables")
def get_wind_standard_tables(
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        return load_wind_standard_dataset()
    except WindStandardTableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/report/evidence")
def download_site_report_evidence(
    site: SiteDefinition,
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        calculation = calculate_site_definition(site)
        payload = site_report_evidence(
            site.model_dump(mode="json"),
            calculation,
        )
    except (SiteWindError, WindStandardTableError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": (
                'attachment; filename="tertius-site-wind-evidence.json"'
            )
        },
    )


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


@app.get("/gis/health")
def get_gis_health(_ctx: AuthContext = Depends(get_auth_context)):
    response = _checked_gis_response(
        _gis_request("GET", "/health/ready"),
        "GIS cache health check failed",
    )
    return response.json()


@app.get("/gis/geocode/status")
def get_gis_geocode_status(_ctx: AuthContext = Depends(get_auth_context)):
    response = _checked_gis_response(
        _gis_request("GET", "/v1/geocode/status"),
        "G-NAF status lookup failed",
    )
    return response.json()


@app.get("/gis/geocode")
def geocode_site_address(
    query: str = Query(min_length=3, max_length=300),
    limit: int = Query(default=5, ge=1, le=10),
    _ctx: AuthContext = Depends(get_auth_context),
):
    response = _checked_gis_response(
        _gis_request(
            "GET",
            "/v1/geocode",
            params={"q": query, "limit": limit},
        ),
        "G-NAF address search failed",
    )
    return response.json()


@app.post("/gis/terrain/site", status_code=201)
def fetch_gis_site_terrain(
    latitude: float = Query(ge=-44.5, le=-9.0),
    longitude: float = Query(ge=112.0, le=154.0),
    radius_m: int = Query(default=2000, ge=100, le=10000),
    _ctx: AuthContext = Depends(get_auth_context),
):
    response = _checked_gis_response(
        _gis_request(
            "POST",
            "/v1/terrain/site",
            json={
                "latitude": latitude,
                "longitude": longitude,
                "radius_m": radius_m,
            },
        ),
        "GA terrain acquisition failed",
    )
    return JSONResponse(status_code=201, content=response.json())


@app.post("/gis/evidence", status_code=201)
async def upload_gis_evidence(
    raster: UploadFile = File(description="Single-band elevation GeoTIFF"),
    provider: str = Form(min_length=1, max_length=80),
    dataset: str = Form(min_length=1, max_length=200),
    licence: str = Form(min_length=1, max_length=200),
    attribution: str = Form(min_length=1, max_length=500),
    dataset_version: str = Form(default="manual-test", min_length=1, max_length=120),
    source_uri: str | None = Form(default=None, max_length=2048),
    _ctx: AuthContext = Depends(get_auth_context),
):
    data = {
        "provider": provider,
        "dataset": dataset,
        "dataset_version": dataset_version,
        "licence": licence,
        "attribution": attribution,
    }
    if source_uri:
        data["source_uri"] = source_uri

    def forward_upload() -> httpx.Response:
        return _gis_request(
            "POST",
            "/v1/evidence",
            data=data,
            files={
                "raster": (
                    raster.filename or "terrain.tif",
                    raster.file,
                    raster.content_type or "image/tiff",
                )
            },
        )

    try:
        response = await run_in_threadpool(forward_upload)
        response = _checked_gis_response(response, "GIS evidence upload failed")
        return JSONResponse(status_code=201, content=response.json())
    finally:
        await raster.close()


@app.get("/gis/evidence/{evidence_id}")
def get_gis_evidence(
    evidence_id: str = Path(pattern=GIS_EVIDENCE_PATTERN),
    _ctx: AuthContext = Depends(get_auth_context),
):
    response = _checked_gis_response(
        _gis_request("GET", f"/v1/evidence/{evidence_id}"),
        "GIS evidence lookup failed",
    )
    return response.json()


@app.get("/gis/evidence/{evidence_id}/point")
def get_gis_elevation_point(
    evidence_id: str = Path(pattern=GIS_EVIDENCE_PATTERN),
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    _ctx: AuthContext = Depends(get_auth_context),
):
    response = _checked_gis_response(
        _gis_request(
            "GET",
            f"/v1/raster/point/{longitude},{latitude}",
            params={"evidence_id": evidence_id},
        ),
        "GIS elevation query failed",
    )
    return response.json()


@app.get("/gis/evidence/{evidence_id}/preview.png")
def get_gis_preview(
    evidence_id: str = Path(pattern=GIS_EVIDENCE_PATTERN),
    _ctx: AuthContext = Depends(get_auth_context),
):
    response = _checked_gis_response(
        _gis_request(
            "GET",
            "/v1/raster/preview.png",
            params={"evidence_id": evidence_id, "rescale": "0,255"},
        ),
        "GIS preview failed",
    )
    return Response(
        content=response.content,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/gis/evidence/{evidence_id}/terrain-rgb/{z}/{x}/{y}.png")
def get_gis_terrain_rgb_tile(
    evidence_id: str = Path(pattern=GIS_EVIDENCE_PATTERN),
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    _ctx: AuthContext = Depends(get_auth_context),
):
    response = _checked_gis_response(
        _gis_request(
            "GET",
            f"/v1/evidence/{evidence_id}/terrain-rgb/{z}/{x}/{y}.png",
        ),
        "GIS terrain tile failed",
    )
    return Response(
        content=response.content,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/gis/evidence/{evidence_id}/relief/{z}/{x}/{y}.png")
def get_gis_relief_tile(
    evidence_id: str = Path(pattern=GIS_EVIDENCE_PATTERN),
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    _ctx: AuthContext = Depends(get_auth_context),
):
    response = _checked_gis_response(
        _gis_request(
            "GET",
            f"/v1/raster/tiles/WebMercatorQuad/{z}/{x}/{y}.png",
            params={
                "evidence_id": evidence_id,
                "rescale": "-20,500",
                "colormap_name": "terrain",
            },
        ),
        "GIS terrain relief tile failed",
    )
    return Response(
        content=response.content,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )
