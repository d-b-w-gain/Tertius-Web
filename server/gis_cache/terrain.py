from __future__ import annotations

import json
import math
import os
import tempfile
from hashlib import sha256
from pathlib import Path

import rasterio
from rasterio.windows import from_bounds

from .models import EvidenceManifest, SourceMetadata
from .settings import GisCacheSettings
from .store import EvidenceStore, EvidenceValidationError

GA_ATTRIBUTION = (
    "Geoscience Australia, 1 second SRTM Digital Elevation Model (DEM), CC BY 4.0"
)


class TerrainFetcher:
    def __init__(self, settings: GisCacheSettings, store: EvidenceStore):
        self.settings = settings
        self.store = store
        self.requests_dir = settings.root / "requests"

    def initialize(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)

    def fetch(
        self, latitude: float, longitude: float, radius_m: int | None = None
    ) -> EvidenceManifest:
        radius = radius_m or self.settings.terrain_default_radius_m
        if radius > self.settings.terrain_max_radius_m:
            raise EvidenceValidationError(
                f"terrain radius exceeds {self.settings.terrain_max_radius_m} metres"
            )
        self.initialize()
        request_key = sha256(
            json.dumps(
                {
                    "provider": "ga-srtm-1sec-v1.0",
                    "latitude": round(latitude, 6),
                    "longitude": round(longitude, 6),
                    "radius_m": radius,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        request_path = self.requests_dir / f"{request_key}.json"
        if request_path.exists():
            try:
                return self.store.get_manifest(
                    json.loads(request_path.read_text())["evidence_id"]
                )
            except KeyError, ValueError, OSError:
                request_path.unlink(missing_ok=True)

        lat_delta = radius / 111_320.0
        lon_delta = radius / (111_320.0 * max(math.cos(math.radians(latitude)), 0.2))
        bounds = (
            longitude - lon_delta,
            latitude - lat_delta,
            longitude + lon_delta,
            latitude + lat_delta,
        )
        source_url = self.settings.terrain_source_url
        remote_path = (
            source_url
            if source_url.startswith("/vsicurl/") or not source_url.startswith("http")
            else f"/vsicurl/{source_url}"
        )
        fd, name = tempfile.mkstemp(
            prefix="ga-terrain-", suffix=".tif", dir=self.store.staging_dir
        )
        os.close(fd)
        path = Path(name)
        try:
            with rasterio.Env(
                GDAL_HTTP_MULTIRANGE="YES",
                GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
            ):
                with rasterio.open(remote_path) as source:
                    if source.crs is None or source.crs.to_epsg() != 4326:
                        raise EvidenceValidationError(
                            "GA terrain source CRS is not EPSG:4326"
                        )
                    left = max(bounds[0], source.bounds.left)
                    bottom = max(bounds[1], source.bounds.bottom)
                    right = min(bounds[2], source.bounds.right)
                    top = min(bounds[3], source.bounds.top)
                    if left >= right or bottom >= top:
                        raise EvidenceValidationError(
                            "site is outside the GA terrain source"
                        )
                    window = (
                        from_bounds(left, bottom, right, top, source.transform)
                        .round_offsets()
                        .round_lengths()
                    )
                    data = source.read(1, window=window)
                    if data.size > self.settings.max_pixels:
                        raise EvidenceValidationError(
                            "requested terrain window is too large"
                        )
                    profile = source.profile.copy()
                    profile.update(
                        driver="GTiff",
                        width=data.shape[1],
                        height=data.shape[0],
                        transform=source.window_transform(window),
                        count=1,
                        tiled=True,
                        compress="deflate",
                    )
                    with rasterio.open(path, "w", **profile) as target:
                        target.write(data, 1)
            source = SourceMetadata(
                provider="Geoscience Australia - Digital Earth Australia",
                dataset="1 second SRTM Digital Elevation Model (DEM)",
                dataset_version="ga_srtm_dem1sv1_0",
                licence="Creative Commons Attribution 4.0 International",
                attribution=GA_ATTRIBUTION,
                source_uri=self.settings.terrain_source_url,
            )
            with path.open("rb") as handle:
                manifest = self.store.ingest(handle, source)
            temp = request_path.with_suffix(".tmp")
            temp.write_text(
                json.dumps({"evidence_id": manifest.evidence_id}), encoding="utf-8"
            )
            temp.replace(request_path)
            return manifest
        except rasterio.errors.RasterioError as exc:
            raise EvidenceValidationError(f"GA terrain fetch failed: {exc}") from exc
        finally:
            path.unlink(missing_ok=True)
