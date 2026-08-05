from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(
        min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$"
    )
    dataset: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(default="unknown", min_length=1, max_length=120)
    licence: str = Field(min_length=1, max_length=200)
    attribution: str = Field(min_length=1, max_length=500)
    source_uri: HttpUrl | None = None


class RasterAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: str
    media_type: Literal["image/tiff; application=geotiff; profile=cloud-optimized"]
    size_bytes: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    band_count: int = Field(ge=1)
    dtype: str
    crs: str
    bounds: tuple[float, float, float, float]
    resolution: tuple[float, float]
    nodata: float | int | None


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tertius.gis.evidence.v1"] = "tertius.gis.evidence.v1"
    evidence_id: str = Field(pattern=r"^gisv1-[0-9a-f]{32}$")
    created_at: datetime
    source: SourceMetadata
    asset: RasterAsset
