from __future__ import annotations

from copy import deepcopy

import pytest

from core.structural.abcb_protocol import assess_abcb_protocol_scope
from core.structural.contracts import ABCBProtocolGeometry


MAXIMUM_GEOMETRY = {
    "ground_level_m": 0.0,
    "eaves_height_m": 6.0,
    "roof_height_m": 8.5,
    "building_width_m": 16.0,
    "building_length_m": 80.0,
    "length_width_ratio": 5.0,
    "roof_pitch_degrees": 35.0,
    "basis": "FAT boundary geometry",
}


def test_abcb_protocol_scope_accepts_every_exact_boundary() -> None:
    assessment = assess_abcb_protocol_scope(
        geometry=ABCBProtocolGeometry.model_validate(MAXIMUM_GEOMETRY),
        compliance_pathway="Deemed-to-Satisfy",
    )

    assert assessment.status == "within_scope"
    assert len(assessment.checks) == 6
    assert {check.status for check in assessment.checks} == {"pass"}
    assert assessment.blocking_reasons == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eaves_height_m", 6.001),
        ("roof_height_m", 8.501),
        ("building_width_m", 16.001),
        ("building_length_m", 80.016),
        ("roof_pitch_degrees", 35.001),
    ],
)
def test_abcb_protocol_scope_rejects_one_increment_over_each_geometry_limit(
    field: str,
    value: float,
) -> None:
    values = deepcopy(MAXIMUM_GEOMETRY)
    values[field] = value
    building_length = value if field == "building_length_m" else 80.0
    building_width = value if field == "building_width_m" else 16.0
    values["length_width_ratio"] = building_length / building_width

    assessment = assess_abcb_protocol_scope(
        geometry=ABCBProtocolGeometry.model_validate(values),
        compliance_pathway="Deemed-to-Satisfy",
    )

    assert assessment.status == "outside_scope"
    assert any(check.status == "fail" for check in assessment.checks)
    assert assessment.blocking_reasons


def test_abcb_protocol_scope_rejects_non_dts_pathway() -> None:
    assessment = assess_abcb_protocol_scope(
        geometry=ABCBProtocolGeometry.model_validate(MAXIMUM_GEOMETRY),
        compliance_pathway="Engineered solution",
    )

    assert assessment.status == "outside_scope"
    pathway = next(
        check for check in assessment.checks if check.id == "deemed_to_satisfy"
    )
    assert pathway.status == "fail"
