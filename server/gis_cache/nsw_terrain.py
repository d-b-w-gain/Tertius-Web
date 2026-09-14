from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import rasterio
from rasterio.shutil import copy as raster_copy
from rasterio.warp import transform
from rasterio.windows import from_bounds

from .models import EvidenceManifest, SourceMetadata
from .settings import GisCacheSettings
from .store import EvidenceStore, EvidenceValidationError

NSW_ATTRIBUTION = "© State of New South Wales (Spatial Services)"
_SAFE_DEM_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_UNCOMPRESSED_SHEET_BYTES = 2_000_000_000


class NswTerrainUnavailable(RuntimeError):
    """The optional NSW source could not supply this site right now."""


@dataclass(frozen=True)
class NswDemSheet:
    dem_id: str
    map_number: str
    map_title: str
    zone: int
    index_updated_ms: int | None

    @property
    def dataset_version(self) -> str:
        if self.index_updated_ms is None:
            return self.dem_id
        updated = (
            datetime.fromtimestamp(self.index_updated_ms / 1000, tz=UTC)
            .date()
            .isoformat()
        )
        return f"{self.dem_id}; index-updated-{updated}"


class NswTerrainProvider:
    def __init__(self, settings: GisCacheSettings, store: EvidenceStore):
        self.settings = settings
        self.store = store
        self.sheets_dir = settings.root / "providers" / "nsw-elevation" / "sheets"
        self._sheet_lock = threading.Lock()

    def initialize(self) -> None:
        self.sheets_dir.mkdir(parents=True, exist_ok=True)

    def find_sheet(self, latitude: float, longitude: float) -> NswDemSheet | None:
        params = urllib.parse.urlencode(
            {
                "f": "json",
                "geometry": f"{longitude:.8f},{latitude:.8f}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": ("mapnumber,maptitle,zone,dems5mid,last_edited_date"),
                "returnGeometry": "false",
            }
        )
        request = urllib.request.Request(
            f"{self.settings.nsw_elevation_index_url}?{params}",
            headers={"User-Agent": "Tertius-GIS-cache/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except (OSError, ValueError) as exc:
            raise NswTerrainUnavailable("NSW elevation index is unavailable") from exc

        if payload.get("error"):
            raise NswTerrainUnavailable("NSW elevation index rejected the query")
        features = payload.get("features")
        if not isinstance(features, list) or not features:
            return None
        attributes = features[0].get("attributes", {})
        dem_id = str(attributes.get("dems5mid") or "")
        zone_text = str(attributes.get("zone") or "")
        if not _SAFE_DEM_ID.fullmatch(dem_id) or not zone_text.isdigit():
            raise EvidenceValidationError(
                "NSW elevation index returned an invalid DEM identifier"
            )
        zone = int(zone_text)
        if zone < 49 or zone > 56:
            raise EvidenceValidationError(
                "NSW elevation index returned an invalid MGA zone"
            )
        edited = attributes.get("last_edited_date")
        return NswDemSheet(
            dem_id=dem_id,
            map_number=str(attributes.get("mapnumber") or "unknown"),
            map_title=str(attributes.get("maptitle") or "unknown"),
            zone=zone,
            index_updated_ms=int(edited) if isinstance(edited, (int, float)) else None,
        )

    def fetch(
        self,
        sheet: NswDemSheet,
        latitude: float,
        longitude: float,
        radius_m: int,
    ) -> EvidenceManifest:
        self.initialize()
        sheet_path = self._ensure_sheet_raster(sheet)
        source_url = self._download_url(sheet)
        fd, name = tempfile.mkstemp(
            prefix="nsw-terrain-", suffix=".tif", dir=self.store.staging_dir
        )
        os.close(fd)
        path = Path(name)
        try:
            with rasterio.open(sheet_path) as source:
                if source.crs is None:
                    raise EvidenceValidationError(
                        "NSW DEM sheet does not declare a CRS"
                    )
                centre_x_values, centre_y_values = transform(
                    "EPSG:4326", source.crs, [longitude], [latitude]
                )
                centre_x = centre_x_values[0]
                centre_y = centre_y_values[0]
                requested = (
                    centre_x - radius_m,
                    centre_y - radius_m,
                    centre_x + radius_m,
                    centre_y + radius_m,
                )
                left = max(requested[0], source.bounds.left)
                bottom = max(requested[1], source.bounds.bottom)
                right = min(requested[2], source.bounds.right)
                top = min(requested[3], source.bounds.top)
                if left >= right or bottom >= top:
                    raise EvidenceValidationError("site is outside the NSW DEM sheet")
                window = (
                    from_bounds(left, bottom, right, top, source.transform)
                    .round_offsets()
                    .round_lengths()
                )
                data = source.read(1, window=window)
                if data.size > self.settings.max_pixels:
                    raise EvidenceValidationError(
                        "requested NSW terrain window is too large"
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

            metadata = SourceMetadata(
                provider="NSW Spatial Services",
                dataset="NSW 5 metre Digital Elevation Model",
                dataset_version=sheet.dataset_version,
                licence="NSW Spatial Services open access; verify redistribution terms",
                attribution=NSW_ATTRIBUTION,
                source_uri=source_url,
            )
            with path.open("rb") as handle:
                return self.store.ingest(handle, metadata)
        except rasterio.errors.RasterioError as exc:
            raise EvidenceValidationError(f"NSW terrain fetch failed: {exc}") from exc
        finally:
            path.unlink(missing_ok=True)

    def _download_url(self, sheet: NswDemSheet) -> str:
        dem_id = urllib.parse.quote(sheet.dem_id, safe="")
        return f"{self.settings.nsw_dem_download_base_url.rstrip('/')}/{sheet.zone}/{dem_id}.zip"

    def _ensure_sheet_raster(self, sheet: NswDemSheet) -> Path:
        destination = self.sheets_dir / f"{sheet.dem_id}.tif"
        with self._sheet_lock:
            if self._valid_cached_sheet(destination):
                return destination
            destination.unlink(missing_ok=True)
            archive = self.sheets_dir / f"{sheet.dem_id}.zip.partial"
            try:
                self._download_archive(self._download_url(sheet), archive)
                self._convert_archive(archive, sheet, destination)
            finally:
                archive.unlink(missing_ok=True)
            if not self._valid_cached_sheet(destination):
                destination.unlink(missing_ok=True)
                raise EvidenceValidationError(
                    "NSW DEM sheet conversion failed validation"
                )
        return destination

    def _valid_cached_sheet(self, path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            with rasterio.open(path) as source:
                resolution = tuple(abs(float(value)) for value in source.res)
                return (
                    source.count == 1
                    and source.crs is not None
                    and 4.0 <= resolution[0] <= 6.0
                    and 4.0 <= resolution[1] <= 6.0
                )
        except rasterio.errors.RasterioError:
            return False

    def _download_archive(self, url: str, destination: Path) -> None:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Tertius-GIS-cache/0.1"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > self.settings.nsw_max_archive_bytes:
                    raise EvidenceValidationError("NSW DEM archive exceeds size limit")
                total = 0
                with destination.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > self.settings.nsw_max_archive_bytes:
                            raise EvidenceValidationError(
                                "NSW DEM archive exceeds size limit"
                            )
                        output.write(chunk)
        except EvidenceValidationError:
            raise
        except (OSError, ValueError) as exc:
            raise NswTerrainUnavailable("NSW DEM download is unavailable") from exc

    def _convert_archive(
        self, archive: Path, sheet: NswDemSheet, destination: Path
    ) -> None:
        work_dir = Path(
            tempfile.mkdtemp(prefix="nsw-sheet-", dir=self.store.staging_dir)
        )
        converted = destination.with_suffix(".tif.partial")
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = {
                    Path(item.filename).name.lower(): item for item in bundle.infolist()
                }
                asc = members.get(f"{sheet.dem_id}.asc".lower())
                prj = members.get(f"{sheet.dem_id}.prj".lower())
                if asc is None or prj is None:
                    raise EvidenceValidationError(
                        "NSW DEM archive is missing its expected ASC or PRJ"
                    )
                if (
                    asc.file_size <= 0
                    or asc.file_size > _MAX_UNCOMPRESSED_SHEET_BYTES
                    or prj.file_size > 64_000
                ):
                    raise EvidenceValidationError("NSW DEM archive has unsafe members")
                asc_path = work_dir / f"{sheet.dem_id}.asc"
                prj_path = work_dir / f"{sheet.dem_id}.prj"
                for member, target in ((asc, asc_path), (prj, prj_path)):
                    with bundle.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)

            raster_copy(
                asc_path,
                converted,
                driver="GTiff",
                tiled=True,
                compress="deflate",
                predictor=3,
                blockxsize=512,
                blockysize=512,
                BIGTIFF="IF_SAFER",
            )
            os.replace(converted, destination)
        except zipfile.BadZipFile as exc:
            raise EvidenceValidationError(
                "NSW DEM download is not a valid ZIP"
            ) from exc
        finally:
            converted.unlink(missing_ok=True)
            shutil.rmtree(work_dir, ignore_errors=True)
