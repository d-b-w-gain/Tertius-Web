from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _states(name: str, default: str) -> tuple[str, ...]:
    allowed = {"ACT", "NSW", "NT", "OT", "QLD", "SA", "TAS", "VIC", "WA"}
    values = tuple(
        value.strip().upper()
        for value in os.getenv(name, default).split(",")
        if value.strip()
    )
    if not values or any(value not in allowed for value in values):
        raise ValueError(f"{name} must contain Australian state abbreviations")
    return values


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class GisCacheSettings:
    root: Path
    max_upload_bytes: int
    max_pixels: int
    gnaf_states: tuple[str, ...] = ("NSW",)
    terrain_default_radius_m: int = 2_000
    terrain_max_radius_m: int = 10_000
    terrain_source_url: str = (
        "https://dea-public-data.s3-ap-southeast-2.amazonaws.com/"
        "projects/elevation/ga_srtm_dem1sv1_0/dem1sv1_0.tif"
    )
    nsw_terrain_enabled: bool = True
    nsw_elevation_index_url: str = (
        "https://portal.spatial.nsw.gov.au/server/rest/services/Hosted/"
        "Elevation_Index_Public/FeatureServer/0/query"
    )
    nsw_dem_download_base_url: str = "https://portal.spatial.nsw.gov.au/download/dem"
    nsw_max_archive_bytes: int = 419_430_400
    ga_wind_multipliers_enabled: bool = True
    ga_wind_multipliers_base_url: str = "https://thredds.nci.org.au/thredds"
    ga_wind_multiplier_timeout_seconds: float = 30.0
    ga_wind_multiplier_workers: int = 8
    nsw_property_feature_url: str = (
        "https://portal.spatial.nsw.gov.au/server/rest/services/"
        "NSW_Land_Parcel_Property_Theme/FeatureServer/12/query"
    )
    nsw_property_timeout_seconds: float = 30.0
    microsoft_buildings_enabled: bool = True
    microsoft_buildings_index_url: str = (
        "https://bfppub.blob.core.windows.net/"
        "$web/2026-07-24/dataset-links.csv"
    )
    microsoft_buildings_timeout_seconds: float = 90.0
    overture_buildings_enabled: bool = True
    overture_buildings_timeout_seconds: float = 90.0
    elvis_building_heights_enabled: bool = True
    elvis_downloadables_url: str = (
        "https://api.elevation.fsdf.org.au/elevation/downloadables"
    )
    elvis_building_height_radius_m: int = 120
    elvis_point_cloud_timeout_seconds: float = 180.0
    elvis_point_cloud_max_bytes: int = 268_435_456
    elvis_point_cloud_total_max_bytes: int = 536_870_912
    elvis_aws_region: str = "ap-southeast-2"
    elvis_identity_pool_id: str = (
        "ap-southeast-2:56462c13-533a-4f84-9a68-631dcd3345ad"
    )

    @classmethod
    def from_env(cls) -> GisCacheSettings:
        return cls(
            root=Path(os.getenv("GIS_CACHE_ROOT", "/var/lib/tertius-gis")),
            max_upload_bytes=_positive_int("GIS_MAX_UPLOAD_BYTES", 536_870_912),
            max_pixels=_positive_int("GIS_MAX_RASTER_PIXELS", 250_000_000),
            gnaf_states=_states("GIS_GNAF_STATES", "NSW"),
            terrain_default_radius_m=_positive_int(
                "GIS_TERRAIN_DEFAULT_RADIUS_M", 2_000
            ),
            terrain_max_radius_m=_positive_int("GIS_TERRAIN_MAX_RADIUS_M", 10_000),
            terrain_source_url=os.getenv(
                "GIS_GA_TERRAIN_SOURCE_URL",
                cls.terrain_source_url,
            ),
            nsw_terrain_enabled=_boolean("GIS_NSW_TERRAIN_ENABLED", True),
            nsw_elevation_index_url=os.getenv(
                "GIS_NSW_ELEVATION_INDEX_URL", cls.nsw_elevation_index_url
            ),
            nsw_dem_download_base_url=os.getenv(
                "GIS_NSW_DEM_DOWNLOAD_BASE_URL", cls.nsw_dem_download_base_url
            ),
            nsw_max_archive_bytes=_positive_int(
                "GIS_NSW_MAX_ARCHIVE_BYTES", 419_430_400
            ),
            ga_wind_multipliers_enabled=_boolean(
                "GIS_GA_WIND_MULTIPLIERS_ENABLED", True
            ),
            ga_wind_multipliers_base_url=os.getenv(
                "GIS_GA_WIND_MULTIPLIERS_BASE_URL",
                cls.ga_wind_multipliers_base_url,
            ),
            ga_wind_multiplier_timeout_seconds=_positive_float(
                "GIS_GA_WIND_MULTIPLIER_TIMEOUT_SECONDS", 30.0
            ),
            ga_wind_multiplier_workers=_positive_int(
                "GIS_GA_WIND_MULTIPLIER_WORKERS", 8
            ),
            nsw_property_feature_url=os.getenv(
                "GIS_NSW_PROPERTY_FEATURE_URL", cls.nsw_property_feature_url
            ),
            nsw_property_timeout_seconds=_positive_float(
                "GIS_NSW_PROPERTY_TIMEOUT_SECONDS", 30.0
            ),
            microsoft_buildings_enabled=_boolean(
                "GIS_MICROSOFT_BUILDINGS_ENABLED", True
            ),
            microsoft_buildings_index_url=os.getenv(
                "GIS_MICROSOFT_BUILDINGS_INDEX_URL",
                cls.microsoft_buildings_index_url,
            ),
            microsoft_buildings_timeout_seconds=_positive_float(
                "GIS_MICROSOFT_BUILDINGS_TIMEOUT_SECONDS", 90.0
            ),
            overture_buildings_enabled=_boolean(
                "GIS_OVERTURE_BUILDINGS_ENABLED", True
            ),
            overture_buildings_timeout_seconds=_positive_float(
                "GIS_OVERTURE_BUILDINGS_TIMEOUT_SECONDS", 90.0
            ),
            elvis_building_heights_enabled=_boolean(
                "GIS_ELVIS_BUILDING_HEIGHTS_ENABLED", True
            ),
            elvis_downloadables_url=os.getenv(
                "GIS_ELVIS_DOWNLOADABLES_URL", cls.elvis_downloadables_url
            ),
            elvis_building_height_radius_m=_positive_int(
                "GIS_ELVIS_BUILDING_HEIGHT_RADIUS_M", 120
            ),
            elvis_point_cloud_timeout_seconds=_positive_float(
                "GIS_ELVIS_POINT_CLOUD_TIMEOUT_SECONDS", 180.0
            ),
            elvis_point_cloud_max_bytes=_positive_int(
                "GIS_ELVIS_POINT_CLOUD_MAX_BYTES", 268_435_456
            ),
            elvis_point_cloud_total_max_bytes=_positive_int(
                "GIS_ELVIS_POINT_CLOUD_TOTAL_MAX_BYTES", 536_870_912
            ),
            elvis_aws_region=os.getenv(
                "GIS_ELVIS_AWS_REGION", cls.elvis_aws_region
            ),
            elvis_identity_pool_id=os.getenv(
                "GIS_ELVIS_IDENTITY_POOL_ID", cls.elvis_identity_pool_id
            ),
        )
