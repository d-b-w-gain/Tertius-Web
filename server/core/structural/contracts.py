from __future__ import annotations

from collections.abc import Sequence
from math import sqrt
from typing import Any, Literal

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


class SectionCatalogReference(StructuralContract):
    catalog_id: str
    catalog_version: str
    section_key: str
    source: str
    record_sha256: str = Field(min_length=64, max_length=64)
    axis_mapping: dict[str, str]
    properties: dict[str, Any]


class SectionProperties(StructuralContract):
    id: str
    label: str
    area_m2: float
    iy_m4: float
    iz_m4: float
    torsion_j_m4: float
    mass_kg_m: float | None = None
    bending_reference_kNm: float | None = None
    bending_reference_axis: Literal["local_y", "local_z", "resultant"] | None = None
    bending_reference_basis: str | None = None
    catalog: SectionCatalogReference | None = None

    @model_validator(mode="after")
    def validate_bending_reference(self) -> SectionProperties:
        reference_fields = (
            self.bending_reference_kNm,
            self.bending_reference_axis,
            self.bending_reference_basis,
        )
        if any(value is None for value in reference_fields) and any(
            value is not None for value in reference_fields
        ):
            raise ValueError(
                "section bending reference requires capacity, axis, and basis"
            )
        if self.bending_reference_kNm is not None and self.bending_reference_kNm <= 0:
            raise ValueError("section bending reference must be positive")
        return self


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
    rotation_deg: float = 0.0
    start_releases: Restraints = Field(default_factory=Restraints)
    end_releases: Restraints = Field(default_factory=Restraints)
    deflection_limit_ratio: float | None = None
    deflection_limit_mm: float | None = None
    deflection_limit_basis: str | None = None
    assumption: str


class LoadCase(StructuralContract):
    id: str
    label: str
    category: Literal["dead", "live", "wind", "imperfection", "fixture"]


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
    source_load_id: str | None = None
    provenance: str


class MemberDistributedLoad(StructuralContract):
    id: str
    label: str
    member_id: str
    case_id: str
    start_distance_m: float
    end_distance_m: float
    start_force_kN_m: Vector3
    end_force_kN_m: Vector3
    source_kind: Literal["self_weight", "surface", "authored"]
    source_load_id: str | None = None
    provenance: str


class LoadCombination(StructuralContract):
    id: str
    label: str
    limit_state: Literal["serviceability", "ultimate"]
    factors: dict[str, float]


class StabilityDefinition(StructuralContract):
    method: Literal["p_delta"]
    stability_combination_id: str
    imperfection_case_id: str
    imperfection_basis: str
    base_stiffness_basis: str
    base_stiffness_status: Literal["verified", "assumed"]
    amplification_warning_ratio: float = Field(default=1.1, gt=1.0)


class MemberStabilityComparison(StructuralContract):
    member_id: str
    first_order_max_moment_kNm: float
    second_order_max_moment_kNm: float
    moment_amplification: float
    first_order_max_displacement_mm: float
    second_order_max_displacement_mm: float
    displacement_amplification: float


class StabilityResult(StructuralContract):
    method: Literal["p_delta"]
    combination_id: str
    imperfection_case_id: str
    converged: bool
    amplification_warning_ratio: float
    governing_moment_amplification: float
    governing_displacement_amplification: float
    member_comparisons: list[MemberStabilityComparison]


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


class ServiceabilityCheck(StructuralContract):
    member_id: str
    label: str
    combination_id: str
    displacement_mm: float
    limit_mm: float | None
    utilisation: float | None
    status: Literal["pass", "fail", "not_checked"]
    basis: str


class LoadSummary(StructuralContract):
    member_mass_kg: float
    self_weight_kN: float
    additional_dead_load_kN: float
    imposed_load_kN: float
    wind_load_kN: float


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


VerificationStatus = Literal[
    "pass",
    "fail",
    "warning",
    "not_checked",
    "unsupported",
    "blocked",
]


class StructuralDesignBasis(StructuralContract):
    framework_id: str
    framework_label: str
    framework_reference: str
    jurisdiction: str
    analysis_method: str
    standards: dict[str, str] = Field(default_factory=dict)


class CalculationInput(StructuralContract):
    symbol: str
    label: str
    value: float | str | bool
    unit: str | None = None
    source: str


class CalculationEquation(StructuralContract):
    label: str
    expression: str
    substitution: str
    result: float | str
    unit: str | None = None


class CalculationSheet(StructuralContract):
    id: str
    stage_id: str
    title: str
    status: VerificationStatus
    p399_reference: str
    purpose: str
    assumptions: list[str] = Field(default_factory=list)
    inputs: list[CalculationInput] = Field(default_factory=list)
    equations: list[CalculationEquation] = Field(default_factory=list)
    outputs: list[CalculationInput] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    related_member_ids: list[str] = Field(default_factory=list)
    related_node_ids: list[str] = Field(default_factory=list)
    related_load_case_ids: list[str] = Field(default_factory=list)
    related_combination_ids: list[str] = Field(default_factory=list)


class VerificationStage(StructuralContract):
    id: str
    order: int
    label: str
    p399_reference: str
    status: VerificationStatus
    summary: str
    sheet_ids: list[str] = Field(default_factory=list)
    blocking_stage_ids: list[str] = Field(default_factory=list)


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
    case_id: str | None = None
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
    member_distributed_loads: list[MemberDistributedLoad] = Field(default_factory=list)
    load_combinations: list[LoadCombination] = Field(default_factory=list)
    stability: StabilityDefinition | None = None


class ProjectStructuralCapture(StructuralContract):
    schema_version: Literal["0.1"] = "0.1"
    project_name: str
    design_hash: str
    title: str
    authoring_mode: Literal["legacy", "generated"]
    design_basis: StructuralDesignBasis | None = None
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
                raise ValueError(
                    f"connection {connection.id!r} connects a component to itself"
                )
            for connector_id in connection.connector_component_ids:
                _require_reference(
                    "connection connector component", connector_id, component_ids
                )

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
                _require_reference(
                    "load path connection", connection_id, connection_ids
                )
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
            _unique_ids(
                "analysis member distributed loads",
                self.analysis.member_distributed_loads,
            )
            _unique_ids(
                "analysis load combinations",
                self.analysis.load_combinations,
            )
            surface_load_ids = {load.id for load in self.loads}
            components_by_id = {
                component.id: component for component in self.components
            }
            member_lengths: dict[str, float] = {}
            for member in self.analysis.members:
                _require_reference(
                    "analysis member component", member.component_id, component_ids
                )
                _require_reference(
                    "analysis member section", member.section_id, section_ids
                )
                _require_reference(
                    "analysis member material", member.material_id, material_ids
                )
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
            for member_point_load in self.analysis.member_loads:
                _require_reference(
                    "analysis load member", member_point_load.member_id, member_ids
                )
                _require_reference(
                    "analysis load case", member_point_load.case_id, load_case_ids
                )
                if member_point_load.source_load_id is not None:
                    _require_reference(
                        "analysis load source",
                        member_point_load.source_load_id,
                        surface_load_ids,
                    )
                if (
                    not 0
                    <= member_point_load.distance_m
                    <= member_lengths[member_point_load.member_id]
                ):
                    raise ValueError(
                        f"analysis load {member_point_load.id!r} lies outside its member"
                    )
            for member_line_load in self.analysis.member_distributed_loads:
                _require_reference(
                    "analysis distributed load member",
                    member_line_load.member_id,
                    member_ids,
                )
                _require_reference(
                    "analysis distributed load case",
                    member_line_load.case_id,
                    load_case_ids,
                )
                if member_line_load.source_load_id is not None:
                    _require_reference(
                        "analysis distributed load source",
                        member_line_load.source_load_id,
                        surface_load_ids,
                    )
                member_length = member_lengths[member_line_load.member_id]
                if not (
                    0
                    <= member_line_load.start_distance_m
                    < member_line_load.end_distance_m
                    <= member_length
                ):
                    raise ValueError(
                        f"analysis distributed load {member_line_load.id!r} lies "
                        "outside its member"
                    )
            for combination in self.analysis.load_combinations:
                if not combination.factors:
                    raise ValueError(
                        f"analysis load combination {combination.id!r} has no factors"
                    )
                for case_id in combination.factors:
                    _require_reference(
                        "analysis load combination factor",
                        case_id,
                        load_case_ids,
                    )
            if self.analysis.stability is not None:
                stability = self.analysis.stability
                _require_reference(
                    "stability combination",
                    stability.stability_combination_id,
                    {item.id for item in self.analysis.load_combinations},
                )
                _require_reference(
                    "stability imperfection case",
                    stability.imperfection_case_id,
                    load_case_ids,
                )
                imperfection_case = next(
                    item
                    for item in self.analysis.load_cases
                    if item.id == stability.imperfection_case_id
                )
                if imperfection_case.category != "imperfection":
                    raise ValueError(
                        "stability imperfection case must use category 'imperfection'"
                    )
        return self


class CompiledStructuralManifest(StructuralContract):
    schema_version: Literal["1.0"] = "1.0"
    source_hash: str = Field(min_length=64, max_length=64)
    design_hash: str = Field(min_length=64, max_length=64)
    declaration: dict[str, Any]


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
    design_basis: StructuralDesignBasis | None = None
    units: StructuralUnits = Field(default_factory=StructuralUnits)
    nodes: list[StructuralNode]
    members: list[StructuralMember]
    sections: list[SectionProperties]
    materials: list[StructuralMaterial]
    load_cases: list[LoadCase]
    load_combinations: list[LoadCombination] = Field(default_factory=list)
    loads: list[NodalLoad]
    member_loads: list[MemberPointLoad] = Field(default_factory=list)
    member_distributed_loads: list[MemberDistributedLoad] = Field(default_factory=list)
    reactions: list[NodeReaction]
    member_results: list[MemberResult]
    member_diagrams: list[MemberDiagram] = Field(default_factory=list)
    member_checks: list[MemberCheck]
    serviceability_checks: list[ServiceabilityCheck] = Field(default_factory=list)
    load_summary: LoadSummary = Field(
        default_factory=lambda: LoadSummary(
            member_mass_kg=0,
            self_weight_kN=0,
            additional_dead_load_kN=0,
            imposed_load_kN=0,
            wind_load_kN=0,
        )
    )
    equilibrium: EquilibriumDiagnostic
    solver: SolverMetadata
    stability: StabilityResult | None = None
    verification_stages: list[VerificationStage] = Field(default_factory=list)
    calculation_sheets: list[CalculationSheet] = Field(default_factory=list)
    capabilities: list[CapabilityState]
    warnings: list[str]

    @model_validator(mode="after")
    def validate_graph_references(self) -> StructuralSnapshot:
        node_ids = _unique_ids("nodes", self.nodes)
        member_ids = _unique_ids("members", self.members)
        section_ids = _unique_ids("sections", self.sections)
        material_ids = _unique_ids("materials", self.materials)
        load_case_ids = _unique_ids("load cases", self.load_cases)
        _unique_ids("load combinations", self.load_combinations)

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
        for member_point_load in self.member_loads:
            _require_reference(
                "member load member", member_point_load.member_id, member_ids
            )
            _require_reference(
                "member load case", member_point_load.case_id, load_case_ids
            )
        for member_line_load in self.member_distributed_loads:
            _require_reference(
                "distributed member load member",
                member_line_load.member_id,
                member_ids,
            )
            _require_reference(
                "distributed member load case",
                member_line_load.case_id,
                load_case_ids,
            )
        for combination in self.load_combinations:
            for case_id in combination.factors:
                _require_reference(
                    "load combination factor",
                    case_id,
                    load_case_ids,
                )
        for reaction in self.reactions:
            _require_reference("reaction node", reaction.node_id, node_ids)
        for result in self.member_results:
            _require_reference("result member", result.member_id, member_ids)
        for diagram in self.member_diagrams:
            _require_reference("diagram member", diagram.member_id, member_ids)
        for capacity_check in self.member_checks:
            _require_reference("check member", capacity_check.member_id, member_ids)
        for service_check in self.serviceability_checks:
            _require_reference(
                "serviceability check member",
                service_check.member_id,
                member_ids,
            )
        return self


def _unique_ids(label: str, values: Sequence[StructuralContract]) -> set[str]:
    identifiers = [str(getattr(value, "id")) for value in values]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label} contain duplicate IDs")
    return set(identifiers)


def _require_reference(label: str, reference: str, valid_ids: set[str]) -> None:
    if reference not in valid_ids:
        raise ValueError(f"{label} references missing ID {reference!r}")
