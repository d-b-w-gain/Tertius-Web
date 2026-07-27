from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import version
from math import sqrt
from typing import Literal

from .contracts import (
    CapabilityState,
    EquilibriumDiagnostic,
    MemberCheck,
    MemberDiagram,
    MemberDiagramStation,
    MemberResult,
    NodeReaction,
    ProjectStructuralCapture,
    SnapshotSource,
    SolverMetadata,
    StructuralMember,
    StructuralNode,
    StructuralSnapshot,
    Vector3,
)

COMBINATION_ID = "SLS-1.0"
STATION_INTERVALS = 32
RESIDUAL_TOLERANCE = 1e-8


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
        (end.x - start.x) ** 2
        + (end.y - start.y) ** 2
        + (end.z - start.z) ** 2
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
) -> None:
    values = (value.x, value.y, value.z) if isinstance(value, Vector3) else value
    for index in range(3):
        accumulator[index] += values[index]


def _local_to_global(rotation, values: tuple[float, float, float]) -> Vector3:
    return _vector(
        tuple(
            sum(float(rotation[row][column]) * values[row] for row in range(3))
            for column in range(3)
        )
    )


def solve_project_structural(
    capture: ProjectStructuralCapture,
) -> StructuralSnapshot:
    analysis = capture.analysis
    if analysis is None or not analysis.members or not analysis.member_loads:
        raise StructuralAnalysisError(
            "Active design has no analytical member axis and distributed member loads"
        )
    if len(analysis.members) != 1:
        raise StructuralAnalysisError(
            "The current structural MVP solves exactly one analytical member; "
            f"the active design declares {len(analysis.members)}"
        )

    from Pynite import FEModel3D

    declaration = analysis.members[0]
    member_length = _length(declaration.start, declaration.end)
    member_loads = [
        load for load in analysis.member_loads if load.member_id == declaration.id
    ]
    if not member_loads:
        raise StructuralAnalysisError(
            f"Analytical member {declaration.id!r} has no member loads"
        )

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

    start_node_id = f"{declaration.id}-start"
    end_node_id = f"{declaration.id}-end"
    model.add_node(
        start_node_id,
        declaration.start.x,
        declaration.start.y,
        declaration.start.z,
    )
    model.add_node(
        end_node_id,
        declaration.end.x,
        declaration.end.y,
        declaration.end.z,
    )
    for node_id, restraints in (
        (start_node_id, declaration.start_restraints),
        (end_node_id, declaration.end_restraints),
    ):
        model.def_support(
            node_id,
            support_DX=restraints.dx,
            support_DY=restraints.dy,
            support_DZ=restraints.dz,
            support_RX=restraints.rx,
            support_RY=restraints.ry,
            support_RZ=restraints.rz,
        )
    model.add_member(
        declaration.id,
        start_node_id,
        end_node_id,
        declaration.material_id,
        declaration.section_id,
    )

    direction_names = (
        ("FX", "x"),
        ("FY", "y"),
        ("FZ", "z"),
        ("MX", "x"),
        ("MY", "y"),
        ("MZ", "z"),
    )
    for load in member_loads:
        for direction, axis in direction_names[:3]:
            magnitude = getattr(load.force, axis)
            if magnitude:
                model.add_member_pt_load(
                    declaration.id,
                    direction,
                    magnitude,
                    load.distance_m,
                    case=load.case_id,
                )
        for direction, axis in direction_names[3:]:
            magnitude = getattr(load.moment, axis)
            if magnitude:
                model.add_member_pt_load(
                    declaration.id,
                    direction,
                    magnitude,
                    load.distance_m,
                    case=load.case_id,
                )

    combination_factors = {
        case.id: 1.0
        for case in analysis.load_cases
        if any(load.case_id == case.id for load in member_loads)
    }
    if not combination_factors:
        raise StructuralAnalysisError("Analytical loads reference no declared load case")
    model.add_load_combo(COMBINATION_ID, combination_factors)
    try:
        model.analyze(check_statics=False, log=False)
    except Exception as exc:
        raise StructuralAnalysisError(f"PyNite could not solve the active design: {exc}") from exc

    component = next(
        item for item in capture.components if item.id == declaration.component_id
    )
    member = model.members[declaration.id]
    rotation = member.T()[:3, :3]

    station_distances = {
        member_length * index / STATION_INTERVALS
        for index in range(STATION_INTERVALS + 1)
    }
    station_distances.update(load.distance_m for load in member_loads)
    stations: list[MemberDiagramStation] = []
    max_moment = 0.0
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
            member.moment("My", distance, COMBINATION_ID),
            member.moment("Mz", distance, COMBINATION_ID),
        )
        local_shear = (
            0.0,
            member.shear("Fy", distance, COMBINATION_ID),
            member.shear("Fz", distance, COMBINATION_ID),
        )
        local_displacement = (
            member.deflection("dx", distance, COMBINATION_ID) * 1000.0,
            member.deflection("dy", distance, COMBINATION_ID) * 1000.0,
            member.deflection("dz", distance, COMBINATION_ID) * 1000.0,
        )
        global_moment = _local_to_global(rotation, local_moment)
        global_shear = _local_to_global(rotation, local_shear)
        global_displacement = _local_to_global(rotation, local_displacement)
        axial = member.axial(distance, COMBINATION_ID)
        max_moment = max(
            max_moment,
            sqrt(sum(value**2 for value in local_moment)),
        )
        max_shear = max(max_shear, sqrt(sum(value**2 for value in local_shear)))
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

    reaction_values: list[NodeReaction] = []
    reaction_force_sum = [0.0, 0.0, 0.0]
    reaction_moment_sum = [0.0, 0.0, 0.0]
    node_declarations = (
        (start_node_id, declaration.start, declaration.start_restraints),
        (end_node_id, declaration.end, declaration.end_restraints),
    )
    for node_id, position, restraints in node_declarations:
        if not any(
            (
                restraints.dx,
                restraints.dy,
                restraints.dz,
                restraints.rx,
                restraints.ry,
                restraints.rz,
            )
        ):
            continue
        node = model.nodes[node_id]
        force = Vector3(
            x=_clean(node.RxnFX[COMBINATION_ID]),
            y=_clean(node.RxnFY[COMBINATION_ID]),
            z=_clean(node.RxnFZ[COMBINATION_ID]),
        )
        moment = Vector3(
            x=_clean(node.RxnMX[COMBINATION_ID]),
            y=_clean(node.RxnMY[COMBINATION_ID]),
            z=_clean(node.RxnMZ[COMBINATION_ID]),
        )
        reaction_values.append(
            NodeReaction(
                node_id=node_id,
                combination_id=COMBINATION_ID,
                force=force,
                moment=moment,
            )
        )
        _add(reaction_force_sum, force)
        _add(reaction_moment_sum, moment)
        _add(reaction_moment_sum, _cross(position, force))

    applied_force_sum = [0.0, 0.0, 0.0]
    applied_moment_sum = [0.0, 0.0, 0.0]
    analytical_axis = Vector3(
        x=(declaration.end.x - declaration.start.x) / member_length,
        y=(declaration.end.y - declaration.start.y) / member_length,
        z=(declaration.end.z - declaration.start.z) / member_length,
    )
    for load in member_loads:
        position = Vector3(
            x=declaration.start.x + analytical_axis.x * load.distance_m,
            y=declaration.start.y + analytical_axis.y * load.distance_m,
            z=declaration.start.z + analytical_axis.z * load.distance_m,
        )
        _add(applied_force_sum, load.force)
        _add(applied_moment_sum, load.moment)
        _add(applied_moment_sum, _cross(position, load.force))

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

    return StructuralSnapshot(
        mode="design",
        title=capture.title,
        subtitle="Active-project first-order elastic member demand",
        source=SnapshotSource(
            kind="design",
            label=capture.project_name,
            design_id=capture.project_name,
            design_hash=capture.design_hash,
        ),
        nodes=[
            StructuralNode(
                id=start_node_id,
                label=f"{declaration.label} start",
                position=declaration.start,
                restraints=declaration.start_restraints,
                visual_node_id=component.visual_node_id,
            ),
            StructuralNode(
                id=end_node_id,
                label=f"{declaration.label} end",
                position=declaration.end,
                restraints=declaration.end_restraints,
                visual_node_id=component.visual_node_id,
            ),
        ],
        members=[
            StructuralMember(
                id=declaration.id,
                label=declaration.label,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                section_id=declaration.section_id,
                material_id=declaration.material_id,
                visual_node_id=component.visual_node_id,
            )
        ],
        sections=analysis.sections,
        materials=analysis.materials,
        load_cases=analysis.load_cases,
        loads=[],
        member_loads=member_loads,
        reactions=reaction_values,
        member_results=[
            MemberResult(
                member_id=declaration.id,
                combination_id=COMBINATION_ID,
                max_moment_kNm=max_moment,
                max_shear_kN=max_shear,
                max_axial_kN=max_axial,
                max_displacement_mm=max_displacement,
            )
        ],
        member_diagrams=[
            MemberDiagram(
                member_id=declaration.id,
                visual_node_id=component.visual_node_id,
                stations=stations,
            )
        ],
        member_checks=[
            MemberCheck(
                member_id=declaration.id,
                label=f"{declaration.label} bending demand",
                demand_kNm=max_moment,
                capacity_kNm=None,
                utilisation=None,
                status="not_checked",
                basis="Elastic demand only — no AS 4600 member capacity is connected.",
            )
        ],
        equilibrium=EquilibriumDiagnostic(
            force_residual_kN=_vector(force_residual),
            moment_residual_kNm=_vector(moment_residual),
            tolerance=RESIDUAL_TOLERANCE,
            status=equilibrium_status,
        ),
        solver=SolverMetadata(
            name="PyNiteFEA",
            version=version("PyNiteFEA"),
            analysis="3D first-order elastic; authored member point loads",
            combination_id=COMBINATION_ID,
        ),
        capabilities=[
            CapabilityState(
                id="design-capture",
                label="Design capture",
                status="online",
                detail="Analytical declarations were statically parsed from design.py.",
            ),
            CapabilityState(
                id="solver",
                label="PyNite demand",
                status="online",
                detail="Member actions, reactions, and displacements are solved.",
            ),
            CapabilityState(
                id="equilibrium",
                label="Equilibrium",
                status="online" if equilibrium_status == "pass" else "blocked",
                detail=f"Global residual is {residual:.3e}.",
            ),
            CapabilityState(
                id="checks",
                label="Member capacity",
                status="pending",
                detail="No AS 4600 C100 capacity has been connected.",
            ),
            CapabilityState(
                id="connections",
                label="Connections",
                status="pending",
                detail="Screws, bolts, bracket, anchors, and concrete are not checked.",
            ),
        ],
        warnings=[
            "ELASTIC MEMBER DEMAND ONLY — NOT FOR DESIGN, CERTIFICATION, OR ORDERING.",
            declaration.assumption,
            "Wind pressure is distributed equally to the authored screw positions.",
            "C100 capacity, local buckling, restraint, connections, anchors, and concrete are not checked.",
        ],
    )
