from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from core.structural.action_standard_packs import (
    ActionStandardPackId,
    StructuralActionCase,
    resolve_action_standard_pack,
)
from core.structural.contracts import (
    Restraints,
    StructuralContract,
    StructuralDesignBasis,
    Vector3,
)


class ConfiguredMemberPointLoad(StructuralContract):
    id: str
    label: str
    component_id: str
    case_id: str
    distance_m: float = Field(ge=0)
    force: Vector3
    moment: Vector3 = Field(default_factory=lambda: Vector3(x=0, y=0, z=0))
    provenance: str


class ConfiguredMemberDistributedLoad(StructuralContract):
    id: str
    label: str
    component_id: str
    case_id: str
    start_distance_m: float = Field(default=0, ge=0)
    end_distance_m: float | None = Field(default=None, gt=0)
    start_force_kN_m: Vector3
    end_force_kN_m: Vector3 | None = None
    provenance: str


class ConfiguredPortalFrameWindActions(StructuralContract):
    """Working building-envelope action model derived from mechanical member roles.

    The geometry remains owned by ``design.py``.  This revisioned Structural
    input identifies the portal members that receive Site wind and R2 roof
    imposed actions. Tertius owns the action formulae and uses the compiled
    geometry to derive tributary areas and solver line actions.
    """

    model_id: Literal["transverse_portal_frame_strip_v1"] = (
        "transverse_portal_frame_strip_v1"
    )
    column_role: str = "portal column"
    rafter_role: str = "portal rafter"
    windward_wall_coefficient: float = Field(default=0.8, gt=0)
    leeward_wall_coefficient: float = Field(default=-0.5, lt=0)
    roof_suction_coefficient: float = Field(default=-0.9, lt=0)
    coefficient_status: Literal["assumed"] = "assumed"
    coefficient_basis: str = Field(min_length=1)


class ConfiguredMemberCriteria(StructuralContract):
    component_id: str | None = None
    deflection_limit_ratio: float | None = Field(default=None, gt=0)
    deflection_limit_mm: float | None = Field(default=None, gt=0)
    deflection_limit_basis: str | None = None

    @model_validator(mode="after")
    def validate_limit_basis(self) -> ConfiguredMemberCriteria:
        if (
            self.deflection_limit_ratio is not None
            or self.deflection_limit_mm is not None
        ) and not self.deflection_limit_basis:
            raise ValueError("member deflection limits require a basis")
        return self


class ConfiguredCrossSectionVerification(StructuralContract):
    pack_id: Literal["as_nzs_4600_2018_ewm"]
    component_ids: list[str] = Field(default_factory=list)
    off_axis_tolerance: float = Field(default=1e-6, ge=0)


class ConfiguredMemberStabilitySegment(StructuralContract):
    id: str
    component_id: str
    start_distance_m: float = Field(default=0, ge=0)
    end_distance_m: float | None = Field(default=None, gt=0)
    minor_axis_effective_length_factor: float = Field(default=1.0, gt=0)
    torsional_effective_length_factor: float = Field(default=1.0, gt=0)
    lateral_bending_restraint: Literal[
        "unverified",
        "continuous_compression_flange",
    ] = "unverified"
    restraint_status: Literal["assumed", "verified"] = "assumed"
    restraint_basis: str
    distortional_buckling_status: Literal["unverified", "verified"] = "unverified"
    distortional_buckling_basis: str

    @model_validator(mode="after")
    def validate_restraint_evidence(self) -> ConfiguredMemberStabilitySegment:
        if (
            self.lateral_bending_restraint == "continuous_compression_flange"
            and self.restraint_status != "verified"
        ):
            raise ValueError(
                "continuous compression-flange restraint requires verified evidence"
            )
        return self


class ConfiguredMemberStabilityVerification(StructuralContract):
    pack_id: Literal["as_nzs_4600_2018_ewm_member"]
    segments: list[ConfiguredMemberStabilitySegment] = Field(default_factory=list)
    off_axis_tolerance: float = Field(default=1e-6, ge=0)


class StructuralProjectConfiguration(StructuralContract):
    """Revisioned project analysis inputs owned by the Structural workbench."""

    schema_version: Literal["2.0"] = "2.0"
    title: str
    design_basis: StructuralDesignBasis
    action_standard_pack_id: ActionStandardPackId
    action_cases: list[StructuralActionCase]
    include_self_weight: bool = True
    member_loads: list[ConfiguredMemberPointLoad] = Field(default_factory=list)
    member_distributed_loads: list[ConfiguredMemberDistributedLoad] = Field(
        default_factory=list
    )
    portal_frame_wind_actions: ConfiguredPortalFrameWindActions | None = None
    member_criteria: list[ConfiguredMemberCriteria] = Field(default_factory=list)
    cross_section_verification: ConfiguredCrossSectionVerification | None = None
    member_stability_verification: ConfiguredMemberStabilityVerification | None = None
    approval_policy: Literal["draft_analysis", "verified_only"] = "verified_only"

    @model_validator(mode="after")
    def validate_references(self) -> StructuralProjectConfiguration:
        case_ids = [case.id for case in self.action_cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("action cases contain duplicate IDs")
        action_roles = [case.role for case in self.action_cases]
        if len(action_roles) != len(set(action_roles)):
            raise ValueError("action cases contain duplicate semantic roles")
        case_id_set = set(case_ids)
        if (
            sum(criterion.component_id is None for criterion in self.member_criteria)
            > 1
        ):
            raise ValueError(
                "member criteria can contain at most one all-members default"
            )
        for point_load in self.member_loads:
            if point_load.case_id not in case_id_set:
                raise ValueError(
                    f"member load {point_load.id!r} references missing case "
                    f"{point_load.case_id!r}"
                )
        for distributed_load in self.member_distributed_loads:
            if distributed_load.case_id not in case_id_set:
                raise ValueError(
                    f"member load {distributed_load.id!r} references missing case "
                    f"{distributed_load.case_id!r}"
                )
        if self.include_self_weight and "permanent" not in action_roles:
            raise ValueError("self-weight requires a permanent action case")
        resolve_action_standard_pack(
            self.action_standard_pack_id,
            self.action_cases,
        )
        if not self.member_loads and not self.member_distributed_loads:
            if not self.include_self_weight:
                raise ValueError(
                    "structural configuration requires at least one action"
                )
        return self

    @property
    def configuration_digest(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class StructuralConfigurationRevisionResponse(StructuralContract):
    revision: int = Field(gt=0)
    digest: str = Field(min_length=64, max_length=64)
    configuration: StructuralProjectConfiguration


def fixed_restraints() -> Restraints:
    return Restraints(dx=True, dy=True, dz=True, rx=True, ry=True, rz=True)


def pinned_restraints() -> Restraints:
    return Restraints(dx=True, dy=True, dz=True)
