from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config
import laspy
import numpy as np
from pyproj import Transformer
from shapely import contains_xy
from shapely.geometry import box, shape
from shapely.ops import transform as transform_geometry

from .models import (
    BuildingEvidence,
    BuildingFeature,
    BuildingFeatureSource,
    BuildingHeightObservation,
)
from .settings import GisCacheSettings


ELVIS_SOURCE_URI = "https://elevation.fsdf.org.au/"
ELVIS_ORIGIN = "https://elevation.fsdf.org.au"
ELVIS_USER_AGENT = "Tertius GIS evidence cache/1.0 (+https://github.com/d-b-w-gain/Tertius-Web)"
MAX_CATALOGUE_BYTES = 16 * 1024 * 1024
POINT_CLOUD_SOURCE = "NSW Spatial Services classified LiDAR via ELVIS"
POINT_CLOUD_LICENCE = "Licence and attribution recorded by the ELVIS source metadata"
SOURCE_HEIGHT_ALGORITHM = "source-height-intervals-v1"
ALLOWED_POINT_CLOUD_HOSTS = {
    "nsw-elvis.s3-ap-southeast-2.amazonaws.com",
    "nsw-dpie-elvis.s3-ap-southeast-2.amazonaws.com",
    "nsw-dpie-elvis.s3.amazonaws.com",
    "s3.ap-southeast-2.amazonaws.com",
    "s3-ap-southeast-2.amazonaws.com",
}


class BuildingHeightDataUnavailable(RuntimeError):
    """Raised when ELVIS cannot provide suitable classified point-cloud evidence."""


@dataclass(frozen=True)
class PointCloudAsset:
    source: str
    index_name: str
    file_name: str
    file_url: str
    file_size: int
    file_last_modified: str
    bbox: tuple[float, float, float, float]
    metadata_url: str | None
    licence: str

    @property
    def acquisition_version(self) -> str:
        match = re.search(r"((?:19|20)\d{4})", self.file_name)
        if match:
            value = match.group(1)
            return f"{value[:4]}-{value[4:]}"
        return self.file_last_modified or "unknown"


@dataclass
class _PointSamples:
    roof: list[np.ndarray]
    ground: list[np.ndarray]
    vegetation_count: int = 0
    inside_count: int = 0
    assets: list[PointCloudAsset] | None = None


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


def _bbox_intersects(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return not (
        left[2] < right[0]
        or left[0] > right[2]
        or left[3] < right[1]
        or left[1] > right[3]
    )


def _without_query(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _source_interval(height_m: float) -> tuple[float, float]:
    """A modelled height is useful evidence, but it is not an exact measurement."""

    margin = max(1.0, height_m * 0.20)
    return max(0.1, height_m - margin), min(1_000.0, height_m + margin)


def _storey_interval(
    num_floors: int,
    roof_height_m: float | None,
    roof_shape: str | None,
) -> tuple[float, float, float]:
    """Convert declared storeys into deliberately broad total-height bounds.

    The lower bound uses 2.4 m per occupied storey and no roof credit. That is
    intentionally the least shielding the attribute can support. The upper bound
    allows 3.6 m floor-to-floor plus a roof allowance, so a one-storey house does
    not become a falsely exact 3 m obstruction.
    """

    roof_kind = (roof_shape or "").strip().lower()
    if roof_height_m is not None:
        roof_best = float(roof_height_m)
        roof_upper = float(roof_height_m) + 0.5
    elif roof_kind == "flat":
        roof_best = 0.3
        roof_upper = 0.8
    elif roof_kind in {"gabled", "hipped", "pyramidal", "gambrel", "mansard"}:
        roof_best = 1.5
        roof_upper = 3.5
    else:
        roof_best = 1.0
        roof_upper = 3.5
    lower = 2.4 * num_floors
    best = 3.0 * num_floors + roof_best
    upper = 3.6 * num_floors + roof_upper
    return lower, best, upper


def _source_record(feature: BuildingFeature, property_name: str) -> BuildingFeatureSource | None:
    return next(
        (
            source
            for source in feature.sources
            if source.property == property_name
        ),
        None,
    )


def _source_uri(evidence: BuildingEvidence, source: BuildingFeatureSource | None) -> str:
    if source and source.dataset == "OpenStreetMap" and source.record_id:
        match = re.fullmatch(r"([nwr])(\d+)(?:@\d+)?", source.record_id)
        if match:
            kind = {"n": "node", "w": "way", "r": "relation"}[match.group(1)]
            return f"https://www.openstreetmap.org/{kind}/{match.group(2)}"
    return str(evidence.source_uri)


def add_source_height_intervals(evidence: BuildingEvidence) -> BuildingEvidence:
    """Turn inherited height attributes into auditable, bounded evidence.

    Explicit/modelled heights retain a conservative error interval. Where a
    footprint has storeys but no height, a broad floor/roof envelope is used.
    This can prove a multi-storey obstruction is taller than the candidate while
    leaving ordinary one-storey houses uncertain instead of inventing a height.
    """

    enriched = evidence.model_copy(deep=True)
    already_versioned = SOURCE_HEIGHT_ALGORITHM in enriched.dataset_version
    base_evidence_id = enriched.evidence_id
    storey_count = 0
    for feature in enriched.features:
        if feature.height_m is not None:
            if feature.height_lower_m is None or feature.height_upper_m is None:
                lower, upper = _source_interval(float(feature.height_m))
                feature.height_lower_m = lower
                feature.height_upper_m = upper
            if not any(
                observation.method == "source_estimate"
                for observation in feature.height_observations
            ):
                source = _source_record(feature, "/properties/height")
                feature.height_observations.append(
                    BuildingHeightObservation(
                        method="source_estimate",
                        confidence_class="modelled",
                        provider=(source.dataset if source else feature.height_source or enriched.provider),
                        dataset=source.dataset if source else enriched.dataset,
                        dataset_version=enriched.dataset_version,
                        licence=source.licence if source and source.licence else enriched.licence,
                        source_uri=_source_uri(enriched, source),
                        acquired_at=source.update_time if source else None,
                        height_lower_m=feature.height_lower_m,
                        height_best_m=feature.height_m,
                        height_upper_m=feature.height_upper_m,
                        warnings=[
                            "Source/model height is retained as an interval, not treated as a surveyed height."
                        ],
                    )
                )
            continue
        if feature.num_floors is None:
            continue
        lower, best, upper = _storey_interval(
            feature.num_floors,
            feature.roof_height_m,
            feature.roof_shape,
        )
        feature.height_m = round(best, 3)
        feature.height_lower_m = round(lower, 3)
        feature.height_upper_m = round(upper, 3)
        source = _source_record(feature, "/properties/num_floors") or next(
            (value for value in feature.sources if value.dataset == "OpenStreetMap"),
            None,
        )
        feature.height_source = "OpenStreetMap storeys via Overture"
        if not any(
            observation.method == "source_storeys"
            for observation in feature.height_observations
        ):
            feature.height_observations.append(
                BuildingHeightObservation(
                    method="source_storeys",
                    confidence_class="modelled",
                    provider="OpenStreetMap via Overture",
                    dataset="building:levels and roof attributes",
                    dataset_version=enriched.dataset_version,
                    licence=source.licence if source and source.licence else "ODbL-1.0",
                    source_uri=_source_uri(enriched, source),
                    acquired_at=source.update_time if source else None,
                    eave_height_m=round(3.0 * feature.num_floors, 3),
                    roof_median_height_m=round(best, 3),
                    ridge_height_m=round(upper, 3),
                    height_lower_m=round(lower, 3),
                    height_best_m=round(best, 3),
                    height_upper_m=round(upper, 3),
                    warnings=[
                        "Height is a conservative envelope derived from declared storeys; it is not a LiDAR or survey measurement."
                    ],
                )
            )
        storey_count += 1
    enriched.measured_height_count = sum(
        feature.height_m is not None for feature in enriched.features
    )
    enriched.height_observation_count = sum(
        len(feature.height_observations) for feature in enriched.features
    )
    enriched.height_source_counts = dict(
        Counter(
            feature.height_source
            for feature in enriched.features
            if feature.height_m is not None and feature.height_source
        )
    )
    enriched.height_method_counts = dict(
        Counter(
            observation.method
            for feature in enriched.features
            for observation in feature.height_observations
        )
    )
    storey_count = enriched.height_method_counts.get("source_storeys", 0)
    enriched.quality.height_coverage_ratio = round(
        enriched.measured_height_count / enriched.footprint_count
        if enriched.footprint_count
        else 0.0,
        6,
    )
    enriched.quality.warnings = [
        warning
        for warning in enriched.quality.warnings
        if not warning.startswith("Storey attributes supplied")
        and not warning.startswith("Bounded height evidence covers")
        and not warning.startswith("Only ")
    ]
    if storey_count:
        enriched.quality.warnings.append(
            f"Storey attributes supplied conservative height bounds for {storey_count} additional buildings; one-storey bounds remain deliberately inconclusive near the candidate height."
        )
    enriched.quality.warnings.append(
        f"Bounded height evidence covers {enriched.quality.height_coverage_ratio:.0%} of footprints overall; directional Ms is decided per sector from each candidate's lower and upper bounds."
    )
    if not already_versioned:
        enriched.evidence_id = "buildingv1-" + sha256(
            json.dumps(
                {
                    "base_evidence_id": base_evidence_id,
                    "algorithm": SOURCE_HEIGHT_ALGORITHM,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:32]
        enriched.dataset_version = (
            f"{enriched.dataset_version}; {SOURCE_HEIGHT_ALGORITHM}"
        )
    return enriched


class ElvisBuildingHeightProvider:
    """Measure roof heights from classified public NSW point clouds.

    ELVIS is used only as a catalogue. Immutable LAZ assets are cached on the GIS
    volume, and every derived value retains its exact source file and metadata URL.
    Roof samples must be class 6 (building); nearby class 2 samples establish the
    local AHD ground plane. Vegetation is reported but never treated as a roof.
    """

    def __init__(self, settings: GisCacheSettings):
        self.settings = settings
        self.root = settings.root / "buildings" / "elvis"
        self.assets_dir = self.root / "assets"
        self.evidence_dir = self.root / "evidence"
        self._aws_lock = threading.Lock()
        self._aws_s3: Any | None = None
        self._aws_expiry: datetime | None = None

    def initialize(self) -> None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _request(url: str) -> Request:
        return Request(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": ELVIS_ORIGIN,
                "Referer": f"{ELVIS_ORIGIN}/",
                "User-Agent": ELVIS_USER_AGENT,
            },
        )

    def _catalogue(self, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
        polygon = (
            f"POLYGON(({bbox[0]} {bbox[1]},{bbox[2]} {bbox[1]},"
            f"{bbox[2]} {bbox[3]},{bbox[0]} {bbox[3]},"
            f"{bbox[0]} {bbox[1]}))"
        )
        # ELVIS currently returns HTTP 500 when WKT spaces arrive as '+'. Its
        # browser client sends percent-encoded spaces, so preserve that contract.
        query = urlencode({"polygon": polygon}, quote_via=quote)
        url = f"{self.settings.elvis_downloadables_url}?{query}"
        try:
            with urlopen(
                self._request(url),
                timeout=self.settings.elvis_point_cloud_timeout_seconds,
            ) as response:
                declared = int(response.headers.get("Content-Length", "0") or 0)
                if declared > MAX_CATALOGUE_BYTES:
                    raise BuildingHeightDataUnavailable("ELVIS catalogue response is too large")
                payload = response.read(MAX_CATALOGUE_BYTES + 1)
            if len(payload) > MAX_CATALOGUE_BYTES:
                raise BuildingHeightDataUnavailable("ELVIS catalogue response is too large")
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("catalogue root is not an object")
            return value
        except BuildingHeightDataUnavailable:
            raise
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise BuildingHeightDataUnavailable("ELVIS catalogue is unavailable") from exc

    @staticmethod
    def _assets(payload: dict[str, Any]) -> list[PointCloudAsset]:
        assets: list[PointCloudAsset] = []
        for source_entry in payload.get("available_data", []):
            if not isinstance(source_entry, dict):
                continue
            source = str(source_entry.get("source") or "ELVIS")
            point_clouds = (
                (source_entry.get("downloadables") or {}).get("Point Clouds") or {}
            )
            if not isinstance(point_clouds, dict):
                continue
            for vertical_datum, records in point_clouds.items():
                if str(vertical_datum).upper() != "AHD" or not isinstance(records, list):
                    continue
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    file_url = str(record.get("file_url") or "")
                    parsed = urlsplit(file_url)
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname not in ALLOWED_POINT_CLOUD_HOSTS
                        or not parsed.path.lower().endswith((".laz", ".las"))
                    ):
                        continue
                    try:
                        values = tuple(float(v) for v in str(record["bbox"]).split(","))
                        file_size = int(record.get("file_size") or 0)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if len(values) != 4 or file_size <= 0:
                        continue
                    assets.append(
                        PointCloudAsset(
                            source=source,
                            index_name=str(record.get("index_poly_name") or record.get("file_name")),
                            file_name=str(record.get("file_name") or Path(parsed.path).name),
                            file_url=file_url,
                            file_size=file_size,
                            file_last_modified=str(record.get("file_last_modified") or ""),
                            bbox=(values[0], values[1], values[2], values[3]),
                            metadata_url=_without_query(str(record.get("metadata_url") or "")),
                            licence=str(record.get("license") or POINT_CLOUD_LICENCE),
                        )
                    )
        return assets

    @staticmethod
    def _select_assets(
        assets: list[PointCloudAsset],
        query_bbox: tuple[float, float, float, float],
    ) -> list[PointCloudAsset]:
        grouped: dict[str, list[PointCloudAsset]] = {}
        for asset in assets:
            if _bbox_intersects(asset.bbox, query_bbox):
                grouped.setdefault(asset.index_name, []).append(asset)
        selected: list[PointCloudAsset] = []
        for values in grouped.values():
            selected.append(
                max(
                    values,
                    key=lambda value: (
                        "Spatial Services" in value.source,
                        value.acquisition_version,
                        value.file_last_modified,
                    ),
                )
            )
        return sorted(selected, key=lambda value: (value.index_name, value.file_name))

    def _asset_path(self, asset: PointCloudAsset) -> Path:
        digest = sha256(asset.file_url.encode()).hexdigest()[:20]
        suffix = ".laz" if asset.file_url.lower().endswith(".laz") else ".las"
        return self.assets_dir / f"{digest}-{Path(asset.file_name).stem}{suffix}"

    def _requester_pays_client(self) -> Any:
        now = datetime.now(UTC)
        with self._aws_lock:
            if (
                self._aws_s3 is not None
                and self._aws_expiry is not None
                and self._aws_expiry.timestamp() - now.timestamp() > 300
            ):
                return self._aws_s3
            timeout = self.settings.elvis_point_cloud_timeout_seconds
            config = Config(
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"max_attempts": 3, "mode": "standard"},
                signature_version="s3v4",
            )
            identity = boto3.client(
                "cognito-identity",
                region_name=self.settings.elvis_aws_region,
                config=config,
            )
            identity_id = identity.get_id(
                IdentityPoolId=self.settings.elvis_identity_pool_id
            )["IdentityId"]
            response = identity.get_credentials_for_identity(IdentityId=identity_id)
            credentials = response["Credentials"]
            self._aws_s3 = boto3.client(
                "s3",
                region_name=self.settings.elvis_aws_region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretKey"],
                aws_session_token=credentials["SessionToken"],
                config=config,
            )
            expiry = credentials["Expiration"]
            self._aws_expiry = (
                expiry.astimezone(UTC) if isinstance(expiry, datetime) else now
            )
            return self._aws_s3

    @staticmethod
    def _s3_location(url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        path = parsed.path.lstrip("/")
        if hostname.startswith("s3.") or hostname.startswith("s3-"):
            bucket, separator, key = path.partition("/")
            if not separator:
                raise BuildingHeightDataUnavailable("ELVIS S3 URL has no object key")
            return bucket, key
        marker = hostname.find(".s3")
        if marker <= 0 or not path:
            raise BuildingHeightDataUnavailable("ELVIS S3 URL is not recognised")
        return hostname[:marker], path

    def _download(self, asset: PointCloudAsset) -> Path:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        if asset.file_size > self.settings.elvis_point_cloud_max_bytes:
            raise BuildingHeightDataUnavailable(
                f"ELVIS point-cloud tile exceeds the {self.settings.elvis_point_cloud_max_bytes} byte limit"
            )
        target = self._asset_path(asset)
        if target.is_file() and target.stat().st_size == asset.file_size:
            return target
        temporary = target.with_suffix(target.suffix + ".part")
        body = None
        try:
            bucket, key = self._s3_location(asset.file_url)
            response = self._requester_pays_client().get_object(
                Bucket=bucket,
                Key=key,
                RequestPayer="requester",
            )
            declared = int(response.get("ContentLength") or 0)
            if declared and declared != asset.file_size:
                raise BuildingHeightDataUnavailable(
                    f"ELVIS S3 size mismatch: catalogue {asset.file_size}, object {declared}"
                )
            body = response["Body"]
            with temporary.open("wb") as output:
                total = 0
                while chunk := body.read(1024 * 1024):
                    total += len(chunk)
                    if total > self.settings.elvis_point_cloud_max_bytes:
                        raise BuildingHeightDataUnavailable("ELVIS point-cloud download exceeded its limit")
                    output.write(chunk)
            if total != asset.file_size:
                raise BuildingHeightDataUnavailable(
                    f"ELVIS point-cloud size mismatch: expected {asset.file_size}, received {total}"
                )
            temporary.replace(target)
            return target
        except BuildingHeightDataUnavailable:
            temporary.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise BuildingHeightDataUnavailable(
                "ELVIS requester-pays point-cloud download failed"
            ) from exc
        finally:
            if body is not None:
                body.close()

    @staticmethod
    def _measure(
        features: list[BuildingFeature],
        asset_paths: list[tuple[PointCloudAsset, Path]],
    ) -> dict[str, BuildingHeightObservation]:
        samples = {
            feature.source_id: _PointSamples(roof=[], ground=[], assets=[])
            for feature in features
        }
        feature_shapes = {
            feature.source_id: shape(feature.geometry)
            for feature in features
        }
        for asset, path in asset_paths:
            with laspy.open(path) as reader:
                crs = reader.header.parse_crs()
                if crs is None:
                    raise BuildingHeightDataUnavailable(
                        f"Point cloud {asset.file_name} has no readable CRS"
                    )
                transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
                projected: dict[str, tuple[Any, Any, float]] = {}
                tile_polygon = box(*asset.bbox)
                for feature in features:
                    source_geometry = feature_shapes[feature.source_id]
                    if not source_geometry.intersects(tile_polygon):
                        continue
                    geometry = transform_geometry(transformer.transform, source_geometry)
                    if geometry.is_empty or geometry.area <= 0:
                        continue
                    inner = geometry.buffer(-0.25)
                    if inner.is_empty:
                        inner = geometry
                    ground_ring = geometry.buffer(4.0).difference(geometry.buffer(0.5))
                    projected[feature.source_id] = (inner, ground_ring, float(geometry.area))
                if not projected:
                    continue
                for points in reader.chunk_iterator(1_000_000):
                    x = np.asarray(points.x)
                    y = np.asarray(points.y)
                    z = np.asarray(points.z)
                    classification = np.asarray(points.classification)
                    for source_id, (inner, ground_ring, _area) in projected.items():
                        inside = contains_xy(inner, x, y)
                        if np.any(inside):
                            target = samples[source_id]
                            target.inside_count += int(np.count_nonzero(inside))
                            roof = inside & (classification == 6)
                            if np.any(roof):
                                target.roof.append(z[roof].copy())
                            target.vegetation_count += int(
                                np.count_nonzero(inside & np.isin(classification, (3, 4, 5)))
                            )
                            if target.assets is not None and asset not in target.assets:
                                target.assets.append(asset)
                        ground = contains_xy(ground_ring, x, y) & (classification == 2)
                        if np.any(ground):
                            samples[source_id].ground.append(z[ground].copy())

        observations: dict[str, BuildingHeightObservation] = {}
        for feature in features:
            sample = samples[feature.source_id]
            if not sample.roof or not sample.ground:
                continue
            roof = np.concatenate(sample.roof)
            ground = np.concatenate(sample.ground)
            if roof.size < 8 or ground.size < 8:
                continue
            ground_low, ground_mid, ground_high = np.percentile(ground, (25, 50, 75))
            roof_low, roof_mid, roof_high = np.percentile(roof, (10, 50, 95))
            lower = float(roof_low - ground_high)
            best = float(roof_mid - ground_mid)
            upper = float(roof_high - ground_low)
            if lower <= 0.5 or best <= 0.5 or upper <= 0.5:
                continue
            source_assets = sample.assets or []
            primary = source_assets[0] if source_assets else asset_paths[0][0]
            geometry_area = max(float(feature_shapes[feature.source_id].area), 1e-12)
            latitude = feature_shapes[feature.source_id].centroid.y
            area_m2 = geometry_area * 111_320.0**2 * max(
                math.cos(math.radians(latitude)), 0.2
            )
            vegetation_fraction = (
                sample.vegetation_count / sample.inside_count
                if sample.inside_count
                else 0.0
            )
            warnings: list[str] = []
            if vegetation_fraction > 0.25:
                warnings.append(
                    "Vegetation overlaps the footprint; only class-6 building returns were used."
                )
            observations[feature.source_id] = BuildingHeightObservation(
                method="classified_lidar",
                confidence_class="measured",
                provider=primary.source,
                dataset="ELVIS classified AHD point cloud",
                dataset_version="+".join(
                    sorted({value.acquisition_version for value in source_assets})
                ) or primary.acquisition_version,
                licence=primary.licence,
                source_uri=primary.file_url,
                metadata_uri=primary.metadata_url,
                acquired_at=primary.acquisition_version,
                ground_elevation_m=round(float(ground_mid), 3),
                eave_height_m=round(lower, 3),
                roof_median_height_m=round(best, 3),
                ridge_height_m=round(upper, 3),
                height_lower_m=round(lower, 3),
                height_best_m=round(best, 3),
                height_upper_m=round(upper, 3),
                roof_point_count=int(roof.size),
                ground_point_count=int(ground.size),
                point_density_per_m2=round(float(roof.size / max(area_m2, 0.1)), 3),
                vegetation_fraction=round(float(vegetation_fraction), 4),
                warnings=warnings,
            )
        return observations

    def enrich(
        self,
        evidence: BuildingEvidence,
        latitude: float,
        longitude: float,
        radius_m: float,
    ) -> BuildingEvidence:
        evidence = add_source_height_intervals(evidence)
        if not self.settings.elvis_building_heights_enabled:
            return evidence
        self.initialize()
        height_radius = min(radius_m, float(self.settings.elvis_building_height_radius_m))
        query_bbox = _query_bbox(latitude, longitude, height_radius)
        target_box = box(*query_bbox)
        target_features = [
            feature
            for feature in evidence.features
            if shape(feature.geometry).intersects(target_box)
        ]
        if not target_features:
            return evidence
        assets = self._select_assets(self._assets(self._catalogue(query_bbox)), query_bbox)
        if not assets:
            raise BuildingHeightDataUnavailable(
                "ELVIS has no classified AHD point cloud for this site"
            )
        total_bytes = sum(asset.file_size for asset in assets)
        if total_bytes > self.settings.elvis_point_cloud_total_max_bytes:
            raise BuildingHeightDataUnavailable(
                f"Selected ELVIS tiles exceed the {self.settings.elvis_point_cloud_total_max_bytes} byte site limit"
            )
        identity = json.dumps(
            {
                "base_evidence_id": evidence.evidence_id,
                "height_radius_m": round(height_radius, 1),
                "assets": [
                    [asset.file_name, asset.file_size, asset.file_last_modified]
                    for asset in assets
                ],
                "algorithm": "classified-building-p10-p50-p95-ground-ring-v1",
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
                evidence_path.unlink(missing_ok=True)

        asset_paths = [(asset, self._download(asset)) for asset in assets]
        observations = self._measure(target_features, asset_paths)
        enriched = evidence.model_copy(deep=True)
        enriched.evidence_id = evidence_id
        enriched.fetched_at = datetime.now(UTC)
        enriched.provider = f"{evidence.provider} + Geoscience Australia ELVIS"
        enriched.dataset = f"{evidence.dataset}; classified NSW point-cloud heights"
        enriched.dataset_version = (
            f"{evidence.dataset_version}; ELVIS "
            + "+".join(sorted({asset.acquisition_version for asset in assets}))
        )
        enriched.attribution = f"{evidence.attribution}; NSW Spatial Services via ELVIS"
        enriched.source_uri = ELVIS_SOURCE_URI
        for feature in enriched.features:
            observation = observations.get(feature.source_id)
            if observation is None:
                continue
            feature.height_m = observation.height_best_m
            feature.height_lower_m = observation.height_lower_m
            feature.height_upper_m = observation.height_upper_m
            feature.height_source = POINT_CLOUD_SOURCE
            feature.height_observations.append(observation)
            feature.sources.append(
                BuildingFeatureSource(
                    property="/properties/height",
                    dataset=POINT_CLOUD_SOURCE,
                    licence=observation.licence,
                    record_id=Path(str(observation.source_uri)).name,
                    update_time=observation.acquired_at,
                )
            )
        enriched.measured_height_count = sum(
            feature.height_m is not None for feature in enriched.features
        )
        enriched.height_observation_count = sum(
            len(feature.height_observations) for feature in enriched.features
        )
        enriched.height_source_counts = dict(
            Counter(
                feature.height_source
                for feature in enriched.features
                if feature.height_m is not None and feature.height_source
            )
        )
        enriched.height_method_counts = dict(
            Counter(
                observation.method
                for feature in enriched.features
                for observation in feature.height_observations
            )
        )
        height_coverage = (
            enriched.measured_height_count / enriched.footprint_count
            if enriched.footprint_count
            else 0.0
        )
        enriched.quality.height_coverage_ratio = round(height_coverage, 6)
        enriched.quality.suitable_for_local_shielding = False
        enriched.quality.warnings = [
            warning
            for warning in enriched.quality.warnings
            if not warning.startswith("Only ")
        ]
        enriched.quality.warnings.append(
            f"ELVIS measured {len(observations)} buildings inside the {height_radius:.0f} m "
            "height-analysis window. Directional shielding uses per-building lower/upper "
            "bounds, not this whole-query percentage."
        )
        temporary = evidence_path.with_suffix(".tmp")
        temporary.write_text(enriched.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(evidence_path)
        return enriched
