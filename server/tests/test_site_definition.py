from copy import deepcopy

import pytest

from core.compile_runtime import (
    runtime_files_hash,
    structural_runtime_files_hash,
)
from core.site_definition import (
    SiteCardinalDirectionMultipliers,
    SiteDefinitionError,
    apply_site_definition,
    calculate_site_definition,
    default_site_definition,
    parse_site_definition,
    render_site_definition,
    validate_design_site_usage,
)


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
    assert calculation["q_z_kPa"] == pytest.approx(0.683438)
    assert calculation["annual_recurrence_interval_years"] == 500
    assert calculation["revision"]
    assert calculation["directional_mode"] == "cardinal"
    assert calculation["governing_cardinal_direction"] == "SW"
    assert calculation["building_face_wind_speeds"] == [
        {
            "face": "front",
            "bearing_degrees": 20.0,
            "site_wind_speed_m_s": pytest.approx(30.375),
            "q_z_kPa": pytest.approx(0.553584),
            "governing_cardinal_direction": "N",
            "contributing_cardinal_directions": ["N", "NE"],
        },
        {
            "face": "right",
            "bearing_degrees": 110.0,
            "site_wind_speed_m_s": pytest.approx(28.6875),
            "q_z_kPa": pytest.approx(0.493784),
            "governing_cardinal_direction": "SE",
            "contributing_cardinal_directions": ["E", "SE"],
        },
        {
            "face": "back",
            "bearing_degrees": 200.0,
            "site_wind_speed_m_s": pytest.approx(33.75),
            "q_z_kPa": pytest.approx(0.683438),
            "governing_cardinal_direction": "SW",
            "contributing_cardinal_directions": ["S", "SW"],
        },
        {
            "face": "left",
            "bearing_degrees": 290.0,
            "site_wind_speed_m_s": pytest.approx(30.375),
            "q_z_kPa": pytest.approx(0.553584),
            "governing_cardinal_direction": "W",
            "contributing_cardinal_directions": ["W", "NW"],
        },
    ]


def test_legacy_single_direction_multiplier_remains_conservative_but_incomplete():
    site = default_site_definition()
    site.location.address = "14 Porter St"
    site.wind.direction_multiplier = 0.95
    site.wind.region_status = "verified"
    site.wind.table_status = "verified"
    site.project_basis.standards.confirmed = True

    calculation = calculate_site_definition(site)

    assert calculation["directional_mode"] == "single_conservative"
    assert calculation["site_ready"] is False
    assert {sector["direction_multiplier"] for sector in calculation["cardinal_wind_speeds"]} == {0.95}


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
    assert overlaid["loads"][0]["pressure_kPa"] == pytest.approx(0.8 * 0.683438)
    assert overlaid["loads"][0]["coefficient_status"] == "working_conservative"
    assert overlaid["design_basis"]["standards"]["wind_actions"] == "AS/NZS 1170.2:2021"
    assert "confirm" not in (
        overlaid["design_basis"]["standards"]["action_combinations"].lower()
    )


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
