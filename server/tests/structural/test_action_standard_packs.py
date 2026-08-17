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
        "ULS-1.35G": {"case-g": 1.35},
        "SLS-G+Q": {"case-g": 1.0, "case-q": 1.0},
        "ULS-1.2G+1.5Q": {"case-g": 1.2, "case-q": 1.5},
        "SLS-G+WX+": {"case-g": 1.0, "case-wx-positive": 1.0},
        "ULS-1.2G+WX+": {"case-g": 1.2, "case-wx-positive": 1.0},
        "ULS-0.9G+WX+": {"case-g": 0.9, "case-wx-positive": 1.0},
        "SLS-G+WX-": {"case-g": 1.0, "case-wx-negative": 1.0},
        "ULS-1.2G+WX-": {"case-g": 1.2, "case-wx-negative": 1.0},
        "ULS-0.9G+WX-": {"case-g": 0.9, "case-wx-negative": 1.0},
    }
    assert resolved.evidence.status == "working"
    assert resolved.evidence.pack_version == "1.1.0"
    assert resolved.evidence.combination_ids == list(factors)
    assert {item.id for item in resolved.unavailable_combinations} == {
        "SLS-G+WY+",
        "ULS-1.2G+WY+",
        "ULS-0.9G+WY+",
        "SLS-G+WY-",
        "ULS-1.2G+WY-",
        "ULS-0.9G+WY-",
    }
    assert all(
        item.missing_inputs in (["wind_positive_y"], ["wind_negative_y"])
        for item in resolved.unavailable_combinations
    )


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


def test_working_pack_expands_every_declared_wind_direction_centrally() -> None:
    resolved = resolve_action_standard_pack(
        "as_nzs_1170_0_2002_working_v1",
        [
            StructuralActionCase(id="dead", label="Permanent", role="permanent"),
            StructuralActionCase(id="live", label="Imposed", role="imposed"),
            StructuralActionCase(id="wx+", label="Wind +X", role="wind_positive_x"),
            StructuralActionCase(id="wx-", label="Wind -X", role="wind_negative_x"),
            StructuralActionCase(id="wy+", label="Wind +Y", role="wind_positive_y"),
            StructuralActionCase(id="wy-", label="Wind -Y", role="wind_negative_y"),
        ],
    )

    combinations = {item.id: item for item in resolved.load_combinations}

    assert len(combinations) == 16
    for suffix in ("WX+", "WX-", "WY+", "WY-"):
        assert f"SLS-G+{suffix}" in combinations
        assert f"ULS-1.2G+{suffix}" in combinations
        assert f"ULS-0.9G+{suffix}" in combinations
    assert all(
        combination.id in resolved.evidence.combination_ids
        for combination in combinations.values()
    )
    assert resolved.unavailable_combinations == []


def test_working_pack_explains_missing_imposed_and_wind_actions() -> None:
    resolved = resolve_action_standard_pack(
        "as_nzs_1170_0_2002_working_v1",
        [StructuralActionCase(id="dead", label="Permanent", role="permanent")],
    )

    unavailable = {item.id: item for item in resolved.unavailable_combinations}

    assert len(unavailable) == 14
    assert unavailable["SLS-G+Q"].missing_inputs == ["imposed"]
    assert "No imposed action (Q)" in unavailable["SLS-G+Q"].reason
    assert unavailable["ULS-0.9G+WX+"].missing_inputs == ["wind_positive_x"]
    assert "transverse wind +X" in unavailable["ULS-0.9G+WX+"].reason


def test_schema_one_configuration_is_rejected_without_runtime_compatibility() -> None:
    legacy = default_structural_configuration()
    legacy["schema_version"] = "1.0"

    with pytest.raises(ValidationError, match="Input should be '2.0'"):
        StructuralProjectConfiguration.model_validate(legacy)
