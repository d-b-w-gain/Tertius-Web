from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy
import rasterio
from rasterio.warp import transform

from .models import (
    CardinalTerrainProfileEvidence,
    DirectionalTerrainProfile,
)
if TYPE_CHECKING:
    from .store import EvidenceStore


EARTH_RADIUS_M = 6_371_008.8
CARDINAL_BEARINGS = {
    "n": 0,
    "ne": 45,
    "e": 90,
    "se": 135,
    "s": 180,
    "sw": 225,
    "w": 270,
    "nw": 315,
}


@dataclass(frozen=True)
class TopographicTransect:
    """Two-sided terrain section through the site for one wind bearing."""

    direction: str
    bearing_degrees: float
    distances_m: tuple[float, ...]
    elevations_m: tuple[float | None, ...]
    site_elevation_m: float


def _destination(
    latitude: float,
    longitude: float,
    bearing_degrees: float,
    distance_m: float,
) -> tuple[float, float]:
    angular_distance = distance_m / EARTH_RADIUS_M
    latitude_radians = math.radians(latitude)
    longitude_radians = math.radians(longitude)
    bearing = math.radians(bearing_degrees)
    result_latitude = math.asin(
        math.sin(latitude_radians) * math.cos(angular_distance)
        + math.cos(latitude_radians)
        * math.sin(angular_distance)
        * math.cos(bearing)
    )
    result_longitude = longitude_radians + math.atan2(
        math.sin(bearing)
        * math.sin(angular_distance)
        * math.cos(latitude_radians),
        math.cos(angular_distance)
        - math.sin(latitude_radians) * math.sin(result_latitude),
    )
    return math.degrees(result_latitude), math.degrees(result_longitude)


class TerrainProfileSampler:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def sample(
        self,
        evidence_id: str,
        latitude: float,
        longitude: float,
        distance_m: float,
        sample_interval_m: float,
    ) -> CardinalTerrainProfileEvidence:
        path = self.store.asset_path(evidence_id)
        sample_count = max(2, math.ceil(distance_m / sample_interval_m) + 1)
        distances = numpy.linspace(0.0, distance_m, sample_count).tolist()
        profiles: dict[str, DirectionalTerrainProfile] = {}
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                raise ValueError("terrain evidence has no coordinate system")
            for direction, bearing in CARDINAL_BEARINGS.items():
                geographic_points = [
                    _destination(latitude, longitude, bearing, distance)
                    for distance in distances
                ]
                longitudes = [point[1] for point in geographic_points]
                latitudes = [point[0] for point in geographic_points]
                xs, ys = transform("EPSG:4326", dataset.crs, longitudes, latitudes)
                elevations: list[float | None] = []
                for value in dataset.sample(zip(xs, ys), indexes=1, masked=True):
                    sample = value[0]
                    elevations.append(
                        None
                        if numpy.ma.is_masked(sample)
                        or not math.isfinite(float(sample))
                        else float(sample)
                    )
                valid = [
                    (sample_distance, elevation)
                    for sample_distance, elevation in zip(distances, elevations)
                    if elevation is not None
                ]
                if not valid or elevations[0] is None:
                    raise ValueError(
                        f"terrain evidence does not cover the {direction.upper()} profile"
                    )
                maximum_distance, maximum = max(valid, key=lambda value: value[1])
                minimum = min(value[1] for value in valid)
                profiles[direction] = DirectionalTerrainProfile(
                    direction=direction,
                    bearing_degrees=bearing,
                    distances_m=distances,
                    elevations_m=elevations,
                    site_elevation_m=float(elevations[0]),
                    minimum_elevation_m=minimum,
                    maximum_elevation_m=maximum,
                    maximum_elevation_distance_m=maximum_distance,
                    endpoint_elevation_m=elevations[-1],
                )
        return CardinalTerrainProfileEvidence(
            evidence_id=evidence_id,
            latitude=latitude,
            longitude=longitude,
            distance_m=distance_m,
            sample_interval_m=distance_m / (sample_count - 1),
            profiles=profiles,
        )

    def sample_topographic_sectors(
        self,
        evidence_id: str,
        latitude: float,
        longitude: float,
        distance_m: float,
        sample_interval_m: float,
        angular_interval_degrees: float = 2.5,
    ) -> dict[str, list[TopographicTransect]]:
        """Sample two-sided sections across every +/-22.5 degree cardinal sector."""

        if angular_interval_degrees <= 0 or angular_interval_degrees > 22.5:
            raise ValueError("topographic angular interval must be within (0, 22.5]")
        path = self.store.asset_path(evidence_id)
        half_count = max(1, math.ceil(distance_m / sample_interval_m))
        distances = numpy.linspace(
            -distance_m,
            distance_m,
            2 * half_count + 1,
        ).tolist()
        sector_steps = max(1, math.ceil(45.0 / angular_interval_degrees))
        sampled: dict[float, tuple[float | None, ...]] = {}
        sectors: dict[str, list[TopographicTransect]] = {}

        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                raise ValueError("terrain evidence has no coordinate system")
            for direction, cardinal_bearing in CARDINAL_BEARINGS.items():
                transects: list[TopographicTransect] = []
                for step in range(sector_steps + 1):
                    bearing = (
                        cardinal_bearing
                        - 22.5
                        + 45.0 * step / sector_steps
                    ) % 360.0
                    cache_key = round(bearing, 8)
                    elevations = sampled.get(cache_key)
                    if elevations is None:
                        geographic_points = [
                            _destination(
                                latitude,
                                longitude,
                                bearing if distance >= 0 else bearing + 180.0,
                                abs(distance),
                            )
                            for distance in distances
                        ]
                        longitudes = [point[1] for point in geographic_points]
                        latitudes = [point[0] for point in geographic_points]
                        xs, ys = transform(
                            "EPSG:4326", dataset.crs, longitudes, latitudes
                        )
                        values: list[float | None] = []
                        for value in dataset.sample(zip(xs, ys), indexes=1, masked=True):
                            sample = value[0]
                            values.append(
                                None
                                if numpy.ma.is_masked(sample)
                                or not math.isfinite(float(sample))
                                else float(sample)
                            )
                        elevations = tuple(values)
                        sampled[cache_key] = elevations
                    site_index = len(distances) // 2
                    site_elevation = elevations[site_index]
                    if site_elevation is None:
                        raise ValueError(
                            f"terrain evidence does not cover the {direction.upper()} site point"
                        )
                    transects.append(
                        TopographicTransect(
                            direction=direction,
                            bearing_degrees=bearing,
                            distances_m=tuple(float(value) for value in distances),
                            elevations_m=elevations,
                            site_elevation_m=float(site_elevation),
                        )
                    )
                sectors[direction] = transects
        return sectors
