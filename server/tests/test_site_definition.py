from copy import deepcopy

import pytest

from core.compile_runtime import (
    runtime_files_hash,
    structural_runtime_files_hash,
)
from core.site_definition import (
    SiteCardinalDirectionMultipliers,
    SiteDefinition,
    SiteDefinitionError,
    SiteTerrainEvidenceReference,
    SiteWindMultiplierEvidenceReference,
    apply_site_definition,
    calculate_site_definition,
    default_site_definition,
    parse_site_definition,
    render_site_definition,
    validate_design_site_usage,
)
from core.structural.site_wind import SiteWindError, TABLE_VERSION


def verified_site():
    site = default_site_definition()
    site.location.address = "14 Porter St, North Wollongong, NSW, 2500"
    site.wind.region = "A2"
    site.wind.region_status = "verified"
    site.wind.table_status = "verified"
    site.wind.cardinal_direction_multipliers = SiteCardinalDirectionMultipliers(**{
        "n": 0.90,
        "ne": 0.85,
        "e": 0.80,
        "se": 0.85,
        "s": 0.95,
        "sw": 1.00,
        "w": 0.90,
        "nw": 0.85,
    })
    site.wind.reference_height_m = 1.6
    site.structure.front_bearing_degrees = 20.0
    site.structure.orientation_status = "verified"
    site.project_basis.standards.confirmed = True
    site.project_basis.standards.combinations = "AS/NZS 1170.0:2002"
    site.project_basis.standards.permanent_and_imposed = "AS/NZS 1170.1:2002"
    return site


def declaration():
    return {
        "design_basis": {
            "framework_id": "SCI-P399",
            "framework_label": "SCI P399",
            "framework_reference": "Table 3.1",
            "jurisdiction": "Australia",
            "analysis_method": "3D elastic",
            "standards": {
                "action_combinations": "confirm",
                "permanent_and_imposed_actions": "confirm",
                "wind_actions": "confirm",
            },
        },
        "wind_action_bases": [
            {
                "id": "legacy-site",
                "q_z_kPa": 1.0,
            }
        ],
        "loads": [
            {
                "id": "wind-in",
                "case": "wind",
                "wind_basis_id": "legacy-site",
                "net_pressure_coefficient": 0.8,
                "coefficient_status": "assumed",
                "pressure_kPa": 0.8,
                "provenance": "Roof sheet; site basis old",
            }
        ],
    }


def test_site_definition_round_trips_as_literal_python():
    site = verified_site()

    source = render_site_definition(site)
    parsed = parse_site_definition(source)

    assert parsed == site
    assert "site_dict = {" in source
    assert "q_z_kPa" not in source


def test_site_definition_migrates_legacy_warning_text_to_explicit_fields():
    source = (
        render_site_definition(default_site_definition())
        .replace(
            "'AS/NZS 1170.0:2002'",
            "'AS/NZS 1170.0 — project edition to confirm'",
        )
        .replace(
            "'10a'",
            "'Class 10a — confirm for project'",
        )
    )

    parsed = parse_site_definition(source)

    assert parsed.project_basis.standards.combinations == "AS/NZS 1170.0:2002"
    assert parsed.project_basis.standards.confirmed is False
    assert parsed.project_basis.building_classification == "10a"


def test_missing_md_and_mc_are_populated_from_the_embedded_region_tables():
    payload = default_site_definition().model_dump(mode="json")
    payload["wind"].update({
        "region": "A2",
        "cardinal_direction_multipliers": None,
        "climate_change_multiplier": None,
        "table_status": "verified",
    })

    site = SiteDefinition.model_validate(payload)

    assert site.wind.cardinal_direction_multipliers == (
        SiteCardinalDirectionMultipliers(
            n=0.85,
            ne=0.75,
            e=0.85,
            se=0.95,
            s=0.95,
            sw=0.95,
            w=1.00,
            nw=0.95,
        )
    )
    assert site.wind.climate_change_multiplier == pytest.approx(1.0)
    assert site.wind.table_status == "starter"


def test_site_definition_rejects_executable_code():
    with pytest.raises(SiteDefinitionError, match="may only contain"):
        parse_site_definition(
            "from pathlib import Path\nsite_dict = {'schema_version': Path('1.0')}"
        )


def test_design_site_dict_is_restricted_to_structural_link():
    validate_design_site_usage(
        "from tertius_site import site_dict\n"
        "site_wind = structure.site_wind_basis(site_dict)\n"
    )

    with pytest.raises(SiteDefinitionError, match="must not alter Build123D"):
        validate_design_site_usage(
            "from tertius_site import site_dict\n"
            "wall_height = site_dict['wind']['reference_height_m']\n"
        )


def test_site_calculation_keeps_derived_values_out_of_source():
    site = verified_site()

    calculation = calculate_site_definition(site)

    assert calculation["site_ready"] is True
    assert calculation["q_z_kPa"] == pytest.approx(0.837014)
    assert calculation["annual_recurrence_interval_years"] == 500
    assert calculation["revision"]
    assert calculation["directional_mode"] == "cardinal"
    assert calculation["governing_cardinal_direction"] == "SW"
    assert calculation["building_face_wind_speeds"] == [
        {
            "face": "front",
            "bearing_degrees": 20.0,
            "site_wind_speed_m_s": pytest.approx(33.615),
            "q_z_kPa": pytest.approx(0.677981),
            "governing_cardinal_direction": "N",
            "contributing_cardinal_directions": ["N", "NE"],
        },
        {
            "face": "right",
            "bearing_degrees": 110.0,
            "site_wind_speed_m_s": pytest.approx(31.7475),
            "q_z_kPa": pytest.approx(0.604742),
            "governing_cardinal_direction": "SE",
            "contributing_cardinal_directions": ["E", "SE"],
        },
        {
            "face": "back",
            "bearing_degrees": 200.0,
            "site_wind_speed_m_s": pytest.approx(37.35),
            "q_z_kPa": pytest.approx(0.837014),
            "governing_cardinal_direction": "SW",
            "contributing_cardinal_directions": ["S", "SW"],
        },
        {
            "face": "left",
            "bearing_degrees": 290.0,
            "site_wind_speed_m_s": pytest.approx(33.615),
            "q_z_kPa": pytest.approx(0.677981),
            "governing_cardinal_direction": "W",
            "contributing_cardinal_directions": ["W", "NW"],
        },
    ]


def test_legacy_single_direction_multiplier_remains_conservative_but_incomplete():
    site = default_site_definition()
    site.location.address = "14 Porter St"
    site.wind.direction_multiplier = 0.95
    site.wind.cardinal_direction_multipliers = None
    site.wind.region_status = "verified"
    site.wind.table_status = "verified"
    site.project_basis.standards.confirmed = True

    calculation = calculate_site_definition(site)

    assert calculation["directional_mode"] == "single_conservative"
    assert calculation["site_ready"] is False
    assert {sector["direction_multiplier"] for sector in calculation["cardinal_wind_speeds"]} == {0.95}


def test_all_four_site_multipliers_can_vary_by_cardinal_direction():
    site = verified_site()
    site.wind.cardinal_terrain_height_multipliers = SiteCardinalDirectionMultipliers(
        n=0.80, ne=0.81, e=0.82, se=0.83, s=0.84, sw=0.85, w=0.86, nw=0.87
    )
    site.wind.cardinal_shielding_multipliers = SiteCardinalDirectionMultipliers(
        n=0.90, ne=0.91, e=0.92, se=0.93, s=0.94, sw=0.95, w=0.96, nw=0.97
    )
    site.wind.cardinal_topographic_multipliers = SiteCardinalDirectionMultipliers(
        n=1.00, ne=1.01, e=1.02, se=1.03, s=1.04, sw=1.05, w=1.06, nw=1.07
    )

    calculation = calculate_site_definition(site)
    north = next(
        sector
        for sector in calculation["cardinal_wind_speeds"]
        if sector["direction"] == "N"
    )
    northwest = next(
        sector
        for sector in calculation["cardinal_wind_speeds"]
        if sector["direction"] == "NW"
    )

    assert calculation["directional_multiplier_modes"] == {
        "direction": "cardinal",
        "terrain_height": "cardinal",
        "shielding": "cardinal",
        "topographic": "cardinal",
    }
    assert north["direction_multiplier"] == pytest.approx(0.90)
    assert north["terrain_height_multiplier"] == pytest.approx(0.80)
    assert north["shielding_multiplier"] == pytest.approx(0.90)
    assert north["topographic_multiplier"] == pytest.approx(1.00)
    assert northwest["terrain_height_multiplier"] == pytest.approx(0.87)
    assert northwest["shielding_multiplier"] == pytest.approx(0.97)
    assert northwest["topographic_multiplier"] == pytest.approx(1.07)


def test_adopted_gis_multiplier_evidence_must_be_reviewed_and_current():
    site = verified_site()
    site.wind.cardinal_shielding_multipliers = SiteCardinalDirectionMultipliers()
    site.wind.multiplier_evidence = SiteWindMultiplierEvidenceReference(
        evidence_id="windv1-0123456789abcdef0123456789abcdef",
        provider="Geoscience Australia",
        dataset="National wind multiplier dataset",
        dataset_version="January 2016",
        source_uri="https://thredds.nci.org.au/thredds/catalog/fj6/multipliers/catalog.html",
        site_latitude=site.location.latitude,
        site_longitude=site.location.longitude,
        terrain_reference_height_m=10,
        adopted_components=["M_s"],
    )

    suggested = calculate_site_definition(site)
    assert suggested["site_ready"] is False
    assert suggested["working_basis_ready"] is True
    assert suggested["certification_ready"] is False
    assert suggested["multiplier_evidence_stale"] is False

    site.wind.multiplier_evidence = SiteWindMultiplierEvidenceReference(
        **{
            **site.wind.multiplier_evidence.model_dump(),
            "review_status": "verified",
            "review_reason": "Checked as a conservative comparison for this site.",
        }
    )
    verified = calculate_site_definition(site)
    assert verified["site_ready"] is True
    assert verified["working_basis_ready"] is True
    assert verified["certification_ready"] is True

    site.location.latitude += 0.001
    stale = calculate_site_definition(site)
    assert stale["multiplier_evidence_stale"] is True
    assert stale["site_ready"] is False
    assert stale["working_basis_ready"] is False


def test_local_multiplier_evidence_stales_with_candidate_or_terrain_changes():
    site = verified_site()
    site.structure.placement_latitude = site.location.latitude + 0.0001
    site.structure.placement_longitude = site.location.longitude + 0.0001
    site.terrain_evidence = SiteTerrainEvidenceReference(
        evidence_id="gisv1-0123456789abcdef0123456789abcdef",
        site_latitude=site.location.latitude,
        site_longitude=site.location.longitude,
        radius_m=2000,
    )
    site.wind.multiplier_evidence = SiteWindMultiplierEvidenceReference(
        evidence_id="windv1-0123456789abcdef0123456789abcdef",
        provider="Tertius GIS cache",
        dataset="Pinned local wind evidence",
        dataset_version="fixture",
        source_uri="https://example.com/evidence",
        site_latitude=site.location.latitude,
        site_longitude=site.location.longitude,
        terrain_reference_height_m=site.wind.reference_height_m,
        method_status="automated_local_analysis",
        terrain_evidence_id="gisv1-0123456789abcdef0123456789abcdef",
        placement_latitude=site.structure.placement_latitude,
        placement_longitude=site.structure.placement_longitude,
        footprint_length_m=site.structure.footprint_length_m,
        footprint_width_m=site.structure.footprint_width_m,
        front_bearing_degrees=site.structure.front_bearing_degrees,
        adopted_components=["M_z_cat", "M_s", "M_t"],
    )

    assert calculate_site_definition(site)["multiplier_evidence_stale"] is False

    site.structure.front_bearing_degrees = 45
    assert calculate_site_definition(site)["multiplier_evidence_stale"] is True


def test_site_overlay_recalculates_wind_load_without_changing_topology():
    site = verified_site()
    before = declaration()

    overlaid = apply_site_definition(before, site)

    assert before["loads"][0]["pressure_kPa"] == 0.8
    assert overlaid["wind_action_bases"][0]["id"] == "legacy-site"
    assert overlaid["wind_action_bases"][0]["enclosure"] == "enclosed"
    assert (
        overlaid["wind_action_bases"][0]["coefficient_selection_policy"]
        == "worst_available_credible"
    )
    assert overlaid["loads"][0]["pressure_kPa"] == pytest.approx(0.8 * 0.837014)
    assert overlaid["loads"][0]["coefficient_status"] == "working_conservative"
    assert overlaid["design_basis"]["standards"]["wind_actions"] == "AS/NZS 1170.2:2021"
    assert overlaid["design_basis"]["framework_id"] == "AU-NCC-2022"
    assert overlaid["design_basis"]["building_classification"] == "Class 10a"
    assert overlaid["design_basis"]["importance_level"] == "2"
    assert overlaid["design_basis"]["supplemental_methods"][0]["id"] == "SCI-P399"
    assert "confirm" not in (
        overlaid["design_basis"]["standards"]["action_combinations"].lower()
    )


def test_site_overlay_maps_directional_cases_to_the_governing_building_faces():
    site = verified_site()
    before = declaration()
    before["loads"] = [
        {
            **before["loads"][0],
            "id": f"wind-{case_id}",
            "case_id": f"case-{case_id}",
        }
        for case_id in (
            "wind-plus-x",
            "wind-minus-x",
            "wind-plus-y",
            "wind-minus-y",
        )
    ]

    overlaid = apply_site_definition(before, site)

    bases = {
        basis["structural_action_direction"]: basis
        for basis in overlaid["wind_action_bases"]
    }
    assert set(bases) == {"+X", "-X", "+Y", "-Y"}
    assert bases["+X"]["building_face"] == "left"
    assert bases["+X"]["governing_cardinal_direction"] == "W"
    assert bases["-X"]["building_face"] == "right"
    assert bases["+Y"]["building_face"] == "front"
    assert bases["-Y"]["building_face"] == "back"

    loads = {load["case_id"]: load for load in overlaid["loads"]}
    assert loads["case-wind-plus-x"]["wind_basis_id"] == bases["+X"]["id"]
    assert loads["case-wind-minus-x"]["wind_basis_id"] == bases["-X"]["id"]
    assert loads["case-wind-plus-y"]["wind_basis_id"] == bases["+Y"]["id"]
    assert loads["case-wind-minus-y"]["wind_basis_id"] == bases["-Y"]["id"]
    assert loads["case-wind-plus-x"]["pressure_kPa"] == pytest.approx(
        0.8 * 0.677981
    )
    assert loads["case-wind-minus-x"]["pressure_kPa"] == pytest.approx(
        0.8 * 0.604742
    )
    assert loads["case-wind-plus-y"]["pressure_kPa"] == pytest.approx(
        0.8 * 0.677981
    )
    assert loads["case-wind-minus-y"]["pressure_kPa"] == pytest.approx(
        0.8 * 0.837014
    )


def test_directional_component_values_reach_each_structural_face_basis():
    site = verified_site()
    site.wind.cardinal_terrain_height_multipliers = SiteCardinalDirectionMultipliers(
        n=0.80, ne=0.81, e=0.82, se=0.83, s=0.84, sw=0.85, w=0.86, nw=0.87
    )
    site.wind.cardinal_shielding_multipliers = SiteCardinalDirectionMultipliers(
        n=0.90, ne=0.91, e=0.92, se=0.93, s=0.94, sw=0.95, w=0.96, nw=0.97
    )
    site.wind.cardinal_topographic_multipliers = SiteCardinalDirectionMultipliers(
        n=1.00, ne=1.01, e=1.02, se=1.03, s=1.04, sw=1.05, w=1.06, nw=1.07
    )
    before = declaration()
    before["loads"] = [
        {
            **before["loads"][0],
            "id": f"wind-{case_id}",
            "case_id": f"case-{case_id}",
        }
        for case_id in (
            "wind-plus-x",
            "wind-minus-x",
            "wind-plus-y",
            "wind-minus-y",
        )
    ]

    calculation = calculate_site_definition(site)
    overlaid = apply_site_definition(before, site)
    sectors = {
        sector["direction"]: sector
        for sector in calculation["cardinal_wind_speeds"]
    }
    faces = {
        face["face"]: face for face in calculation["building_face_wind_speeds"]
    }

    for basis in overlaid["wind_action_bases"]:
        face = faces[basis["building_face"]]
        sector = sectors[face["governing_cardinal_direction"]]
        assert basis["direction_multiplier"] == sector["direction_multiplier"]
        assert basis["terrain_height_multiplier"] == sector[
            "terrain_height_multiplier"
        ]
        assert basis["shielding_multiplier"] == sector["shielding_multiplier"]
        assert basis["topographic_multiplier"] == sector[
            "topographic_multiplier"
        ]
        assert basis["q_z_kPa"] == sector["q_z_kPa"]


def test_site_only_edit_does_not_invalidate_structural_source_hash():
    files = {
        "design.py": "from tertius_site import site_dict\n",
        "tertius_site.py": render_site_definition(verified_site()),
    }
    changed = deepcopy(files)
    changed["tertius_site.py"] = changed["tertius_site.py"].replace(
        "'A2'",
        "'A3'",
    )

    assert runtime_files_hash(files) != runtime_files_hash(changed)
    assert structural_runtime_files_hash(files) == structural_runtime_files_hash(
        changed
    )


def test_saved_table_dataset_is_pinned_and_placement_is_independent_of_address():
    site = verified_site()
    site.structure.placement_latitude = site.location.latitude + 0.0001
    site.structure.placement_longitude = site.location.longitude + 0.0001
    rendered = render_site_definition(site)
    restored = parse_site_definition(rendered)

    assert restored.wind.table_dataset_version == TABLE_VERSION
    assert restored.structure.placement_latitude != restored.location.latitude
    restored.wind.table_dataset_version = "AS1170.2-2021-starter-v1"
    with pytest.raises(SiteWindError, match="migrate the project basis explicitly"):
        calculate_site_definition(restored)
