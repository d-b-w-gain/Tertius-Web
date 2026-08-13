from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from core.structural.contracts import (
    LoadCase,
    LoadCombination,
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


class ConfiguredMemberCriteria(StructuralContract):
    component_id: str
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


class StructuralProjectConfiguration(StructuralContract):
    """Revisioned project analysis inputs owned by the Structural workbench."""

    schema_version: Literal["1.0"] = "1.0"
    title: str
    design_basis: StructuralDesignBasis
    load_cases: list[LoadCase]
    load_combinations: list[LoadCombination]
    include_self_weight: bool = True
    member_loads: list[ConfiguredMemberPointLoad] = Field(default_factory=list)
    member_distributed_loads: list[ConfiguredMemberDistributedLoad] = Field(
        default_factory=list
    )
    member_criteria: list[ConfiguredMemberCriteria] = Field(default_factory=list)
    approval_policy: Literal["draft_analysis", "verified_only"] = "verified_only"

    @model_validator(mode="after")
    def validate_references(self) -> StructuralProjectConfiguration:
        case_ids = [case.id for case in self.load_cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("load cases contain duplicate IDs")
        combination_ids = [combination.id for combination in self.load_combinations]
        if len(combination_ids) != len(set(combination_ids)):
            raise ValueError("load combinations contain duplicate IDs")
        case_id_set = set(case_ids)
        for combination in self.load_combinations:
            missing = set(combination.factors) - case_id_set
            if missing:
                raise ValueError(
                    f"load combination {combination.id!r} references missing cases "
                    f"{sorted(missing)}"
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
        if not self.member_loads and not self.member_distributed_loads:
            if not self.include_self_weight:
                raise ValueError("structural configuration requires at least one action")
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
