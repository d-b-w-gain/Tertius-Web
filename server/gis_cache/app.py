from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers
from titiler.core.factory import TilerFactory

from .models import EvidenceManifest, SourceMetadata
from .settings import GisCacheSettings
from .store import (
    EvidenceNotFoundError,
    EvidenceStore,
    EvidenceValidationError,
    UploadTooLargeError,
)


def create_app(settings: GisCacheSettings | None = None) -> FastAPI:
    resolved_settings = settings or GisCacheSettings.from_env()
    store = EvidenceStore(resolved_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.initialize()
        yield

    application = FastAPI(
        title="Tertius GIS cache",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    application.state.evidence_store = store

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
