from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

import numpy
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from rasterio.fill import fillnodata
from rio_tiler.io import COGReader
from rio_tiler.utils import render
from starlette.concurrency import run_in_threadpool
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers
from titiler.core.factory import TilerFactory

from .gnaf import GnafIndex
from .cadastre import NswPropertyBoundaryProvider, SiteBoundaryUnavailable
from .buildings import BuildingDataUnavailable, OpenBuildingProvider
from .models import (
    BuildingEvidence,
    CardinalTerrainProfileEvidence,
    EvidenceManifest,
    DirectionalWindMultiplierEvidence,
    GeocodeCandidate,
    LocalDirectionalWindEvidence,
    SiteBoundaryEvidence,
    SourceMetadata,
    TerrainSiteRequest,
)
from .settings import GisCacheSettings
from .store import (
    EvidenceNotFoundError,
    EvidenceStore,
    EvidenceValidationError,
    UploadTooLargeError,
)
from .terrain import TerrainFetcher
from .terrain_profiles import TerrainProfileSampler
from .local_wind_analysis import LocalWindAnalyzer
from .wind_multipliers import GaWindMultiplierProvider, WindMultiplierUnavailable


def create_app(
    settings: GisCacheSettings | None = None,
    wind_multiplier_provider: GaWindMultiplierProvider | None = None,
    site_boundary_provider: NswPropertyBoundaryProvider | None = None,
    building_provider: OpenBuildingProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or GisCacheSettings.from_env()
    store = EvidenceStore(resolved_settings)
    gnaf = GnafIndex(resolved_settings.root)
    terrain = TerrainFetcher(resolved_settings, store)
    wind_multipliers = wind_multiplier_provider or GaWindMultiplierProvider(
        resolved_settings
    )
    site_boundaries = site_boundary_provider or NswPropertyBoundaryProvider(
        resolved_settings
    )
    buildings = building_provider or OpenBuildingProvider(resolved_settings)
    terrain_profiles = TerrainProfileSampler(store)
    local_wind = LocalWindAnalyzer(
        resolved_settings.root,
        terrain_profiles,
        buildings,
        wind_multipliers,
        terrain,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.initialize()
        gnaf.initialize()
        terrain.initialize()
        wind_multipliers.initialize()
        site_boundaries.initialize()
        buildings.initialize()
        local_wind.initialize()
        yield

    application = FastAPI(
        title="Tertius GIS cache",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    application.state.evidence_store = store
    application.state.gnaf_index = gnaf
    application.state.wind_multiplier_provider = wind_multipliers
    application.state.site_boundary_provider = site_boundaries
    application.state.building_provider = buildings

    @application.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @application.get("/health/ready", include_in_schema=False)
    def ready() -> dict[str, int | str]:
        try:
            return store.readiness()
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail="cache storage is unavailable"
            ) from exc

    @application.get("/v1/geocode/status")
    def geocode_status() -> dict[str, object]:
        return gnaf.status()

    @application.post("/v1/geocode/gnaf/sync", status_code=202)
    def sync_gnaf(force: bool = False) -> dict[str, object]:
        return gnaf.start_sync(resolved_settings.gnaf_states, force=force)

    @application.get("/v1/geocode", response_model=list[GeocodeCandidate])
    def geocode(
        q: Annotated[str, Query(min_length=3, max_length=300)],
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ) -> list[GeocodeCandidate]:
        return gnaf.search(q, limit)

    @application.post(
        "/v1/terrain/site", response_model=EvidenceManifest, status_code=201
    )
    async def fetch_site_terrain(request: TerrainSiteRequest) -> EvidenceManifest:
        try:
            return await run_in_threadpool(
                terrain.fetch, request.latitude, request.longitude, request.radius_m
            )
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get(
        "/v1/wind-multipliers/site",
        response_model=DirectionalWindMultiplierEvidence,
    )
    async def fetch_site_wind_multipliers(
        latitude: Annotated[float, Query(ge=-44.5, le=-9.0)],
        longitude: Annotated[float, Query(ge=112.0, le=154.0)],
    ) -> DirectionalWindMultiplierEvidence:
        try:
            return await run_in_threadpool(
                wind_multipliers.fetch, latitude, longitude
            )
        except WindMultiplierUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.get(
        "/v1/cadastre/site",
        response_model=SiteBoundaryEvidence,
    )
    async def fetch_site_boundary(
        latitude: Annotated[float, Query(ge=-37.6, le=-28.0)],
        longitude: Annotated[float, Query(ge=140.9, le=154.0)],
    ) -> SiteBoundaryEvidence:
        try:
            return await run_in_threadpool(
                site_boundaries.fetch, latitude, longitude
            )
        except SiteBoundaryUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.get(
        "/v1/buildings/site",
        response_model=BuildingEvidence,
    )
    async def fetch_site_buildings(
        latitude: Annotated[float, Query(ge=-44.5, le=-9.0)],
        longitude: Annotated[float, Query(ge=112.0, le=154.0)],
        radius_m: Annotated[float, Query(ge=50, le=5_000)] = 220.0,
    ) -> BuildingEvidence:
        try:
            return await run_in_threadpool(
                buildings.fetch, latitude, longitude, radius_m
            )
        except BuildingDataUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.get(
        "/v1/evidence/{evidence_id}/terrain-profiles/cardinal",
        response_model=CardinalTerrainProfileEvidence,
    )
    async def fetch_cardinal_terrain_profiles(
        evidence_id: str,
        latitude: Annotated[float, Query(ge=-44.5, le=-9.0)],
        longitude: Annotated[float, Query(ge=112.0, le=154.0)],
        distance_m: Annotated[float, Query(ge=100, le=5_000)] = 500.0,
        sample_interval_m: Annotated[float, Query(ge=2, le=100)] = 10.0,
    ) -> CardinalTerrainProfileEvidence:
        try:
            return await run_in_threadpool(
                terrain_profiles.sample,
                evidence_id,
                latitude,
                longitude,
                distance_m,
                sample_interval_m,
            )
        except EvidenceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc
        except (EvidenceValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get(
        "/v1/evidence/{evidence_id}/local-wind",
        response_model=LocalDirectionalWindEvidence,
    )
    async def analyze_local_wind(
        evidence_id: str,
        latitude: Annotated[float, Query(ge=-44.5, le=-9.0)],
        longitude: Annotated[float, Query(ge=112.0, le=154.0)],
        placement_latitude: Annotated[float, Query(ge=-44.5, le=-9.0)],
        placement_longitude: Annotated[float, Query(ge=112.0, le=154.0)],
        reference_height_m: Annotated[float, Query(gt=0, le=200)],
        footprint_length_m: Annotated[float, Query(gt=0, le=2_000)],
        footprint_width_m: Annotated[float, Query(gt=0, le=2_000)],
        front_bearing_degrees: Annotated[float, Query(ge=0, lt=360)],
        wind_region: Annotated[str, Query(min_length=2, max_length=3)],
    ) -> LocalDirectionalWindEvidence:
        try:
            return await run_in_threadpool(
                local_wind.analyze,
                evidence_id,
                latitude,
                longitude,
                placement_latitude,
                placement_longitude,
                reference_height_m,
                footprint_length_m,
                footprint_width_m,
                front_bearing_degrees,
                wind_region,
            )
        except EvidenceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc
        except (EvidenceValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (BuildingDataUnavailable, WindMultiplierUnavailable) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.post("/v1/evidence", response_model=EvidenceManifest, status_code=201)
    async def ingest_evidence(
        raster: Annotated[
            UploadFile, File(description="Single-band elevation GeoTIFF")
        ],
        provider: Annotated[str, Form(min_length=1, max_length=80)],
        dataset: Annotated[str, Form(min_length=1, max_length=200)],
        licence: Annotated[str, Form(min_length=1, max_length=200)],
        attribution: Annotated[str, Form(min_length=1, max_length=500)],
        dataset_version: Annotated[str, Form(min_length=1, max_length=120)] = "unknown",
        source_uri: Annotated[str | None, Form(max_length=2048)] = None,
    ) -> EvidenceManifest:
        try:
            source = SourceMetadata(
                provider=provider,
                dataset=dataset,
                dataset_version=dataset_version,
                licence=licence,
                attribution=attribution,
                source_uri=source_uri,
            )
            return await run_in_threadpool(store.ingest, raster.file, source)
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await raster.close()

    @application.get("/v1/evidence/{evidence_id}", response_model=EvidenceManifest)
    def evidence_manifest(evidence_id: str) -> EvidenceManifest:
        try:
            return store.get_manifest(evidence_id)
        except EvidenceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc

    @application.get(
        "/v1/evidence/{evidence_id}/terrain-rgb/{z}/{x}/{y}.png",
        response_class=Response,
    )
    def terrain_rgb_tile(
        evidence_id: str,
        z: int,
        x: int,
        y: int,
    ) -> Response:
        """Encode cached elevation as Terrarium RGB for WebGL terrain clients."""
        try:
            path = store.asset_path(evidence_id)
            with COGReader(str(path)) as cog:
                image = cog.tile(x, y, z, tilesize=256)
        except EvidenceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=404, detail="terrain tile is outside evidence bounds"
            ) from exc

        elevation = image.data[0].astype("float64")
        valid = (image.mask > 0) & numpy.isfinite(elevation)
        if not valid.any():
            raise HTTPException(
                status_code=404, detail="terrain tile is outside evidence bounds"
            )

        # MapLibre's raster-dem decoder reads RGB but ignores PNG alpha. Leaving
        # masked pixels as RGB zero therefore produces artificial -32768 m
        # terrain and can force the camera far away from a small site patch.
        # Extend the nearest valid surface through the masked part of the tile
        # before Terrarium encoding so the bounded DEM has a stable edge.
        elevation = fillnodata(
            elevation,
            mask=valid.astype("uint8"),
            max_search_distance=512,
        )
        fallback_elevation = float(numpy.median(image.data[0][valid]))
        elevation = numpy.where(
            numpy.isfinite(elevation), elevation, fallback_elevation
        )
        elevation += 32_768.0
        elevation = numpy.clip(elevation, 0.0, 65_535.996)
        whole = numpy.floor(elevation)
        red = numpy.floor(whole / 256.0)
        green = whole - red * 256.0
        blue = numpy.floor((elevation - whole) * 256.0)
        encoded = numpy.stack((red, green, blue)).astype("uint8")
        return Response(
            content=render(encoded, img_format="PNG"),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    def evidence_path(
        evidence_id: Annotated[
            str,
            Query(
                pattern=r"^gisv1-[0-9a-f]{32}$",
                description="Tertius GIS evidence identifier",
            ),
        ],
    ) -> str:
        try:
            return str(store.asset_path(evidence_id))
        except EvidenceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc
        except EvidenceValidationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    tiler = TilerFactory(
        path_dependency=evidence_path,
        add_viewer=False,
        add_ogc_maps=False,
        router_prefix="/v1/raster",
    )
    application.include_router(tiler.router, prefix="/v1/raster", tags=["raster"])
    add_exception_handlers(application, DEFAULT_STATUS_CODES)
    return application


app = create_app()
