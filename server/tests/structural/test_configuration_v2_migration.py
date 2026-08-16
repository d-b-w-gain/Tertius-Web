from __future__ import annotations

import pytest

from core.project_templates import default_structural_configuration
from core.structural.configuration_v2_migration import (
    migrate_configuration_v1_content,
)


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
    assert {action.role for action in migrated.action_cases} == {
        "permanent",
        "wind_positive_x",
        "wind_negative_x",
    }
    dumped = migrated.model_dump(mode="json")
    assert "load_combinations" not in dumped
    assert "combination_ids" not in dumped["cross_section_verification"]
    assert "combination_ids" not in dumped["member_stability_verification"]


def test_v1_migration_refuses_ambiguous_wind_case() -> None:
    legacy = default_structural_configuration()
    legacy.update(
        {
            "schema_version": "1.0",
            "load_cases": [
                {"id": "dead", "label": "Permanent", "category": "dead"},
                {"id": "wind", "label": "Wind", "category": "wind"},
            ],
            "load_combinations": [],
        }
    )
    legacy.pop("action_standard_pack_id")
    legacy.pop("action_cases")

    with pytest.raises(ValueError, match="cannot be mapped"):
        migrate_configuration_v1_content(legacy)
