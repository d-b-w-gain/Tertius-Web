from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
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
        )
