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
    tension_only: bool = False
    compression_only: bool = False
    analytical_role: Literal["physical", "rigid_zone"] = "physical"
    source_connection_id: str | None = None


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
    start_node_key: str | None = None
    end_node_key: str | None = None
    start_restraints: Restraints = Field(default_factory=Restraints)
    end_restraints: Restraints = Field(default_factory=Restraints)
    section_id: str
    material_id: str
    rotation_deg: float = 0.0
    start_releases: Restraints = Field(default_factory=Restraints)
    end_releases: Restraints = Field(default_factory=Restraints)
    tension_only: bool = False
    compression_only: bool = False
    analytical_role: Literal["physical", "rigid_zone"] = "physical"
    source_connection_id: str | None = None
    tension_capacity_status: Literal["not_checked", "candidate", "verified"] = (
        "not_checked"
    )
    tension_capacity_kN: float | None = None
    tension_capacity_basis: str | None = None
    end_fastener_count: int | None = None
    end_connection_capacity_kN: float | None = None
    end_connection_basis: str | None = None
    deflection_limit_ratio: float | None = None
    deflection_limit_mm: float | None = None
    deflection_limit_basis: str | None = None
    serviceability_group_id: str | None = None
    serviceability_group_label: str | None = None
    serviceability_span_m: float | None = Field(default=None, gt=0)
    assumption: str

    @model_validator(mode="after")
    def validate_tension_evidence(self) -> AnalyticalMemberDeclaration:
        if self.analytical_role == "rigid_zone" and not self.source_connection_id:
            raise ValueError("rigid-zone members require a source connection ID")
        if any(
            value is not None and value <= 0
            for value in (self.tension_capacity_kN, self.end_connection_capacity_kN)
        ):
            raise ValueError("tension capacities must be positive")
        if self.end_fastener_count is not None and self.end_fastener_count <= 0:
            raise ValueError("end fastener count must be positive")
        if (
            any(
                value is not None
                for value in (
                    self.tension_capacity_kN,
                    self.end_fastener_count,
                    self.end_connection_capacity_kN,
                )
            )
            and not self.tension_only
        ):
            raise ValueError("tension evidence requires a tension-only member")
        if self.tension_capacity_status == "verified" and (
            self.tension_capacity_kN is None
            or not self.tension_capacity_basis
            or self.end_connection_capacity_kN is None
            or not self.end_connection_basis
        ):
            raise ValueError(
                "verified tension evidence requires member and connection capacities"
            )
        return self


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
    provenance: str | None = None


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
    purpose: Literal["design", "stability_probe"] = "design"


class UnavailableLoadCombination(StructuralContract):
    """A Tertius-owned formula that cannot yet be assembled for this project."""

    id: str
    label: str
    limit_state: Literal["serviceability", "ultimate"]
    family: Literal["action_standard", "global_stability"]
    missing_inputs: list[str] = Field(default_factory=list)
    reason: str


class ActionStandardPackEvidence(StructuralContract):
    pack_id: str
    pack_version: str
    standard_reference: str
    status: Literal["working", "verified"]
    combination_ids: list[str]
    basis: str


class StabilityDirectionDefinition(StructuralContract):
    id: str
    base_combination_id: str | None = None
    stability_combination_id: str
    imperfection_case_id: str
    nhf_combination_id: str
    horizontal_axis: Literal["x", "y"] = "x"
    direction_sign: Literal[-1, 1] = 1


class StabilityDefinition(StructuralContract):
    method: Literal["p_delta"]
    stability_combination_id: str
    imperfection_case_id: str
    imperfection_basis: str
    base_stiffness_basis: str
    base_stiffness_status: Literal["verified", "assumed"]
    amplification_warning_ratio: float = Field(default=1.1, gt=1.0)
    direction_cases: list[StabilityDirectionDefinition] = Field(default_factory=list)
    column_component_ids: list[str] = Field(default_factory=list)
    eaves_member_ids: list[str] = Field(default_factory=list)
    rafter_member_ids: list[str] = Field(default_factory=list)
    column_height_m: float | None = Field(default=None, gt=0)
    analysis_base_model: Literal[
        "unspecified", "perfectly_pinned", "rotational_spring", "fixed"
    ] = "unspecified"
    analysis_basis_status: Literal["assumed", "verified", "verified_conservative"] = (
        "assumed"
    )
    physical_connection_stiffness_status: Literal[
        "not_checked", "not_relied_upon", "verified"
    ] = "not_checked"


class MemberStabilityComparison(StructuralContract):
    member_id: str
    first_order_max_moment_kNm: float
    second_order_max_moment_kNm: float
    moment_amplification: float
    first_order_max_displacement_mm: float
    second_order_max_displacement_mm: float
    displacement_amplification: float


class StabilityDirectionResult(StructuralContract):
    id: str
    combination_id: str
    imperfection_case_id: str
    nhf_combination_id: str
    horizontal_axis: Literal["x", "y"]
    converged: bool
    governing_moment_amplification: float
    governing_displacement_amplification: float
    nhf_eaves_displacement_mm: float
    alpha_cr: float | None = None
    member_comparisons: list[MemberStabilityComparison]


class StabilityResult(StructuralContract):
    method: Literal["p_delta"]
    combination_id: str
    imperfection_case_id: str
    converged: bool
    amplification_warning_ratio: float
    governing_moment_amplification: float
    governing_displacement_amplification: float
    member_comparisons: list[MemberStabilityComparison]
    direction_results: list[StabilityDirectionResult] = Field(default_factory=list)
    governing_direction_id: str | None = None
    minimum_alpha_cr: float | None = None
    second_order_required: bool | None = None
    rafter_design_axial_kN: float | None = None
    rafter_elastic_critical_load_kN: float | None = None
    rafter_axial_limit_kN: float | None = None
    rafter_axial_force_significant: bool | None = None
    simplified_alpha_cr_applicable: bool | None = None


class CrossSectionVerificationDefinition(StructuralContract):
    pack_id: Literal["as_nzs_4600_2018_ewm"]
    combination_ids: list[str] = Field(min_length=1)
    member_ids: list[str] = Field(default_factory=list)
    off_axis_tolerance: float = Field(default=1e-6, ge=0)


class MemberStabilitySegmentDefinition(StructuralContract):
    id: str
    member_id: str
    start_distance_m: float = Field(ge=0)
    end_distance_m: float = Field(gt=0)
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
    start_restraint_candidate_ids: list[str] = Field(default_factory=list)
    end_restraint_candidate_ids: list[str] = Field(default_factory=list)


class RestraintConfigurationIdentity(StructuralContract):
    """Exact rendered component identities used to select restraint evidence."""

    primary_part_number: str | None = None
    bracing_part_number: str | None = None
    connector_part_numbers: list[str] = Field(default_factory=list)


class MemberRestraintCandidateDefinition(StructuralContract):
    id: str
    member_id: str
    bracing_component_id: str
    connection_id: str
    connector_component_ids: list[str] = Field(default_factory=list)
    member_position: Vector3
    brace_position: Vector3
    distance_m: float = Field(ge=0)
    axis_separation_m: float = Field(ge=0)
    restrains_lateral_translation: bool
    restrains_twist: bool
    restrained_flange: Literal[
        "auto",
        "positive_local_y",
        "negative_local_y",
        "both",
    ] = "auto"
    demand_model: Literal[
        "not_defined",
        "aisi_2004_d3_2_2_eccentric_load_couple",
    ] = "not_defined"
    demand_factor: float = Field(default=1.5, gt=0)
    design_force_capacity_kN: float | None = Field(default=None, gt=0)
    design_moment_capacity_kNm: float | None = Field(default=None, gt=0)
    stiffness_status: Literal["unverified", "verified"] = "unverified"
    evidence_status: Literal["candidate", "verified", "unsupported"] = "candidate"
    evidence_basis: str
    capacity_basis: str
    provenance: str
    evidence_pack_id: str | None = None
    configuration: RestraintConfigurationIdentity = Field(
        default_factory=RestraintConfigurationIdentity
    )
    anchorage_status: Literal["unverified", "verified"] = "unverified"
    anchorage_component_ids: list[str] = Field(default_factory=list)
    anchorage_connection_ids: list[str] = Field(default_factory=list)
    anchorage_grounded_component_id: str | None = None
    anchorage_basis: str = "No longitudinal anchorage evidence is declared."


class MemberStabilityVerificationDefinition(StructuralContract):
    pack_id: Literal["as_nzs_4600_2018_ewm_member"]
    combination_ids: list[str] = Field(min_length=1)
    segments: list[MemberStabilitySegmentDefinition] = Field(min_length=1)
    restraint_candidates: list[MemberRestraintCandidateDefinition] = Field(
        default_factory=list
    )
    off_axis_tolerance: float = Field(default=1e-6, ge=0)


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
    major_moment_kNm: Vector3
    minor_moment_kNm: Vector3
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


class ConnectionCheck(StructuralContract):
    connection_id: str
    label: str
    status: Literal["pass", "fail", "not_checked", "unsupported"]
    evidence_status: Literal["unverified", "candidate", "verified"]
    pack_id: str
    pack_version: str
    identity_status: Literal["pass", "fail"]
    identity_mismatches: list[str] = Field(default_factory=list)
    governing_combination_id: str | None = None
    governing_member_id: str | None = None
    axial_demand_kN: float
    shear_demand_kN: float
    moment_demand_kNm: float
    design_axial_capacity_kN: float | None = None
    design_shear_capacity_kN: float | None = None
    design_moment_capacity_kNm: float | None = None
    axial_utilisation: float | None = None
    shear_utilisation: float | None = None
    moment_utilisation: float | None = None
    governing_utilisation: float | None = None
    expected_connector_part_numbers: list[str] = Field(default_factory=list)
    rendered_connector_part_numbers: list[str] = Field(default_factory=list)
    source: str | None = None
    source_sha256: str | None = None
    basis: str
    assumptions: list[str] = Field(default_factory=list)


class TensionMemberCheck(StructuralContract):
    member_id: str
    label: str
    status: Literal["pass", "fail", "not_checked", "unsupported"]
    capacity_status: Literal["not_checked", "candidate", "verified"]
    governing_combination_id: str | None = None
    tension_demand_kN: float
    tension_capacity_kN: float | None = None
    end_connection_capacity_kN: float | None = None
    governing_capacity_kN: float | None = None
    member_utilisation: float | None = None
    connection_utilisation: float | None = None
    governing_utilisation: float | None = None
    end_fastener_count: int | None = None
    required_force_per_end_fastener_kN: float | None = None
    basis: str
    assumptions: list[str] = Field(default_factory=list)


class MemberCrossSectionCheck(StructuralContract):
    member_id: str
    label: str
    pack_id: Literal["as_nzs_4600_2018_ewm"]
    status: Literal["pass", "fail", "not_checked", "unsupported"]
    governing_combination_id: str | None = None
    governing_station_m: float | None = None
    axial_kN: float | None = None
    major_moment_kNm: float | None = None
    minor_moment_kNm: float | None = None
    web_shear_kN: float | None = None
    off_axis_shear_kN: float | None = None
    torsion_kNm: float | None = None
    design_compression_capacity_kN: float | None = None
    design_major_bending_capacity_kNm: float | None = None
    design_web_shear_capacity_kN: float | None = None
    axial_bending_utilisation: float | None = None
    bending_shear_utilisation: float | None = None
    governing_utilisation: float | None = None
    section_record_sha256: str | None = None
    capacity_factors: dict[str, float] = Field(default_factory=dict)
    web_slenderness: float | None = None
    shear_regime: Literal["stocky", "inelastic_buckling", "elastic_buckling"] | None = (
        None
    )
    off_axis_load_path_status: Literal["not_declared", "candidate", "verified"] = (
        "not_declared"
    )
    off_axis_required_reaction_kN: float | None = None
    off_axis_source_component_ids: list[str] = Field(default_factory=list)
    off_axis_source_connection_ids: list[str] = Field(default_factory=list)
    off_axis_collector_component_ids: list[str] = Field(default_factory=list)
    off_axis_collector_connection_ids: list[str] = Field(default_factory=list)
    off_axis_grounded_component_id: str | None = None
    off_axis_load_path_basis: str | None = None
    basis: str
    assumptions: list[str] = Field(default_factory=list)


class MemberStabilityCheck(StructuralContract):
    segment_id: str
    member_id: str
    label: str
    pack_id: Literal["as_nzs_4600_2018_ewm_member"]
    status: Literal["pass", "fail", "not_checked", "unsupported"]
    governing_combination_id: str | None = None
    governing_station_m: float | None = None
    segment_start_m: float
    segment_end_m: float
    unbraced_length_m: float
    axial_kN: float | None = None
    major_moment_kNm: float | None = None
    elastic_flexural_buckling_stress_MPa: float | None = None
    elastic_torsional_buckling_stress_MPa: float | None = None
    elastic_flexural_torsional_buckling_stress_MPa: float | None = None
    nominal_global_buckling_stress_MPa: float | None = None
    design_member_compression_capacity_kN: float | None = None
    design_major_bending_capacity_kNm: float | None = None
    axial_utilisation: float | None = None
    axial_bending_utilisation: float | None = None
    governing_utilisation: float | None = None
    lateral_bending_restraint: Literal[
        "unverified",
        "continuous_compression_flange",
    ]
    restraint_status: Literal[
        "missing",
        "candidate",
        "inadequate",
        "assumed",
        "verified",
    ]
    compression_flange: Literal[
        "positive_local_y",
        "negative_local_y",
        "none",
        "mixed",
    ] = "none"
    restraint_candidate_ids: list[str] = Field(default_factory=list)
    distortional_buckling_status: Literal["unverified", "verified"]
    section_record_sha256: str | None = None
    basis: str
    assumptions: list[str] = Field(default_factory=list)


class MemberRestraintTrace(StructuralContract):
    id: str
    member_id: str
    combination_id: str
    segment_start_m: float
    segment_end_m: float
    start_position: Vector3
    end_position: Vector3
    compression_flange: Literal[
        "positive_local_y",
        "negative_local_y",
        "none",
    ]
    status: Literal[
        "missing",
        "candidate",
        "inadequate",
        "verified",
        "not_required",
    ]
    start_restraint_candidate_ids: list[str] = Field(default_factory=list)
    end_restraint_candidate_ids: list[str] = Field(default_factory=list)
    effective_restraint_candidate_ids: list[str] = Field(default_factory=list)
    governing_candidate_check_ids: list[str] = Field(default_factory=list)
    required_restraint_force_kN: float | None = None
    available_restraint_force_kN: float | None = None
    restraint_force_utilisation: float | None = None
    basis: str


class MemberRestraintCandidateCheck(StructuralContract):
    id: str
    candidate_id: str
    member_id: str
    connection_id: str
    combination_id: str
    contact_flange: Literal[
        "positive_local_y",
        "negative_local_y",
        "both",
        "none",
    ]
    status: Literal["unsupported", "candidate", "pass", "fail", "not_required"]
    demand_model: Literal[
        "not_defined",
        "aisi_2004_d3_2_2_eccentric_load_couple",
    ]
    transferred_load_kN: float | None = None
    load_eccentricity_m: float | None = None
    member_depth_m: float | None = None
    required_force_kN: float | None = None
    required_moment_kNm: float | None = None
    available_force_kN: float | None = None
    available_moment_kNm: float | None = None
    force_utilisation: float | None = None
    moment_utilisation: float | None = None
    stiffness_status: Literal["unverified", "verified"]
    evidence_pack_id: str | None = None
    evidence_pack_version: str | None = None
    identity_status: Literal["not_declared", "pass", "fail"] = "not_declared"
    identity_mismatches: list[str] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)
    anchorage_status: Literal["unverified", "verified"] = "unverified"
    anchorage_component_ids: list[str] = Field(default_factory=list)
    anchorage_connection_ids: list[str] = Field(default_factory=list)
    anchorage_grounded_component_id: str | None = None
    anchorage_basis: str = "No longitudinal anchorage evidence is declared."
    mechanism: str
    provenance: str
    basis: str


class ServiceabilityCheck(StructuralContract):
    member_id: str
    physical_member_id: str | None = None
    analytical_member_ids: list[str] = Field(default_factory=list)
    span_m: float | None = Field(default=None, gt=0)
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
    combination_selection: Literal[
        "requested", "default", "governing_working_envelope"
    ] = "default"


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


class SupplementalMethod(StructuralContract):
    id: str
    label: str
    reference: str
    role: str


class StructuralDesignBasis(StructuralContract):
    framework_id: str
    framework_label: str
    framework_reference: str
    jurisdiction: str
    analysis_method: str
    building_classification: str | None = None
    importance_level: str | None = None
    design_life_years: int | None = Field(default=None, gt=0)
    compliance_pathway: str = "Engineered solution"
    standards: dict[str, str] = Field(default_factory=dict)
    supplemental_methods: list[SupplementalMethod] = Field(default_factory=list)


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
    primary_reference: str
    supplemental_references: list[str] = Field(default_factory=list)
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
    primary_reference: str
    supplemental_references: list[str] = Field(default_factory=list)
    status: VerificationStatus
    summary: str
    sheet_ids: list[str] = Field(default_factory=list)
    blocking_stage_ids: list[str] = Field(default_factory=list)


class CertificationGate(StructuralContract):
    id: str
    order: int
    label: str
    status: VerificationStatus
    primary_reference: str
    summary: str
    stage_ids: list[str] = Field(default_factory=list)


class CertificationReadiness(StructuralContract):
    scheme_id: Literal["AU-NCC-2022"] = "AU-NCC-2022"
    scheme_label: str = "Australian structural certification readiness"
    document_status: Literal[
        "analysis_incomplete",
        "engineering_review_draft",
        "certificate_ready",
    ]
    draft_document_label: str
    ready_for_engineering_review: bool
    ready_for_certificate: bool
    ready_for_order: bool
    conclusion: str
    blocking_gate_ids: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    gates: list[CertificationGate] = Field(default_factory=list)


class DesignComponent(StructuralContract):
    id: str
    label: str
    kind: Literal["ground", "member", "surface", "connector", "support"]
    visual_node_id: str
    grounded: bool = False
    part_number: str | None = None
    role: str | None = None


class ConnectionMemberEngagement(StructuralContract):
    role: str
    component_id: str
    member_end: Literal["start", "end"]
    joint_point: Vector3
    flexible_axis_end: Vector3
    engagement_length_m: float = Field(gt=0)
    plate_length_m: float = Field(gt=0)
    bolt_line_distances_m: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_connection_engagement(self) -> ConnectionMemberEngagement:
        if self.plate_length_m < self.engagement_length_m:
            raise ValueError("joint plate length cannot be shorter than engagement")
        if any(value <= 0 for value in self.bolt_line_distances_m):
            raise ValueError("joint bolt-line distances must be positive")
        if abs(max(self.bolt_line_distances_m) - self.engagement_length_m) > 1e-9:
            raise ValueError("joint engagement must equal the outermost bolt line")
        return self


class ConnectionJointModel(StructuralContract):
    analysis_model: Literal["pinned", "rigid_zone", "semi_rigid"]
    stiffness_status: Literal["assumed", "candidate", "verified"]
    stiffness_basis: str
    member_engagements: list[ConnectionMemberEngagement] = Field(min_length=2)


class ConnectionResistanceEvidence(StructuralContract):
    pack_id: str
    version: str
    status: Literal["unverified", "candidate", "verified"]
    basis: str
    connector_part_numbers: list[str] = Field(min_length=1)
    source: str | None = None
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    design_axial_capacity_kN: float | None = Field(default=None, gt=0)
    design_shear_capacity_kN: float | None = Field(default=None, gt=0)
    design_moment_capacity_kNm: float | None = Field(default=None, gt=0)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verified_source(self) -> ConnectionResistanceEvidence:
        if self.status == "verified" and (
            self.source is None or self.source_sha256 is None
        ):
            raise ValueError("verified connection resistance requires a hashed source")
        return self


class DesignConnection(StructuralContract):
    id: str
    label: str
    from_component_id: str
    to_component_id: str
    connector_component_ids: list[str] = Field(default_factory=list)
    transfers: list[Literal["force", "shear", "moment", "wind_normal"]]
    joint_model: ConnectionJointModel | None = None
    resistance: ConnectionResistanceEvidence | None = None


class StructuralWindActionBasis(StructuralContract):
    id: str
    site_address: str
    latitude: float
    longitude: float
    region: str
    region_area: str
    region_source: str
    region_approximate: bool = True
    region_status: Literal["suggested", "verified"]
    standard: str
    table_version: str
    table_status: Literal["starter", "verified"]
    importance_level: str
    annual_recurrence_interval_years: int
    terrain_category: str
    reference_height_m: float
    regional_wind_speed_m_s: float
    climate_change_multiplier: float
    direction_multiplier: float
    terrain_height_multiplier: float
    shielding_multiplier: float
    topographic_multiplier: float
    site_wind_speed_m_s: float
    q_z_kPa: float
    enclosure: Literal["enclosed", "open_sided"] | None = None
    openings_operating_state: Literal["normally_closed", "normally_open"] | None = None
    opening_capacity_status: Literal["unverified", "verified"] | None = None
    coefficient_selection_policy: (
        Literal["worst_available_credible", "verified_only"] | None
    ) = None
    building_face: Literal["front", "right", "back", "left"] | None = None
    face_bearing_degrees: float | None = None
    structural_action_direction: Literal["+X", "-X", "+Y", "-Y"] | None = None
    governing_cardinal_direction: (
        Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"] | None
    ) = None
    contributing_cardinal_directions: list[
        Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    ] = Field(default_factory=list)
    verifier_hash: str
    provenance: str


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
    wind_basis_id: str | None = None
    net_pressure_coefficient: float | None = None
    coefficient_status: (
        Literal["assumed", "working_conservative", "verified"] | None
    ) = None


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
    unavailable_load_combinations: list[UnavailableLoadCombination] = Field(
        default_factory=list
    )
    action_standard_pack: ActionStandardPackEvidence | None = None
    stability: StabilityDefinition | None = None
    cross_section_verification: CrossSectionVerificationDefinition | None = None
    member_stability_verification: MemberStabilityVerificationDefinition | None = None


class ProjectStructuralCapture(StructuralContract):
    schema_version: Literal["0.1"] = "0.1"
    project_name: str
    design_hash: str
    analysis_configuration_revision: int | None = None
    analysis_configuration_digest: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    title: str
    authoring_mode: Literal["legacy", "generated"]
    design_basis: StructuralDesignBasis | None = None
    wind_action_bases: list[StructuralWindActionBasis] = Field(default_factory=list)
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
        wind_basis_ids = _unique_ids("wind action bases", self.wind_action_bases)
        wind_bases_by_id = {basis.id: basis for basis in self.wind_action_bases}

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
            if load.wind_basis_id is not None:
                _require_reference(
                    "load wind action basis",
                    load.wind_basis_id,
                    wind_basis_ids,
                )
                if load.case != "wind":
                    raise ValueError(
                        f"load {load.id!r} references a wind basis but is not wind"
                    )
                if load.net_pressure_coefficient is None:
                    raise ValueError(
                        f"load {load.id!r} must declare its net pressure coefficient"
                    )
                if load.coefficient_status is None:
                    raise ValueError(
                        f"load {load.id!r} must declare its coefficient status"
                    )
                expected_pressure = wind_bases_by_id[load.wind_basis_id].q_z_kPa * abs(
                    load.net_pressure_coefficient
                )
                if abs(load.pressure_kPa - expected_pressure) > 1e-6:
                    raise ValueError(
                        f"load {load.id!r} pressure does not equal q_z times "
                        "its net pressure coefficient"
                    )

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
                combination_ids = {
                    item.id for item in self.analysis.load_combinations
                }
                _require_reference(
                    "stability combination",
                    stability.stability_combination_id,
                    combination_ids,
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
                for direction in stability.direction_cases:
                    if direction.base_combination_id is not None:
                        _require_reference(
                            "stability base combination",
                            direction.base_combination_id,
                            combination_ids,
                        )
                    _require_reference(
                        "stability direction combination",
                        direction.stability_combination_id,
                        combination_ids,
                    )
                    _require_reference(
                        "stability NHF combination",
                        direction.nhf_combination_id,
                        combination_ids,
                    )
                    _require_reference(
                        "stability direction imperfection case",
                        direction.imperfection_case_id,
                        load_case_ids,
                    )
            if self.analysis.cross_section_verification is not None:
                verification = self.analysis.cross_section_verification
                combinations_by_id = {
                    item.id: item for item in self.analysis.load_combinations
                }
                analytical_member_ids = {item.id for item in self.analysis.members}
                if len(verification.member_ids) != len(set(verification.member_ids)):
                    raise ValueError(
                        "cross-section verification member IDs must be unique"
                    )
                for member_id in verification.member_ids:
                    _require_reference(
                        "cross-section verification member",
                        member_id,
                        analytical_member_ids,
                    )
                for combination_id in verification.combination_ids:
                    _require_reference(
                        "cross-section verification combination",
                        combination_id,
                        set(combinations_by_id),
                    )
                    if combinations_by_id[combination_id].limit_state != "ultimate":
                        raise ValueError(
                            "cross-section verification combinations must use "
                            f"the ultimate limit state; {combination_id!r} does not"
                        )
            if self.analysis.member_stability_verification is not None:
                member_verification = self.analysis.member_stability_verification
                combinations_by_id = {
                    item.id: item for item in self.analysis.load_combinations
                }
                for combination_id in member_verification.combination_ids:
                    _require_reference(
                        "member-stability verification combination",
                        combination_id,
                        set(combinations_by_id),
                    )
                    if combinations_by_id[combination_id].limit_state != "ultimate":
                        raise ValueError(
                            "member-stability verification combinations must use "
                            f"the ultimate limit state; {combination_id!r} does not"
                        )
                restraint_candidates_by_id: dict[
                    str, MemberRestraintCandidateDefinition
                ] = {}
                for candidate in member_verification.restraint_candidates:
                    if candidate.id in restraint_candidates_by_id:
                        raise ValueError(
                            f"duplicate member-restraint candidate ID {candidate.id!r}"
                        )
                    restraint_candidates_by_id[candidate.id] = candidate
                    _require_reference(
                        "member-restraint candidate member",
                        candidate.member_id,
                        member_ids,
                    )
                    _require_reference(
                        "member-restraint bracing component",
                        candidate.bracing_component_id,
                        component_ids,
                    )
                    _require_reference(
                        "member-restraint connection",
                        candidate.connection_id,
                        connection_ids,
                    )
                    for connector_id in candidate.connector_component_ids:
                        _require_reference(
                            "member-restraint connector component",
                            connector_id,
                            component_ids,
                        )
                    for anchorage_component_id in candidate.anchorage_component_ids:
                        _require_reference(
                            "member-restraint anchorage component",
                            anchorage_component_id,
                            component_ids,
                        )
                    for anchorage_connection_id in candidate.anchorage_connection_ids:
                        _require_reference(
                            "member-restraint anchorage connection",
                            anchorage_connection_id,
                            connection_ids,
                        )
                    if candidate.anchorage_grounded_component_id is not None:
                        _require_reference(
                            "member-restraint grounded anchorage component",
                            candidate.anchorage_grounded_component_id,
                            component_ids,
                        )
                        grounded_component = components_by_id[
                            candidate.anchorage_grounded_component_id
                        ]
                        if not grounded_component.grounded:
                            raise ValueError(
                                f"member-restraint candidate {candidate.id!r} "
                                "identifies an ungrounded anchorage endpoint"
                            )
                    if (
                        candidate.anchorage_status == "verified"
                        and candidate.anchorage_grounded_component_id is None
                    ):
                        raise ValueError(
                            f"verified member-restraint candidate {candidate.id!r} "
                            "requires a grounded anchorage endpoint"
                        )
                    if (
                        candidate.distance_m
                        > member_lengths[candidate.member_id] + 1e-9
                    ):
                        raise ValueError(
                            f"member-restraint candidate {candidate.id!r} lies outside "
                            f"member {candidate.member_id!r}"
                        )
                    if candidate.evidence_status == "verified" and (
                        candidate.design_force_capacity_kN is None
                        or candidate.design_moment_capacity_kNm is None
                        or candidate.stiffness_status != "verified"
                    ):
                        raise ValueError(
                            f"verified member-restraint candidate {candidate.id!r} "
                            "requires verified stiffness and force/moment capacities"
                        )
                segment_ids: set[str] = set()
                for segment in member_verification.segments:
                    if segment.id in segment_ids:
                        raise ValueError(
                            f"duplicate member-stability segment ID {segment.id!r}"
                        )
                    segment_ids.add(segment.id)
                    _require_reference(
                        "member-stability segment member",
                        segment.member_id,
                        member_ids,
                    )
                    member_length = member_lengths[segment.member_id]
                    if not (
                        0
                        <= segment.start_distance_m
                        < segment.end_distance_m
                        <= member_length + 1e-9
                    ):
                        raise ValueError(
                            f"member-stability segment {segment.id!r} lies outside "
                            f"member {segment.member_id!r}"
                        )
                    if (
                        segment.lateral_bending_restraint
                        == "continuous_compression_flange"
                        and segment.restraint_status != "verified"
                    ):
                        raise ValueError(
                            "continuous compression-flange restraint may only be "
                            "used when restraint_status is 'verified'"
                        )
                    for boundary, candidate_ids in (
                        ("start", segment.start_restraint_candidate_ids),
                        ("end", segment.end_restraint_candidate_ids),
                    ):
                        for candidate_id in candidate_ids:
                            _require_reference(
                                f"member-stability segment {boundary} restraint",
                                candidate_id,
                                set(restraint_candidates_by_id),
                            )
                            if (
                                restraint_candidates_by_id[candidate_id].member_id
                                != segment.member_id
                            ):
                                raise ValueError(
                                    f"member-stability segment {segment.id!r} uses "
                                    f"restraint {candidate_id!r} from another member"
                                )
        return self


class CompiledStructuralManifest(StructuralContract):
    schema_version: Literal["1.0"] = "1.0"
    source_hash: str = Field(min_length=64, max_length=64)
    structural_source_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    design_hash: str = Field(min_length=64, max_length=64)
    declaration: dict[str, Any]


class SnapshotSource(StructuralContract):
    kind: Literal["fixture", "design"]
    label: str
    design_id: str | None = None
    design_hash: str | None = None
    analysis_configuration_revision: int | None = None
    analysis_configuration_digest: str | None = None


class StructuralSnapshot(StructuralContract):
    schema_version: Literal["2.0"] = "2.0"
    mode: Literal["fixture", "design"]
    title: str
    subtitle: str
    source: SnapshotSource
    design_basis: StructuralDesignBasis | None = None
    wind_action_bases: list[StructuralWindActionBasis] = Field(default_factory=list)
    units: StructuralUnits = Field(default_factory=StructuralUnits)
    nodes: list[StructuralNode]
    members: list[StructuralMember]
    sections: list[SectionProperties]
    materials: list[StructuralMaterial]
    load_cases: list[LoadCase]
    load_combinations: list[LoadCombination] = Field(default_factory=list)
    unavailable_load_combinations: list[UnavailableLoadCombination] = Field(
        default_factory=list
    )
    action_standard_pack: ActionStandardPackEvidence | None = None
    loads: list[NodalLoad]
    member_loads: list[MemberPointLoad] = Field(default_factory=list)
    member_distributed_loads: list[MemberDistributedLoad] = Field(default_factory=list)
    reactions: list[NodeReaction]
    member_results: list[MemberResult]
    member_diagrams: list[MemberDiagram] = Field(default_factory=list)
    member_checks: list[MemberCheck]
    connection_checks: list[ConnectionCheck] = Field(default_factory=list)
    tension_member_checks: list[TensionMemberCheck] = Field(default_factory=list)
    cross_section_checks: list[MemberCrossSectionCheck] = Field(default_factory=list)
    member_stability_checks: list[MemberStabilityCheck] = Field(default_factory=list)
    member_restraint_candidate_checks: list[MemberRestraintCandidateCheck] = Field(
        default_factory=list
    )
    member_restraint_traces: list[MemberRestraintTrace] = Field(default_factory=list)
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
    certification_readiness: CertificationReadiness | None = None
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
        for stability_check in self.member_stability_checks:
            _require_reference(
                "member-stability check member",
                stability_check.member_id,
                member_ids,
            )
        candidate_check_ids = _unique_ids(
            "member-restraint candidate checks",
            self.member_restraint_candidate_checks,
        )
        candidate_ids = {
            check.candidate_id for check in self.member_restraint_candidate_checks
        }
        for candidate_check in self.member_restraint_candidate_checks:
            _require_reference(
                "member-restraint candidate check member",
                candidate_check.member_id,
                member_ids,
            )
            _require_reference(
                "member-restraint candidate check combination",
                candidate_check.combination_id,
                {combination.id for combination in self.load_combinations},
            )
        for trace in self.member_restraint_traces:
            _require_reference(
                "member-restraint trace member", trace.member_id, member_ids
            )
            _require_reference(
                "member-restraint trace combination",
                trace.combination_id,
                {combination.id for combination in self.load_combinations},
            )
            for check_id in trace.governing_candidate_check_ids:
                _require_reference(
                    "member-restraint trace candidate check",
                    check_id,
                    candidate_check_ids,
                )
            if trace.effective_restraint_candidate_ids:
                missing_candidates = (
                    set(trace.effective_restraint_candidate_ids) - candidate_ids
                )
                if missing_candidates:
                    raise ValueError(
                        "member-restraint trace references candidates without checks "
                        f"{sorted(missing_candidates)}"
                    )
        for service_check in self.serviceability_checks:
            _require_reference(
                "serviceability check member",
                service_check.member_id,
                member_ids,
            )
            for analytical_member_id in service_check.analytical_member_ids:
                _require_reference(
                    "serviceability check analytical member",
                    analytical_member_id,
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
