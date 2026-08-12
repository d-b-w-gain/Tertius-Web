from datetime import UTC, datetime

import httpx
import pytest

from core.site_report_spatial import fetch_site_report_spatial_context


@pytest.mark.parametrize(
    "supplied_terrain_evidence",
    ["gisv1-450fede237748adb380d7dc0a040903b", None],
)
def test_fetches_independent_spatial_report_evidence(supplied_terrain_evidence):
    requested_paths: list[str] = []

    def response(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requested_paths.append(path)
        if path.endswith(
            "/v1/evidence/gisv1-450fede237748adb380d7dc0a040903b"
        ):
            return httpx.Response(
                200,
                json={
                    "evidence_id": "gisv1-450fede237748adb380d7dc0a040903b",
                    "created_at": "2026-08-09T04:22:38Z",
                    "source": {"provider": "NSW Spatial Services"},
                },
            )
        if path.endswith("/v1/terrain/site"):
            return httpx.Response(
                200,
                json={
                    "evidence_id": "gisv1-450fede237748adb380d7dc0a040903b",
                    "created_at": "2026-08-09T04:22:38Z",
                    "source": {"provider": "NSW Spatial Services"},
                },
            )
        if path.endswith("/v1/raster/statistics"):
            return httpx.Response(
                200,
                json={
                    "b1": {
                        "min": -0.4,
                        "max": 121.7,
                        "percentile_2": -0.1,
                        "percentile_98": 78.0,
                    }
                },
            )
        if path.endswith("/v1/raster/preview.png"):
            assert request.url.params["rescale"] == "-0.1,78.0"
            assert request.url.params["colormap_name"] == "terrain"
            return httpx.Response(200, content=b"terrain-png")
        if path.endswith("/terrain-profiles/cardinal"):
            profiles = {
                direction: {
                    "direction": direction,
                    "bearing_degrees": bearing,
                    "distances_m": [0.0, 500.0],
                    "elevations_m": [5.0, endpoint],
                    "site_elevation_m": 5.0,
                    "minimum_elevation_m": min(5.0, endpoint),
                    "maximum_elevation_m": max(5.0, endpoint),
                    "maximum_elevation_distance_m": (
                        500.0 if endpoint > 5.0 else 0.0
                    ),
                    "endpoint_elevation_m": endpoint,
                }
                for direction, bearing, endpoint in (
                    ("n", 0, 18.0),
                    ("e", 90, 4.0),
                    ("s", 180, 3.0),
                    ("w", 270, 30.0),
                )
            }
            return httpx.Response(
                200,
                json={
                    "evidence_id": "gisv1-450fede237748adb380d7dc0a040903b",
                    "latitude": -34.4116,
                    "longitude": 150.8909,
                    "distance_m": 500.0,
                    "sample_interval_m": 10.0,
                    "profiles": profiles,
                },
            )
        if path.endswith("/v1/wind-multipliers/site"):
            return httpx.Response(
                200,
                json={
                    "terrain_height_multipliers": {"n": 0.8},
                    "topographic_multipliers": {"n": 1.0},
                },
            )
        if path.endswith("/v1/cadastre/site"):
            return httpx.Response(
                200,
                json={
                    "evidence_id": "parcelv1-0123456789abcdef0123456789abcdef",
                    "provider": "NSW Spatial Services",
                    "feature": {
                        "properties": {"address": "14 PORTER STREET"},
                        "geometry": {"type": "Polygon", "coordinates": []},
                    },
                },
            )
        if path.endswith("/local-wind"):
            return httpx.Response(
                200,
                json={
                    "evidence_id": "windv1-0123456789abcdef0123456789abcdef",
                    "terrain_evidence_id": "gisv1-450fede237748adb380d7dc0a040903b",
                    "building_evidence_id": "buildingv1-0123456789abcdef0123456789abcdef",
                    "directions": {"n": {"topographic_multiplier": 1.0}},
                },
            )
        if path.endswith("/World_Imagery/MapServer/export"):
            return httpx.Response(
                200,
                json={
                    "href": "https://images.example/site.png",
                    "width": 1200,
                    "height": 800,
                    "scale": 1600,
                    "extent": {
                        "xmin": 150.88,
                        "ymin": -34.42,
                        "xmax": 150.90,
                        "ymax": -34.40,
                    },
                },
            )
        if request.url.host == "images.example":
            return httpx.Response(200, content=b"satellite-png")
        if path.endswith("/v1/buildings/site"):
            assert request.url.params["radius_m"] == "500.0"
            return httpx.Response(
                200,
                json={
                    "evidence_id": "buildingv1-0123456789abcdef0123456789abcdef",
                    "fetched_at": "2026-08-10T00:00:00Z",
                    "provider": "Microsoft",
                    "dataset": "Global ML Building Footprints",
                    "dataset_version": "2026-02-03",
                    "licence": "CDLA Permissive 2.0",
                    "attribution": "Microsoft Global ML Building Footprints",
                    "source_uri": "https://github.com/microsoft/GlobalMLBuildingFootprints",
                    "query_point": [150.8909, -34.4116],
                    "query_radius_m": 500.0,
                    "features": [{
            "source_id": "microsoft-42",
            "height_m": 6.2,
            "height_lower_m": None,
            "height_upper_m": None,
            "height_observations": [],
            "confidence": 0.91,
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [150.890, -34.412],
                                [150.891, -34.412],
                                [150.891, -34.411],
                                [150.890, -34.411],
                            ]],
                        },
                    }],
                    "footprint_count": 1,
                    "measured_height_count": 1,
                },
            )
        if path.endswith("/api/interpreter"):
            return httpx.Response(
                200,
                json={
                    "elements": [{
                        "id": 42,
                        "tags": {
                            "building:levels": "3",
                            "roof:shape": "gabled",
                        },
                        "geometry": [
                            {"lon": 150.890, "lat": -34.412},
                            {"lon": 150.891, "lat": -34.412},
                            {"lon": 150.891, "lat": -34.411},
                            {"lon": 150.890, "lat": -34.411},
                        ]
                    }]
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        context = fetch_site_report_spatial_context(
            latitude=-34.4116,
            longitude=150.8909,
            gis_cache_url="http://gis-cache.test",
            terrain_evidence_id=supplied_terrain_evidence,
            accessed_at=datetime(2026, 8, 9, 4, 29, tzinfo=UTC),
            client=client,
        )

    assert context["accessed_at_utc"] == "2026-08-09T04:29:00Z"
    assert context["terrain"]["display_range_m"] == [-0.1, 78.0]
    assert context["terrain"]["heatmap_png"] == b"terrain-png"
    assert context["terrain"]["query_radius_m"] == 2000
    assert context["wind_multipliers"]["topographic_multipliers"]["n"] == 1.0
    assert context["local_wind"]["directions"]["n"]["topographic_multiplier"] == 1.0
    assert context["site_boundary"]["feature"]["properties"]["address"] == "14 PORTER STREET"
    assert context["satellite"]["image_png"] == b"satellite-png"
    assert context["satellite"]["query_radius_m"] == 170
    assert len(context["buildings"]["footprints"]) == 1
    assert context["buildings"]["profiles"] == [{
        "source_id": "microsoft-42",
        "height_m": 6.2,
        "height_lower_m": None,
        "height_upper_m": None,
        "height_observations": [],
        "confidence": 0.91,
        "outline_source": None,
        "height_source": None,
        "levels": None,
        "roof_height_m": None,
        "roof_levels": None,
        "roof_shape": None,
    }]
    assert context["buildings"]["profile_summary"] == {
        "measured_height_count": 1,
        "level_count": 0,
        "roof_height_count": 0,
        "roof_shape_count": 0,
        "height_coverage_ratio": 0,
        "height_source_counts": {},
        "height_observation_count": 0,
        "height_method_counts": {},
        "source_counts": {},
    }
    assert context["buildings"]["dataset_version"] == "2026-02-03"
    assert context["buildings"]["query_radius_m"] == 500
    assert context["terrain"]["cardinal_profiles"]["profiles"]["n"]["endpoint_elevation_m"] == 18.0
    assert context["warnings"] == []
    assert any(path.endswith("/local-wind") for path in requested_paths)
    assert any(path.endswith("/v1/terrain/site") for path in requested_paths) is (
        supplied_terrain_evidence is None
    )
