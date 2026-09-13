from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

from .buildings import OpenBuildingProvider
from .directional_geometry import (
    oriented_rectangle,
    polygon_intersects_directional_sector,
    polygons_intersect,
)
from .models import (
    BuildingFeature,
    CardinalMultiplierValues,
    DirectionalLocalWindAssessment,
    LocalDirectionalWindEvidence,
)
from .terrain_profiles import (
    CARDINAL_BEARINGS,
    TerrainProfileSampler,
    TopographicTransect,
)
from .wind_multipliers import GaWindMultiplierProvider

if TYPE_CHECKING:
    from .terrain import TerrainFetcher


ALGORITHM_VERSION = "tertius-local-wind-2021-amd2-sector-v8-ga-shielding-baseline"
EARTH_METRES_PER_DEGREE = 111_320.0
TOPOGRAPHIC_SEARCH_RADIUS_M = 5_000.0
TOPOGRAPHIC_SAMPLE_INTERVAL_M = 10.0
TOPOGRAPHIC_ANGULAR_INTERVAL_DEGREES = 2.5
TERRAIN_TABLE = {
    3.0: {1.0: 0.97, 2.0: 0.91, 2.5: 0.87, 3.0: 0.83, 4.0: 0.75},
    5.0: {1.0: 1.01, 2.0: 0.91, 2.5: 0.87, 3.0: 0.83, 4.0: 0.75},
    10.0: {1.0: 1.08, 2.0: 1.00, 2.5: 0.92, 3.0: 0.83, 4.0: 0.75},
    15.0: {1.0: 1.12, 2.0: 1.05, 2.5: 0.97, 3.0: 0.89, 4.0: 0.75},
    20.0: {1.0: 1.14, 2.0: 1.08, 2.5: 1.01, 3.0: 0.94, 4.0: 0.75},
    30.0: {1.0: 1.18, 2.0: 1.12, 2.5: 1.06, 3.0: 1.00, 4.0: 0.80},
}
DIRECTIONS = tuple(CARDINAL_BEARINGS)


def _interpolate(points: list[tuple[float, float]], query: float) -> float:
    ordered = sorted(points)
    if query <= ordered[0][0]:
        return ordered[0][1]
    if query >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:], strict=True):
        if x0 <= query <= x1:
            ratio = (query - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return ordered[-1][1]


def _terrain_multiplier(category: float, height_m: float) -> float:
    by_height = [
        (
            height,
            _interpolate(
                [(terrain_category, value) for terrain_category, value in row.items()],
                category,
            ),
        )
        for height, row in TERRAIN_TABLE.items()
    ]
    return _interpolate(by_height, height_m)


def _category_from_ga_10m(multiplier: float) -> float:
    row = TERRAIN_TABLE[10.0]
    # Mz decreases as terrain category increases, so reverse the axes before
    # interpolation. This preserves the GA grid's continuous effective category.
    return _interpolate(
        [(value, category) for category, value in row.items()],
        multiplier,
    )


def _local_xy(
    longitude: float,
    latitude: float,
    origin_longitude: float,
    origin_latitude: float,
) -> tuple[float, float]:
    return (
        (longitude - origin_longitude)
        * EARTH_METRES_PER_DEGREE
        * math.cos(math.radians(origin_latitude)),
        (latitude - origin_latitude) * EARTH_METRES_PER_DEGREE,
    )


def _polygon_xy(
    feature: BuildingFeature,
    longitude: float,
    latitude: float,
) -> list[tuple[float, float]]:
    geometry = feature.geometry
    if geometry.get("type") != "Polygon":
        return []
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        return []
    return [
        _local_xy(float(point[0]), float(point[1]), longitude, latitude)
        for point in coordinates[0]
        if isinstance(point, list) and len(point) >= 2
    ]


def _credible_height_interval(
    feature: BuildingFeature,
) -> tuple[float, float, float] | None:
    """Return conservative lower, best and upper building-height evidence."""

    if (
        feature.height_lower_m is not None
        and feature.height_m is not None
        and feature.height_upper_m is not None
    ):
        return (
            float(feature.height_lower_m),
            float(feature.height_m),
            float(feature.height_upper_m),
        )
    if feature.height_m is None:
        return None
    best = float(feature.height_m)
    margin = max(1.0, best * 0.20)
    return (max(0.1, best - margin), best, best + margin)


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    ring = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    if not ring:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in ring) / len(ring),
        sum(point[1] for point in ring) / len(ring),
    )


def _area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return (
        abs(
            sum(
                first[0] * second[1] - second[0] * first[1]
                for first, second in zip(points, points[1:] + points[:1], strict=True)
            )
        )
        / 2.0
    )


def _morphology_category(
    building_fraction: float, buildings_per_hectare: float
) -> float:
    # A transparent morphology screen, not a claim that footprint density is a
    # substitute for the full terrain definitions. Sparse/unknown coverage is
    # deliberately treated as exposed terrain so it cannot reduce wind actions.
    if building_fraction >= 0.30 and buildings_per_hectare >= 25:
        return 4.0
    if building_fraction >= 0.12 or buildings_per_hectare >= 10:
        return 3.0
    if building_fraction >= 0.04 or buildings_per_hectare >= 3:
        return 2.5
    if building_fraction >= 0.01 or buildings_per_hectare >= 1:
        return 2.0
    return 1.0


def _shielding_multiplier(parameter: float) -> float:
    return _interpolate([(1.5, 0.7), (3.0, 0.8), (6.0, 0.9), (12.0, 1.0)], parameter)


def _profile_value(
    distances: list[float], elevations: list[float], query: float
) -> float | None:
    if not distances or query < distances[0] or query > distances[-1]:
        return None
    for index in range(1, len(distances)):
        if distances[index - 1] <= query <= distances[index]:
            span = distances[index] - distances[index - 1]
            ratio = 0.0 if span == 0 else (query - distances[index - 1]) / span
            return elevations[index - 1] + ratio * (
                elevations[index] - elevations[index - 1]
            )
    return elevations[-1]


def _site_profile_segment(
    transect: TopographicTransect,
) -> tuple[list[float], list[float], bool]:
    site_index = min(
        range(len(transect.distances_m)),
        key=lambda index: abs(transect.distances_m[index]),
    )
    if transect.elevations_m[site_index] is None:
        return [], [], False
    start = site_index
    while start > 0 and transect.elevations_m[start - 1] is not None:
        start -= 1
    end = site_index
    while (
        end + 1 < len(transect.elevations_m)
        and transect.elevations_m[end + 1] is not None
    ):
        end += 1
    distances = [float(value) for value in transect.distances_m[start : end + 1]]
    elevations = [
        float(value)
        for value in transect.elevations_m[start : end + 1]
        if value is not None
    ]
    requested = max(
        abs(float(transect.distances_m[0])), abs(float(transect.distances_m[-1]))
    )
    complete = distances[0] <= -0.98 * requested and distances[-1] >= 0.98 * requested
    return distances, elevations, complete


def _smoothed(values: list[float]) -> list[float]:
    result: list[float] = []
    for index in range(len(values)):
        window = sorted(values[max(0, index - 1) : min(len(values), index + 2)])
        result.append(window[len(window) // 2])
    return result


def _regional_mt(
    mh: float,
    *,
    wind_region: str,
    site_elevation_m: float,
) -> float:
    region = wind_region.upper()
    if region == "A0":
        return 0.5 + 0.5 * mh
    if region == "A4" and site_elevation_m > 500.0:
        return mh * (1.0 + 0.00015 * site_elevation_m)
    return mh


def _transect_candidates(
    transect: TopographicTransect,
    reference_height_m: float,
    wind_region: str,
) -> tuple[list[dict[str, object]], bool]:
    distances, raw_elevations, complete = _site_profile_segment(transect)
    if len(distances) < 7:
        return [], False
    # Preserve the measured crest location. Median filtering can move a sharp
    # escarpment edge by one cell, which materially changes x and the L2 taper.
    elevations = raw_elevations
    interval = max(
        1.0,
        sum(
            abs(second - first)
            for first, second in zip(distances, distances[1:], strict=False)
        )
        / max(len(distances) - 1, 1),
    )
    crest_window = max(2, math.ceil(40.0 / interval))
    threshold = min(0.4 * reference_height_m, 5.0)
    candidates: list[dict[str, object]] = []

    for crest_index in range(1, len(distances) - 2):
        crest_elevation = elevations[crest_index]
        local = elevations[
            max(0, crest_index - crest_window) : min(
                len(elevations), crest_index + crest_window + 1
            )
        ]
        if crest_elevation < max(local) - 0.25:
            continue
        # For a flat-topped ridge or escarpment, the crest is the windward
        # plateau edge, not an arbitrary point farther downwind on the plateau.
        if elevations[crest_index + 1] >= crest_elevation - 0.25:
            continue
        upwind_end = len(elevations)
        for index in range(crest_index + crest_window + 1, len(elevations)):
            if elevations[index] > crest_elevation + max(0.5, threshold * 0.25):
                upwind_end = index
                break
        if upwind_end <= crest_index + 1:
            continue
        base_index = min(
            range(crest_index + 1, upwind_end),
            key=lambda index: elevations[index],
        )
        base_elevation = elevations[base_index]
        feature_height = crest_elevation - base_elevation
        if feature_height < threshold:
            continue
        immediate_upwind = elevations[
            crest_index + 1 : min(len(elevations), crest_index + crest_window + 2)
        ]
        if not immediate_upwind or crest_elevation - min(immediate_upwind) < min(
            0.5, threshold * 0.25
        ):
            continue
        if base_index == len(elevations) - 1 and len(elevations) >= 5:
            tail_distance = distances[-1] - distances[-5]
            tail_slope = (
                0.0
                if tail_distance == 0
                else (elevations[-5] - elevations[-1]) / tail_distance
            )
            if tail_slope > 0.05:
                continue

        half_elevation = crest_elevation - feature_height / 2.0
        half_distance: float | None = None
        for index in range(crest_index + 1, base_index + 1):
            previous_elevation = elevations[index - 1]
            current_elevation = elevations[index]
            if current_elevation <= half_elevation <= previous_elevation:
                span = previous_elevation - current_elevation
                ratio = (
                    0.0 if span == 0 else (previous_elevation - half_elevation) / span
                )
                half_distance = distances[index - 1] + ratio * (
                    distances[index] - distances[index - 1]
                )
                break
        if half_distance is None:
            continue
        crest_offset = distances[crest_index]
        lu = half_distance - crest_offset
        if lu <= 0:
            continue
        slope = feature_height / (2.0 * lu)
        l1 = max(0.36 * lu, 0.4 * feature_height)
        upwind_l2 = 4.0 * l1
        escarpment_downwind_l2 = 10.0 * l1
        downwind_target = crest_offset - escarpment_downwind_l2
        target_elevation = _profile_value(distances, elevations, downwind_target)
        downwind_slope = (
            None
            if target_elevation is None or escarpment_downwind_l2 <= 0
            else abs(crest_elevation - target_elevation) / escarpment_downwind_l2
        )
        site_position = (
            "downwind"
            if crest_offset > interval / 2
            else "upwind"
            if crest_offset < -interval / 2
            else "crest"
        )
        if downwind_slope is not None and downwind_slope <= 0.05:
            feature_type = "escarpment"
            downwind_l2 = escarpment_downwind_l2
        elif downwind_slope is None and site_position == "downwind":
            feature_type = "unresolved_conservative_escarpment"
            downwind_l2 = escarpment_downwind_l2
        else:
            feature_type = "hill_or_ridge"
            downwind_l2 = upwind_l2
        l2 = downwind_l2 if site_position == "downwind" else upwind_l2
        x = abs(crest_offset)
        inside = x <= l2
        taper = max(0.0, 1.0 - x / l2) if l2 > 0 else 0.0
        if not inside or slope < 0.05:
            mh = 1.0
            equation = "outside_zone" if not inside else "gentle_slope"
        elif slope <= 0.45:
            mh = 1.0 + feature_height / (3.5 * (reference_height_m + l1)) * taper
            equation = "4.4(3)"
        else:
            ordinary = 1.0 + feature_height / (3.5 * (reference_height_m + l1)) * taper
            peak = 1.0 + 0.71 * taper
            mh = max(ordinary, peak)
            equation = "4.4(4)_conservative_peak_envelope"
            feature_type = f"steep_{feature_type}"
        candidates.append(
            {
                "height": feature_height,
                "crest": x,
                "crest_offset": crest_offset,
                "crest_elevation": crest_elevation,
                "base_elevation": base_elevation,
                "half_height_distance": lu,
                "lu": lu,
                "l1": l1,
                "l2": l2,
                "mh": mh,
                "local": _regional_mt(
                    mh,
                    wind_region=wind_region,
                    site_elevation_m=transect.site_elevation_m,
                ),
                "slope": slope,
                "feature_type": feature_type,
                "site_position": site_position,
                "bearing": transect.bearing_degrees,
                "inside": inside,
                "equation": equation,
                "distances": distances,
                "elevations": raw_elevations,
            }
        )
    return candidates, complete


def _topographic_assessment(
    transects: list[TopographicTransect],
    reference_height_m: float,
    ga_multiplier: float,
    wind_region: str,
    search_radius_m: float,
) -> tuple[float, dict[str, object]]:
    threshold = min(0.4 * reference_height_m, 5.0)
    all_candidates: list[dict[str, object]] = []
    completeness: list[bool] = []
    for transect in transects:
        candidates, complete = _transect_candidates(
            transect,
            reference_height_m,
            wind_region,
        )
        all_candidates.extend(candidates)
        completeness.append(complete)
    central = min(
        transects,
        key=lambda value: abs(
            ((value.bearing_degrees - CARDINAL_BEARINGS[value.direction] + 180) % 360)
            - 180
        ),
    )
    search_complete = bool(completeness) and all(completeness)
    if all_candidates:
        strongest = max(
            all_candidates,
            key=lambda value: (
                float(value["local"]),
                float(value["mh"]),
                float(value["height"]),
                -float(value["crest"]),
            ),
        )
        profile_distances = list(strongest["distances"])
        profile_elevations = list(strongest["elevations"])
    else:
        strongest = None
        profile_distances = [float(value) for value in central.distances_m]
        profile_elevations = [
            None if value is None else float(value) for value in central.elevations_m
        ]
    adopted = max(
        1.0,
        ga_multiplier,
        float(strongest["local"]) if strongest is not None else 1.0,
    )
    if strongest is None:
        reason = (
            f"Swept {len(transects)} two-sided cross-sections across +/-22.5 deg to "
            f"{search_radius_m / 1000:.1f} km. No resolved feature exceeded the Amd 2 "
            f"screen H={threshold:.2f} m. Search coverage is "
            f"{'complete' if search_complete else 'partial'}; Australian Mlee=1.000 and "
            f"max(GA={ga_multiplier:.3f}, 1.000) gives Mt={adopted:.3f}."
        )
        return adopted, {
            "height": None,
            "crest": None,
            "crest_offset": None,
            "crest_elevation": None,
            "base_elevation": None,
            "half_height_distance": None,
            "lu": None,
            "l1": None,
            "l2": None,
            "mh": 1.0,
            "feature_type": None,
            "site_position": None,
            "slope": None,
            "bearing": central.bearing_degrees,
            "candidate_count": 0,
            "search_complete": search_complete,
            "profile_distances": profile_distances,
            "profile_elevations": profile_elevations,
            "reason": reason,
        }

    zone_status = "inside" if strongest["inside"] else "outside"
    reason = (
        f"Swept {len(transects)} two-sided cross-sections across +/-22.5 deg to "
        f"{search_radius_m / 1000:.1f} km; {len(all_candidates)} resolved candidates. "
        f"Governing {strongest['feature_type']} at bearing "
        f"{float(strongest['bearing']):.1f} deg places the site "
        f"{strongest['site_position']} of the crest: H={float(strongest['height']):.1f} m, "
        f"x={float(strongest['crest']):.1f} m, Lu={float(strongest['lu']):.1f} m, "
        f"H/(2Lu)={float(strongest['slope']):.3f}, L1={float(strongest['l1']):.1f} m, "
        f"applicable L2={float(strongest['l2']):.1f} m. Site is {zone_status}; "
        f"{strongest['equation']} gives Mh={float(strongest['mh']):.3f}. "
        f"Coverage is {'complete' if search_complete else 'partial'}; Australian "
        f"Mlee=1.000 and max(local={float(strongest['local']):.3f}, "
        f"GA={ga_multiplier:.3f}, 1.000) gives Mt={adopted:.3f}."
    )
    return adopted, {
        **strongest,
        "candidate_count": len(all_candidates),
        "search_complete": search_complete,
        "profile_distances": profile_distances,
        "profile_elevations": profile_elevations,
        "reason": reason,
    }


class LocalWindAnalyzer:
    def __init__(
        self,
        root: Path,
        profiles: TerrainProfileSampler,
        buildings: OpenBuildingProvider,
        ga: GaWindMultiplierProvider,
        terrain: TerrainFetcher | None = None,
    ):
        self.root = root / "local-wind"
        self.profiles = profiles
        self.buildings = buildings
        self.ga = ga
        self.terrain = terrain

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def analyze(
        self,
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
    ) -> LocalDirectionalWindEvidence:
        self.initialize()
        profile_distance = max(500.0, 40.0 * reference_height_m)
        topographic_evidence_id = terrain_evidence_id
        if self.terrain is not None:
            try:
                topographic_evidence_id = self.terrain.fetch(
                    placement_latitude,
                    placement_longitude,
                    int(TOPOGRAPHIC_SEARCH_RADIUS_M),
                ).evidence_id
            except OSError, ValueError:
                # The pinned local tile remains a reproducible fallback. Missing
                # broad coverage is carried into the evidence as a partial screen.
                topographic_evidence_id = terrain_evidence_id
        topographic_sectors = self.profiles.sample_topographic_sectors(
            topographic_evidence_id,
            placement_latitude,
            placement_longitude,
            TOPOGRAPHIC_SEARCH_RADIUS_M,
            TOPOGRAPHIC_SAMPLE_INTERVAL_M,
            TOPOGRAPHIC_ANGULAR_INTERVAL_DEGREES,
        )
        building_evidence = self.buildings.fetch(
            placement_latitude, placement_longitude, profile_distance
        )
        # The GA multiplier grid is site evidence, not structure-placement evidence.
        # Keeping it anchored to the geocoded site also lets a shed be repositioned
        # within the parcel without invalidating and redownloading the same grid cell.
        ga = self.ga.fetch(latitude, longitude)
        identity = {
            "algorithm": ALGORITHM_VERSION,
            "terrain_evidence_id": terrain_evidence_id,
            "topographic_terrain_evidence_id": topographic_evidence_id,
            "building_evidence_id": building_evidence.evidence_id,
            "ga_evidence_id": ga.evidence_id,
            "location": [round(latitude, 7), round(longitude, 7)],
            "placement": [round(placement_latitude, 7), round(placement_longitude, 7)],
            "reference_height_m": round(reference_height_m, 4),
            "footprint": [round(footprint_length_m, 4), round(footprint_width_m, 4)],
            "front_bearing_degrees": round(front_bearing_degrees, 4),
            "wind_region": wind_region.upper(),
        }
        evidence_id = (
            "windv1-"
            + sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:32]
        )
        target = self.root / f"{evidence_id}.json"
        if target.is_file():
            try:
                return LocalDirectionalWindEvidence.model_validate_json(
                    target.read_text(encoding="utf-8")
                )
            except OSError, ValueError:
                target.unlink(missing_ok=True)

        feature_geometry = [
            (feature, _polygon_xy(feature, placement_longitude, placement_latitude))
            for feature in building_evidence.features
        ]
        feature_geometry = [value for value in feature_geometry if len(value[1]) >= 3]
        terrain_values: dict[str, float] = {}
        shielding_values: dict[str, float] = {}
        topographic_values: dict[str, float] = {}
        assessments: dict[str, DirectionalLocalWindAssessment] = {}
        terrain_lag = 20.0 * reference_height_m
        shielding_radius = 20.0 * reference_height_m
        sector_area = math.pi * max(profile_distance**2 - terrain_lag**2, 1.0) / 8.0
        candidate_footprint = oriented_rectangle(
            front_bearing_degrees=front_bearing_degrees,
            length_m=footprint_length_m,
            width_m=footprint_width_m,
        )

        for direction, bearing in CARDINAL_BEARINGS.items():
            in_sector: list[
                tuple[BuildingFeature, list[tuple[float, float]], float]
            ] = []
            shielding_candidates: list[
                tuple[BuildingFeature, list[tuple[float, float]], float]
            ] = []
            for feature, polygon in feature_geometry:
                centre_x, centre_y = _centroid(polygon)
                distance = math.hypot(centre_x, centre_y)
                if (
                    terrain_lag <= distance <= profile_distance
                    and polygon_intersects_directional_sector(
                        polygon, bearing, profile_distance
                    )
                ):
                    in_sector.append((feature, polygon, distance))
                if polygon_intersects_directional_sector(
                    polygon, bearing, shielding_radius
                ) and not polygons_intersect(polygon, candidate_footprint):
                    shielding_candidates.append((feature, polygon, distance))

            building_area = sum(
                _area(polygon) for _feature, polygon, _distance in in_sector
            )
            building_fraction = building_area / sector_area
            per_hectare = len(in_sector) / (sector_area / 10_000.0)
            morphology = _morphology_category(building_fraction, per_hectare)
            ga_mz = float(getattr(ga.terrain_height_multipliers, direction))
            ga_category = _category_from_ga_10m(ga_mz)
            terrain_category = min(morphology, ga_category)
            mz = _terrain_multiplier(terrain_category, reference_height_m)

            intervals = {
                feature.source_id: _credible_height_interval(feature)
                for feature, _polygon, _distance in shielding_candidates
            }
            height_coverage = (
                sum(value is not None for value in intervals.values())
                / len(shielding_candidates)
                if shielding_candidates
                else 0.0
            )
            definitely_eligible = [
                (feature, polygon, interval)
                for feature, polygon, _distance in shielding_candidates
                if (interval := intervals[feature.source_id]) is not None
                and interval[0] >= reference_height_m
            ]
            definitely_ineligible = [
                feature
                for feature, _polygon, _distance in shielding_candidates
                if (interval := intervals[feature.source_id]) is not None
                and interval[2] < reference_height_m
            ]
            uncertain = [
                feature
                for feature, _polygon, _distance in shielding_candidates
                if (interval := intervals[feature.source_id]) is None
                or not (
                    interval[0] >= reference_height_m
                    or interval[2] < reference_height_m
                )
            ]
            decision_coverage = (
                (len(definitely_eligible) + len(definitely_ineligible))
                / len(shielding_candidates)
                if shielding_candidates
                else 0.0
            )
            included_ids: list[str] = []
            shielding_parameter: float | None = None
            average_height: float | None = None
            average_breadth: float | None = None
            local_ms: float | None = None
            if definitely_eligible:
                normal = (
                    math.cos(math.radians(bearing)),
                    -math.sin(math.radians(bearing)),
                )
                breadths = [
                    max(
                        point[0] * normal[0] + point[1] * normal[1] for point in polygon
                    )
                    - min(
                        point[0] * normal[0] + point[1] * normal[1] for point in polygon
                    )
                    for _feature, polygon, _interval in definitely_eligible
                ]
                # The lower credible height gives the least shielding credit and is
                # therefore the conservative value for the adopted calculation.
                average_height = sum(
                    interval[0] for _feature, _polygon, interval in definitely_eligible
                ) / len(definitely_eligible)
                average_breadth = sum(breadths) / len(breadths)
                average_spacing = reference_height_m * (
                    10.0 / len(definitely_eligible) + 5.0
                )
                shielding_parameter = average_spacing / math.sqrt(
                    max(average_height * average_breadth, 1e-6)
                )
                local_ms = _shielding_multiplier(shielding_parameter)
                included_ids = [
                    feature.source_id
                    for feature, _polygon, _interval in definitely_eligible
                ]
            # The January 2016 GA grid is the established directional baseline.
            # Patchy present-day reconstruction may add defensible shielding credit,
            # but a missing footprint or height must not erase baseline evidence.
            ga_ms = float(getattr(ga.shielding_multipliers, direction))
            local_improves_baseline = local_ms is not None and local_ms < ga_ms - 1e-9
            ms = local_ms if local_improves_baseline else ga_ms
            shielding_basis = (
                "local_improvement" if local_improves_baseline else "ga_2016_baseline"
            )
            if local_improves_baseline:
                shielding_reason = (
                    f"The January 2016 GA directional baseline is Ms={ga_ms:.3f}. "
                    f"Table 4.2 uses {len(definitely_eligible)} current building(s) whose "
                    f"lower credible height meets h={reference_height_m:.2f} m; conservative "
                    f"lower heights give s={shielding_parameter:.2f} and local "
                    f"Ms={local_ms:.3f}. Because this is an evidence-backed improvement, "
                    f"local Ms={local_ms:.3f} is adopted. {len(uncertain)} uncertain "
                    f"candidate(s) receive no additional local credit and "
                    f"{len(definitely_ineligible)} are definitely below h."
                )
            elif local_ms is not None:
                shielding_reason = (
                    f"The January 2016 GA directional baseline Ms={ga_ms:.3f} is retained. "
                    f"Table 4.2 uses {len(definitely_eligible)} current building(s) whose "
                    f"lower credible height meets h={reference_height_m:.2f} m and gives "
                    f"s={shielding_parameter:.2f}, local Ms={local_ms:.3f}; that partial local "
                    f"result is not an improvement on the GA baseline. {len(uncertain)} "
                    f"uncertain candidate(s) receive no additional local credit and "
                    f"{len(definitely_ineligible)} are definitely below h."
                )
            elif not shielding_candidates:
                shielding_reason = (
                    f"The January 2016 GA directional baseline Ms={ga_ms:.3f} is retained. "
                    f"The current open-data reconstruction found no non-overlapping building "
                    f"footprint inside 20h={shielding_radius:.1f} m; missing reconstruction "
                    f"evidence cannot worsen the established baseline to Ms=1.000."
                )
            else:
                shielding_reason = (
                    f"The January 2016 GA directional baseline Ms={ga_ms:.3f} is retained. "
                    f"No current candidate has a lower credible height meeting "
                    f"h={reference_height_m:.2f} m; {len(uncertain)} candidate(s) remain "
                    f"uncertain and {len(definitely_ineligible)} are definitely below h. "
                    f"Incomplete local reconstruction cannot worsen the established baseline "
                    f"to Ms=1.000."
                )

            ga_mt = float(getattr(ga.topographic_multipliers, direction))
            mt, topography = _topographic_assessment(
                topographic_sectors[direction],
                reference_height_m,
                ga_mt,
                wind_region,
                TOPOGRAPHIC_SEARCH_RADIUS_M,
            )
            terrain_values[direction] = round(mz, 6)
            shielding_values[direction] = round(ms, 6)
            topographic_values[direction] = round(mt, 6)
            assessments[direction] = DirectionalLocalWindAssessment(
                direction=direction,
                bearing_degrees=bearing,
                terrain_category=round(terrain_category, 4),
                terrain_height_multiplier=round(mz, 6),
                ga_terrain_height_multiplier_10m=ga_mz,
                terrain_building_fraction=round(building_fraction, 6),
                terrain_buildings_per_hectare=round(per_hectare, 4),
                terrain_reason=(
                    f"GA 10 m Mz={ga_mz:.3f} implies effective TC{ga_category:.2f}; "
                    f"the {profile_distance:.0f} m building-morphology screen gives "
                    f"TC{morphology:.1f}. The more exposed TC{terrain_category:.2f} "
                    f"gives Mz,cat={mz:.3f} at z={reference_height_m:.1f} m."
                ),
                shielding_multiplier=round(ms, 6),
                ga_shielding_multiplier_2016=round(ga_ms, 6),
                local_shielding_multiplier=(
                    round(local_ms, 6) if local_ms is not None else None
                ),
                shielding_basis=shielding_basis,
                shielding_parameter=(
                    round(shielding_parameter, 5)
                    if shielding_parameter is not None
                    else None
                ),
                shielding_building_count=len(included_ids),
                shielding_candidate_count=len(shielding_candidates),
                shielding_height_coverage=round(height_coverage, 5),
                shielding_height_decision_coverage=round(decision_coverage, 5),
                shielding_definitely_eligible_count=len(definitely_eligible),
                shielding_definitely_ineligible_count=len(definitely_ineligible),
                shielding_uncertain_building_ids=[
                    feature.source_id for feature in uncertain
                ],
                shielding_average_height_m=(
                    round(average_height, 4) if average_height is not None else None
                ),
                shielding_average_breadth_m=(
                    round(average_breadth, 4) if average_breadth is not None else None
                ),
                shielding_building_ids=included_ids,
                shielding_reason=shielding_reason,
                topographic_multiplier=round(mt, 6),
                topographic_feature_height_m=(
                    round(float(topography["height"]), 4)
                    if topography["height"] is not None
                    else None
                ),
                topographic_crest_distance_m=(
                    round(float(topography["crest"]), 4)
                    if topography["crest"] is not None
                    else None
                ),
                topographic_lu_m=(
                    round(float(topography["lu"]), 4)
                    if topography["lu"] is not None
                    else None
                ),
                topographic_l1_m=(
                    round(float(topography["l1"]), 4)
                    if topography["l1"] is not None
                    else None
                ),
                topographic_l2_m=(
                    round(float(topography["l2"]), 4)
                    if topography["l2"] is not None
                    else None
                ),
                topographic_mh=round(float(topography["mh"]), 6),
                topographic_feature_type=(
                    str(topography["feature_type"])
                    if topography["feature_type"] is not None
                    else None
                ),
                topographic_cross_section_bearing_degrees=(
                    round(float(topography["bearing"]) % 360.0, 4)
                    if topography["bearing"] is not None
                    else None
                ),
                topographic_site_position=(
                    str(topography["site_position"])
                    if topography["site_position"] is not None
                    else None
                ),
                topographic_slope=(
                    round(float(topography["slope"]), 6)
                    if topography["slope"] is not None
                    else None
                ),
                topographic_crest_offset_m=(
                    round(float(topography["crest_offset"]), 4)
                    if topography["crest_offset"] is not None
                    else None
                ),
                topographic_crest_elevation_m=(
                    round(float(topography["crest_elevation"]), 4)
                    if topography["crest_elevation"] is not None
                    else None
                ),
                topographic_base_elevation_m=(
                    round(float(topography["base_elevation"]), 4)
                    if topography["base_elevation"] is not None
                    else None
                ),
                topographic_half_height_distance_m=(
                    round(float(topography["half_height_distance"]), 4)
                    if topography["half_height_distance"] is not None
                    else None
                ),
                topographic_threshold_m=round(min(0.4 * reference_height_m, 5.0), 4),
                topographic_candidate_count=int(topography["candidate_count"]),
                topographic_search_radius_m=TOPOGRAPHIC_SEARCH_RADIUS_M,
                topographic_search_complete=bool(topography["search_complete"]),
                topographic_profile_distances_m=[
                    round(float(value), 3) for value in topography["profile_distances"]
                ],
                topographic_profile_elevations_m=[
                    None if value is None else round(float(value), 3)
                    for value in topography["profile_elevations"]
                ],
                topographic_reason=str(topography["reason"]),
            )

        result = LocalDirectionalWindEvidence(
            evidence_id=evidence_id,
            latitude=latitude,
            longitude=longitude,
            placement_latitude=placement_latitude,
            placement_longitude=placement_longitude,
            terrain_evidence_id=terrain_evidence_id,
            topographic_terrain_evidence_id=topographic_evidence_id,
            building_evidence_id=building_evidence.evidence_id,
            wind_region=wind_region.upper(),
            terrain_reference_height_m=reference_height_m,
            footprint_length_m=footprint_length_m,
            footprint_width_m=footprint_width_m,
            front_bearing_degrees=front_bearing_degrees,
            terrain_height_multipliers=CardinalMultiplierValues(**terrain_values),
            shielding_multipliers=CardinalMultiplierValues(**shielding_values),
            topographic_multipliers=CardinalMultiplierValues(**topographic_values),
            directions=assessments,
            dataset_version=(
                f"{ALGORITHM_VERSION}; terrain={terrain_evidence_id}; "
                f"topography={topographic_evidence_id}; "
                f"buildings={building_evidence.dataset_version}; ga={ga.dataset_version}"
            ),
        )
        temporary = target.with_suffix(".tmp")
        temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)
        return result
