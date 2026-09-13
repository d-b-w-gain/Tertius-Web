from __future__ import annotations

from datetime import UTC, datetime
import math
import re
from typing import Any

import httpx


SATELLITE_EXPORT_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/export"
)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SATELLITE_ATTRIBUTION = "Imagery (c) Esri and contributors"
BUILDING_ATTRIBUTION = "Building outlines (c) OpenStreetMap contributors"
TERRAIN_REPORT_RADIUS_M = 2_000
SATELLITE_REPORT_RADIUS_M = 170
BUILDING_QUERY_RADIUS_M = 220
OSM_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:m|metres?)?\s*$", re.I)


def _utc_text(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _map_bbox(
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


def _terrain_context(
    client: httpx.Client,
    gis_cache_url: str,
    latitude: float,
    longitude: float,
    profile_distance_m: float,
    terrain_evidence_id: str | None,
) -> dict[str, Any]:
    if terrain_evidence_id:
        response = client.get(
            f"{gis_cache_url}/v1/evidence/{terrain_evidence_id}"
        )
    else:
        response = client.post(
            f"{gis_cache_url}/v1/terrain/site",
            json={
                "latitude": latitude,
                "longitude": longitude,
                "radius_m": TERRAIN_REPORT_RADIUS_M,
            },
        )
    response.raise_for_status()
    manifest = response.json()
    evidence_id = str(manifest["evidence_id"])
    statistics_response = client.get(
        f"{gis_cache_url}/v1/raster/statistics",
        params={"evidence_id": evidence_id},
    )
    statistics_response.raise_for_status()
    statistics = statistics_response.json()["b1"]
    low = float(statistics.get("percentile_2", statistics["min"]))
    high = float(statistics.get("percentile_98", statistics["max"]))
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low = float(statistics["min"])
        high = float(statistics["max"])
    preview_response = client.get(
        f"{gis_cache_url}/v1/raster/preview.png",
        params={
            "evidence_id": evidence_id,
            "rescale": f"{low},{high}",
            "colormap_name": "terrain",
            "max_size": 900,
        },
    )
    preview_response.raise_for_status()
    cardinal_profiles: dict[str, Any] | None = None
    profile_warning: str | None = None
    try:
        profile_response = client.get(
            f"{gis_cache_url}/v1/evidence/{evidence_id}/terrain-profiles/cardinal",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "distance_m": profile_distance_m,
                "sample_interval_m": 10,
            },
        )
        profile_response.raise_for_status()
        cardinal_profiles = profile_response.json()
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        profile_warning = f"terrain profiles unavailable: {type(exc).__name__}"
    return {
        "manifest": manifest,
        "statistics": statistics,
        "display_range_m": [low, high],
        "heatmap_png": preview_response.content,
        "query_radius_m": TERRAIN_REPORT_RADIUS_M,
        "cardinal_profiles": cardinal_profiles,
        "profile_warning": profile_warning,
    }


def _multiplier_context(
    client: httpx.Client,
    gis_cache_url: str,
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    response = client.get(
        f"{gis_cache_url}/v1/wind-multipliers/site",
        params={"latitude": latitude, "longitude": longitude},
    )
    response.raise_for_status()
    return response.json()


def _local_wind_context(
    client: httpx.Client,
    gis_cache_url: str,
    *,
    terrain_evidence_id: str,
    latitude: float,
    longitude: float,
    placement_latitude: float,
    placement_longitude: float,
    reference_height_m: float,
    footprint_length_m: float,
    footprint_width_m: float,
    front_bearing_degrees: float,
    wind_region: str,
) -> dict[str, Any]:
    response = client.get(
        f"{gis_cache_url}/v1/evidence/{terrain_evidence_id}/local-wind",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "placement_latitude": placement_latitude,
            "placement_longitude": placement_longitude,
            "reference_height_m": reference_height_m,
            "footprint_length_m": footprint_length_m,
            "footprint_width_m": footprint_width_m,
            "front_bearing_degrees": front_bearing_degrees,
            "wind_region": wind_region,
        },
    )
    response.raise_for_status()
    return response.json()


def _site_boundary_context(
    client: httpx.Client,
    gis_cache_url: str,
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    response = client.get(
        f"{gis_cache_url}/v1/cadastre/site",
        params={"latitude": latitude, "longitude": longitude},
    )
    response.raise_for_status()
    return response.json()


def _satellite_context(
    client: httpx.Client,
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    requested_bbox = _map_bbox(latitude, longitude, SATELLITE_REPORT_RADIUS_M)
    metadata_response = client.get(
        SATELLITE_EXPORT_URL,
        params={
            "bbox": ",".join(str(value) for value in requested_bbox),
            "bboxSR": 4326,
            "imageSR": 4326,
            "size": "1200,800",
            "format": "png32",
            "f": "json",
        },
    )
    metadata_response.raise_for_status()
    metadata = metadata_response.json()
    image_response = client.get(str(metadata["href"]))
    image_response.raise_for_status()
    extent = metadata["extent"]
    return {
        "image_png": image_response.content,
        "extent": [
            float(extent["xmin"]),
            float(extent["ymin"]),
            float(extent["xmax"]),
            float(extent["ymax"]),
        ],
        "width": int(metadata["width"]),
        "height": int(metadata["height"]),
        "scale": float(metadata.get("scale", 0.0)),
        "query_radius_m": SATELLITE_REPORT_RADIUS_M,
        "source": SATELLITE_ATTRIBUTION,
        "source_uri": SATELLITE_EXPORT_URL.rsplit("/export", 1)[0],
    }


def _osm_building_context(
    client: httpx.Client,
    latitude: float,
    longitude: float,
    radius_m: float,
) -> dict[str, Any]:
    query = (
        f'[out:json][timeout:25];way["building"]'
        f"(around:{radius_m},{latitude},{longitude});out body geom;"
    )
    response = client.post(
        OVERPASS_URL,
        content=query,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    footprints: list[list[list[float]]] = []
    profiles: list[dict[str, Any]] = []
    for element in response.json().get("elements", [])[:400]:
        geometry = element.get("geometry")
        if not isinstance(geometry, list) or len(geometry) < 4:
            continue
        polygon = [
            [float(point["lon"]), float(point["lat"])]
            for point in geometry
            if "lon" in point and "lat" in point
        ]
        if len(polygon) >= 4:
            footprints.append(polygon)
            tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}

            def number(key: str) -> float | None:
                raw = tags.get(key)
                match = OSM_NUMBER.fullmatch(str(raw)) if raw is not None else None
                return float(match.group(1)) if match else None

            profiles.append(
                {
                    "source_id": f"osm-way-{element.get('id')}",
                    "height_m": number("height"),
                    "levels": number("building:levels"),
                    "roof_height_m": number("roof:height"),
                    "roof_levels": number("roof:levels"),
                    "roof_shape": tags.get("roof:shape"),
                }
            )
    profile_summary = {
        "measured_height_count": sum(
            profile["height_m"] is not None for profile in profiles
        ),
        "level_count": sum(profile["levels"] is not None for profile in profiles),
        "roof_height_count": sum(
            profile["roof_height_m"] is not None for profile in profiles
        ),
        "roof_shape_count": sum(
            bool(profile["roof_shape"]) for profile in profiles
        ),
    }
    return {
        "footprints": footprints,
        "profiles": profiles,
        "profile_summary": profile_summary,
        "source": BUILDING_ATTRIBUTION,
        "source_uri": "https://www.openstreetmap.org/copyright",
        "query_radius_m": radius_m,
    }


def _building_context(
    client: httpx.Client,
    gis_cache_url: str,
    latitude: float,
    longitude: float,
    radius_m: float,
) -> dict[str, Any]:
    if gis_cache_url:
        try:
            response = client.get(
                f"{gis_cache_url}/v1/buildings/site",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius_m": radius_m,
                },
            )
            response.raise_for_status()
            evidence = response.json()
            features = evidence.get("features", [])
            polygon_features = [
                feature
                for feature in features
                if feature.get("geometry", {}).get("type") == "Polygon"
                and feature.get("geometry", {}).get("coordinates")
            ]
            footprints = [
                feature["geometry"]["coordinates"][0]
                for feature in polygon_features
            ]
            profiles = [
                {
                    "source_id": feature.get("source_id"),
                    "height_m": feature.get("height_m"),
                    "height_lower_m": feature.get("height_lower_m"),
                    "height_upper_m": feature.get("height_upper_m"),
                    "height_observations": feature.get("height_observations", []),
                    "confidence": feature.get("confidence"),
                    "outline_source": feature.get("outline_source"),
                    "height_source": feature.get("height_source"),
                    "levels": feature.get("num_floors"),
                    "roof_height_m": feature.get("roof_height_m"),
                    "roof_levels": None,
                    "roof_shape": feature.get("roof_shape"),
                }
                for feature in polygon_features
            ]
            return {
                "footprints": footprints,
                "profiles": profiles,
                "profile_summary": {
                    "measured_height_count": int(
                        evidence.get("measured_height_count", 0)
                    ),
                    "level_count": sum(
                        profile.get("levels") is not None for profile in profiles
                    ),
                    "roof_height_count": sum(
                        profile.get("roof_height_m") is not None for profile in profiles
                    ),
                    "roof_shape_count": sum(
                        bool(profile.get("roof_shape")) for profile in profiles
                    ),
                    "source_counts": evidence.get("source_counts", {}),
                    "height_source_counts": evidence.get("height_source_counts", {}),
                    "height_observation_count": evidence.get(
                        "height_observation_count", 0
                    ),
                    "height_method_counts": evidence.get("height_method_counts", {}),
                    "height_coverage_ratio": evidence.get("quality", {}).get(
                        "height_coverage_ratio", 0
                    ),
                },
                "source": evidence.get(
                    "attribution", "Open building footprint evidence"
                ),
                "source_uri": evidence.get("source_uri"),
                "dataset_version": evidence.get("dataset_version", "unknown"),
                "evidence_id": evidence.get("evidence_id"),
                "fetched_at": evidence.get("fetched_at"),
                "query_radius_m": float(
                    evidence.get("query_radius_m", radius_m)
                ),
                "quality": (
                    "reconciled open-source footprints with attributed height evidence"
                    if evidence.get("quality", {}).get("source_fusion")
                    else "single-source footprint and height evidence"
                ),
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            pass
    fallback = _osm_building_context(client, latitude, longitude, radius_m)
    fallback["quality"] = "community-authored footprint evidence"
    return fallback


def fetch_site_report_spatial_context(
    *,
    latitude: float,
    longitude: float,
    gis_cache_url: str,
    terrain_profile_distance_m: float = 500.0,
    terrain_evidence_id: str | None = None,
    placement_latitude: float | None = None,
    placement_longitude: float | None = None,
    reference_height_m: float = 3.0,
    footprint_length_m: float = 12.0,
    footprint_width_m: float = 6.0,
    front_bearing_degrees: float = 0.0,
    wind_region: str = "A2",
    accessed_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch optional visual evidence without making report creation fragile."""

    context: dict[str, Any] = {
        "accessed_at_utc": _utc_text(accessed_at),
        "terrain": None,
        "wind_multipliers": None,
        "satellite": None,
        "buildings": None,
        "site_boundary": None,
        "local_wind": None,
        "warnings": [],
    }
    own_client = client is None
    requester = client or httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "Tertius-Site-Report/0.1"},
    )
    try:
        base_url = gis_cache_url.strip().rstrip("/")
        candidate_latitude = placement_latitude or latitude
        candidate_longitude = placement_longitude or longitude
        if base_url:
            for key, loader in (
                (
                    "terrain",
                    lambda: _terrain_context(
                        requester,
                        base_url,
                        candidate_latitude,
                        candidate_longitude,
                        terrain_profile_distance_m,
                        terrain_evidence_id,
                    ),
                ),
                (
                    "wind_multipliers",
                    lambda: _multiplier_context(
                        requester, base_url, latitude, longitude
                    ),
                ),
                (
                    "site_boundary",
                    lambda: _site_boundary_context(
                        requester, base_url, latitude, longitude
                    ),
                ),
            ):
                try:
                    context[key] = loader()
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    context["warnings"].append(
                        f"{key.replace('_', ' ')} unavailable: {type(exc).__name__}"
                    )
            resolved_terrain_evidence_id = terrain_evidence_id or str(
                ((context.get("terrain") or {}).get("manifest") or {}).get(
                    "evidence_id", ""
                )
            ).strip()
            if resolved_terrain_evidence_id:
                try:
                    context["local_wind"] = _local_wind_context(
                        requester,
                        base_url,
                        terrain_evidence_id=resolved_terrain_evidence_id,
                        latitude=latitude,
                        longitude=longitude,
                        placement_latitude=candidate_latitude,
                        placement_longitude=candidate_longitude,
                        reference_height_m=reference_height_m,
                        footprint_length_m=footprint_length_m,
                        footprint_width_m=footprint_width_m,
                        front_bearing_degrees=front_bearing_degrees,
                        wind_region=wind_region,
                    )
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    context["warnings"].append(
                        f"local wind unavailable: {type(exc).__name__}"
                    )
        else:
            context["warnings"].append("GIS cache URL is not configured")

        for key, loader in (
            (
                "satellite",
                lambda: _satellite_context(
                    requester, candidate_latitude, candidate_longitude
                ),
            ),
            (
                "buildings",
                lambda: _building_context(
                    requester,
                    base_url,
                    candidate_latitude,
                    candidate_longitude,
                    max(500.0, 40.0 * reference_height_m),
                ),
            ),
        ):
            try:
                context[key] = loader()
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                context["warnings"].append(
                    f"{key} unavailable: {type(exc).__name__}"
                )
    finally:
        if own_client:
            requester.close()
    return context
