from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.project_templates import default_structural_configuration
from core.structural.action_standard_packs import (
    ActionRole,
    ActionStandardPackId,
    StructuralActionCase,
    resolve_action_standard_pack,
)
from core.structural.project_configuration import StructuralProjectConfiguration


PACK_ID: ActionStandardPackId = "as_nzs_1170_0_2002_amd5_roof_wind_v1"


def _actions(*directions: str) -> list[StructuralActionCase]:
    actions = [
        StructuralActionCase(id="case-g", label="Permanent", role="permanent"),
        StructuralActionCase(
            id="case-q",
            label="Distributed roof imposed",
            role="imposed",
            imposed_profile="all_other_roofs_distributed",
        ),
        StructuralActionCase(
            id="case-qc-purlin-1",
            label="Concentrated roof action on purlin 1",
            role="imposed",
            imposed_profile="all_other_roofs_concentrated",
        ),
    ]
    role_specs: dict[str, tuple[str, ActionRole, ActionRole]] = {
        "positive_x": (
            "plus-x",
            "wind_serviceability_positive_x",
            "wind_ultimate_positive_x",
        ),
        "negative_x": (
            "minus-x",
            "wind_serviceability_negative_x",
            "wind_ultimate_negative_x",
        ),
        "positive_y": (
            "plus-y",
            "wind_serviceability_positive_y",
            "wind_ultimate_positive_y",
        ),
        "negative_y": (
            "minus-y",
            "wind_serviceability_negative_y",
            "wind_ultimate_negative_y",
        ),
    }
    for direction in directions:
        case_suffix, serviceability_role, ultimate_role = role_specs[direction]
        actions.extend(
            (
                StructuralActionCase(
                    id=f"wind-sls-{case_suffix}",
                    label=f"SLS wind {direction}",
                    role=serviceability_role,
                ),
                StructuralActionCase(
                    id=f"wind-uls-{case_suffix}",
                    label=f"ULS wind {direction}",
                    role=ultimate_role,
                ),
            )
        )
    return actions


def test_verified_pack_owns_gravity_roof_and_directional_wind_factors() -> None:
    resolved = resolve_action_standard_pack(
        PACK_ID,
        _actions("positive_x", "negative_x"),
    )

    factors = {
        combination.id: combination.factors
        for combination in resolved.load_combinations
    }
    assert factors == {
        "SLS-G": {"case-g": 1.0},
        "ULS-0.9G": {"case-g": 0.9},
        "ULS-1.35G": {"case-g": 1.35},
        "SLS-G+Q": {"case-g": 1.0, "case-q": 0.7},
        "ULS-1.2G+1.5Q": {"case-g": 1.2, "case-q": 1.5},
        "SLS-G+Qc:case-qc-purlin-1": {
            "case-g": 1.0,
            "case-qc-purlin-1": 1.0,
        },
        "ULS-1.2G+1.5Qc:case-qc-purlin-1": {
            "case-g": 1.2,
            "case-qc-purlin-1": 1.5,
        },
        "SLS-G+WX+": {"case-g": 1.0, "wind-sls-plus-x": 1.0},
        "ULS-1.2G+WX+": {"case-g": 1.2, "wind-uls-plus-x": 1.0},
        "ULS-0.9G+WX+": {"case-g": 0.9, "wind-uls-plus-x": 1.0},
        "SLS-G+WX-": {"case-g": 1.0, "wind-sls-minus-x": 1.0},
        "ULS-1.2G+WX-": {"case-g": 1.2, "wind-uls-minus-x": 1.0},
        "ULS-0.9G+WX-": {"case-g": 0.9, "wind-uls-minus-x": 1.0},
    }
    assert resolved.evidence.status == "verified"
    assert resolved.evidence.pack_version == "1.1.0"
    assert resolved.evidence.source_document_sha256 == (
        "df3c4e7afa753fe06ddf94fb1ae4fe103d62c633db04cf5360478443be247b37"
    )
    assert resolved.evidence.combination_ids == list(factors)
    assert {item.id for item in resolved.unavailable_combinations} == {
        "SLS-G+WY+",
        "ULS-1.2G+WY+",
        "ULS-0.9G+WY+",
        "SLS-G+WY-",
        "ULS-1.2G+WY-",
        "ULS-0.9G+WY-",
    }


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


def test_verified_pack_expands_every_declared_wind_direction_centrally() -> None:
    resolved = resolve_action_standard_pack(
        PACK_ID,
        _actions("positive_x", "negative_x", "positive_y", "negative_y"),
    )

    combinations = {item.id: item for item in resolved.load_combinations}

    assert len(combinations) == 19
    for suffix in ("WX+", "WX-", "WY+", "WY-"):
        assert f"SLS-G+{suffix}" in combinations
        assert f"ULS-1.2G+{suffix}" in combinations
        assert f"ULS-0.9G+{suffix}" in combinations
    assert all(
        combination.id in resolved.evidence.combination_ids
        for combination in combinations.values()
    )
    assert resolved.unavailable_combinations == []


def test_verified_pack_explains_missing_imposed_and_wind_actions() -> None:
    resolved = resolve_action_standard_pack(
        PACK_ID,
        [StructuralActionCase(id="dead", label="Permanent", role="permanent")],
    )

    unavailable = {item.id: item for item in resolved.unavailable_combinations}

    assert len(unavailable) == 16
    assert unavailable["SLS-G+Q"].missing_inputs == ["imposed"]
    assert "No imposed action (Q)" in unavailable["SLS-G+Q"].reason
    assert unavailable["ULS-0.9G+WX+"].missing_inputs == [
        "wind_ultimate_positive_x",
    ]


def test_serviceability_wind_does_not_depend_on_the_ultimate_event() -> None:
    actions = _actions("positive_x")
    actions = [
        action for action in actions if action.role != "wind_ultimate_positive_x"
    ]

    resolved = resolve_action_standard_pack(PACK_ID, actions)

    assert "SLS-G+WX+" in {combination.id for combination in resolved.load_combinations}
    unavailable = {item.id: item for item in resolved.unavailable_combinations}
    assert unavailable["ULS-1.2G+WX+"].missing_inputs == ["wind_ultimate_positive_x"]


def test_concentrated_roof_receivers_are_mutually_exclusive_alternatives() -> None:
    actions = [
        *_actions(),
        StructuralActionCase(
            id="case-qc-purlin-2",
            label="Concentrated roof action on purlin 2",
            role="imposed",
            imposed_profile="all_other_roofs_concentrated",
        ),
    ]

    resolved = resolve_action_standard_pack(PACK_ID, actions)
    concentrated = [
        combination
        for combination in resolved.load_combinations
        if "Qc:" in combination.id
    ]

    assert len(concentrated) == 4
    assert all("case-q" not in combination.factors for combination in concentrated)
    assert all(
        sum(case_id.startswith("case-qc-") for case_id in combination.factors) == 1
        for combination in concentrated
    )


def test_imposed_action_requires_a_supported_profile() -> None:
    with pytest.raises(ValidationError, match="imposed_profile"):
        StructuralActionCase(id="live", label="Generic imposed", role="imposed")


def test_schema_one_configuration_is_rejected_without_runtime_compatibility() -> None:
    legacy = default_structural_configuration()
    legacy["schema_version"] = "1.0"

    with pytest.raises(ValidationError, match="Input should be '2.0'"):
        StructuralProjectConfiguration.model_validate(legacy)
