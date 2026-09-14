from datetime import UTC, datetime

from core.site_definition import (
    SiteDefinition,
    calculate_site_definition,
    default_site_definition,
)
from core.site_wind_report import (
    DIRECTION_TO_FACE_SOURCE_IMAGE,
    REGIONAL_DIRECTION_SOURCE_IMAGE,
    TABLE_3_3_SOURCE_IMAGE,
    TERRAIN_AVERAGING_SOURCE_IMAGE,
    TERRAIN_HEIGHT_SOURCE_IMAGE,
    WIND_REGIONS_SOURCE_IMAGE,
    build_site_wind_report,
)
from core.structural.wind_standard_tables import site_report_evidence


DIRECTIONS = ("n", "ne", "e", "se", "s", "sw", "w", "nw")


def _report_site() -> SiteDefinition:
    payload = default_site_definition().model_dump(mode="json")
    payload["location"]["address"] = "14 PORTER STREET, NORTH WOLLONGONG NSW 2500"
    payload["structure"]["front_bearing_degrees"] = 98.0
    payload["wind"]["cardinal_direction_multipliers"] = {
        "n": 0.85,
        "ne": 0.75,
        "e": 0.85,
        "se": 0.95,
        "s": 0.95,
        "sw": 0.95,
        "w": 1.0,
        "nw": 0.95,
    }
    payload["wind"]["cardinal_shielding_multipliers"] = {
        "n": 0.79585713,
        "ne": 0.765,
        "e": 0.765,
        "se": 0.765,
        "s": 0.765,
        "sw": 0.8281,
        "w": 0.84377354,
        "nw": 0.783,
    }
    payload["wind"]["cardinal_topographic_multipliers"] = {
        direction: 1.0 for direction in DIRECTIONS
    }
    payload["wind"]["multiplier_evidence"] = {
        "evidence_id": "windv1-686a4f05e9c5b3866cedab19893f7bbc",
        "provider": "Geoscience Australia",
        "dataset": "National wind multiplier dataset",
        "dataset_version": "Wind Multiplier Software 2.0 output (January 2016)",
        "source_uri": (
            "https://thredds.nci.org.au/thredds/catalog/fj6/multipliers/catalog.html"
        ),
        "site_latitude": payload["location"]["latitude"],
        "site_longitude": payload["location"]["longitude"],
        "terrain_reference_height_m": 10.0,
        "method_status": "indicative_hazard_evidence",
        "adopted_components": ["M_s", "M_t"],
        "review_status": "suggested",
        "review_reason": "",
    }
    return SiteDefinition.model_validate(payload)


def _spatial_context(site: SiteDefinition) -> dict:
    latitude = site.location.latitude
    longitude = site.location.longitude
    image = TABLE_3_3_SOURCE_IMAGE.read_bytes()
    directions = {
        "n": 0.7636886,
        "ne": 0.7278838,
        "e": 0.7461655,
        "se": 0.7447497,
        "s": 0.7450783,
        "sw": 0.7811401,
        "w": 0.75135756,
        "nw": 0.83116555,
    }
    north_footprint = [
        [longitude - 0.00004, latitude + 0.00008],
        [longitude + 0.00004, latitude + 0.00008],
        [longitude + 0.00004, latitude + 0.00014],
        [longitude - 0.00004, latitude + 0.00014],
        [longitude - 0.00004, latitude + 0.00008],
    ]
    local_directions = {
        direction: {
            "direction": direction,
            "bearing_degrees": bearing,
            "terrain_category": 3.0,
            "terrain_height_multiplier": 0.83,
            "terrain_building_fraction": 0.18,
            "terrain_buildings_per_hectare": 7.5,
            "shielding_multiplier": 0.8 if direction == "n" else 0.9,
            "ga_shielding_multiplier_2016": 0.9,
            "local_shielding_multiplier": 0.8 if direction == "n" else None,
            "shielding_basis": "local_improvement"
            if direction == "n"
            else "ga_2016_baseline",
            "shielding_parameter": 3.0 if direction == "n" else None,
            "shielding_building_count": 1 if direction == "n" else 0,
            "shielding_candidate_count": 1 if direction == "n" else 0,
            "shielding_height_coverage": 1.0 if direction == "n" else 0.0,
            "shielding_height_decision_coverage": 1.0 if direction == "n" else 0.0,
            "shielding_definitely_eligible_count": 1 if direction == "n" else 0,
            "shielding_definitely_ineligible_count": 0,
            "shielding_uncertain_building_ids": [],
            "shielding_average_height_m": 6.2 if direction == "n" else None,
            "shielding_average_breadth_m": 8.0 if direction == "n" else None,
            "shielding_building_ids": ["microsoft-n"] if direction == "n" else [],
            "shielding_reason": (
                "One eligible height-known building gives s=3.00 and local Ms=0.800."
                if direction == "n"
                else "No footprint-qualified building occurs inside the current 20h sector."
            ),
            "topographic_multiplier": 1.0,
            "topographic_mh": 1.0,
            "topographic_feature_type": "none",
            "topographic_cross_section_bearing_degrees": float(bearing),
            "topographic_site_position": None,
            "topographic_slope": 0.02,
            "topographic_feature_height_m": 2.0,
            "topographic_crest_distance_m": 30.0,
            "topographic_crest_offset_m": -30.0,
            "topographic_crest_elevation_m": 7.0,
            "topographic_base_elevation_m": 5.0,
            "topographic_half_height_distance_m": 20.0,
            "topographic_threshold_m": 1.172,
            "topographic_lu_m": 20.0,
            "topographic_l1_m": 8.0,
            "topographic_l2_m": 32.0,
            "topographic_candidate_count": 1,
            "topographic_search_radius_m": 5000.0,
            "topographic_search_complete": True,
            "topographic_profile_distances_m": [-500.0, -250.0, 0.0, 250.0, 500.0],
            "topographic_profile_elevations_m": [8.0, 6.0, 5.0, 4.0, 3.0],
            "topographic_standard_basis": "AS/NZS 1170.2:2021 Amd 2 Clause 4.4.2",
        }
        for direction, bearing in (
            ("n", 0),
            ("ne", 45),
            ("e", 90),
            ("se", 135),
            ("s", 180),
            ("sw", 225),
            ("w", 270),
            ("nw", 315),
        )
    }
    return {
        "accessed_at_utc": "2026-08-09T04:29:00Z",
        "satellite": {
            "image_png": image,
            "extent": [
                longitude - 0.002,
                latitude - 0.002,
                longitude + 0.002,
                latitude + 0.002,
            ],
            "source": "Imagery (c) Esri and contributors",
            "query_radius_m": 170.0,
        },
        "buildings": {
            "footprints": [north_footprint],
            "profiles": [
                {
                    "source_id": "microsoft-n",
                    "height_m": 6.2,
                    "height_lower_m": 5.2,
                    "height_upper_m": 7.2,
                    "confidence": 0.91,
                }
            ],
            "profile_summary": {
                "measured_height_count": 1,
                "level_count": 0,
                "roof_height_count": 0,
                "roof_shape_count": 0,
            },
            "source": "Microsoft Global ML Building Footprints",
            "dataset_version": "2026-02-03",
            "evidence_id": "buildingv1-0123456789abcdef0123456789abcdef",
            "query_radius_m": 220.0,
        },
        "terrain": {
            "heatmap_png": image,
            "display_range_m": [0.0, 80.0],
            "statistics": {"min": -0.4, "max": 121.7},
            "manifest": {
                "evidence_id": "gisv1-450fede237748adb380d7dc0a040903b",
                "created_at": "2026-08-09T04:22:38Z",
                "source": {
                    "provider": "NSW Spatial Services",
                    "dataset": "NSW 5 metre Digital Elevation Model",
                    "dataset_version": "Wollongong-DEM-AHD_56_5m",
                },
                "asset": {"resolution": [5.0, 5.0]},
            },
            "cardinal_profiles": {
                "evidence_id": "gisv1-450fede237748adb380d7dc0a040903b",
                "latitude": latitude,
                "longitude": longitude,
                "distance_m": 500.0,
                "sample_interval_m": 10.0,
                "profiles": {
                    direction: {
                        "direction": direction,
                        "bearing_degrees": bearing,
                        "distances_m": [0.0, 250.0, 500.0],
                        "elevations_m": [5.0, middle, endpoint],
                        "site_elevation_m": 5.0,
                        "minimum_elevation_m": min(5.0, middle, endpoint),
                        "maximum_elevation_m": max(5.0, middle, endpoint),
                        "maximum_elevation_distance_m": (
                            [5.0, middle, endpoint].index(max(5.0, middle, endpoint))
                            * 250.0
                        ),
                        "endpoint_elevation_m": endpoint,
                    }
                    for direction, bearing, middle, endpoint in (
                        ("n", 0, 12.0, 18.0),
                        ("e", 90, 4.0, 3.0),
                        ("s", 180, 5.0, 4.0),
                        ("w", 270, 20.0, 35.0),
                    )
                },
            },
        },
        "wind_multipliers": {
            "terrain_height_multipliers": directions,
            "topographic_multipliers": {direction: 1.0 for direction in DIRECTIONS},
        },
        "local_wind": {
            "evidence_id": "windv1-local-0123456789abcdef0123456789",
            "dataset_version": "tertius-local-wind-2021-screen-v1",
            "terrain_evidence_id": "gisv1-450fede237748adb380d7dc0a040903b",
            "building_evidence_id": "buildingv1-0123456789abcdef0123456789abcdef",
            "placement_latitude": latitude,
            "placement_longitude": longitude,
            "terrain_reference_height_m": float(site.wind.reference_height_m),
            "directions": local_directions,
        },
    }


def test_site_wind_report_contains_calculation_visuals_and_provenance():
    site = _report_site()
    site_payload = site.model_dump(mode="json")
    calculation = calculate_site_definition(site)
    evidence = site_report_evidence(site_payload, calculation)

    content = build_site_wind_report(
        project_name="structural-wind-faces-dev",
        site=site_payload,
        calculation=calculation,
        evidence=evidence,
        spatial_context=_spatial_context(site),
        generated_at=datetime(2026, 8, 9, 4, 30, tzinfo=UTC),
    )

    assert content.startswith(b"%PDF-")
    assert content.rstrip().endswith(b"%%EOF")
    assert content.count(b"/Type /Page") >= 13
    assert b"Site wind basis report" in content
    expected_speed = f"{calculation['site_wind_speed_m_s']:.3f}".encode()
    assert expected_speed in content
    assert b"Directional calculation ledger" in content
    assert b"Satellite placement and surrounding buildings" in content
    assert b"Directional shielding diagnostics 1/2" in content
    assert b"N shielding sector" in content
    assert b"Footprint candidates 1; height decisions 1/1" in content
    assert b"Colour shows calculation use, not footprint confidence" in content
    assert b"Local terrain measurements" in content
    assert b"Local shielding measurement schedule" in content
    assert b"GA 2016" in content
    assert b"GA baseline" in content
    assert b"Local improvement" in content
    assert b"Directional inclusion and conservative adoption reasons" in content
    assert b"Terrain heat map and directional-multiplier explanation" in content
    assert b"Displayed radius: 600 m" in content
    assert b"Source cache radius: 2 km" in content
    assert b"500 m terrain averaging limit" in content
    assert b"1 supplied height estimates; 0 floor counts; 0 roof shapes" in content
    assert b"Microsoft Global ML Building Footprints" in content
    assert b"N-NE-E-SE governing x-z topographic cross-sections" in content
    assert b"S-SW-W-NW governing x-z topographic cross-sections" in content
    assert b"Directional topographic calculation schedule" in content
    assert b"not a lee-shelter reduction zone" in content
    assert b"Australian wind-region profile" in content
    assert b"Regional speed and direction - source audit" in content
    assert b"Climate and terrain-height - source audit" in content
    assert b"Shielding and topographic multipliers - source audit" in content
    assert b"Directional site speed to building faces - source audit" in content
    assert b"Why directional Mz,cat differs" in content
    assert b"Geoscience Australia" in content
    assert b"windv1-686a4f05e9c5b3866cedab19893f7bbc" in content
    assert b"Original GA fetch time" in content
    assert b"terrain_category_height_table" in content
    assert b"Tertius-authored" in content
    assert WIND_REGIONS_SOURCE_IMAGE.is_file()
    assert REGIONAL_DIRECTION_SOURCE_IMAGE.is_file()
    assert TERRAIN_HEIGHT_SOURCE_IMAGE.is_file()
    assert TERRAIN_AVERAGING_SOURCE_IMAGE.is_file()
    assert DIRECTION_TO_FACE_SOURCE_IMAGE.is_file()


def test_site_wind_report_exposes_shielding_geometry_when_local_analysis_is_down():
    site = _report_site()
    site_payload = site.model_dump(mode="json")
    calculation = calculate_site_definition(site)
    spatial_context = _spatial_context(site)
    spatial_context["local_wind"] = None

    content = build_site_wind_report(
        project_name="structural-wind-faces-dev",
        site=site_payload,
        calculation=calculation,
        evidence=site_report_evidence(site_payload, calculation),
        spatial_context=spatial_context,
        generated_at=datetime(2026, 8, 9, 4, 30, tzinfo=UTC),
    )

    assert b"Directional shielding diagnostics 1/2" in content
    assert b"Footprint candidates 1; height decisions 1/1" in content
    assert content.count(b"/Type /Page") >= 15
