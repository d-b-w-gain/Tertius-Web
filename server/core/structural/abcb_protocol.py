from __future__ import annotations

from .contracts import (
    ABCBProtocolGeometry,
    ABCBProtocolScopeAssessment,
    ABCBProtocolScopeCheck,
)


ABCB_PROTOCOL_EDITION = "2011.2"
ABCB_NCC_REFERENCE = "NCC 2022 B1D5 / H1D6 / Housing Provisions 2.2.5"


def assess_abcb_protocol_scope(
    *,
    geometry: ABCBProtocolGeometry,
    compliance_pathway: str,
) -> ABCBProtocolScopeAssessment:
    """Assess the objective job limits for the ABCB protocol pathway.

    This is a scope decision only. It does not assert that the software release
    has been independently appraised or that the operator's training is valid.
    """

    normalized_pathway = (
        compliance_pathway.strip().lower().replace("–", "-").replace("—", "-")
    )
    deemed_to_satisfy = normalized_pathway in {
        "deemed-to-satisfy",
        "deemed to satisfy",
        "dts",
    }
    checks = [
        ABCBProtocolScopeCheck(
            id="deemed_to_satisfy",
            label="NCC Deemed-to-Satisfy pathway",
            value=compliance_pathway,
            limit="Deemed-to-Satisfy",
            status="pass" if deemed_to_satisfy else "fail",
            reference=ABCB_NCC_REFERENCE,
        ),
        _numeric_check(
            id="eaves_height",
            label="Ground to underside of eaves",
            value=geometry.eaves_height_m,
            limit=6.0,
        ),
        _numeric_check(
            id="roof_height",
            label="Ground to highest roof point",
            value=geometry.roof_height_m,
            limit=8.5,
        ),
        _numeric_check(
            id="building_width",
            label="Building width including roofed verandahs",
            value=geometry.building_width_m,
            limit=16.0,
        ),
        _numeric_check(
            id="length_width_ratio",
            label="Building length divided by width",
            value=geometry.length_width_ratio,
            limit=5.0,
        ),
        _numeric_check(
            id="roof_pitch",
            label="Roof pitch",
            value=geometry.roof_pitch_degrees,
            limit=35.0,
        ),
    ]
    failed = [check for check in checks if check.status == "fail"]
    return ABCBProtocolScopeAssessment(
        ncc_reference=ABCB_NCC_REFERENCE,
        status="outside_scope" if failed else "within_scope",
        structural_system="steel_framed_building",
        compliance_pathway=compliance_pathway,
        geometry=geometry,
        checks=checks,
        blocking_reasons=[
            f"{check.label}: {check.value} exceeds or does not match {check.limit}."
            for check in failed
        ],
        basis=(
            "Scope geometry is measured from compiled physical member axes. "
            "Release appraisal, Compliance Document and trained-user controls "
            "are assessed separately."
        ),
    )


def _numeric_check(
    *,
    id: str,
    label: str,
    value: float,
    limit: float,
) -> ABCBProtocolScopeCheck:
    return ABCBProtocolScopeCheck.model_validate(
        {
            "id": id,
            "label": label,
            "value": value,
            "limit": limit,
            "status": "pass" if value <= limit else "fail",
            "reference": ABCB_NCC_REFERENCE,
        }
    )
