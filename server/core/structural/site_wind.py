from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
from math import cos, hypot, pi
from pathlib import Path
from typing import Any

from core.structural.wind_standard_tables import (
    AUSTRALIAN_REGIONS,
    lookup_climate_change_multiplier,
)


# Ported from the working ContextUI shed/FBD wind_pressure.py calculation.
# The version is deliberately retained so design.py snapshots and calculation
# sheets can identify the exact starter table set that produced q_z.
TABLE_VERSION = "AS1170.2-2021-starter-v1"
REGION_DATA_VERSION = "GA-1170.2-wind-regions-simplified-t0.0500"
AIR_DENSITY_KG_M3 = 1.2

REGION_SOURCE = (
    "Geoscience Australia AS/NZS 1170.2 Wind Regions (DOI 10.26186/146359, CC-BY 4.0)"
)
REGION_VERIFY_AGAINST = "AS/NZS 1170.2:2021 Fig. 3.1(A)"

_A_VALUES = {
    25: 37.0,
    100: 41.0,
    200: 43.0,
    500: 45.0,
    1000: 47.0,
    2000: 48.0,
    2500: 49.0,
}
_B_VALUES = {
    25: 39.0,
    100: 47.0,
    200: 52.0,
    500: 57.0,
    1000: 60.0,
    2000: 62.0,
    2500: 63.0,
}
_C_VALUES = {
    25: 47.0,
    100: 56.0,
    200: 61.0,
    500: 66.0,
    1000: 70.0,
    2000: 73.0,
    2500: 74.0,
}
_D_VALUES = {
    25: 53.0,
    100: 66.0,
    200: 72.0,
    500: 80.0,
    1000: 85.0,
    2000: 88.0,
    2500: 91.0,
}

V_R_TABLE_AU: dict[tuple[str, int], float] = {
    **{
        (region, ari): speed
        for region in ("A0", "A1", "A2", "A3", "A4", "A5")
        for ari, speed in _A_VALUES.items()
    },
    **{
        (region, ari): speed
        for region in ("B1", "B2")
        for ari, speed in _B_VALUES.items()
    },
    **{("C", ari): speed for ari, speed in _C_VALUES.items()},
    **{("D", ari): speed for ari, speed in _D_VALUES.items()},
}

M_C_TABLE: dict[str, float] = {
    region: lookup_climate_change_multiplier(region)
    for region in AUSTRALIAN_REGIONS
}

M_Z_CAT_TABLE: dict[float, dict[str, float]] = {
    3.0: {"1": 0.99, "2": 0.91, "2.5": 0.83, "3": 0.75, "4": 0.75},
    5.0: {"1": 1.05, "2": 0.91, "2.5": 0.83, "3": 0.75, "4": 0.75},
    10.0: {"1": 1.12, "2": 1.00, "2.5": 0.89, "3": 0.83, "4": 0.75},
    15.0: {"1": 1.16, "2": 1.05, "2.5": 0.94, "3": 0.89, "4": 0.75},
    20.0: {"1": 1.19, "2": 1.08, "2.5": 0.97, "3": 0.91, "4": 0.80},
    30.0: {"1": 1.22, "2": 1.12, "2.5": 1.01, "3": 0.96, "4": 0.89},
}
_MZ_HEIGHTS = sorted(M_Z_CAT_TABLE)

IL_TO_ARI_ULS: dict[str, int] = {"1": 100, "2": 500, "3": 1000, "4": 2500}
IL_TO_ARI_SLS: dict[str, int] = {"1": 25, "2": 25, "3": 25, "4": 25}

_REGION_GEOJSON_PATH = (
    Path(__file__).parent / "data" / "wind_regions_simplified_t0.0500.json"
)


class SiteWindError(ValueError):
    """Raised when a site wind input cannot produce an auditable q_z."""


def parse_annual_probability(value: str) -> int:
    text = str(value).strip()
    if not text:
        raise SiteWindError("annual probability must not be empty")
    try:
        if "/" in text:
            _numerator, denominator = text.split("/", 1)
            return int(float(denominator.strip()))
        return int(float(text))
    except ValueError as exc:
        raise SiteWindError(
            f"annual probability {value!r} must look like '1/500' or '500'"
        ) from exc


def lookup_regional_wind_speed(region: str, ari: int) -> float:
    code = str(region).strip().upper()
    key = (code, int(ari))
    if key in V_R_TABLE_AU:
        return V_R_TABLE_AU[key]
    candidates = sorted(
        candidate_ari
        for candidate_region, candidate_ari in V_R_TABLE_AU
        if candidate_region == code
    )
    if not candidates:
        raise SiteWindError(f"wind region {region!r} is not one of {sorted(M_C_TABLE)}")
    larger = [candidate for candidate in candidates if candidate >= ari]
    selected = larger[0] if larger else candidates[-1]
    return V_R_TABLE_AU[(code, selected)]


def lookup_terrain_height_multiplier(
    terrain_category: str,
    reference_height_m: float,
) -> float:
    category = str(terrain_category).strip()
    if category not in {"1", "2", "2.5", "3", "4"}:
        raise SiteWindError("terrain category must be one of 1, 2, 2.5, 3, or 4")
    height = float(reference_height_m)
    if height <= 0:
        raise SiteWindError("reference height must be positive")
    if height <= _MZ_HEIGHTS[0]:
        return M_Z_CAT_TABLE[_MZ_HEIGHTS[0]][category]
    if height >= _MZ_HEIGHTS[-1]:
        return M_Z_CAT_TABLE[_MZ_HEIGHTS[-1]][category]
    for lower, upper in zip(_MZ_HEIGHTS, _MZ_HEIGHTS[1:], strict=True):
        if lower <= height <= upper:
            ratio = (height - lower) / (upper - lower)
            return M_Z_CAT_TABLE[lower][category] + ratio * (
                M_Z_CAT_TABLE[upper][category] - M_Z_CAT_TABLE[lower][category]
            )
    raise SiteWindError(
        f"terrain multiplier lookup failed for category {category}, z={height}"
    )


def compute_site_wind(
    *,
    region: str,
    terrain_category: str,
    importance_level: str = "2",
    annual_probability_uls: str = "",
    reference_height_m: float,
    direction_multiplier: float = 1.0,
    shielding_multiplier: float = 1.0,
    topographic_multiplier: float = 1.0,
    climate_change_multiplier: float | None = None,
) -> dict[str, Any]:
    code = str(region).strip().upper()
    importance = str(importance_level).strip() or "2"
    if annual_probability_uls.strip():
        ari = parse_annual_probability(annual_probability_uls)
        ari_source = "annual probability override"
    else:
        if importance not in IL_TO_ARI_ULS:
            raise SiteWindError("importance level must be one of 1, 2, 3, or 4")
        ari = IL_TO_ARI_ULS[importance]
        ari_source = f"IL{importance} default per AS/NZS 1170.0 starter mapping"

    regional_speed = lookup_regional_wind_speed(code, ari)
    climate = (
        M_C_TABLE[code]
        if climate_change_multiplier is None
        else float(climate_change_multiplier)
    )
    terrain = lookup_terrain_height_multiplier(
        terrain_category,
        reference_height_m,
    )
    direction = float(direction_multiplier)
    shielding = float(shielding_multiplier)
    topographic = float(topographic_multiplier)
    if min(climate, direction, terrain, shielding, topographic) <= 0:
        raise SiteWindError("all wind multipliers must be positive")

    site_speed = (
        regional_speed * climate * direction * terrain * shielding * topographic
    )
    q_z = 0.5 * AIR_DENSITY_KG_M3 * site_speed**2 / 1000.0
    digest_input = {
        "region": code,
        "terrain_category": str(terrain_category),
        "importance_level": importance,
        "ari": ari,
        "reference_height_m": float(reference_height_m),
        "M_c": climate,
        "M_d": direction,
        "M_z_cat": terrain,
        "M_s": shielding,
        "M_t": topographic,
        "table_version": TABLE_VERSION,
    }
    verifier_hash = sha256(
        json.dumps(
            digest_input,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]

    return {
        "standard": "AS/NZS 1170.2:2021",
        "table_version": TABLE_VERSION,
        "table_status": "starter",
        "region": code,
        "terrain_category": str(terrain_category),
        "importance_level": importance,
        "annual_recurrence_interval_years": ari,
        "ari_source": ari_source,
        "reference_height_m": round(float(reference_height_m), 6),
        "regional_wind_speed_m_s": round(regional_speed, 6),
        "climate_change_multiplier": round(climate, 6),
        "direction_multiplier": round(direction, 6),
        "terrain_height_multiplier": round(terrain, 6),
        "shielding_multiplier": round(shielding, 6),
        "topographic_multiplier": round(topographic, 6),
        "site_wind_speed_m_s": round(site_speed, 6),
        "q_z_kPa": round(q_z, 6),
        "verifier_hash": verifier_hash,
        "formula": ("q_z = 0.5 rho V_sit^2; V_sit = V_R M_c M_d M_z,cat M_s M_t"),
        "verify_against": (
            "Engineer must verify V_R, M_z,cat, region, multipliers, and "
            "return-period selection against the project editions of "
            "AS/NZS 1170.0 and AS/NZS 1170.2 before design use."
        ),
    }


def verify_site_wind_snapshot(snapshot: dict[str, Any]) -> list[str]:
    """Return field-level drift messages for a design.py wind snapshot."""

    try:
        expected = compute_site_wind(
            region=str(snapshot["region"]),
            terrain_category=str(snapshot["terrain_category"]),
            importance_level=str(snapshot["importance_level"]),
            annual_probability_uls=str(snapshot["annual_recurrence_interval_years"]),
            reference_height_m=float(snapshot["reference_height_m"]),
            direction_multiplier=float(snapshot["direction_multiplier"]),
            shielding_multiplier=float(snapshot["shielding_multiplier"]),
            topographic_multiplier=float(snapshot["topographic_multiplier"]),
            climate_change_multiplier=float(snapshot["climate_change_multiplier"]),
        )
    except (KeyError, TypeError, ValueError, SiteWindError) as exc:
        return [f"wind action basis cannot be recomputed: {exc}"]

    comparisons = {
        "regional_wind_speed_m_s": 1e-6,
        "terrain_height_multiplier": 1e-6,
        "site_wind_speed_m_s": 1e-6,
        "q_z_kPa": 1e-6,
    }
    messages: list[str] = []
    for field, tolerance in comparisons.items():
        try:
            actual = float(snapshot[field])
        except KeyError, TypeError, ValueError:
            messages.append(f"wind action basis is missing numeric {field}")
            continue
        if abs(actual - float(expected[field])) > tolerance:
            messages.append(
                f"wind action basis {field}={actual:g} does not match "
                f"{TABLE_VERSION} recomputation {float(expected[field]):g}"
            )
    if snapshot.get("table_version") != TABLE_VERSION:
        messages.append(
            "wind action basis table_version does not match the active "
            f"calculation engine ({TABLE_VERSION})"
        )
    if snapshot.get("verifier_hash") != expected["verifier_hash"]:
        messages.append(
            "wind action basis verifier_hash does not match its authored inputs"
        )
    return messages


@lru_cache(maxsize=1)
def wind_region_geojson() -> dict[str, Any]:
    if not _REGION_GEOJSON_PATH.exists():
        raise SiteWindError(f"wind region overlay is missing at {_REGION_GEOJSON_PATH}")
    value = json.loads(_REGION_GEOJSON_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("type") != "FeatureCollection"
        or not isinstance(value.get("features"), list)
    ):
        raise SiteWindError("wind region overlay is not a GeoJSON FeatureCollection")
    return value


def _point_in_ring(
    longitude: float,
    latitude: float,
    ring: list[list[float]],
) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        crosses = (y1 > latitude) != (y2 > latitude)
        if crosses:
            x_at_y = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < x_at_y:
                inside = not inside
        previous = current
    return inside


def _point_in_polygon(
    longitude: float,
    latitude: float,
    polygon: list[list[list[float]]],
) -> bool:
    if not polygon or not _point_in_ring(longitude, latitude, polygon[0]):
        return False
    return not any(_point_in_ring(longitude, latitude, hole) for hole in polygon[1:])


def _feature_contains(
    feature: dict[str, Any], latitude: float, longitude: float
) -> bool:
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        return _point_in_polygon(longitude, latitude, coordinates)
    if geometry.get("type") == "MultiPolygon":
        return any(
            _point_in_polygon(longitude, latitude, polygon) for polygon in coordinates
        )
    return False


def _iter_rings(feature: dict[str, Any]):
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        yield from coordinates
    elif geometry.get("type") == "MultiPolygon":
        for polygon in coordinates:
            yield from polygon


def _point_segment_distance_km(
    latitude: float,
    longitude: float,
    start: list[float],
    end: list[float],
) -> float:
    latitude_scale = 111.195
    longitude_scale = latitude_scale * cos(latitude * pi / 180.0)
    px = longitude * longitude_scale
    py = latitude * latitude_scale
    ax = float(start[0]) * longitude_scale
    ay = float(start[1]) * latitude_scale
    bx = float(end[0]) * longitude_scale
    by = float(end[1]) * latitude_scale
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return hypot(px - ax, py - ay)
    ratio = max(
        0.0,
        min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)),
    )
    return hypot(px - (ax + ratio * dx), py - (ay + ratio * dy))


def lookup_wind_region(
    *,
    latitude: float,
    longitude: float,
    fallback_km: float = 10.0,
) -> dict[str, Any] | None:
    lat = float(latitude)
    lng = float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise SiteWindError("latitude/longitude are outside WGS84 bounds")

    features = wind_region_geojson()["features"]
    for feature in features:
        if _feature_contains(feature, lat, lng):
            properties = feature.get("properties") or {}
            return {
                "region": properties.get("region"),
                "area": properties.get("area") or "",
                "approximate": True,
                "source": REGION_SOURCE,
                "dataset_version": REGION_DATA_VERSION,
                "verify_against": REGION_VERIFY_AGAINST,
                "detail": (
                    "Suggested from the deployed simplified Geoscience Australia "
                    "overlay; verify the boundary against the Standard."
                ),
            }

    best_feature: dict[str, Any] | None = None
    best_distance = float(fallback_km)
    for feature in features:
        for ring in _iter_rings(feature):
            for start, end in zip(ring, ring[1:], strict=False):
                distance = _point_segment_distance_km(lat, lng, start, end)
                if distance < best_distance:
                    best_distance = distance
                    best_feature = feature
    if best_feature is None:
        return None
    properties = best_feature.get("properties") or {}
    return {
        "region": properties.get("region"),
        "area": properties.get("area") or "",
        "approximate": True,
        "source": REGION_SOURCE,
        "dataset_version": REGION_DATA_VERSION,
        "verify_against": REGION_VERIFY_AGAINST,
        "detail": (
            f"Nearest simplified region boundary is {best_distance:.2f} km away; "
            "verify against the Standard."
        ),
    }
