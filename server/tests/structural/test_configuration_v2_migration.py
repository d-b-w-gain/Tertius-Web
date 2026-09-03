from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.project_templates import default_structural_configuration
from core.structural.configuration_v2_migration import (
    migrate_configuration_v1_content,
    migrate_working_v2_content,
)
from core.structural.project_configuration import StructuralProjectConfiguration


def test_v1_migration_discards_formulas_and_preserves_semantic_actions() -> None:
    current = default_structural_configuration()
    legacy = {
        **current,
        "schema_version": "1.0",
        "load_cases": [
            {"id": "dead", "label": "Permanent", "category": "dead"},
            {"id": "wind-plus-x", "label": "Wind +X", "category": "wind"},
            {"id": "wind-minus-x", "label": "Wind -X", "category": "wind"},
        ],
        "load_combinations": [
            {
                "id": "unsafe",
                "label": "Project-authored formula",
                "limit_state": "ultimate",
                "factors": {"dead": 99},
            }
        ],
    }
    legacy.pop("action_standard_pack_id")
    legacy.pop("action_cases")
    cross_section = legacy["cross_section_verification"]
    member_stability = legacy["member_stability_verification"]
    assert isinstance(cross_section, dict)
    assert isinstance(member_stability, dict)
    cross_section["combination_ids"] = ["unsafe"]
    member_stability["combination_ids"] = ["unsafe"]

    migrated = migrate_configuration_v1_content(legacy)

    assert migrated.schema_version == "2.0"
    assert {action.role for action in migrated.action_cases} == {"permanent"}
    dumped = migrated.model_dump(mode="json")
    assert "load_combinations" not in dumped
    assert "combination_ids" not in dumped["cross_section_verification"]
    assert "combination_ids" not in dumped["member_stability_verification"]


def test_v1_migration_refuses_unknown_action_category() -> None:
    legacy = default_structural_configuration()
    legacy.update(
        {
            "schema_version": "1.0",
            "load_cases": [
                {"id": "dead", "label": "Permanent", "category": "dead"},
                {"id": "special", "label": "Special", "category": "other"},
            ],
            "load_combinations": [],
        }
    )
    legacy.pop("action_standard_pack_id")
    legacy.pop("action_cases")

    with pytest.raises(ValueError, match="cannot be mapped"):
        migrate_configuration_v1_content(legacy)


def test_working_v2_migration_replaces_pack_and_regenerates_derived_actions() -> None:
    legacy = default_structural_configuration()
    legacy["action_standard_pack_id"] = "as_nzs_1170_0_2002_working_v1"
    legacy["action_cases"] = [
        {"id": "dead", "label": "Permanent", "role": "permanent"},
        {"id": "roof-imposed", "label": "Roof", "role": "imposed"},
        {"id": "wind-plus-x", "label": "Wind +X", "role": "wind_positive_x"},
    ]
    legacy["member_loads"] = [
        {
            "id": "dead-point",
            "label": "Retained permanent point load",
            "component_id": "P1",
            "case_id": "dead",
            "distance_m": 0.5,
            "force": {"x": 0, "y": 0, "z": -1},
            "provenance": "migration test",
        },
        {
            "id": "roof-point",
            "label": "Retired imposed point load",
            "component_id": "P1",
            "case_id": "roof-imposed",
            "distance_m": 0.5,
            "force": {"x": 0, "y": 0, "z": -1},
            "provenance": "migration test",
        },
    ]
    legacy["member_distributed_loads"] = [
        {
            "id": "wind-line",
            "label": "Retired wind line load",
            "component_id": "P1",
            "case_id": "wind-plus-x",
            "start_force_kN_m": {"x": 1, "y": 0, "z": 0},
            "provenance": "migration test",
        }
    ]

    migrated = migrate_working_v2_content(legacy)

    assert migrated.action_standard_pack_id == ("as_nzs_1170_0_2002_amd5_roof_wind_v1")
    assert [(action.id, action.role) for action in migrated.action_cases] == [
        ("dead", "permanent")
    ]
    assert [load.id for load in migrated.member_loads] == ["dead-point"]
    assert migrated.member_distributed_loads == []


def test_working_v2_migration_refuses_unknown_semantic_action() -> None:
    legacy = default_structural_configuration()
    legacy["action_standard_pack_id"] = "as_nzs_1170_0_2002_working_v1"
    legacy["action_cases"] = [
        {"id": "dead", "label": "Permanent", "role": "permanent"},
        {"id": "special", "label": "Unknown", "role": "special"},
    ]

    with pytest.raises(ValueError, match="cannot be migrated"):
        migrate_working_v2_content(legacy)


def test_runtime_rejects_pre_flag_day_as_nzs_4600_pack_ids() -> None:
    legacy = default_structural_configuration()
    cross_section = legacy["cross_section_verification"]
    member_stability = legacy["member_stability_verification"]
    assert isinstance(cross_section, dict)
    assert isinstance(member_stability, dict)
    cross_section["pack_id"] = "as_nzs_4600_2018_ewm"
    member_stability["pack_id"] = "as_nzs_4600_2018_ewm_member"

    with pytest.raises(ValidationError):
        StructuralProjectConfiguration.model_validate(legacy)
