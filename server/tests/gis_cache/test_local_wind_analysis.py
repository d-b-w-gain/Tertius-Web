from datetime import UTC, datetime

import pytest

from server.gis_cache.local_wind_analysis import (
    LocalWindAnalyzer,
    _topographic_assessment,
)
from server.gis_cache.models import (
    BuildingEvidence,
    BuildingFeature,
    CardinalMultiplierValues,
    CardinalTerrainProfileEvidence,
    DirectionalTerrainProfile,
    DirectionalWindMultiplierEvidence,
)
from server.gis_cache.terrain_profiles import CARDINAL_BEARINGS, TopographicTransect


LATITUDE = -34.4
LONGITUDE = 150.9


def _profile(direction: str, bearing: int) -> DirectionalTerrainProfile:
    distances = [0.0, 30.0, 60.0, 100.0, 330.0, 500.0]
    elevations = (
        [5.0, 5.5, 6.0, 7.0, 15.5, 12.0]
        if direction == "n"
        else [5.0, 17.0, 11.0, 5.0, 4.0, 4.0]
        if direction == "s"
        else [5.0, 5.5, 5.2, 5.0, 4.5, 4.0]
    )
    maximum = max(elevations)
    return DirectionalTerrainProfile(
        direction=direction,
        bearing_degrees=bearing,
        distances_m=distances,
        elevations_m=elevations,
        site_elevation_m=elevations[0],
        minimum_elevation_m=min(elevations),
        maximum_elevation_m=maximum,
        maximum_elevation_distance_m=distances[elevations.index(maximum)],
        endpoint_elevation_m=elevations[-1],
    )


class FakeProfiles:
    def sample(self, evidence_id, latitude, longitude, distance_m, sample_interval_m):
        return CardinalTerrainProfileEvidence(
            evidence_id=evidence_id,
            latitude=latitude,
            longitude=longitude,
            distance_m=distance_m,
            sample_interval_m=sample_interval_m,
            profiles={
                direction: _profile(direction, bearing)
                for direction, bearing in CARDINAL_BEARINGS.items()
            },
        )

    def sample_topographic_sectors(
        self,
        evidence_id,
        latitude,
        longitude,
        distance_m,
        sample_interval_m,
        angular_interval_degrees,
    ):
        return {
            direction: [_topographic_transect(direction, bearing)]
            for direction, bearing in CARDINAL_BEARINGS.items()
        }


def _transect(
    direction: str,
    bearing: float,
    distances: list[float],
    elevations: list[float],
) -> TopographicTransect:
    return TopographicTransect(
        direction=direction,
        bearing_degrees=bearing,
        distances_m=tuple(distances),
        elevations_m=tuple(elevations),
        site_elevation_m=elevations[distances.index(0.0)],
    )


def _topographic_transect(direction: str, bearing: float) -> TopographicTransect:
    if direction == "n":
        return _transect(
            direction,
            bearing,
            [-500.0, -100.0, 0.0, 100.0, 250.0, 330.0, 400.0, 500.0],
            [4.0, 4.5, 5.0, 7.0, 12.0, 17.0, 9.0, 5.0],
        )
    if direction == "s":
        return _transect(
            direction,
            bearing,
            [-500.0, -100.0, 0.0, 10.0, 30.0, 60.0, 100.0, 500.0],
            [4.0, 4.0, 5.0, 17.0, 12.0, 7.0, 5.0, 5.0],
        )
    return _transect(
        direction,
        bearing,
        [-500.0, -100.0, 0.0, 100.0, 500.0],
        [5.0, 5.0, 5.0, 5.0, 5.0],
    )


def _building(source_id: str, north_m: float, height_m: float | None):
    latitude_delta = north_m / 111_320.0
    longitude_delta = 4.0 / (111_320.0 * 0.825)
    latitude0 = LATITUDE + latitude_delta
    return BuildingFeature(
        source_id=source_id,
        height_m=height_m,
        confidence=0.9,
        geometry={
            "type": "Polygon",
            "coordinates": [
                [
                    [LONGITUDE - longitude_delta, latitude0 - 0.000025],
                    [LONGITUDE + longitude_delta, latitude0 - 0.000025],
                    [LONGITUDE + longitude_delta, latitude0 + 0.000025],
                    [LONGITUDE - longitude_delta, latitude0 + 0.000025],
                    [LONGITUDE - longitude_delta, latitude0 - 0.000025],
                ]
            ],
        },
    )


def _building_xy(
    source_id: str,
    east_m: float,
    north_m: float,
    height_m: float | None,
) -> BuildingFeature:
    latitude_delta = north_m / 111_320.0
    longitude_delta = east_m / (111_320.0 * 0.825)
    half_width = 2.0 / (111_320.0 * 0.825)
    half_depth = 2.0 / 111_320.0
    centre_longitude = LONGITUDE + longitude_delta
    centre_latitude = LATITUDE + latitude_delta
    return BuildingFeature(
        source_id=source_id,
        height_m=height_m,
        confidence=0.9,
        geometry={
            "type": "Polygon",
            "coordinates": [
                [
                    [centre_longitude - half_width, centre_latitude - half_depth],
                    [centre_longitude + half_width, centre_latitude - half_depth],
                    [centre_longitude + half_width, centre_latitude + half_depth],
                    [centre_longitude - half_width, centre_latitude + half_depth],
                    [centre_longitude - half_width, centre_latitude - half_depth],
                ]
            ],
        },
    )


class FakeBuildings:
    def fetch(self, latitude, longitude, radius_m):
        features = [_building("north-1", 20, 6), _building("north-2", 45, 7)]
        return BuildingEvidence(
            evidence_id="buildingv1-0123456789abcdef0123456789abcdef",
            fetched_at=datetime.now(UTC),
            dataset_version="fixture",
            source_uri="https://example.com/buildings",
            query_point=(longitude, latitude),
            query_radius_m=radius_m,
            features=features,
            footprint_count=len(features),
            measured_height_count=len(features),
        )


class FakeGa:
    def __init__(self):
        self.calls = []

    def fetch(self, latitude, longitude):
        self.calls.append((latitude, longitude))
        values = CardinalMultiplierValues(
            **{direction: 0.83 for direction in CARDINAL_BEARINGS}
        )
        shielding = CardinalMultiplierValues(
            **{direction: 0.9 for direction in CARDINAL_BEARINGS}
        )
        topographic = CardinalMultiplierValues(
            **{direction: 1.0 for direction in CARDINAL_BEARINGS}
        )
        return DirectionalWindMultiplierEvidence(
            evidence_id="windv1-0123456789abcdef0123456789abcdef",
            latitude=latitude,
            longitude=longitude,
            tile_id="e150.3512s33.9946",
            terrain_height_multipliers=values,
            shielding_multipliers=shielding,
            topographic_multipliers=topographic,
        )


def test_local_analysis_uses_amended_two_sided_sector_topography(tmp_path):
    ga = FakeGa()
    analyzer = LocalWindAnalyzer(tmp_path, FakeProfiles(), FakeBuildings(), ga)

    evidence = analyzer.analyze(
        "gisv1-0123456789abcdef0123456789abcdef",
        LATITUDE + 0.000001,
        LONGITUDE,
        LATITUDE,
        LONGITUDE,
        3.0,
        5.0,
        3.0,
        0.0,
        "A2",
    )

    assert set(evidence.directions) == set(CARDINAL_BEARINGS)
    assert evidence.directions["n"].topographic_multiplier == pytest.approx(1.0)
    assert "5.0 km" in evidence.directions["n"].topographic_reason
    assert evidence.directions["s"].topographic_multiplier > 1.0
    assert evidence.directions["s"].topographic_threshold_m == pytest.approx(1.2)
    assert evidence.directions["s"].topographic_crest_distance_m == pytest.approx(10.0)
    assert evidence.directions["n"].shielding_building_count == 2
    assert evidence.directions["n"].shielding_multiplier < 0.9
    assert evidence.directions["n"].local_shielding_multiplier == pytest.approx(
        evidence.directions["n"].shielding_multiplier
    )
    assert evidence.directions["n"].shielding_basis == "local_improvement"
    assert (
        "Table 4.2 uses 2 current building(s)"
        in evidence.directions["n"].shielding_reason
    )
    assert evidence.directions["n"].ga_shielding_multiplier_2016 == pytest.approx(0.9)
    assert evidence.directions["s"].shielding_multiplier == pytest.approx(0.9)
    assert evidence.directions["s"].local_shielding_multiplier is None
    assert evidence.directions["s"].shielding_basis == "ga_2016_baseline"
    assert (
        "missing reconstruction evidence cannot worsen"
        in evidence.directions["s"].shielding_reason
    )
    assert evidence.terrain_height_multipliers.n == pytest.approx(0.97)
    assert ga.calls == [(LATITUDE + 0.000001, LONGITUDE)]

    cached = analyzer.analyze(
        "gisv1-0123456789abcdef0123456789abcdef",
        LATITUDE + 0.000001,
        LONGITUDE,
        LATITUDE,
        LONGITUDE,
        3.0,
        5.0,
        3.0,
        0.0,
        "A2",
    )
    assert cached.evidence_id == evidence.evidence_id
    assert ga.calls == [
        (LATITUDE + 0.000001, LONGITUDE),
        (LATITUDE + 0.000001, LONGITUDE),
    ]


def test_topography_uses_most_adverse_cross_section_inside_cardinal_sector():
    flat = _transect(
        "n",
        0.0,
        [-200.0, -100.0, 0.0, 100.0, 200.0],
        [5.0, 5.0, 5.0, 5.0, 5.0],
    )
    off_axis = _transect(
        "n",
        10.0,
        [-200.0, -100.0, 0.0, 10.0, 30.0, 60.0, 100.0, 200.0],
        [4.0, 4.0, 5.0, 17.0, 12.0, 7.0, 5.0, 5.0],
    )

    mt, evidence = _topographic_assessment(
        [flat, off_axis],
        3.0,
        1.0,
        "A2",
        200.0,
    )

    assert mt > 1.0
    assert evidence["bearing"] == pytest.approx(10.0)
    assert evidence["crest"] == pytest.approx(10.0)


def test_topography_uses_long_downwind_escarpment_zone():
    escarpment = _transect(
        "w",
        270.0,
        [-200.0, -100.0, 0.0, 40.0, 60.0, 80.0, 100.0, 150.0, 200.0],
        [20.0, 20.0, 20.0, 20.0, 20.0, 10.0, 0.0, 0.0, 0.0],
    )

    mt, evidence = _topographic_assessment(
        [escarpment],
        3.0,
        1.0,
        "A2",
        200.0,
    )

    assert str(evidence["feature_type"]).endswith("escarpment")
    assert evidence["site_position"] == "downwind"
    assert evidence["l2"] == pytest.approx(80.0)
    assert evidence["crest"] == pytest.approx(60.0)
    assert mt > 1.0


def test_topography_does_not_amplify_a_gentle_feature():
    gentle = _transect(
        "n",
        0.0,
        [-500.0, -300.0, -100.0, 0.0, 100.0, 250.0, 400.0, 500.0],
        [4.0, 4.0, 5.0, 6.0, 10.0, 7.0, 4.0, 4.0],
    )

    mt, evidence = _topographic_assessment(
        [gentle],
        3.0,
        1.0,
        "A2",
        500.0,
    )

    assert evidence["slope"] == pytest.approx(0.02)
    assert evidence["equation"] == "gentle_slope"
    assert evidence["mh"] == pytest.approx(1.0)
    assert mt == pytest.approx(1.0)


def test_amendment_two_screen_retains_a_low_but_significant_feature():
    low_feature = _transect(
        "n",
        0.0,
        [-100.0, -50.0, 0.0, 10.0, 20.0, 30.0, 50.0, 100.0],
        [0.0, 0.0, 0.0, 2.0, 1.0, 0.0, 0.0, 0.0],
    )

    mt, evidence = _topographic_assessment(
        [low_feature],
        3.0,
        1.0,
        "A2",
        100.0,
    )

    assert evidence["height"] == pytest.approx(2.0)
    assert evidence["height"] > min(0.4 * 3.0, 5.0)
    assert evidence["equation"] == "4.4(3)"
    assert mt > 1.0


def test_incomplete_local_heights_ignore_unknown_candidate_but_use_definite_shielder(
    tmp_path,
):
    class IncompleteBuildings(FakeBuildings):
        def fetch(self, latitude, longitude, radius_m):
            evidence = super().fetch(latitude, longitude, radius_m)
            evidence.features[1].height_m = None
            evidence.measured_height_count = 1
            evidence.evidence_id = "buildingv1-fedcba9876543210fedcba9876543210"
            return evidence

    analyzer = LocalWindAnalyzer(
        tmp_path,
        FakeProfiles(),
        IncompleteBuildings(),
        FakeGa(),
    )

    evidence = analyzer.analyze(
        "gisv1-0123456789abcdef0123456789abcdef",
        LATITUDE,
        LONGITUDE,
        LATITUDE,
        LONGITUDE,
        3.0,
        5.0,
        3.0,
        0.0,
        "A2",
    )

    north = evidence.directions["n"]
    assert north.shielding_height_coverage == pytest.approx(0.5)
    assert north.shielding_building_count == 1
    assert north.shielding_building_ids == ["north-1"]
    assert north.shielding_multiplier == pytest.approx(0.9)
    assert north.local_shielding_multiplier is not None
    assert 0.9 < north.local_shielding_multiplier < 1.0
    assert north.shielding_basis == "ga_2016_baseline"
    assert (
        "1 uncertain candidate(s) receive no additional local credit"
        in north.shielding_reason
    )
    assert "not an improvement" in north.shielding_reason
    assert north.ga_shielding_multiplier_2016 == pytest.approx(0.9)


def test_shielding_counts_footprint_crossing_sector_boundary(tmp_path):
    class BoundaryBuildings(FakeBuildings):
        def fetch(self, latitude, longitude, radius_m):
            # Centroid bearing is outside the north sector, while part of the 4 m
            # footprint crosses the 22.5 degree boundary.
            feature = _building_xy("north-boundary", 10.0, 22.0, 6.0)
            return BuildingEvidence(
                evidence_id="buildingv1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                fetched_at=datetime.now(UTC),
                dataset_version="fixture-boundary",
                source_uri="https://example.com/buildings",
                query_point=(longitude, latitude),
                query_radius_m=radius_m,
                features=[feature],
                footprint_count=1,
                measured_height_count=1,
            )

    analyzer = LocalWindAnalyzer(
        tmp_path,
        FakeProfiles(),
        BoundaryBuildings(),
        FakeGa(),
    )
    evidence = analyzer.analyze(
        "gisv1-0123456789abcdef0123456789abcdef",
        LATITUDE,
        LONGITUDE,
        LATITUDE,
        LONGITUDE,
        3.0,
        5.0,
        3.0,
        0.0,
        "A2",
    )

    north = evidence.directions["n"]
    assert north.shielding_building_count == 1
    assert north.shielding_building_ids == ["north-boundary"]


def test_shielding_uses_definite_lower_bound_without_uncertain_candidate(tmp_path):
    class IntervalBuildings(FakeBuildings):
        def fetch(self, latitude, longitude, radius_m):
            evidence = super().fetch(latitude, longitude, radius_m)
            evidence.features[0].height_lower_m = 4.5
            evidence.features[0].height_upper_m = 7.0
            evidence.features[1].height_lower_m = 2.5
            evidence.features[1].height_upper_m = 7.5
            evidence.evidence_id = "buildingv1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            return evidence

    analyzer = LocalWindAnalyzer(
        tmp_path,
        FakeProfiles(),
        IntervalBuildings(),
        FakeGa(),
    )
    evidence = analyzer.analyze(
        "gisv1-0123456789abcdef0123456789abcdef",
        LATITUDE,
        LONGITUDE,
        LATITUDE,
        LONGITUDE,
        3.0,
        5.0,
        3.0,
        0.0,
        "A2",
    )

    north = evidence.directions["n"]
    assert north.shielding_candidate_count == 2
    assert north.shielding_height_decision_coverage == pytest.approx(0.5)
    assert north.shielding_definitely_eligible_count == 1
    assert north.shielding_uncertain_building_ids == ["north-2"]
    assert north.shielding_building_count == 1
    assert north.shielding_building_ids == ["north-1"]
    assert north.shielding_parameter == pytest.approx(7.5, abs=0.02)
    assert north.shielding_multiplier == pytest.approx(0.9)
    assert north.local_shielding_multiplier == pytest.approx(0.925, abs=0.001)
    assert north.shielding_basis == "ga_2016_baseline"
    assert (
        "1 uncertain candidate(s) receive no additional local credit"
        in north.shielding_reason
    )


def test_shielding_with_only_uncertain_heights_retains_ga_baseline(tmp_path):
    class UncertainBuildings(FakeBuildings):
        def fetch(self, latitude, longitude, radius_m):
            evidence = super().fetch(latitude, longitude, radius_m)
            for feature in evidence.features:
                feature.height_lower_m = 2.5
                feature.height_upper_m = 7.5
            evidence.evidence_id = "buildingv1-cccccccccccccccccccccccccccccccc"
            return evidence

    analyzer = LocalWindAnalyzer(
        tmp_path,
        FakeProfiles(),
        UncertainBuildings(),
        FakeGa(),
    )
    evidence = analyzer.analyze(
        "gisv1-0123456789abcdef0123456789abcdef",
        LATITUDE,
        LONGITUDE,
        LATITUDE,
        LONGITUDE,
        3.0,
        5.0,
        3.0,
        0.0,
        "A2",
    )

    north = evidence.directions["n"]
    assert north.shielding_candidate_count == 2
    assert north.shielding_building_count == 0
    assert north.shielding_parameter is None
    assert north.shielding_multiplier == pytest.approx(0.9)
    assert north.local_shielding_multiplier is None
    assert north.shielding_basis == "ga_2016_baseline"
    assert "2 candidate(s) remain uncertain" in north.shielding_reason
    assert "cannot worsen the established baseline" in north.shielding_reason
    assert north.ga_shielding_multiplier_2016 == pytest.approx(0.9)
