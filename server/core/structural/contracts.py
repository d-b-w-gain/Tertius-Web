from __future__ import annotations

from collections.abc import Sequence
from math import sqrt
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


class AnalyticalMemberDeclaration(StructuralContract):
    id: str
    label: str
    component_id: str
    start: Vector3
    end: Vector3
    start_restraints: Restraints = Field(default_factory=Restraints)
    end_restraints: Restraints = Field(default_factory=Restraints)
    section_id: str
    material_id: str
    assumption: str


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


class MemberPointLoad(StructuralContract):
    id: str
    label: str
    member_id: str
    case_id: str
    distance_m: float
    force: Vector3
    moment: Vector3 = Field(default_factory=lambda: Vector3(x=0, y=0, z=0))
    source_load_id: str
    provenance: str


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


class MemberDiagramStation(StructuralContract):
    distance_m: float
    position: Vector3
    moment_kNm: Vector3
    shear_kN: Vector3
    displacement_mm: Vector3


class MemberDiagram(StructuralContract):
    member_id: str
    visual_node_id: str
    stations: list[MemberDiagramStation]


class MemberCheck(StructuralContract):
    member_id: str
    label: str
    demand_kNm: float
    capacity_kNm: float | None
    utilisation: float | None
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


class DesignComponent(StructuralContract):
    id: str
    label: str
    kind: Literal["ground", "member", "surface", "connector", "support"]
    visual_node_id: str
    grounded: bool = False
    part_number: str | None = None


class DesignConnection(StructuralContract):
    id: str
    label: str
    from_component_id: str
    to_component_id: str
    connector_component_ids: list[str] = Field(default_factory=list)
    transfers: list[Literal["force", "shear", "moment", "wind_normal"]]


class DesignSurfaceLoad(StructuralContract):
    id: str
    label: str
    case: Literal["dead", "live", "wind"]
    component_id: str
    pressure_kPa: float
    area_m2: float
    direction: Vector3
    provenance: str


class DesignLoadPath(StructuralContract):
    load_id: str
    status: Literal["complete", "blocked"]
    component_ids: list[str]
    connection_ids: list[str]
    grounded_component_id: str | None = None
    detail: str


class DesignAnalysisDefinition(StructuralContract):
    materials: list[StructuralMaterial]
    sections: list[SectionProperties]
    members: list[AnalyticalMemberDeclaration]
    load_cases: list[LoadCase]
    member_loads: list[MemberPointLoad]


class ProjectStructuralCapture(StructuralContract):
    schema_version: Literal["0.1"] = "0.1"
    project_name: str
    design_hash: str
    title: str
    authoring_mode: Literal["legacy", "generated"]
    components: list[DesignComponent]
    connections: list[DesignConnection]
    loads: list[DesignSurfaceLoad]
    load_paths: list[DesignLoadPath]
    analysis: DesignAnalysisDefinition | None = None
    capabilities: list[CapabilityState]
    warnings: list[str]

    @model_validator(mode="after")
    def validate_capture_references(self) -> ProjectStructuralCapture:
        component_ids = _unique_ids("components", self.components)
        _unique_ids("connections", self.connections)
        _unique_ids("loads", self.loads)

        for connection in self.connections:
            _require_reference(
                "connection source component",
                connection.from_component_id,
                component_ids,
            )
            _require_reference(
                "connection target component",
                connection.to_component_id,
                component_ids,
            )
            if connection.from_component_id == connection.to_component_id:
                raise ValueError(f"connection {connection.id!r} connects a component to itself")
            for connector_id in connection.connector_component_ids:
                _require_reference("connection connector component", connector_id, component_ids)

        load_ids = {load.id for load in self.loads}
        connection_ids = {connection.id for connection in self.connections}
        for load in self.loads:
            _require_reference("load component", load.component_id, component_ids)
            if load.area_m2 <= 0:
                raise ValueError(f"load {load.id!r} must have a positive area")
            if load.pressure_kPa == 0:
                raise ValueError(f"load {load.id!r} must have non-zero pressure")
            if load.direction == Vector3(x=0, y=0, z=0):
                raise ValueError(f"load {load.id!r} must have a non-zero direction")

        for path in self.load_paths:
            _require_reference("load path load", path.load_id, load_ids)
            for component_id in path.component_ids:
                _require_reference("load path component", component_id, component_ids)
            for connection_id in path.connection_ids:
                _require_reference("load path connection", connection_id, connection_ids)
            if path.grounded_component_id is not None:
                _require_reference(
                    "load path grounded component",
                    path.grounded_component_id,
                    component_ids,
                )
            if path.status == "complete":
                if path.grounded_component_id is None:
                    raise ValueError(
                        f"complete load path {path.load_id!r} has no grounded component"
                    )
                grounded = next(
                    component
                    for component in self.components
                    if component.id == path.grounded_component_id
                )
                if not grounded.grounded:
                    raise ValueError(
                        f"complete load path {path.load_id!r} ends at an ungrounded component"
                    )
        if self.analysis is not None:
            material_ids = _unique_ids("analysis materials", self.analysis.materials)
            section_ids = _unique_ids("analysis sections", self.analysis.sections)
            member_ids = _unique_ids("analysis members", self.analysis.members)
            load_case_ids = _unique_ids("analysis load cases", self.analysis.load_cases)
            _unique_ids("analysis member loads", self.analysis.member_loads)
            surface_load_ids = {load.id for load in self.loads}
            components_by_id = {component.id: component for component in self.components}
            member_lengths: dict[str, float] = {}
            for member in self.analysis.members:
                _require_reference("analysis member component", member.component_id, component_ids)
                _require_reference("analysis member section", member.section_id, section_ids)
                _require_reference("analysis member material", member.material_id, material_ids)
                if member.start == member.end:
                    raise ValueError(f"analysis member {member.id!r} has zero length")
                if components_by_id[member.component_id].kind != "member":
                    raise ValueError(
                        f"analysis member {member.id!r} component is not a member"
                    )
                member_lengths[member.id] = sqrt(
                    (member.end.x - member.start.x) ** 2
                    + (member.end.y - member.start.y) ** 2
                    + (member.end.z - member.start.z) ** 2
                )
            for member_load in self.analysis.member_loads:
                _require_reference(
                    "analysis load member", member_load.member_id, member_ids
                )
                _require_reference(
                    "analysis load case", member_load.case_id, load_case_ids
                )
                _require_reference(
                    "analysis load source",
                    member_load.source_load_id,
                    surface_load_ids,
                )
                if not 0 < member_load.distance_m < member_lengths[member_load.member_id]:
                    raise ValueError(
                        f"analysis load {member_load.id!r} lies outside its member"
                    )
        return self


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
    member_loads: list[MemberPointLoad] = Field(default_factory=list)
    reactions: list[NodeReaction]
    member_results: list[MemberResult]
    member_diagrams: list[MemberDiagram] = Field(default_factory=list)
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
        for member_load in self.member_loads:
            _require_reference("member load member", member_load.member_id, member_ids)
            _require_reference("member load case", member_load.case_id, load_case_ids)
        for reaction in self.reactions:
            _require_reference("reaction node", reaction.node_id, node_ids)
        for result in self.member_results:
            _require_reference("result member", result.member_id, member_ids)
        for diagram in self.member_diagrams:
            _require_reference("diagram member", diagram.member_id, member_ids)
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
