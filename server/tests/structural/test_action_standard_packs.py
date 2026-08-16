from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.project_templates import default_structural_configuration
from core.structural.action_standard_packs import (
    StructuralActionCase,
    resolve_action_standard_pack,
)
from core.structural.project_configuration import StructuralProjectConfiguration


def test_working_pack_owns_gravity_live_and_directional_wind_factors() -> None:
    resolved = resolve_action_standard_pack(
        "as_nzs_1170_0_2002_working_v1",
        [
            StructuralActionCase(id="case-g", label="Permanent", role="permanent"),
            StructuralActionCase(id="case-q", label="Imposed", role="imposed"),
            StructuralActionCase(
                id="case-wx-positive", label="Wind +X", role="wind_positive_x"
            ),
            StructuralActionCase(
                id="case-wx-negative", label="Wind -X", role="wind_negative_x"
            ),
        ],
    )

    factors = {
        combination.id: combination.factors
        for combination in resolved.load_combinations
    }
    assert factors == {
        "SLS-G": {"case-g": 1.0},
        "ULS-1.2G": {"case-g": 1.2},
        "SLS-G+Q": {"case-g": 1.0, "case-q": 1.0},
        "ULS-1.2G+1.5Q": {"case-g": 1.2, "case-q": 1.5},
        "SLS-G+WX+": {"case-g": 1.0, "case-wx-positive": 1.0},
        "ULS-1.2G+WX+": {"case-g": 1.2, "case-wx-positive": 1.0},
        "SLS-G+WX-": {"case-g": 1.0, "case-wx-negative": 1.0},
        "ULS-1.2G+WX-": {"case-g": 1.2, "case-wx-negative": 1.0},
    }
    assert resolved.evidence.status == "working"
    assert resolved.evidence.combination_ids == list(factors)


def test_project_configuration_cannot_author_combinations_or_check_selection() -> None:
    legacy = default_structural_configuration()
    legacy["load_combinations"] = [
        {
            "id": "project-authored",
            "label": "Unsafe project formula",
            "limit_state": "ultimate",
            "factors": {"dead": 99.0},
        }
    ]
    cross_section = legacy["cross_section_verification"]
    assert isinstance(cross_section, dict)
    cross_section["combination_ids"] = ["project-authored"]

    with pytest.raises(ValidationError) as exc_info:
        StructuralProjectConfiguration.model_validate(legacy)

    messages = str(exc_info.value)
    assert "load_combinations" in messages
    assert "combination_ids" in messages


def test_schema_one_configuration_is_rejected_without_runtime_compatibility() -> None:
    legacy = default_structural_configuration()
    legacy["schema_version"] = "1.0"

    with pytest.raises(ValidationError, match="Input should be '2.0'"):
        StructuralProjectConfiguration.model_validate(legacy)
