from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import version
from math import sqrt
from typing import Any, Literal

from .contracts import (
    CalculationEquation,
    CalculationInput,
    CalculationSheet,
    CapabilityState,
    EquilibriumDiagnostic,
    LoadCombination,
    LoadSummary,
    MemberCheck,
    MemberDiagram,
    MemberDiagramStation,
    MemberDistributedLoad,
    MemberResult,
    NodeReaction,
    ProjectStructuralCapture,
    ServiceabilityCheck,
    SnapshotSource,
    SolverMetadata,
    StructuralMember,
    StructuralNode,
    StructuralSnapshot,
    Vector3,
    VerificationStage,
)

DEFAULT_COMBINATION_ID = "SLS-1.0"
STATION_INTERVALS = 32
RESIDUAL_TOLERANCE = 1e-8
STANDARD_GRAVITY_M_S2 = 9.80665
NODE_COORDINATE_DIGITS = 9


class StructuralAnalysisError(ValueError):
    """Raised when the active design cannot be represented by the MVP solver."""


def _clean(value: float, tolerance: float = 1e-12) -> float:
    return 0.0 if abs(value) < tolerance else float(value)


def _vector(values: Sequence[float]) -> Vector3:
    return Vector3(
        x=_clean(values[0]),
        y=_clean(values[1]),
        z=_clean(values[2]),
    )


def _length(start: Vector3, end: Vector3) -> float:
    return sqrt(
        (end.x - start.x) ** 2 + (end.y - start.y) ** 2 + (end.z - start.z) ** 2
    )


def _axis(start: Vector3, end: Vector3) -> Vector3:
    member_length = _length(start, end)
    return Vector3(
        x=(end.x - start.x) / member_length,
        y=(end.y - start.y) / member_length,
        z=(end.z - start.z) / member_length,
    )


def _cross(left: Vector3, right: Vector3) -> tuple[float, float, float]:
    return (
        left.y * right.z - left.z * right.y,
        left.z * right.x - left.x * right.z,
        left.x * right.y - left.y * right.x,
    )


def _add(
    accumulator: list[float],
    value: Vector3 | tuple[float, float, float],
    scale: float = 1.0,
) -> None:
    values = (value.x, value.y, value.z) if isinstance(value, Vector3) else value
    for index in range(3):
        accumulator[index] += values[index] * scale


def _scaled(value: Vector3, scale: float) -> Vector3:
    return Vector3(
        x=value.x * scale,
        y=value.y * scale,
        z=value.z * scale,
    )


def _plus(left: Vector3, right: Vector3) -> Vector3:
    return Vector3(
        x=left.x + right.x,
        y=left.y + right.y,
        z=left.z + right.z,
    )


def _magnitude(value: Vector3) -> float:
    return sqrt(value.x**2 + value.y**2 + value.z**2)


def _local_to_global(rotation, values: tuple[float, float, float]) -> Vector3:
    return _vector(
        tuple(
            sum(float(rotation[row][column]) * values[row] for row in range(3))
            for column in range(3)
        )
    )


def _coordinate_key(position: Vector3) -> tuple[float, float, float]:
    return tuple(
        round(getattr(position, axis), NODE_COORDINATE_DIGITS)
        for axis in ("x", "y", "z")
    )


def _merge_restraints(
    current: dict[str, bool],
    incoming,
) -> None:
    for axis in ("dx", "dy", "dz", "rx", "ry", "rz"):
        current[axis] = current[axis] or bool(getattr(incoming, axis))


def _combination_list(analysis) -> list[LoadCombination]:
    if analysis.load_combinations:
        return list(analysis.load_combinations)
    used_case_ids = {
        load.case_id
        for load in (
            *analysis.member_loads,
            *analysis.member_distributed_loads,
        )
    }
    return [
        LoadCombination(
            id=DEFAULT_COMBINATION_ID,
            label="Serviceability — all authored actions at 1.0",
            limit_state="serviceability",
            factors={
                case.id: 1.0 for case in analysis.load_cases if case.id in used_case_ids
            },
        )
    ]


def _select_combination(
    combinations: list[LoadCombination],
    combination_id: str | None,
) -> LoadCombination:
    if combination_id is not None:
        selected = next(
            (
                combination
                for combination in combinations
                if combination.id == combination_id
            ),
            None,
        )
        if selected is None:
            raise StructuralAnalysisError(
                f"Unknown load combination {combination_id!r}; available combinations: "
                f"{[combination.id for combination in combinations]}"
            )
        return selected
    return next(
        (
            combination
            for combination in combinations
            if combination.limit_state == "serviceability"
        ),
        combinations[0],
    )


def _distributed_resultant(
    load: MemberDistributedLoad,
) -> tuple[Vector3, Vector3]:
    """Return force and first moment about the start of the loaded segment."""

    span = load.end_distance_m - load.start_distance_m
    resultant = Vector3(
        x=span * (load.start_force_kN_m.x + load.end_force_kN_m.x) / 2.0,
        y=span * (load.start_force_kN_m.y + load.end_force_kN_m.y) / 2.0,
        z=span * (load.start_force_kN_m.z + load.end_force_kN_m.z) / 2.0,
    )
    first_moment = Vector3(
        x=span**2 * (load.start_force_kN_m.x + 2.0 * load.end_force_kN_m.x) / 6.0,
        y=span**2 * (load.start_force_kN_m.y + 2.0 * load.end_force_kN_m.y) / 6.0,
        z=span**2 * (load.start_force_kN_m.z + 2.0 * load.end_force_kN_m.z) / 6.0,
    )
    return resultant, first_moment


def _load_summary(
    analysis,
    combination: LoadCombination,
) -> LoadSummary:
    case_categories = {case.id: case.category for case in analysis.load_cases}
    sections = {section.id: section for section in analysis.sections}
    members = {member.id: member for member in analysis.members}
    member_mass_kg = 0.0
    self_weight_kN = 0.0
    additional_dead_load_kN = 0.0
    imposed_load_kN = 0.0
    wind_load_kN = 0.0

    for point_load_summary in analysis.member_loads:
        factor = abs(combination.factors.get(point_load_summary.case_id, 0.0))
        if factor == 0:
            continue
        magnitude = _magnitude(point_load_summary.force) * factor
        category = case_categories[point_load_summary.case_id]
        if category == "dead":
            additional_dead_load_kN += magnitude
        elif category == "live":
            imposed_load_kN += magnitude
        elif category == "wind":
            wind_load_kN += magnitude

    for line_load_summary in analysis.member_distributed_loads:
        factor = abs(combination.factors.get(line_load_summary.case_id, 0.0))
        if factor == 0:
            continue
        resultant, _first_moment = _distributed_resultant(line_load_summary)
        magnitude = _magnitude(resultant) * factor
        category = case_categories[line_load_summary.case_id]
        if line_load_summary.source_kind == "self_weight":
            self_weight_kN += magnitude
            declaration = members[line_load_summary.member_id]
            section = sections[declaration.section_id]
            if section.mass_kg_m is not None:
                member_mass_kg += section.mass_kg_m * (
                    line_load_summary.end_distance_m
                    - line_load_summary.start_distance_m
                )
        elif category == "dead":
            additional_dead_load_kN += magnitude
        elif category == "live":
            imposed_load_kN += magnitude
        elif category == "wind":
            wind_load_kN += magnitude

    return LoadSummary(
        member_mass_kg=member_mass_kg,
        self_weight_kN=self_weight_kN,
        additional_dead_load_kN=additional_dead_load_kN,
        imposed_load_kN=imposed_load_kN,
        wind_load_kN=wind_load_kN,
    )


def _p399_evidence(
    *,
    capture: ProjectStructuralCapture,
    analysis,
    combination: LoadCombination,
    nodes: list[StructuralNode],
    members: list[StructuralMember],
    member_results: list[MemberResult],
    member_checks: list[MemberCheck],
    serviceability_checks: list[ServiceabilityCheck],
    equilibrium_status: Literal["pass", "fail"],
    residual: float,
) -> tuple[list[VerificationStage], list[CalculationSheet]]:
    """Build inspectable evidence for every P399 process stage.

    These sheets distinguish a completed model/data calculation from an
    engineering verification. Unsupported resistance and stability stages are
    deliberately blocked rather than inferred from an elastic demand result.
    """

    basis = capture.design_basis
    basis_references = (
        [
            f"{basis.framework_label} — {basis.framework_reference}",
            *(
                f"{role}: {reference}"
                for role, reference in basis.standards.items()
            ),
        ]
        if basis is not None
        else ["No design basis declared in design.py."]
    )
    member_ids = [member.id for member in members]
    node_ids = [node.id for node in nodes]
    case_ids = [case.id for case in analysis.load_cases]

    geometry_equations = [
        CalculationEquation(
            label=f"{declaration.label} analytical length",
            expression="L = |x_j - x_i|",
            substitution=(
                f"|({declaration.end.x:g}, {declaration.end.y:g}, "
                f"{declaration.end.z:g}) - ({declaration.start.x:g}, "
                f"{declaration.start.y:g}, {declaration.start.z:g})|"
            ),
            result=_length(declaration.start, declaration.end),
            unit="m",
        )
        for declaration in analysis.members
    ]
    action_equations: list[CalculationEquation] = []
    for load in capture.loads:
        action_equations.append(
            CalculationEquation(
                label=f"{load.label} resultant",
                expression="F = p A",
                substitution=f"{load.pressure_kPa:g} × {load.area_m2:g}",
                result=load.pressure_kPa * load.area_m2,
                unit="kN",
            )
        )
    for load in analysis.member_distributed_loads:
        resultant, _ = _distributed_resultant(load)
        span = load.end_distance_m - load.start_distance_m
        action_equations.append(
            CalculationEquation(
                label=f"{load.label} line-load resultant",
                expression="F = |(w_1 + w_2)L/2|",
                substitution=(
                    f"|({load.start_force_kN_m.model_dump()} + "
                    f"{load.end_force_kN_m.model_dump()}) × {span:g}/2|"
                ),
                result=_magnitude(resultant),
                unit="kN",
            )
        )
    for load in analysis.member_loads:
        action_equations.append(
            CalculationEquation(
                label=f"{load.label} point-load magnitude",
                expression="F = sqrt(F_x² + F_y² + F_z²)",
                substitution=str(load.force.model_dump()),
                result=_magnitude(load.force),
                unit="kN",
            )
        )

    combination_equations = [
        CalculationEquation(
            label=combination.label,
            expression="E_comb = Σ γ_i E_i",
            substitution=" + ".join(
                f"{factor:g}×{case_id}"
                for case_id, factor in combination.factors.items()
            ),
            result=combination.id,
        )
    ]
    result_outputs = [
        output
        for result in member_results
        for output in (
            CalculationInput(
                symbol=f"M_max,{result.member_id}",
                label=f"{result.member_id} maximum resultant moment",
                value=result.max_moment_kNm,
                unit="kN.m",
                source=f"PyNite {combination.id} sampled diagram",
            ),
            CalculationInput(
                symbol=f"V_max,{result.member_id}",
                label=f"{result.member_id} maximum resultant shear",
                value=result.max_shear_kN,
                unit="kN",
                source=f"PyNite {combination.id} sampled diagram",
            ),
            CalculationInput(
                symbol=f"δ_max,{result.member_id}",
                label=f"{result.member_id} maximum displacement",
                value=result.max_displacement_mm,
                unit="mm",
                source=f"PyNite {combination.id} sampled diagram",
            ),
        )
    ]
    reference_outputs = [
        CalculationInput(
            symbol=f"M_ref,{check.member_id}",
            label=f"{check.member_id} renderer-only bending reference",
            value=check.capacity_kNm if check.capacity_kNm is not None else "not supplied",
            unit="kN.m" if check.capacity_kNm is not None else None,
            source=check.basis,
        )
        for check in member_checks
    ]
    checked_serviceability = [
        check for check in serviceability_checks if check.status != "not_checked"
    ]
    serviceability_status: Literal["pass", "fail", "not_checked"] = (
        "not_checked"
        if not checked_serviceability
        else "fail"
        if any(check.status == "fail" for check in checked_serviceability)
        else "pass"
    )
    basis_status: Literal["pass", "blocked"] = "pass" if basis is not None else "blocked"
    action_standard_references = [
        reference
        for role, reference in (basis.standards.items() if basis else [])
        if "action" in role or "wind" in role
    ]
    action_basis_ready = bool(action_standard_references) and all(
        "confirm" not in reference.lower()
        and "not yet active" not in reference.lower()
        for reference in action_standard_references
    )
    actions_status: Literal["pass", "warning", "blocked"] = (
        "blocked"
        if not action_equations or basis is None
        else "pass"
        if action_basis_ready
        else "warning"
    )
    combination_reference = (
        next(
            (
                reference
                for role, reference in basis.standards.items()
                if "combination" in role
            ),
            "",
        )
        if basis
        else ""
    )
    combinations_status: Literal["pass", "warning", "blocked"] = (
        "blocked"
        if not combination.factors or basis is None
        else "warning"
        if "confirm" in combination_reference.lower()
        or "not yet active" in combination_reference.lower()
        else "pass"
    )
    analysis_status: Literal["pass", "fail", "blocked"] = (
        "blocked"
        if basis is None
        else "pass"
        if equilibrium_status == "pass"
        else "fail"
    )

    sheets = [
        CalculationSheet(
            id="sheet-p399-geometry",
            stage_id="geometry",
            title="Geometry and analytical scheme",
            status=basis_status,
            p399_reference="SCI P399 Sections 3 and 6.1",
            purpose="Prove which design.py geometry became nodes, members, and supports.",
            assumptions=list(dict.fromkeys(member.assumption for member in analysis.members)),
            inputs=[
                CalculationInput(
                    symbol="n_member",
                    label="Analytical members",
                    value=len(members),
                    source="design.py StructuralModel.member_axis declarations",
                ),
                CalculationInput(
                    symbol="n_node",
                    label="Shared analytical nodes",
                    value=len(nodes),
                    source="Coincident member end coordinates",
                ),
                CalculationInput(
                    symbol="n_support",
                    label="Restrained nodes",
                    value=sum(any(node.restraints.model_dump().values()) for node in nodes),
                    source="design.py authored end restraints",
                ),
            ],
            equations=geometry_equations,
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
        ),
        CalculationSheet(
            id="sheet-p399-actions",
            stage_id="actions",
            title="Actions and tributary transfer",
            status=actions_status,
            p399_reference="SCI P399 Section 4",
            purpose="Trace every authored action from its physical source to member load.",
            assumptions=(
                []
                if actions_status == "pass"
                else [
                    "The authored actions remain illustrative until the project/site "
                    "Australian action inputs are confirmed."
                ]
            ),
            equations=action_equations,
            references=basis_references,
            related_member_ids=member_ids,
            related_load_case_ids=case_ids,
        ),
        CalculationSheet(
            id="sheet-p399-combinations",
            stage_id="combinations",
            title="Active action combination",
            status=combinations_status,
            p399_reference="SCI P399 Section 4.7",
            purpose="Expose factors selected for the current solve; no hidden combinations.",
            assumptions=(
                []
                if combinations_status == "pass"
                else [
                    "The displayed factors are authored test combinations, not a "
                    "completed AS/NZS 1170.0 project combination set."
                ]
            ),
            inputs=[
                CalculationInput(
                    symbol="limit_state",
                    label="Limit state",
                    value=combination.limit_state,
                    source="design.py load_combination declaration",
                )
            ],
            equations=combination_equations,
            references=basis_references,
            related_load_case_ids=list(combination.factors),
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-p399-analysis",
            stage_id="analysis",
            title="Elastic frame analysis",
            status=analysis_status,
            p399_reference="SCI P399 Section 5",
            purpose="Record the active solver method, member demands, and equilibrium audit.",
            inputs=[
                CalculationInput(
                    symbol="method",
                    label="Declared analysis method",
                    value=basis.analysis_method if basis else "not declared",
                    source="design.py design_basis",
                )
            ],
            equations=[
                CalculationEquation(
                    label="Global equilibrium residual",
                    expression="r = max|ΣF, ΣM|",
                    substitution=f"active combination {combination.id}",
                    result=residual,
                    unit="kN / kN.m",
                )
            ],
            outputs=result_outputs,
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-p399-stability",
            stage_id="stability",
            title="Imperfections and global stability",
            status="blocked",
            p399_reference="SCI P399 Sections 7.2–7.8",
            purpose="Determine first/second-order applicability and frame stability.",
            assumptions=[
                "Equivalent horizontal forces/geometric imperfections are not authored.",
                "Elastic critical factor and second-order amplification are not implemented.",
                "Base rotational stiffness has not been verified for stability analysis.",
            ],
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-p399-cross-section",
            stage_id="cross_section",
            title="Cross-section verification",
            status="not_checked",
            p399_reference="SCI P399 Section 8.1",
            purpose="Check classification/effective properties and governing force interactions.",
            assumptions=[
                "The current Zxe × fy value is retained only to scale renderer demand.",
                "No Australian capacity factor or complete cold-formed interaction check is active.",
            ],
            outputs=reference_outputs,
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-p399-member-stability",
            stage_id="member_stability",
            title="Member stability",
            status="blocked",
            p399_reference="SCI P399 Sections 8.2–8.4",
            purpose="Verify buckling and axial-bending interaction on restraint-defined segments.",
            assumptions=[
                "Unbraced lengths and compression-flange restraints are not authored.",
                "Local, distortional, flexural, and lateral-torsional buckling are not checked.",
            ],
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-p399-bracing",
            stage_id="bracing",
            title="Bracing and restraint",
            status="blocked",
            p399_reference="SCI P399 Section 9",
            purpose="Trace restraint forces and stiffness to a complete resisting system.",
            assumptions=[
                "Cladding and fasteners are not assumed to provide unverified restraint.",
                "No restraint segments or bracing stiffness checks are active.",
            ],
            references=basis_references,
            related_member_ids=member_ids,
        ),
        CalculationSheet(
            id="sheet-p399-connections",
            stage_id="connections",
            title="Connections and bases",
            status="blocked",
            p399_reference="SCI P399 Section 11",
            purpose="Verify brackets, fasteners, anchors, concrete, and base behaviour.",
            assumptions=[
                "Rendered screws, bolts, bracket, anchors, and concrete are physical evidence only.",
                "Connection stiffness and resistance are not yet calculated.",
            ],
            references=basis_references,
            related_node_ids=[
                node.id for node in nodes if any(node.restraints.model_dump().values())
            ],
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-p399-serviceability",
            stage_id="serviceability",
            title="Serviceability",
            status=serviceability_status,
            p399_reference="SCI P399 Section 12",
            purpose="Compare elastic SLS movement with explicitly authored project criteria.",
            outputs=[
                CalculationInput(
                    symbol=f"δ/{check.member_id}",
                    label=check.label,
                    value=check.displacement_mm,
                    unit="mm",
                    source=check.basis,
                )
                for check in serviceability_checks
            ],
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-p399-decision",
            stage_id="decision",
            title="Evidence and order decision",
            status="blocked",
            p399_reference="SCI P399 complete verification process",
            purpose="Prevent a green design/order decision while required stages are incomplete.",
            assumptions=[
                "Current result is analysis evidence only and is not suitable for ordering.",
                "Impact, robustness, progressive collapse, and controlled failure are separate studies.",
            ],
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
            related_combination_ids=[combination.id],
        ),
    ]
    stages = [
        VerificationStage(
            id="geometry",
            order=1,
            label="Geometry",
            p399_reference="§3, §6.1",
            status=basis_status,
            summary=(
                f"{len(members)} members, {len(nodes)} nodes, "
                f"{sum(any(node.restraints.model_dump().values()) for node in nodes)} supports."
            ),
            sheet_ids=["sheet-p399-geometry"],
        ),
        VerificationStage(
            id="actions",
            order=2,
            label="Actions",
            p399_reference="§4",
            status=actions_status,
            summary=f"{len(action_equations)} authored action resultants traced.",
            sheet_ids=["sheet-p399-actions"],
            blocking_stage_ids=[] if basis_status == "pass" else ["geometry"],
        ),
        VerificationStage(
            id="combinations",
            order=3,
            label="Combinations",
            p399_reference="§4.7",
            status=combinations_status,
            summary=f"{combination.id}: {len(combination.factors)} explicit factors.",
            sheet_ids=["sheet-p399-combinations"],
            blocking_stage_ids=[] if actions_status in {"pass", "warning"} else ["actions"],
        ),
        VerificationStage(
            id="analysis",
            order=4,
            label="Analysis",
            p399_reference="§5",
            status=analysis_status,
            summary=f"PyNite elastic solve; equilibrium residual {residual:.3e}.",
            sheet_ids=["sheet-p399-analysis"],
            blocking_stage_ids=(
                [] if combinations_status in {"pass", "warning"} else ["combinations"]
            ),
        ),
        VerificationStage(
            id="stability",
            order=5,
            label="Global stability",
            p399_reference="§7.2–§7.8",
            status="blocked",
            summary="Imperfections, αcr/second-order effects, and base stiffness are missing.",
            sheet_ids=["sheet-p399-stability"],
            blocking_stage_ids=["analysis"],
        ),
        VerificationStage(
            id="cross_section",
            order=6,
            label="Cross-section",
            p399_reference="§8.1",
            status="not_checked",
            summary="Demand is available; Australian resistance and interaction checks are not.",
            sheet_ids=["sheet-p399-cross-section"],
            blocking_stage_ids=["stability"],
        ),
        VerificationStage(
            id="member_stability",
            order=7,
            label="Member stability",
            p399_reference="§8.2–§8.4",
            status="blocked",
            summary="Restraint-defined segments and buckling checks are missing.",
            sheet_ids=["sheet-p399-member-stability"],
            blocking_stage_ids=["stability", "cross_section"],
        ),
        VerificationStage(
            id="bracing",
            order=8,
            label="Bracing/restraint",
            p399_reference="§9",
            status="blocked",
            summary="No verified restraint or bracing load path is active.",
            sheet_ids=["sheet-p399-bracing"],
            blocking_stage_ids=["member_stability"],
        ),
        VerificationStage(
            id="connections",
            order=9,
            label="Connections/bases",
            p399_reference="§11",
            status="blocked",
            summary="Rendered detail exists; resistance and stiffness checks do not.",
            sheet_ids=["sheet-p399-connections"],
            blocking_stage_ids=["analysis"],
        ),
        VerificationStage(
            id="serviceability",
            order=10,
            label="Serviceability",
            p399_reference="§12",
            status=serviceability_status,
            summary=(
                f"{len(checked_serviceability)} authored SLS criteria evaluated."
                if checked_serviceability
                else "Select an SLS combination with an authored deflection criterion."
            ),
            sheet_ids=["sheet-p399-serviceability"],
            blocking_stage_ids=[] if analysis_status == "pass" else ["analysis"],
        ),
        VerificationStage(
            id="decision",
            order=11,
            label="Evidence/decision",
            p399_reference="Complete process",
            status="blocked",
            summary="NOT READY TO ORDER: required P399 stages remain incomplete.",
            sheet_ids=["sheet-p399-decision"],
            blocking_stage_ids=[
                "stability",
                "cross_section",
                "member_stability",
                "bracing",
                "connections",
            ],
        ),
    ]
    return stages, sheets


def solve_project_structural(
    capture: ProjectStructuralCapture,
    *,
    combination_id: str | None = None,
) -> StructuralSnapshot:
    analysis = capture.analysis
    if analysis is None or not analysis.members:
        raise StructuralAnalysisError("Active design has no analytical member axes")
    if not analysis.member_loads and not analysis.member_distributed_loads:
        raise StructuralAnalysisError("Active design has no analytical member loads")

    from Pynite import FEModel3D

    combinations = _combination_list(analysis)
    active_combination = _select_combination(combinations, combination_id)
    components = {component.id: component for component in capture.components}

    model = FEModel3D()
    for material in analysis.materials:
        model.add_material(
            material.id,
            E=material.elastic_modulus_kN_m2,
            G=material.shear_modulus_kN_m2,
            nu=material.poisson_ratio,
            rho=material.density_kg_m3,
        )
    for section in analysis.sections:
        model.add_section(
            section.id,
            A=section.area_m2,
            Iy=section.iy_m4,
            Iz=section.iz_m4,
            J=section.torsion_j_m4,
        )

    nodes_by_coordinate: dict[tuple[float, float, float], dict[str, Any]] = {}
    member_node_ids: dict[str, tuple[str, str]] = {}
    for declaration in analysis.members:
        component = components[declaration.component_id]
        member_nodes: list[str] = []
        for position, restraints in (
            (declaration.start, declaration.start_restraints),
            (declaration.end, declaration.end_restraints),
        ):
            key = _coordinate_key(position)
            node = nodes_by_coordinate.get(key)
            if node is None:
                node = {
                    "id": f"node-{len(nodes_by_coordinate) + 1}",
                    "position": position,
                    "restraints": {
                        "dx": False,
                        "dy": False,
                        "dz": False,
                        "rx": False,
                        "ry": False,
                        "rz": False,
                    },
                    "visual_node_id": component.visual_node_id,
                    "labels": [],
                }
                nodes_by_coordinate[key] = node
            _merge_restraints(node["restraints"], restraints)
            node["labels"].append(declaration.label)
            member_nodes.append(node["id"])
        member_node_ids[declaration.id] = (member_nodes[0], member_nodes[1])

    for node in nodes_by_coordinate.values():
        position = node["position"]
        model.add_node(node["id"], position.x, position.y, position.z)
        restraints = node["restraints"]
        model.def_support(
            node["id"],
            support_DX=restraints["dx"],
            support_DY=restraints["dy"],
            support_DZ=restraints["dz"],
            support_RX=restraints["rx"],
            support_RY=restraints["ry"],
            support_RZ=restraints["rz"],
        )

    for declaration in analysis.members:
        start_node_id, end_node_id = member_node_ids[declaration.id]
        model.add_member(
            declaration.id,
            start_node_id,
            end_node_id,
            declaration.material_id,
            declaration.section_id,
            rotation=declaration.rotation_deg,
        )
        start_releases = declaration.start_releases
        end_releases = declaration.end_releases
        if any(
            getattr(releases, axis)
            for releases in (start_releases, end_releases)
            for axis in ("dx", "dy", "dz", "rx", "ry", "rz")
        ):
            model.def_releases(
                declaration.id,
                Dxi=start_releases.dx,
                Dyi=start_releases.dy,
                Dzi=start_releases.dz,
                Rxi=start_releases.rx,
                Ryi=start_releases.ry,
                Rzi=start_releases.rz,
                Dxj=end_releases.dx,
                Dyj=end_releases.dy,
                Dzj=end_releases.dz,
                Rxj=end_releases.rx,
                Ryj=end_releases.ry,
                Rzj=end_releases.rz,
            )

    direction_names = (
        ("FX", "x"),
        ("FY", "y"),
        ("FZ", "z"),
        ("MX", "x"),
        ("MY", "y"),
        ("MZ", "z"),
    )
    for point_load in analysis.member_loads:
        for direction, axis_name in direction_names[:3]:
            magnitude = getattr(point_load.force, axis_name)
            if magnitude:
                model.add_member_pt_load(
                    point_load.member_id,
                    direction,
                    magnitude,
                    point_load.distance_m,
                    case=point_load.case_id,
                )
        for direction, axis_name in direction_names[3:]:
            magnitude = getattr(point_load.moment, axis_name)
            if magnitude:
                model.add_member_pt_load(
                    point_load.member_id,
                    direction,
                    magnitude,
                    point_load.distance_m,
                    case=point_load.case_id,
                )

    for line_load in analysis.member_distributed_loads:
        for direction, axis_name in direction_names[:3]:
            start_magnitude = getattr(line_load.start_force_kN_m, axis_name)
            end_magnitude = getattr(line_load.end_force_kN_m, axis_name)
            if start_magnitude or end_magnitude:
                model.add_member_dist_load(
                    line_load.member_id,
                    direction,
                    start_magnitude,
                    end_magnitude,
                    x1=line_load.start_distance_m,
                    x2=line_load.end_distance_m,
                    case=line_load.case_id,
                )

    for combination in combinations:
        model.add_load_combo(combination.id, dict(combination.factors))
    try:
        model.analyze(check_statics=False, log=False)
    except Exception as exc:
        raise StructuralAnalysisError(
            f"PyNite could not solve the active design: {exc}"
        ) from exc

    structural_nodes = [
        StructuralNode(
            id=node["id"],
            label=" / ".join(dict.fromkeys(node["labels"])),
            position=node["position"],
            restraints=node["restraints"],
            visual_node_id=node["visual_node_id"],
        )
        for node in nodes_by_coordinate.values()
    ]
    structural_members: list[StructuralMember] = []
    member_results: list[MemberResult] = []
    member_diagrams: list[MemberDiagram] = []
    member_checks: list[MemberCheck] = []
    serviceability_checks: list[ServiceabilityCheck] = []
    sections_by_id = {section.id: section for section in analysis.sections}

    for declaration in analysis.members:
        component = components[declaration.component_id]
        start_node_id, end_node_id = member_node_ids[declaration.id]
        structural_members.append(
            StructuralMember(
                id=declaration.id,
                label=declaration.label,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                section_id=declaration.section_id,
                material_id=declaration.material_id,
                visual_node_id=component.visual_node_id,
            )
        )
        member_length = _length(declaration.start, declaration.end)
        point_loads = [
            load for load in analysis.member_loads if load.member_id == declaration.id
        ]
        distributed_loads = [
            load
            for load in analysis.member_distributed_loads
            if load.member_id == declaration.id
        ]
        station_distances = {
            member_length * index / STATION_INTERVALS
            for index in range(STATION_INTERVALS + 1)
        }
        station_distances.update(load.distance_m for load in point_loads)
        for station_line_load in distributed_loads:
            station_distances.update(
                (
                    station_line_load.start_distance_m,
                    station_line_load.end_distance_m,
                )
            )

        member = model.members[declaration.id]
        rotation = member.T()[:3, :3]
        stations: list[MemberDiagramStation] = []
        max_moment = 0.0
        max_local_moment_y = 0.0
        max_local_moment_z = 0.0
        max_shear = 0.0
        max_axial = 0.0
        max_displacement = 0.0
        for distance in sorted(station_distances):
            ratio = distance / member_length
            position = Vector3(
                x=declaration.start.x
                + (declaration.end.x - declaration.start.x) * ratio,
                y=declaration.start.y
                + (declaration.end.y - declaration.start.y) * ratio,
                z=declaration.start.z
                + (declaration.end.z - declaration.start.z) * ratio,
            )
            local_moment = (
                0.0,
                member.moment("My", distance, active_combination.id),
                member.moment("Mz", distance, active_combination.id),
            )
            local_shear = (
                0.0,
                member.shear("Fy", distance, active_combination.id),
                member.shear("Fz", distance, active_combination.id),
            )
            local_displacement = (
                member.deflection("dx", distance, active_combination.id) * 1000.0,
                member.deflection("dy", distance, active_combination.id) * 1000.0,
                member.deflection("dz", distance, active_combination.id) * 1000.0,
            )
            global_moment = _local_to_global(rotation, local_moment)
            global_shear = _local_to_global(rotation, local_shear)
            global_displacement = _local_to_global(rotation, local_displacement)
            axial = member.axial(distance, active_combination.id)
            max_moment = max(
                max_moment,
                sqrt(sum(value**2 for value in local_moment)),
            )
            max_local_moment_y = max(max_local_moment_y, abs(local_moment[1]))
            max_local_moment_z = max(max_local_moment_z, abs(local_moment[2]))
            max_shear = max(
                max_shear,
                sqrt(sum(value**2 for value in local_shear)),
            )
            max_axial = max(max_axial, abs(axial))
            max_displacement = max(
                max_displacement,
                sqrt(sum(value**2 for value in local_displacement)),
            )
            stations.append(
                MemberDiagramStation(
                    distance_m=distance,
                    position=position,
                    moment_kNm=global_moment,
                    shear_kN=global_shear,
                    displacement_mm=global_displacement,
                )
            )

        member_results.append(
            MemberResult(
                member_id=declaration.id,
                combination_id=active_combination.id,
                max_moment_kNm=max_moment,
                max_shear_kN=max_shear,
                max_axial_kN=max_axial,
                max_displacement_mm=max_displacement,
            )
        )
        member_diagrams.append(
            MemberDiagram(
                member_id=declaration.id,
                visual_node_id=component.visual_node_id,
                stations=stations,
            )
        )
        section = sections_by_id[declaration.section_id]
        if section.bending_reference_kNm is None:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} bending demand",
                    demand_kNm=max_moment,
                    capacity_kNm=None,
                    utilisation=None,
                    status="not_checked",
                    basis=(
                        "Elastic demand only — no traceable bending reference is "
                        "connected."
                    ),
                )
            )
        else:
            reference_demand = (
                max_local_moment_y
                if section.bending_reference_axis == "local_y"
                else max_local_moment_z
                if section.bending_reference_axis == "local_z"
                else max_moment
            )
            unreferenced_demand = (
                max_local_moment_z
                if section.bending_reference_axis == "local_y"
                else max_local_moment_y
                if section.bending_reference_axis == "local_z"
                else 0.0
            )
            bending_utilisation = reference_demand / section.bending_reference_kNm
            if unreferenced_demand > reference_demand + 1e-12:
                member_checks.append(
                    MemberCheck(
                        member_id=declaration.id,
                        label=f"{declaration.label} governing bending reference",
                        demand_kNm=unreferenced_demand,
                        capacity_kNm=None,
                        utilisation=None,
                        status="not_checked",
                        basis=(
                            "The governing demand is outside the authored "
                            f"{section.bending_reference_axis} reference axis. "
                            "A matching catalogue reference and biaxial interaction "
                            "check are required."
                        ),
                    )
                )
            else:
                member_checks.append(
                    MemberCheck(
                        member_id=declaration.id,
                        label=(
                            f"{declaration.label} "
                            f"{section.bending_reference_axis} yield reference"
                        ),
                        demand_kNm=reference_demand,
                        capacity_kNm=section.bending_reference_kNm,
                        utilisation=bending_utilisation,
                        status="not_checked",
                        basis=(
                            "RENDERER REFERENCE ONLY — "
                            + (
                                section.bending_reference_basis
                                or "Authored effective-section yield reference."
                            )
                            + " This is not a P399/Australian member verification."
                        ),
                    )
                )

        limit_candidates: list[float] = []
        if declaration.deflection_limit_ratio is not None:
            limit_candidates.append(
                member_length * 1000.0 / declaration.deflection_limit_ratio
            )
        if declaration.deflection_limit_mm is not None:
            limit_candidates.append(declaration.deflection_limit_mm)
        limit_mm = min(limit_candidates) if limit_candidates else None
        if active_combination.limit_state != "serviceability" or limit_mm is None:
            serviceability_checks.append(
                ServiceabilityCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} deflection",
                    combination_id=active_combination.id,
                    displacement_mm=max_displacement,
                    limit_mm=limit_mm,
                    utilisation=None,
                    status="not_checked",
                    basis=(
                        "Deflection checks require a serviceability combination "
                        "and an authored project criterion."
                        if declaration.deflection_limit_basis is None
                        else declaration.deflection_limit_basis
                    ),
                )
            )
        else:
            utilisation = max_displacement / limit_mm
            serviceability_checks.append(
                ServiceabilityCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} deflection",
                    combination_id=active_combination.id,
                    displacement_mm=max_displacement,
                    limit_mm=limit_mm,
                    utilisation=utilisation,
                    status="pass" if utilisation <= 1.0 else "fail",
                    basis=declaration.deflection_limit_basis
                    or "Authored project deflection criterion.",
                )
            )

    reaction_values: list[NodeReaction] = []
    reaction_force_sum = [0.0, 0.0, 0.0]
    reaction_moment_sum = [0.0, 0.0, 0.0]
    for node in nodes_by_coordinate.values():
        restraints = node["restraints"]
        if not any(restraints.values()):
            continue
        solved_node = model.nodes[node["id"]]
        force = Vector3(
            x=_clean(solved_node.RxnFX[active_combination.id]),
            y=_clean(solved_node.RxnFY[active_combination.id]),
            z=_clean(solved_node.RxnFZ[active_combination.id]),
        )
        moment = Vector3(
            x=_clean(solved_node.RxnMX[active_combination.id]),
            y=_clean(solved_node.RxnMY[active_combination.id]),
            z=_clean(solved_node.RxnMZ[active_combination.id]),
        )
        reaction_values.append(
            NodeReaction(
                node_id=node["id"],
                combination_id=active_combination.id,
                force=force,
                moment=moment,
            )
        )
        _add(reaction_force_sum, force)
        _add(reaction_moment_sum, moment)
        _add(reaction_moment_sum, _cross(node["position"], force))

    applied_force_sum = [0.0, 0.0, 0.0]
    applied_moment_sum = [0.0, 0.0, 0.0]
    declarations = {member.id: member for member in analysis.members}
    for equilibrium_point_load in analysis.member_loads:
        factor = active_combination.factors.get(
            equilibrium_point_load.case_id,
            0.0,
        )
        if factor == 0:
            continue
        declaration = declarations[equilibrium_point_load.member_id]
        member_axis = _axis(declaration.start, declaration.end)
        position = _plus(
            declaration.start,
            _scaled(member_axis, equilibrium_point_load.distance_m),
        )
        _add(applied_force_sum, equilibrium_point_load.force, factor)
        _add(applied_moment_sum, equilibrium_point_load.moment, factor)
        _add(
            applied_moment_sum,
            _cross(position, equilibrium_point_load.force),
            factor,
        )

    for equilibrium_line_load in analysis.member_distributed_loads:
        factor = active_combination.factors.get(
            equilibrium_line_load.case_id,
            0.0,
        )
        if factor == 0:
            continue
        declaration = declarations[equilibrium_line_load.member_id]
        member_axis = _axis(declaration.start, declaration.end)
        segment_start = _plus(
            declaration.start,
            _scaled(member_axis, equilibrium_line_load.start_distance_m),
        )
        resultant, first_moment = _distributed_resultant(equilibrium_line_load)
        _add(applied_force_sum, resultant, factor)
        _add(applied_moment_sum, _cross(segment_start, resultant), factor)
        _add(applied_moment_sum, _cross(member_axis, first_moment), factor)

    force_residual = tuple(
        applied_force_sum[index] + reaction_force_sum[index] for index in range(3)
    )
    moment_residual = tuple(
        applied_moment_sum[index] + reaction_moment_sum[index] for index in range(3)
    )
    residual = max(abs(value) for value in (*force_residual, *moment_residual))
    equilibrium_status: Literal["pass", "fail"] = (
        "pass" if residual <= RESIDUAL_TOLERANCE else "fail"
    )
    summary = _load_summary(analysis, active_combination)
    checked_serviceability = [
        check for check in serviceability_checks if check.status != "not_checked"
    ]
    reference_checks = [
        check for check in member_checks if check.capacity_kNm is not None
    ]
    failed_reference_checks = [
        check
        for check in reference_checks
        if check.utilisation is not None and check.utilisation > 1.0
    ]
    verification_stages, calculation_sheets = _p399_evidence(
        capture=capture,
        analysis=analysis,
        combination=active_combination,
        nodes=structural_nodes,
        members=structural_members,
        member_results=member_results,
        member_checks=member_checks,
        serviceability_checks=serviceability_checks,
        equilibrium_status=equilibrium_status,
        residual=residual,
    )

    return StructuralSnapshot(
        mode="design",
        title=capture.title,
        subtitle=(
            f"Multi-member first-order elastic analysis — {active_combination.label}"
        ),
        source=SnapshotSource(
            kind="design",
            label=capture.project_name,
            design_id=capture.project_name,
            design_hash=capture.design_hash,
        ),
        design_basis=capture.design_basis,
        nodes=structural_nodes,
        members=structural_members,
        sections=analysis.sections,
        materials=analysis.materials,
        load_cases=analysis.load_cases,
        load_combinations=combinations,
        loads=[],
        member_loads=analysis.member_loads,
        member_distributed_loads=analysis.member_distributed_loads,
        reactions=reaction_values,
        member_results=member_results,
        member_diagrams=member_diagrams,
        member_checks=member_checks,
        serviceability_checks=serviceability_checks,
        load_summary=summary,
        equilibrium=EquilibriumDiagnostic(
            force_residual_kN=_vector(force_residual),
            moment_residual_kNm=_vector(moment_residual),
            tolerance=RESIDUAL_TOLERANCE,
            status=equilibrium_status,
        ),
        solver=SolverMetadata(
            name="PyNiteFEA",
            version=version("PyNiteFEA"),
            analysis=(
                "3D first-order elastic frame; shared-coordinate nodes, "
                "point loads, and global distributed loads"
            ),
            combination_id=active_combination.id,
        ),
        verification_stages=verification_stages,
        calculation_sheets=calculation_sheets,
        capabilities=[
            CapabilityState(
                id="design-capture",
                label="Design capture",
                status="online",
                detail="Compiled analytical declarations are linked to CAD members.",
            ),
            CapabilityState(
                id="gravity",
                label="Self-weight",
                status="online" if summary.self_weight_kN > 0 else "pending",
                detail=(
                    f"{summary.member_mass_kg:.3f} kg of catalogue member mass "
                    "is applied as distributed gravity load."
                    if summary.self_weight_kN > 0
                    else "No catalogue member self-weight has been authored."
                ),
            ),
            CapabilityState(
                id="solver",
                label="Frame solver",
                status="online",
                detail=(
                    f"{len(structural_members)} members and "
                    f"{len(structural_nodes)} shared nodes are solved."
                ),
            ),
            CapabilityState(
                id="serviceability",
                label="Deflection",
                status="online" if checked_serviceability else "pending",
                detail=(
                    f"{len(checked_serviceability)} authored deflection criteria "
                    "are evaluated."
                    if checked_serviceability
                    else "No project deflection criteria are authored."
                ),
            ),
            CapabilityState(
                id="equilibrium",
                label="Equilibrium",
                status="online" if equilibrium_status == "pass" else "blocked",
                detail=f"Global residual is {residual:.3e}.",
            ),
            CapabilityState(
                id="checks",
                label="Member resistance",
                status="blocked",
                detail=(
                    f"{len(failed_reference_checks)} of {len(reference_checks)} members "
                    "exceed the renderer-only yield reference; P399/Australian "
                    "cross-section and member-stability checks remain blocked."
                    if failed_reference_checks
                    else f"{len(reference_checks)} renderer-only yield references are "
                    "available; no P399/Australian member pass is active."
                    if reference_checks
                    else "No traceable reference is connected and no member resistance "
                    "calculation pack is active."
                ),
            ),
            CapabilityState(
                id="connections",
                label="Connections",
                status="pending",
                detail="Screws, bolts, brackets, anchors, and concrete are not checked.",
            ),
        ],
        warnings=[
            "ELASTIC MEMBER DEMAND ONLY — NOT FOR DESIGN, CERTIFICATION, OR ORDERING.",
            *dict.fromkeys(member.assumption for member in analysis.members),
            (
                "Non-steel permanent actions are included only where design.py "
                "authors a traceable distributed or point load."
            ),
            (
                "The displayed bending threshold is an effective-section yield "
                "reference only. AS/NZS 4600 member capacity, lateral-torsional "
                "buckling, restraint, connections, anchors, concrete, impact, and "
                "progressive collapse are not checked."
            ),
        ],
    )
