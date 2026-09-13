from __future__ import annotations

import csv
from collections import Counter
from datetime import UTC, datetime
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

from .models import (
    BuildingEvidence,
    BuildingEvidenceQuality,
    BuildingFeature,
    BuildingFeatureSource,
)
from .settings import GisCacheSettings
from .building_heights import (
    BuildingHeightDataUnavailable,
    ElvisBuildingHeightProvider,
    add_source_height_intervals,
)


BUILDING_SOURCE_URI = "https://github.com/microsoft/GlobalMLBuildingFootprints"
OVERTURE_SOURCE_URI = "https://docs.overturemaps.org/guides/buildings/"
OVERTURE_ATTRIBUTION = (
    "© OpenStreetMap contributors, Microsoft, Esri Community Maps contributors, "
    "Google Open Buildings; Overture Maps Foundation"
)
INDEX_REFRESH_SECONDS = 7 * 24 * 60 * 60
MAX_INDEX_BYTES = 32 * 1024 * 1024
MAX_TILE_BYTES = 128 * 1024 * 1024
QUADKEY_ZOOM = 9


class BuildingDataUnavailable(RuntimeError):
    """Raised when the configured open building source cannot serve the site."""


def _tile_coordinates(latitude: float, longitude: float) -> tuple[int, int]:
    scale = 1 << QUADKEY_ZOOM
    x = int((longitude + 180.0) / 360.0 * scale)
    latitude = min(max(latitude, -85.05112878), 85.05112878)
    y = int(
        (
            1.0
            - math.asinh(math.tan(math.radians(latitude))) / math.pi
        )
        / 2.0
        * scale
    )
    return min(max(x, 0), scale - 1), min(max(y, 0), scale - 1)


def _quadkey(x: int, y: int) -> str:
    digits: list[str] = []
    for level in range(QUADKEY_ZOOM, 0, -1):
        mask = 1 << (level - 1)
        digit = (1 if x & mask else 0) + (2 if y & mask else 0)
        digits.append(str(digit))
    return "".join(digits)


def _query_bbox(
    latitude: float,
    longitude: float,
    radius_m: float,
) -> tuple[float, float, float, float]:
    latitude_delta = radius_m / 111_320.0
    longitude_delta = radius_m / (
        111_320.0 * max(math.cos(math.radians(latitude)), 0.2)
    )
    return (
        longitude - longitude_delta,
        latitude - latitude_delta,
        longitude + longitude_delta,
        latitude + latitude_delta,
    )


def _intersects_bbox(
    coordinates: list[list[float]],
    bbox: tuple[float, float, float, float],
) -> bool:
    longitudes = [float(point[0]) for point in coordinates if len(point) >= 2]
    latitudes = [float(point[1]) for point in coordinates if len(point) >= 2]
    if not longitudes or not latitudes:
        return False
    return not (
        max(longitudes) < bbox[0]
        or min(longitudes) > bbox[2]
        or max(latitudes) < bbox[1]
        or min(latitudes) > bbox[3]
    )


def _evidence_quality(
    features: list[BuildingFeature],
    source_counts: dict[str, int],
) -> BuildingEvidenceQuality:
    count = len(features)
    height_coverage = (
        sum(feature.height_m is not None for feature in features) / count
        if count
        else 0.0
    )
    confidence_coverage = (
        sum(feature.confidence is not None for feature in features) / count
        if count
        else 0.0
    )
    warnings: list[str] = []
    if height_coverage < 0.8:
        warnings.append(
            f"Only {height_coverage:.0%} of returned buildings have heights; "
            "targeted height enrichment and per-sector intervals decide whether local "
            "shielding can replace the GA baseline."
        )
    if confidence_coverage == 0:
        warnings.append(
            "The source supplies no footprint-confidence values for this area."
        )
    return BuildingEvidenceQuality(
        source_fusion=len(source_counts) > 1,
        height_coverage_ratio=round(height_coverage, 6),
        confidence_coverage_ratio=round(confidence_coverage, 6),
        suitable_for_local_shielding=False,
        warnings=warnings,
    )


class MicrosoftBuildingProvider:
    """Cache Microsoft Australia tiles and return site-local footprints.

    The upstream distribution is already partitioned into zoom-9 quadkeys. The
    compressed source tile is retained on the GIS volume and shared by every
    site within that tile; a site evidence response is also retained so report
    regeneration performs no network or geometry reconstruction work.
    """

    def __init__(self, settings: GisCacheSettings):
        self.settings = settings
        self.root = settings.root / "buildings" / "microsoft"
        self.tiles_dir = self.root / "tiles"
        self.evidence_dir = self.root / "evidence"
        self.index_path = self.root / "dataset-links.csv"
        self._index: dict[str, tuple[str, str]] | None = None
        self._index_lock = threading.Lock()

    def initialize(self) -> None:
        self.tiles_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def _download(self, url: str, destination: Path, max_bytes: int) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urlopen(  # noqa: S310 - operator-configured HTTPS source
                url,
                timeout=self.settings.microsoft_buildings_timeout_seconds,
            ) as response:
                stated_size = response.headers.get("Content-Length")
                if stated_size and int(stated_size) > max_bytes:
                    raise BuildingDataUnavailable(
                        f"building source exceeds {max_bytes} bytes"
                    )
                with tempfile.NamedTemporaryFile(
                    prefix="building-",
                    suffix=".tmp",
                    dir=destination.parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    size = 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise BuildingDataUnavailable(
                                f"building source exceeds {max_bytes} bytes"
                            )
                        temporary.write(chunk)
            temporary_path.replace(destination)
        except (OSError, URLError, ValueError) as exc:
            raise BuildingDataUnavailable(
                "Microsoft building data is unavailable"
            ) from exc
        finally:
            if "temporary_path" in locals():
                temporary_path.unlink(missing_ok=True)

    def _load_index(self, *, force_refresh: bool = False) -> dict[str, tuple[str, str]]:
        with self._index_lock:
            if self._index is not None and not force_refresh:
                return self._index
            expired = (
                not self.index_path.is_file()
                or time.time() - self.index_path.stat().st_mtime
                > INDEX_REFRESH_SECONDS
            )
            if force_refresh or expired:
                self._download(
                    self.settings.microsoft_buildings_index_url,
                    self.index_path,
                    MAX_INDEX_BYTES,
                )
            try:
                with self.index_path.open(encoding="utf-8-sig", newline="") as handle:
                    rows = csv.DictReader(handle)
                    index = {
                        str(row["QuadKey"]): (
                            str(row["Url"]),
                            str(row.get("UploadDate") or "unknown"),
                        )
                        for row in rows
                        if row.get("Location") == "Australia"
                        and row.get("QuadKey")
                        and row.get("Url")
                    }
            except (OSError, KeyError, csv.Error) as exc:
                raise BuildingDataUnavailable(
                    "Microsoft building index is invalid"
                ) from exc
            if not index:
                raise BuildingDataUnavailable(
                    "Microsoft building index has no Australian tiles"
                )
            self._index = index
            return index

    def _tile_path(self, quadkey: str, url: str) -> Path:
        digest = sha256(url.encode()).hexdigest()[:16]
        return self.tiles_dir / f"{quadkey}-{digest}.geojsonl.gz"

    def _required_quadkeys(
        self,
        bbox: tuple[float, float, float, float],
    ) -> list[str]:
        west_x, north_y = _tile_coordinates(bbox[3], bbox[0])
        east_x, south_y = _tile_coordinates(bbox[1], bbox[2])
        return [
            _quadkey(x, y)
            for x in range(min(west_x, east_x), max(west_x, east_x) + 1)
            for y in range(min(north_y, south_y), max(north_y, south_y) + 1)
        ]

    def _features_from_tile(
        self,
        tile_path: Path,
        bbox: tuple[float, float, float, float],
    ) -> list[BuildingFeature]:
        features: list[BuildingFeature] = []
        try:
            with gzip.open(tile_path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    raw = json.loads(line)
                    geometry = raw.get("geometry")
                    if not isinstance(geometry, dict):
                        continue
                    geometry_type = geometry.get("type")
                    raw_coordinates = geometry.get("coordinates")
                    rings: list[list[list[float]]]
                    if geometry_type == "Polygon" and isinstance(raw_coordinates, list):
                        rings = [raw_coordinates]
                    elif (
                        geometry_type == "MultiPolygon"
                        and isinstance(raw_coordinates, list)
                    ):
                        rings = raw_coordinates
                    else:
                        continue
                    properties = raw.get("properties")
                    properties = properties if isinstance(properties, dict) else {}
                    raw_height = properties.get("height")
                    height = (
                        float(raw_height)
                        if isinstance(raw_height, (int, float))
                        and 0 < float(raw_height) <= 1_000
                        else None
                    )
                    raw_confidence = properties.get("confidence")
                    confidence = (
                        float(raw_confidence)
                        if isinstance(raw_confidence, (int, float))
                        and float(raw_confidence) >= 0
                        else None
                    )
                    for polygon in rings:
                        if not isinstance(polygon, list) or not polygon:
                            continue
                        exterior = polygon[0]
                        if not isinstance(exterior, list) or len(exterior) < 4:
                            continue
                        coordinates = [
                            [float(point[0]), float(point[1])]
                            for point in exterior
                            if isinstance(point, list) and len(point) >= 2
                        ]
                        if len(coordinates) < 4 or not _intersects_bbox(
                            coordinates, bbox
                        ):
                            continue
                        identity = json.dumps(
                            coordinates,
                            separators=(",", ":"),
                        )
                        features.append(
                            BuildingFeature(
                                source_id=(
                                    "microsoft-"
                                    f"{sha256(identity.encode()).hexdigest()[:20]}"
                                ),
                                height_m=height,
                                confidence=confidence,
                                outline_source="Microsoft ML Buildings",
                                height_source=(
                                    "Microsoft ML Buildings" if height is not None else None
                                ),
                                sources=[
                                    BuildingFeatureSource(
                                        property=None,
                                        dataset="Microsoft ML Buildings",
                                        licence="CDLA Permissive 2.0",
                                        confidence=confidence,
                                    )
                                ],
                                geometry={
                                    "type": "Polygon",
                                    "coordinates": [coordinates],
                                },
                            )
                        )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise BuildingDataUnavailable(
                "cached Microsoft building tile is invalid"
            ) from exc
        return features

    def fetch(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
    ) -> BuildingEvidence:
        if not self.settings.microsoft_buildings_enabled:
            raise BuildingDataUnavailable("Microsoft building data is disabled")
        self.initialize()
        bbox = _query_bbox(latitude, longitude, radius_m)
        index = self._load_index()
        quadkeys = self._required_quadkeys(bbox)
        if any(key not in index for key in quadkeys):
            index = self._load_index(force_refresh=True)
        missing = [key for key in quadkeys if key not in index]
        if missing:
            raise BuildingDataUnavailable(
                f"Microsoft has no Australian building tile for {missing[0]}"
            )

        versions = sorted(
            {
                (
                    match.group(1)
                    if (match := re.search(r"/(\d{4}-\d{2}-\d{2})/", index[key][0]))
                    else index[key][1]
                )
                for key in quadkeys
            }
        )
        dataset_version = "+".join(versions)
        evidence_identity = json.dumps(
            {
                "dataset_version": dataset_version,
                "latitude": round(latitude, 6),
                "longitude": round(longitude, 6),
                "radius_m": round(radius_m, 1),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_id = (
            "buildingv1-"
            f"{sha256(evidence_identity.encode()).hexdigest()[:32]}"
        )
        evidence_path = self.evidence_dir / f"{evidence_id}.json"
        if evidence_path.is_file():
            try:
                return BuildingEvidence.model_validate_json(
                    evidence_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass

        features_by_id: dict[str, BuildingFeature] = {}
        for key in quadkeys:
            url, _upload_date = index[key]
            tile_path = self._tile_path(key, url)
            if not tile_path.is_file():
                self._download(url, tile_path, MAX_TILE_BYTES)
            for feature in self._features_from_tile(tile_path, bbox):
                features_by_id[feature.source_id] = feature
        features = list(features_by_id.values())
        source_counts = {"Microsoft ML Buildings": len(features)}
        evidence = BuildingEvidence(
            evidence_id=evidence_id,
            fetched_at=datetime.now(UTC),
            dataset_version=dataset_version,
            source_uri=BUILDING_SOURCE_URI,
            query_point=(longitude, latitude),
            query_radius_m=radius_m,
            features=features,
            footprint_count=len(features),
            measured_height_count=sum(
                feature.height_m is not None for feature in features
            ),
            source_counts=source_counts,
            height_source_counts={
                "Microsoft ML Buildings": sum(
                    feature.height_m is not None for feature in features
                )
            },
            quality=_evidence_quality(features, source_counts),
        )
        temporary = evidence_path.with_suffix(".tmp")
        temporary.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(evidence_path)
        return evidence


class OvertureBuildingProvider:
    """Fetch Overture's reconciled open building theme for one site window."""

    def __init__(self, settings: GisCacheSettings):
        self.settings = settings
        self.root = settings.root / "buildings" / "overture"
        self.evidence_dir = self.root / "evidence"
        self._release: str | None = None
        self._release_lock = threading.Lock()

    def initialize(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def _latest_release(self) -> str:
        with self._release_lock:
            if self._release is None:
                try:
                    from overturemaps.core import get_latest_release

                    self._release = str(get_latest_release())
                except Exception as exc:  # pragma: no cover - upstream client errors vary
                    raise BuildingDataUnavailable(
                        "Overture building release metadata is unavailable"
                    ) from exc
            return self._release

    @staticmethod
    def _source_records(raw_sources: object) -> list[BuildingFeatureSource]:
        if not isinstance(raw_sources, list):
            return []
        records: list[BuildingFeatureSource] = []
        for raw in raw_sources:
            if not isinstance(raw, dict) or not raw.get("dataset"):
                continue
            confidence = raw.get("confidence")
            records.append(
                BuildingFeatureSource(
                    property=str(raw.get("property") or "") or None,
                    dataset=str(raw["dataset"]),
                    licence=(str(raw["license"]) if raw.get("license") else None),
                    record_id=(
                        str(raw["record_id"]) if raw.get("record_id") else None
                    ),
                    update_time=(
                        str(raw["update_time"]) if raw.get("update_time") else None
                    ),
                    confidence=(
                        float(confidence)
                        if isinstance(confidence, (int, float))
                        and 0 <= float(confidence) <= 1
                        else None
                    ),
                )
            )
        return records

    def _fetch_features(
        self,
        bbox: tuple[float, float, float, float],
        release: str,
    ) -> list[BuildingFeature]:
        try:
            from overturemaps.core import record_batch_reader
            from shapely import from_wkb
            from shapely.geometry import mapping

            reader = record_batch_reader(
                "building",
                bbox=bbox,
                release=release,
                stac=True,
                connect_timeout=max(1, int(self.settings.overture_buildings_timeout_seconds)),
                request_timeout=max(1, int(self.settings.overture_buildings_timeout_seconds)),
            )
            features: list[BuildingFeature] = []
            for batch in reader:
                for raw in batch.to_pylist():
                    geometry_value = raw.get("geometry")
                    if not isinstance(geometry_value, (bytes, bytearray)):
                        continue
                    geometry = from_wkb(geometry_value)
                    if geometry.is_empty:
                        continue
                    polygons = (
                        [geometry]
                        if geometry.geom_type == "Polygon"
                        else list(geometry.geoms)
                        if geometry.geom_type == "MultiPolygon"
                        else []
                    )
                    sources = self._source_records(raw.get("sources"))
                    entity_sources = [
                        source for source in sources if source.property is None
                    ]
                    outline_source = (
                        entity_sources[0].dataset if entity_sources else "Overture Maps"
                    )
                    height = raw.get("height")
                    height_m = (
                        float(height)
                        if isinstance(height, (int, float))
                        and 0 < float(height) <= 1_000
                        else None
                    )
                    height_record = next(
                        (
                            source
                            for source in sources
                            if source.property in {
                                "/properties/height",
                                "/properties/num_floors",
                            }
                        ),
                        None,
                    )
                    height_source = (
                        height_record.dataset
                        if height_record is not None
                        else outline_source if height_m is not None else None
                    )
                    confidence = next(
                        (
                            source.confidence
                            for source in entity_sources
                            if source.confidence is not None
                        ),
                        None,
                    )
                    num_floors = raw.get("num_floors")
                    num_floors = (
                        int(num_floors)
                        if isinstance(num_floors, int) and num_floors > 0
                        else None
                    )
                    roof_height = raw.get("roof_height")
                    roof_height_m = (
                        float(roof_height)
                        if isinstance(roof_height, (int, float))
                        and float(roof_height) > 0
                        else None
                    )
                    for part_index, polygon in enumerate(polygons):
                        if polygon.is_empty:
                            continue
                        raw_id = str(raw.get("id") or sha256(geometry_value).hexdigest())
                        suffix = f"-{part_index}" if len(polygons) > 1 else ""
                        features.append(
                            BuildingFeature(
                                source_id=f"overture-{raw_id}{suffix}",
                                height_m=height_m,
                                confidence=confidence,
                                outline_source=outline_source,
                                height_source=height_source,
                                num_floors=num_floors,
                                roof_height_m=roof_height_m,
                                roof_shape=(
                                    str(raw["roof_shape"])
                                    if raw.get("roof_shape")
                                    else None
                                ),
                                sources=sources,
                                geometry=mapping(polygon),
                            )
                        )
            return features
        except BuildingDataUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - pyarrow/network errors vary
            raise BuildingDataUnavailable(
                "Overture building data is unavailable"
            ) from exc

    def fetch(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
    ) -> BuildingEvidence:
        if not self.settings.overture_buildings_enabled:
            raise BuildingDataUnavailable("Overture building data is disabled")
        self.initialize()
        release = self._latest_release()
        identity = json.dumps(
            {
                "provider": "overture",
                "release": release,
                "latitude": round(latitude, 6),
                "longitude": round(longitude, 6),
                "radius_m": round(radius_m, 1),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_id = f"buildingv1-{sha256(identity.encode()).hexdigest()[:32]}"
        evidence_path = self.evidence_dir / f"{evidence_id}.json"
        if evidence_path.is_file():
            try:
                return BuildingEvidence.model_validate_json(
                    evidence_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass

        features = self._fetch_features(
            _query_bbox(latitude, longitude, radius_m),
            release,
        )
        source_counts = dict(
            Counter(
                feature.outline_source or "unknown"
                for feature in features
            )
        )
        height_source_counts = dict(
            Counter(
                feature.height_source
                for feature in features
                if feature.height_m is not None and feature.height_source
            )
        )
        evidence = BuildingEvidence(
            evidence_id=evidence_id,
            fetched_at=datetime.now(UTC),
            provider="Overture Maps Foundation",
            dataset="Overture Buildings — reconciled open sources",
            dataset_version=release,
            licence="ODbL 1.0",
            attribution=OVERTURE_ATTRIBUTION,
            source_uri=OVERTURE_SOURCE_URI,
            query_point=(longitude, latitude),
            query_radius_m=radius_m,
            features=features,
            footprint_count=len(features),
            measured_height_count=sum(
                feature.height_m is not None for feature in features
            ),
            source_counts=source_counts,
            height_source_counts=height_source_counts,
            quality=_evidence_quality(features, source_counts),
        )
        temporary = evidence_path.with_suffix(".tmp")
        temporary.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(evidence_path)
        return evidence


class OpenBuildingProvider:
    """Fuse open footprints with public classified point-cloud heights."""

    def __init__(self, settings: GisCacheSettings):
        self.overture = OvertureBuildingProvider(settings)
        self.microsoft = MicrosoftBuildingProvider(settings)
        self.heights = ElvisBuildingHeightProvider(settings)

    def initialize(self) -> None:
        self.overture.initialize()
        self.microsoft.initialize()
        self.heights.initialize()

    def fetch(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
    ) -> BuildingEvidence:
        try:
            evidence = self.overture.fetch(latitude, longitude, radius_m)
        except BuildingDataUnavailable as overture_error:
            try:
                evidence = self.microsoft.fetch(latitude, longitude, radius_m)
            except BuildingDataUnavailable as microsoft_error:
                raise BuildingDataUnavailable(
                    "Neither Overture nor Microsoft building data is available"
                ) from ExceptionGroup(
                    "open building providers failed",
                    [overture_error, microsoft_error],
                )
        evidence = add_source_height_intervals(evidence)
        try:
            return self.heights.enrich(evidence, latitude, longitude, radius_m)
        except BuildingHeightDataUnavailable as exc:
            fallback = evidence.model_copy(deep=True)
            fallback.quality.suitable_for_local_shielding = False
            fallback.quality.warnings.append(
                f"Classified ELVIS height enrichment was unavailable: {exc}. "
                "Directional analysis retains conservative source-height intervals."
            )
            return fallback
