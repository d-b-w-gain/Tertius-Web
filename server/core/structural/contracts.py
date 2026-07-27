from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StructuralContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Vector3(StructuralContract):
    x: float
    y: float
    z: float


class StructuralUnits(StructuralContract):
    length: Literal["m"] = "m"
    force: Literal["kN"] = "kN"
    moment: Literal["kN.m"] = "kN.m"
    displacement: Literal["mm"] = "mm"
    render_length: Literal["mm"] = "mm"


class Restraints(StructuralContract):
    dx: bool = False
    dy: bool = False
    dz: bool = False
    rx: bool = False
    ry: bool = False
    rz: bool = False


class StructuralNode(StructuralContract):
    id: str
    label: str
    position: Vector3
    restraints: Restraints = Field(default_factory=Restraints)
    visual_node_id: str


class SectionProperties(StructuralContract):
    id: str
    label: str
    area_m2: float
    iy_m4: float
    iz_m4: float
    torsion_j_m4: float


class StructuralMember(StructuralContract):
    id: str
    label: str
    start_node_id: str
    end_node_id: str
    section_id: str
    material_id: str
    visual_node_id: str


class StructuralMaterial(StructuralContract):
    id: str
    label: str
    elastic_modulus_kN_m2: float
    shear_modulus_kN_m2: float
    poisson_ratio: float
    density_kg_m3: float


class LoadCase(StructuralContract):
    id: str
    label: str
    category: Literal["dead", "live", "wind", "fixture"]


class NodalLoad(StructuralContract):
    id: str
    label: str
    node_id: str
    case_id: str
    force: Vector3
    moment: Vector3 = Field(default_factory=lambda: Vector3(x=0, y=0, z=0))
    visual_node_id: str


class NodeReaction(StructuralContract):
    node_id: str
    combination_id: str
    force: Vector3
    moment: Vector3


class MemberResult(StructuralContract):
    member_id: str
    combination_id: str
    max_moment_kNm: float
    max_shear_kN: float
    max_axial_kN: float
    max_displacement_mm: float


class MemberCheck(StructuralContract):
    member_id: str
    label: str
    demand_kNm: float
    capacity_kNm: float
    utilisation: float
    status: Literal["pass", "fail", "not_checked"]
    basis: str


class EquilibriumDiagnostic(StructuralContract):
    force_residual_kN: Vector3
    moment_residual_kNm: Vector3
    tolerance: float
    status: Literal["pass", "fail"]


class SolverMetadata(StructuralContract):
    name: str
    version: str
    analysis: str
    combination_id: str


class CapabilityState(StructuralContract):
    id: str
    label: str
    status: Literal["fixture", "online", "pending", "blocked"]
    detail: str


class SnapshotSource(StructuralContract):
    kind: Literal["fixture", "design"]
    label: str
    design_id: str | None = None
    design_hash: str | None = None


class StructuralSnapshot(StructuralContract):
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["fixture", "design"]
    title: str
    subtitle: str
    source: SnapshotSource
    units: StructuralUnits = Field(default_factory=StructuralUnits)
    nodes: list[StructuralNode]
    members: list[StructuralMember]
    sections: list[SectionProperties]
    materials: list[StructuralMaterial]
    load_cases: list[LoadCase]
    loads: list[NodalLoad]
    reactions: list[NodeReaction]
    member_results: list[MemberResult]
    member_checks: list[MemberCheck]
    equilibrium: EquilibriumDiagnostic
    solver: SolverMetadata
    capabilities: list[CapabilityState]
    warnings: list[str]

    @model_validator(mode="after")
    def validate_graph_references(self) -> StructuralSnapshot:
        node_ids = _unique_ids("nodes", self.nodes)
        member_ids = _unique_ids("members", self.members)
        section_ids = _unique_ids("sections", self.sections)
        material_ids = _unique_ids("materials", self.materials)
        load_case_ids = _unique_ids("load cases", self.load_cases)

        positions = {node.id: node.position for node in self.nodes}
        for member in self.members:
            _require_reference("member start node", member.start_node_id, node_ids)
            _require_reference("member end node", member.end_node_id, node_ids)
            _require_reference("member section", member.section_id, section_ids)
            _require_reference("member material", member.material_id, material_ids)
            if positions[member.start_node_id] == positions[member.end_node_id]:
                raise ValueError(f"member {member.id!r} has zero length")

        for load in self.loads:
            _require_reference("load node", load.node_id, node_ids)
            _require_reference("load case", load.case_id, load_case_ids)
        for reaction in self.reactions:
            _require_reference("reaction node", reaction.node_id, node_ids)
        for result in self.member_results:
            _require_reference("result member", result.member_id, member_ids)
        for check in self.member_checks:
            _require_reference("check member", check.member_id, member_ids)
        return self


def _unique_ids(label: str, values: Sequence[StructuralContract]) -> set[str]:
    identifiers = [str(getattr(value, "id")) for value in values]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label} contain duplicate IDs")
    return set(identifiers)


def _require_reference(label: str, reference: str, valid_ids: set[str]) -> None:
    if reference not in valid_ids:
        raise ValueError(f"{label} references missing ID {reference!r}")
