from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class GisCacheSettings:
    root: Path
    max_upload_bytes: int
    max_pixels: int

    @classmethod
    def from_env(cls) -> GisCacheSettings:
        return cls(
            root=Path(os.getenv("GIS_CACHE_ROOT", "/var/lib/tertius-gis")),
            max_upload_bytes=_positive_int("GIS_MAX_UPLOAD_BYTES", 536_870_912),
            max_pixels=_positive_int("GIS_MAX_RASTER_PIXELS", 250_000_000),
        )
