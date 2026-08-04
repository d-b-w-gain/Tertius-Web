from __future__ import annotations

from collections.abc import Sequence
from collections import deque
from importlib.metadata import version
from math import pi, sqrt
from typing import Any, Literal, cast

from .capacity_packs import (
    CapacityPackError,
    cross_section_capacity,
    member_compression_capacity,
)
from .contracts import (
    CalculationEquation,
    CalculationInput,
    CalculationSheet,
    CapabilityState,
    DesignComponent,
    DesignConnection,
    EquilibriumDiagnostic,
    LoadCombination,
    LoadSummary,
    MemberCheck,
    MemberCrossSectionCheck,
    MemberDiagram,
    MemberDiagramStation,
    MemberDistributedLoad,
    MemberResult,
    MemberRestraintCandidateCheck,
    MemberRestraintTrace,
    MemberStabilityComparison,
    MemberStabilityCheck,
    NodeReaction,
    ProjectStructuralCapture,
    ServiceabilityCheck,
    SnapshotSource,
    SolverMetadata,
    StructuralMember,
    StructuralNode,
    StructuralSnapshot,
    TensionMemberCheck,
    StabilityDirectionResult,
    StabilityResult,
    Vector3,
    VerificationStage,
)
from .site_wind import verify_site_wind_snapshot
from .restraint_evidence import resolve_restraint_evidence

DEFAULT_COMBINATION_ID = "SLS-1.0"
STATION_INTERVALS = 32
RESIDUAL_TOLERANCE = 1e-8
PDELTA_RESIDUAL_RELATIVE_TOLERANCE = 1e-3
PDELTA_EQUILIBRIUM_INTERVALS = 64
STANDARD_GRAVITY_M_S2 = 9.80665
NODE_COORDINATE_DIGITS = 9


class StructuralAnalysisError(ValueError):
    """Raised when the active design cannot be represented by the MVP solver."""


def _tension_member_checks(model, analysis) -> list[TensionMemberCheck]:
    """Envelope authored tension-only members across every ULS combination."""

    ultimate_combinations = [
        combination
        for combination in analysis.load_combinations
        if combination.limit_state == "ultimate"
    ]
    checks: list[TensionMemberCheck] = []
    for declaration in analysis.members:
        if not declaration.tension_only:
            continue
        member = model.members[declaration.id]
        member_length = _length(declaration.start, declaration.end)
        governing_combination_id: str | None = None
        tension_demand_kN = 0.0
        for combination in ultimate_combinations:
            demand = max(
                abs(member.axial(member_length * index / 8, combination.id))
                for index in range(9)
            )
            if demand > tension_demand_kN:
                tension_demand_kN = demand
                governing_combination_id = combination.id

        tension_capacity = declaration.tension_capacity_kN
        connection_capacity = declaration.end_connection_capacity_kN
        capacities = [
            capacity
            for capacity in (tension_capacity, connection_capacity)
            if capacity is not None
        ]
        governing_capacity = min(capacities) if capacities else None
        member_utilisation = (
            tension_demand_kN / tension_capacity
            if tension_capacity is not None
            else None
        )
        connection_utilisation = (
            tension_demand_kN / connection_capacity
            if connection_capacity is not None
            else None
        )
        governing_utilisation = (
            tension_demand_kN / governing_capacity
            if governing_capacity is not None
            else None
        )
        if declaration.tension_capacity_status == "verified":
            status: Literal["pass", "fail", "not_checked", "unsupported"] = (
                "pass"
                if governing_utilisation is not None and governing_utilisation <= 1.0
                else "fail"
            )
        elif declaration.tension_capacity_status == "candidate":
            status = "unsupported"
        else:
            status = "not_checked"
        checks.append(
            TensionMemberCheck(
                member_id=declaration.id,
                label=declaration.label,
                status=status,
                capacity_status=declaration.tension_capacity_status,
                governing_combination_id=governing_combination_id,
                tension_demand_kN=tension_demand_kN,
                tension_capacity_kN=tension_capacity,
                end_connection_capacity_kN=connection_capacity,
                governing_capacity_kN=governing_capacity,
                member_utilisation=member_utilisation,
                connection_utilisation=connection_utilisation,
                governing_utilisation=governing_utilisation,
                end_fastener_count=declaration.end_fastener_count,
                required_force_per_end_fastener_kN=(
                    tension_demand_kN / declaration.end_fastener_count
                    if declaration.end_fastener_count is not None
                    else None
                ),
                basis=(
                    declaration.tension_capacity_basis
                    or "No authored tension resistance basis."
                ),
                assumptions=[
                    declaration.assumption,
                    declaration.end_connection_basis
                    or "End-connection resistance remains unverified.",
                ],
            )
        )
    return checks


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


def _amplification(second_order: float, first_order: float) -> float:
    if abs(first_order) <= 1e-12:
        return 1.0 if abs(second_order) <= 1e-12 else second_order / 1e-12
    return second_order / first_order


def _stability_scope_comparisons(
    comparisons: Sequence[MemberStabilityComparison],
    *,
    eaves_member_ids: Sequence[str],
    rafter_member_ids: Sequence[str],
) -> list[MemberStabilityComparison]:
    """Return the primary-frame members authored to govern global stability."""

    scoped_ids = set(eaves_member_ids) | set(rafter_member_ids)
    if not scoped_ids:
        return list(comparisons)
    scoped = [
        comparison for comparison in comparisons if comparison.member_id in scoped_ids
    ]
    if not scoped:
        raise StructuralAnalysisError(
            "the authored global-stability member scope resolved no members"
        )
    return scoped


def _analysis_base_model_matches(analysis) -> bool:
    stability = analysis.stability
    if stability is None or stability.analysis_base_model == "unspecified":
        return True
    members_by_id = {member.id: member for member in analysis.members}
    if not stability.eaves_member_ids:
        return False
    base_restraints = [
        members_by_id[member_id].start_restraints
        for member_id in stability.eaves_member_ids
        if member_id in members_by_id
    ]
    if len(base_restraints) != len(stability.eaves_member_ids):
        return False
    if stability.analysis_base_model == "perfectly_pinned":
        return all(
            restraints.dx
            and restraints.dy
            and restraints.dz
            and not restraints.rx
            and not restraints.ry
            and not restraints.rz
            for restraints in base_restraints
        )
    if stability.analysis_base_model == "fixed":
        return all(
            restraints.dx
            and restraints.dy
            and restraints.dz
            and restraints.rx
            and restraints.ry
            and restraints.rz
            for restraints in base_restraints
        )
    return False


def _member_extrema(
    model,
    declaration,
    *,
    combination_id: str,
    point_loads,
    distributed_loads,
) -> tuple[float, float]:
    """Sample resultant bending and displacement for one solved member."""

    member_length = _length(declaration.start, declaration.end)
    station_distances = {
        member_length * index / STATION_INTERVALS
        for index in range(STATION_INTERVALS + 1)
    }
    station_distances.update(load.distance_m for load in point_loads)
    for line_load in distributed_loads:
        station_distances.update((line_load.start_distance_m, line_load.end_distance_m))
    member = model.members[declaration.id]
    max_moment = 0.0
    max_displacement = 0.0
    for distance in sorted(station_distances):
        local_moment = (
            member.moment("My", distance, combination_id),
            member.moment("Mz", distance, combination_id),
        )
        local_displacement_mm = (
            member.deflection("dx", distance, combination_id) * 1000.0,
            member.deflection("dy", distance, combination_id) * 1000.0,
            member.deflection("dz", distance, combination_id) * 1000.0,
        )
        max_moment = max(
            max_moment,
            sqrt(sum(value**2 for value in local_moment)),
        )
        max_displacement = max(
            max_displacement,
            sqrt(sum(value**2 for value in local_displacement_mm)),
        )
    return max_moment, max_displacement


def _member_station_distances(analysis, declaration) -> list[float]:
    member_length = _length(declaration.start, declaration.end)
    distances = {
        member_length * index / STATION_INTERVALS
        for index in range(STATION_INTERVALS + 1)
    }
    distances.update(
        load.distance_m
        for load in analysis.member_loads
        if load.member_id == declaration.id
    )
    for load in analysis.member_distributed_loads:
        if load.member_id == declaration.id:
            distances.update((load.start_distance_m, load.end_distance_m))
    return sorted(distances)


def _off_axis_load_path(
    component_id: str,
    components: dict[str, DesignComponent],
    connections: Sequence[DesignConnection],
) -> dict[str, Any]:
    """Trace authored surface action into a member and force/shear path to ground.

    Connectivity is deliberately reported as a candidate only. It demonstrates
    where the design says the reaction goes; it does not infer connector,
    diaphragm, collector, or anchorage resistance from touching CAD solids.
    """
    source_component_ids: list[str] = []
    source_connection_ids: list[str] = []
    adjacency: dict[str, list[tuple[str, DesignConnection]]] = {}
    for connection in connections:
        if connection.from_component_id == component_id:
            other_id = connection.to_component_id
        elif connection.to_component_id == component_id:
            other_id = connection.from_component_id
        else:
            other_id = ""
        if other_id and components.get(other_id, None) is not None:
            if components[other_id].kind == "surface" and "wind_normal" in connection.transfers:
                source_component_ids.append(other_id)
                source_connection_ids.append(connection.id)

        if not ({"force", "shear"} & set(connection.transfers)):
            continue
        adjacency.setdefault(connection.from_component_id, []).append(
            (connection.to_component_id, connection)
        )
        adjacency.setdefault(connection.to_component_id, []).append(
            (connection.from_component_id, connection)
        )

    queue = deque([(component_id, [component_id], [])])
    visited = {component_id}
    while queue:
        current_id, component_path, connection_path = queue.popleft()
        current = components.get(current_id)
        if current is not None and current.grounded and current_id != component_id:
            collector_component_ids = [component_path[0]]
            for next_component_id, connection in zip(
                component_path[1:],
                connection_path,
                strict=True,
            ):
                collector_component_ids.extend(connection.connector_component_ids)
                collector_component_ids.append(next_component_id)
            source_basis = (
                "An authored wind-normal surface connection feeds this member"
                if source_component_ids
                else "No wind-normal surface action source is directly connected"
            )
            return {
                "status": "candidate",
                "source_component_ids": list(dict.fromkeys(source_component_ids)),
                "source_connection_ids": list(dict.fromkeys(source_connection_ids)),
                "collector_component_ids": list(
                    dict.fromkeys(collector_component_ids)
                ),
                "collector_connection_ids": [
                    connection.id for connection in connection_path
                ],
                "grounded_component_id": current_id,
                "basis": (
                    f"{source_basis}; the design connection graph contains a "
                    "force/shear path to the "
                    f"grounded component {current_id!r}. Path continuity is a candidate, "
                    "not verified resistance or stiffness."
                ),
            }
        for next_id, connection in adjacency.get(current_id, []):
            if next_id in visited or components.get(next_id) is None:
                continue
            visited.add(next_id)
            queue.append(
                (next_id, [*component_path, next_id], [*connection_path, connection])
            )

    return {
        "status": "not_declared",
        "source_component_ids": list(dict.fromkeys(source_component_ids)),
        "source_connection_ids": list(dict.fromkeys(source_connection_ids)),
        "collector_component_ids": [component_id],
        "collector_connection_ids": [],
        "grounded_component_id": None,
        "basis": (
            "No authored force/shear connection path from this member reaches a "
            "grounded component."
        ),
    }


def _cross_section_checks(
    model,
    analysis,
    components: dict[str, DesignComponent],
    connections: Sequence[DesignConnection],
) -> list[MemberCrossSectionCheck]:
    definition = analysis.cross_section_verification
    if definition is None:
        return []

    sections_by_id = {section.id: section for section in analysis.sections}
    checks: list[MemberCrossSectionCheck] = []
    selected_member_ids = set(definition.member_ids)
    for declaration in analysis.members:
        if selected_member_ids and declaration.id not in selected_member_ids:
            continue
        section = sections_by_id[declaration.section_id]
        try:
            capacity = cross_section_capacity(definition.pack_id, section)
        except CapacityPackError as exc:
            checks.append(
                MemberCrossSectionCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} cross-section",
                    pack_id=definition.pack_id,
                    status="unsupported",
                    basis=f"Capacity pack could not evaluate this section: {exc}",
                    assumptions=[
                        "No resistance has been inferred from incomplete catalogue data."
                    ],
                )
            )
            continue

        if section.bending_reference_axis != "local_z":
            checks.append(
                MemberCrossSectionCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} cross-section",
                    pack_id=definition.pack_id,
                    status="unsupported",
                    design_compression_capacity_kN=(
                        capacity.design_compression_capacity_kN
                    ),
                    design_major_bending_capacity_kNm=(
                        capacity.design_major_bending_capacity_kNm
                    ),
                    design_web_shear_capacity_kN=(
                        capacity.design_web_shear_capacity_kN
                    ),
                    section_record_sha256=capacity.section_record_sha256,
                    capacity_factors={
                        "phi_c": capacity.phi_c,
                        "phi_b": capacity.phi_b,
                        "phi_v": capacity.phi_v,
                    },
                    web_slenderness=capacity.web_slenderness,
                    shear_regime=capacity.shear_regime,
                    basis=(
                        "The selected pack requires the catalogue major-axis "
                        "reference to map to PyNite local_z."
                    ),
                )
            )
            continue

        governing: dict[str, float | str] | None = None
        off_axis_exceeded = False
        peak_minor_moment_kNm = 0.0
        peak_off_axis_shear_kN = 0.0
        peak_torsion_kNm = 0.0
        for combination_id in definition.combination_ids:
            for distance in _member_station_distances(analysis, declaration):
                member = model.members[declaration.id]
                axial_kN = abs(member.axial(distance, combination_id))
                major_moment_kNm = abs(member.moment("Mz", distance, combination_id))
                minor_moment_kNm = abs(member.moment("My", distance, combination_id))
                web_shear_kN = abs(member.shear("Fy", distance, combination_id))
                off_axis_shear_kN = abs(member.shear("Fz", distance, combination_id))
                torsion_kNm = abs(member.torque(distance, combination_id))
                peak_minor_moment_kNm = max(
                    peak_minor_moment_kNm,
                    minor_moment_kNm,
                )
                peak_off_axis_shear_kN = max(
                    peak_off_axis_shear_kN,
                    off_axis_shear_kN,
                )
                peak_torsion_kNm = max(peak_torsion_kNm, torsion_kNm)

                axial_bending = (
                    axial_kN / capacity.design_compression_capacity_kN
                    + major_moment_kNm / capacity.design_major_bending_capacity_kNm
                )
                bending_shear = sqrt(
                    (major_moment_kNm / capacity.design_major_bending_capacity_kNm) ** 2
                    + (web_shear_kN / capacity.design_web_shear_capacity_kN) ** 2
                )
                utilization = max(axial_bending, bending_shear)
                candidate: dict[str, float | str] = {
                    "combination_id": combination_id,
                    "distance": distance,
                    "axial": axial_kN,
                    "major_moment": major_moment_kNm,
                    "minor_moment": minor_moment_kNm,
                    "web_shear": web_shear_kN,
                    "off_axis_shear": off_axis_shear_kN,
                    "torsion": torsion_kNm,
                    "axial_bending": axial_bending,
                    "bending_shear": bending_shear,
                    "utilization": utilization,
                }
                if governing is None or utilization > float(governing["utilization"]):
                    governing = candidate
                off_axis_exceeded = off_axis_exceeded or any(
                    value > definition.off_axis_tolerance
                    for value in (
                        minor_moment_kNm,
                        off_axis_shear_kN,
                        torsion_kNm,
                    )
                )

        if governing is None:
            raise StructuralAnalysisError(
                f"cross-section envelope for {declaration.id!r} has no stations"
            )
        status: Literal["pass", "fail", "unsupported"] = (
            "unsupported"
            if off_axis_exceeded
            else "fail"
            if float(governing["utilization"]) > 1.0
            else "pass"
        )
        off_axis_path = _off_axis_load_path(
            declaration.component_id,
            components,
            connections,
        )
        checks.append(
            MemberCrossSectionCheck(
                member_id=declaration.id,
                label=f"{declaration.label} cross-section",
                pack_id=definition.pack_id,
                status=status,
                governing_combination_id=str(governing["combination_id"]),
                governing_station_m=float(governing["distance"]),
                axial_kN=float(governing["axial"]),
                major_moment_kNm=float(governing["major_moment"]),
                minor_moment_kNm=peak_minor_moment_kNm,
                web_shear_kN=float(governing["web_shear"]),
                off_axis_shear_kN=peak_off_axis_shear_kN,
                torsion_kNm=peak_torsion_kNm,
                design_compression_capacity_kN=(
                    capacity.design_compression_capacity_kN
                ),
                design_major_bending_capacity_kNm=(
                    capacity.design_major_bending_capacity_kNm
                ),
                design_web_shear_capacity_kN=(capacity.design_web_shear_capacity_kN),
                axial_bending_utilisation=float(governing["axial_bending"]),
                bending_shear_utilisation=float(governing["bending_shear"]),
                governing_utilisation=float(governing["utilization"]),
                section_record_sha256=capacity.section_record_sha256,
                capacity_factors={
                    "phi_c": capacity.phi_c,
                    "phi_b": capacity.phi_b,
                    "phi_v": capacity.phi_v,
                },
                web_slenderness=capacity.web_slenderness,
                shear_regime=capacity.shear_regime,
                off_axis_load_path_status=off_axis_path["status"],
                off_axis_required_reaction_kN=peak_off_axis_shear_kN,
                off_axis_source_component_ids=off_axis_path[
                    "source_component_ids"
                ],
                off_axis_source_connection_ids=off_axis_path[
                    "source_connection_ids"
                ],
                off_axis_collector_component_ids=off_axis_path[
                    "collector_component_ids"
                ],
                off_axis_collector_connection_ids=off_axis_path[
                    "collector_connection_ids"
                ],
                off_axis_grounded_component_id=off_axis_path[
                    "grounded_component_id"
                ],
                off_axis_load_path_basis=off_axis_path["basis"],
                basis=capacity.basis,
                assumptions=[
                    (
                        "Compression resistance conservatively uses Ae×fy for "
                        "the absolute axial demand; tension yielding is not used "
                        "to improve the result."
                    ),
                    (
                        "Member buckling, restraint, connections, bearing, "
                        "local load introduction, and system capacity remain "
                        "outside this Stage 6 check."
                    ),
                    *(
                        [
                            "Minor-axis bending exceeds the authored tolerance; "
                            "the catalogue record has no verified effective "
                            "minor-axis design resistance."
                        ]
                        if peak_minor_moment_kNm > definition.off_axis_tolerance
                        else []
                    ),
                    *(
                        [
                            "Off-axis shear exceeds the authored tolerance; no "
                            "verified flange-direction shear resistance has been "
                            "inferred."
                        ]
                        if peak_off_axis_shear_kN > definition.off_axis_tolerance
                        else []
                    ),
                    *(
                        [
                            "Torsion exceeds the authored tolerance; no verified "
                            "torsional design resistance has been inferred."
                        ]
                        if peak_torsion_kNm > definition.off_axis_tolerance
                        else []
                    ),
                    *(
                        [
                            "An authored collector path reaches ground, but roof-sheet "
                            "fasteners, member support transfer, collector/brace resistance "
                            "and stiffness, and anchorage remain unverified."
                        ]
                        if off_axis_path["status"] == "candidate"
                        and off_axis_exceeded
                        else []
                    ),
                ],
            )
        )
    return checks


def _compression_flange(
    signed_major_moment_kNm: float,
    tolerance: float,
) -> Literal["positive_local_y", "negative_local_y", "none"]:
    # PyNite's positive local Mz produces compressive longitudinal stress at
    # positive local y under sigma_x = -Mz*y/Iz.
    if signed_major_moment_kNm > tolerance:
        return "positive_local_y"
    if signed_major_moment_kNm < -tolerance:
        return "negative_local_y"
    return "none"


def _candidate_contact_flange(
    model,
    candidate,
    tolerance: float,
) -> Literal["positive_local_y", "negative_local_y", "both", "none"]:
    if candidate.restrained_flange != "auto":
        return cast(
            Literal["positive_local_y", "negative_local_y", "both"],
            candidate.restrained_flange,
        )
    rotation = model.members[candidate.member_id].T()[:3, :3]
    local_y_global = tuple(float(value) for value in rotation[1])
    offset = (
        candidate.brace_position.x - candidate.member_position.x,
        candidate.brace_position.y - candidate.member_position.y,
        candidate.brace_position.z - candidate.member_position.z,
    )
    local_y_offset = sum(local_y_global[index] * offset[index] for index in range(3))
    if local_y_offset > tolerance:
        return "positive_local_y"
    if local_y_offset < -tolerance:
        return "negative_local_y"
    return "none"


def _candidate_restrains_flange(
    model,
    candidate,
    compression_flange: Literal["positive_local_y", "negative_local_y", "none"],
    tolerance: float,
) -> bool:
    if compression_flange == "none":
        return True
    contact_flange = _candidate_contact_flange(model, candidate, tolerance)
    return contact_flange in {compression_flange, "both"}


def _member_restraint_candidate_checks(
    model,
    analysis,
    *,
    combination_id: str,
) -> list[MemberRestraintCandidateCheck]:
    definition = analysis.member_stability_verification
    if definition is None:
        return []
    combinations_by_id = {
        combination.id: combination for combination in analysis.load_combinations
    }
    combination = combinations_by_id[combination_id]
    members_by_id = {member.id: member for member in analysis.members}
    sections_by_id = {section.id: section for section in analysis.sections}
    candidates_by_member: dict[str, list[Any]] = {}
    for candidate in definition.restraint_candidates:
        candidates_by_member.setdefault(candidate.member_id, []).append(candidate)
    for candidates in candidates_by_member.values():
        candidates.sort(key=lambda candidate: candidate.distance_m)
    checks: list[MemberRestraintCandidateCheck] = []
    for candidate in definition.restraint_candidates:
        evidence_resolution = (
            resolve_restraint_evidence(
                candidate.evidence_pack_id,
                candidate.configuration,
            )
            if candidate.evidence_pack_id is not None
            else None
        )
        identity_status: Literal["not_declared", "pass", "fail"] = (
            evidence_resolution.identity_status
            if evidence_resolution is not None
            else "not_declared"
        )
        identity_mismatches = (
            list(evidence_resolution.identity_mismatches)
            if evidence_resolution is not None
            else []
        )
        design_force_capacity_kN = (
            evidence_resolution.design_force_capacity_kN
            if evidence_resolution is not None
            and evidence_resolution.identity_status == "pass"
            else candidate.design_force_capacity_kN
            if evidence_resolution is None
            else None
        )
        design_moment_capacity_kNm = (
            evidence_resolution.design_moment_capacity_kNm
            if evidence_resolution is not None
            and evidence_resolution.identity_status == "pass"
            else candidate.design_moment_capacity_kNm
            if evidence_resolution is None
            else None
        )
        stiffness_status = (
            evidence_resolution.stiffness_status
            if evidence_resolution is not None
            and evidence_resolution.identity_status == "pass"
            else candidate.stiffness_status
            if evidence_resolution is None
            else "unverified"
        )
        capacity_basis = (
            evidence_resolution.capacity_basis
            if evidence_resolution is not None
            else candidate.capacity_basis
        )
        declaration = members_by_id[candidate.member_id]
        section = sections_by_id[declaration.section_id]
        member_length = _length(declaration.start, declaration.end)
        candidate_distances = sorted(
            {item.distance_m for item in candidates_by_member[candidate.member_id]}
        )
        candidate_index = candidate_distances.index(candidate.distance_m)
        previous_distance = (
            candidate_distances[candidate_index - 1] if candidate_index > 0 else 0.0
        )
        next_distance = (
            candidate_distances[candidate_index + 1]
            if candidate_index + 1 < len(candidate_distances)
            else member_length
        )
        tributary_start_m = (
            (previous_distance + candidate.distance_m) / 2.0
            if candidate_index > 0
            else 0.0
        )
        tributary_end_m = (
            (candidate.distance_m + next_distance) / 2.0
            if candidate_index + 1 < len(candidate_distances)
            else member_length
        )
        point_loads = [
            load
            for load in analysis.member_loads
            if load.member_id == candidate.member_id
            and tributary_start_m - 1e-9 <= load.distance_m <= tributary_end_m + 1e-9
        ]
        combined_force = Vector3(x=0.0, y=0.0, z=0.0)
        for load in point_loads:
            factor = combination.factors.get(load.case_id, 0.0)
            combined_force = Vector3(
                x=combined_force.x + factor * load.force.x,
                y=combined_force.y + factor * load.force.y,
                z=combined_force.z + factor * load.force.z,
            )
        for load in analysis.member_distributed_loads:
            if load.member_id != candidate.member_id:
                continue
            overlap_start = max(tributary_start_m, load.start_distance_m)
            overlap_end = min(tributary_end_m, load.end_distance_m)
            if overlap_end <= overlap_start:
                continue
            factor = combination.factors.get(load.case_id, 0.0)
            if factor == 0.0:
                continue
            load_span = load.end_distance_m - load.start_distance_m

            def ordinate(vector_start: Vector3, vector_end: Vector3, distance: float):
                ratio = (distance - load.start_distance_m) / load_span
                return Vector3(
                    x=vector_start.x + (vector_end.x - vector_start.x) * ratio,
                    y=vector_start.y + (vector_end.y - vector_start.y) * ratio,
                    z=vector_start.z + (vector_end.z - vector_start.z) * ratio,
                )

            start_force = ordinate(
                load.start_force_kN_m,
                load.end_force_kN_m,
                overlap_start,
            )
            end_force = ordinate(
                load.start_force_kN_m,
                load.end_force_kN_m,
                overlap_end,
            )
            overlap_length = overlap_end - overlap_start
            combined_force = Vector3(
                x=combined_force.x
                + factor * overlap_length * (start_force.x + end_force.x) / 2.0,
                y=combined_force.y
                + factor * overlap_length * (start_force.y + end_force.y) / 2.0,
                z=combined_force.z
                + factor * overlap_length * (start_force.z + end_force.z) / 2.0,
            )
        transferred_load_kN = _magnitude(combined_force)
        depth_value = None
        if section.catalog is not None:
            raw_depth = section.catalog.properties.get(
                "depth_mm",
                section.catalog.properties.get("D_mm"),
            )
            if isinstance(raw_depth, (int, float)) and float(raw_depth) > 0:
                depth_value = float(raw_depth) / 1000.0

        required_force_kN: float | None = None
        required_moment_kNm: float | None = None
        if (
            candidate.demand_model == "aisi_2004_d3_2_2_eccentric_load_couple"
            and depth_value is not None
        ):
            required_force_kN = (
                candidate.demand_factor
                * candidate.axis_separation_m
                / depth_value
                * transferred_load_kN
            )
            required_moment_kNm = required_force_kN * depth_value

        force_utilisation = (
            required_force_kN / design_force_capacity_kN
            if required_force_kN is not None and design_force_capacity_kN is not None
            else None
        )
        moment_utilisation = (
            required_moment_kNm / design_moment_capacity_kNm
            if required_moment_kNm is not None
            and design_moment_capacity_kNm is not None
            else None
        )
        status: Literal["unsupported", "candidate", "pass", "fail"]
        if candidate.evidence_status == "unsupported" or identity_status == "fail":
            status = "unsupported"
        elif (
            candidate.evidence_status != "verified"
            or required_force_kN is None
            or required_moment_kNm is None
            or design_force_capacity_kN is None
            or design_moment_capacity_kNm is None
            or stiffness_status != "verified"
            or (
                candidate.evidence_pack_id is not None
                and candidate.anchorage_status != "verified"
            )
        ):
            status = "candidate"
        elif max(force_utilisation or 0.0, moment_utilisation or 0.0) > 1.0:
            status = "fail"
        else:
            status = "pass"
        checks.append(
            MemberRestraintCandidateCheck(
                id=f"candidate-check-{candidate.id}-{combination_id}",
                candidate_id=candidate.id,
                member_id=candidate.member_id,
                connection_id=candidate.connection_id,
                combination_id=combination_id,
                contact_flange=_candidate_contact_flange(
                    model,
                    candidate,
                    definition.off_axis_tolerance,
                ),
                status=status,
                demand_model=candidate.demand_model,
                transferred_load_kN=transferred_load_kN,
                load_eccentricity_m=(
                    candidate.axis_separation_m
                    if candidate.demand_model
                    == "aisi_2004_d3_2_2_eccentric_load_couple"
                    else None
                ),
                member_depth_m=depth_value,
                required_force_kN=required_force_kN,
                required_moment_kNm=required_moment_kNm,
                available_force_kN=design_force_capacity_kN,
                available_moment_kNm=design_moment_capacity_kNm,
                force_utilisation=force_utilisation,
                moment_utilisation=moment_utilisation,
                stiffness_status=stiffness_status,
                evidence_pack_id=candidate.evidence_pack_id,
                evidence_pack_version=(
                    evidence_resolution.pack_version
                    if evidence_resolution is not None
                    else None
                ),
                identity_status=identity_status,
                identity_mismatches=identity_mismatches,
                evidence_references=(
                    list(evidence_resolution.references)
                    if evidence_resolution is not None
                    else []
                ),
                anchorage_status=candidate.anchorage_status,
                anchorage_component_ids=candidate.anchorage_component_ids,
                anchorage_connection_ids=candidate.anchorage_connection_ids,
                anchorage_grounded_component_id=(
                    candidate.anchorage_grounded_component_id
                ),
                anchorage_basis=candidate.anchorage_basis,
                mechanism=(
                    "Flange-brace force couple generated by the factored point-load "
                    "and distributed-load resultant within the candidate's midpoint "
                    "tributary interval, acting at the connected secondary-member axis."
                    if candidate.demand_model
                    == "aisi_2004_d3_2_2_eccentric_load_couple"
                    else "Connection boundary restraint demand is not yet quantified."
                ),
                provenance=candidate.provenance,
                basis=(
                    "P_L = 1.5(e/d)W from the AISI Cold-Formed Steel Framing "
                    "Design Guide, Second Edition, Example 2 Step 2(a), adapting "
                    "Supplement D3.2.2 to expose a working eccentric-load brace "
                    "demand. This does not replace an AS/NZS 4600 verification. "
                    f"{capacity_basis}"
                    if candidate.demand_model
                    == "aisi_2004_d3_2_2_eccentric_load_couple"
                    else capacity_basis
                ),
            )
        )
    return checks


def _segment_restraint_state(
    model,
    definition,
    segment,
    compression_flange: Literal["positive_local_y", "negative_local_y", "none"],
    candidate_checks_by_id: dict[str, MemberRestraintCandidateCheck] | None = None,
) -> tuple[
    Literal["missing", "candidate", "inadequate", "verified"],
    list[str],
    list[str],
]:
    if compression_flange == "none":
        return "verified", [], []
    if (
        segment.lateral_bending_restraint == "continuous_compression_flange"
        and segment.restraint_status == "verified"
    ):
        return "verified", [], []

    candidates_by_id = {
        candidate.id: candidate for candidate in definition.restraint_candidates
    }
    effective_ids: list[str] = []
    effective_check_ids: list[str] = []
    boundary_statuses: list[
        Literal["missing", "candidate", "inadequate", "verified"]
    ] = []
    for candidate_ids in (
        segment.start_restraint_candidate_ids,
        segment.end_restraint_candidate_ids,
    ):
        effective = [
            candidates_by_id[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in candidates_by_id
            and candidates_by_id[candidate_id].restrains_lateral_translation
            and candidates_by_id[candidate_id].restrains_twist
            and _candidate_restrains_flange(
                model,
                candidates_by_id[candidate_id],
                compression_flange,
                definition.off_axis_tolerance,
            )
        ]
        effective_ids.extend(candidate.id for candidate in effective)
        if not effective:
            boundary_statuses.append("missing")
        else:
            checks = [
                candidate_checks_by_id[candidate.id]
                for candidate in effective
                if candidate_checks_by_id is not None
                and candidate.id in candidate_checks_by_id
            ]
            effective_check_ids.extend(check.id for check in checks)
            if any(check.status == "pass" for check in checks):
                boundary_statuses.append("verified")
            elif any(check.status == "candidate" for check in checks):
                boundary_statuses.append("candidate")
            elif any(check.status == "fail" for check in checks):
                boundary_statuses.append("inadequate")
            elif checks and all(check.status == "unsupported" for check in checks):
                boundary_statuses.append("missing")
            elif any(
                candidate.evidence_status == "candidate" for candidate in effective
            ):
                boundary_statuses.append("candidate")
            else:
                boundary_statuses.append("missing")
    if "missing" in boundary_statuses:
        return "missing", sorted(set(effective_ids)), sorted(set(effective_check_ids))
    if "inadequate" in boundary_statuses:
        return (
            "inadequate",
            sorted(set(effective_ids)),
            sorted(set(effective_check_ids)),
        )
    if "candidate" in boundary_statuses:
        return "candidate", sorted(set(effective_ids)), sorted(set(effective_check_ids))
    return "verified", sorted(set(effective_ids)), sorted(set(effective_check_ids))


def _position_on_member(declaration, distance_m: float) -> Vector3:
    member_length = _length(declaration.start, declaration.end)
    ratio = distance_m / member_length if member_length > 0 else 0.0
    return Vector3(
        x=declaration.start.x + (declaration.end.x - declaration.start.x) * ratio,
        y=declaration.start.y + (declaration.end.y - declaration.start.y) * ratio,
        z=declaration.start.z + (declaration.end.z - declaration.start.z) * ratio,
    )


def _member_restraint_traces(
    model,
    analysis,
    *,
    combination_id: str,
) -> list[MemberRestraintTrace]:
    definition = analysis.member_stability_verification
    if definition is None:
        return []
    candidate_checks = _member_restraint_candidate_checks(
        model,
        analysis,
        combination_id=combination_id,
    )
    candidate_checks_by_id = {check.candidate_id: check for check in candidate_checks}
    members_by_id = {member.id: member for member in analysis.members}
    traces: list[MemberRestraintTrace] = []
    for segment in definition.segments:
        declaration = members_by_id[segment.member_id]
        member = model.members[segment.member_id]
        sample_distances = sorted(
            {
                segment.start_distance_m,
                segment.end_distance_m,
                *(
                    distance
                    for distance in _member_station_distances(analysis, declaration)
                    if segment.start_distance_m < distance < segment.end_distance_m
                ),
            }
        )
        split_distances = [segment.start_distance_m, segment.end_distance_m]
        for start_distance, end_distance in zip(sample_distances, sample_distances[1:]):
            start_moment = member.moment("Mz", start_distance, combination_id)
            end_moment = member.moment("Mz", end_distance, combination_id)
            if start_moment * end_moment < 0:
                fraction = abs(start_moment) / (abs(start_moment) + abs(end_moment))
                split_distances.append(
                    start_distance + fraction * (end_distance - start_distance)
                )
        split_distances = sorted(
            set(round(distance, 12) for distance in split_distances)
        )
        for index, (start_distance, end_distance) in enumerate(
            zip(split_distances, split_distances[1:]),
            start=1,
        ):
            if end_distance - start_distance <= 1e-9:
                continue
            midpoint = (start_distance + end_distance) / 2.0
            compression = _compression_flange(
                member.moment("Mz", midpoint, combination_id),
                definition.off_axis_tolerance,
            )
            restraint_status, effective_ids, effective_check_ids = (
                _segment_restraint_state(
                    model,
                    definition,
                    segment,
                    compression,
                    candidate_checks_by_id,
                )
            )
            effective_checks = [
                candidate_checks_by_id[candidate_id]
                for candidate_id in effective_ids
                if candidate_id in candidate_checks_by_id
            ]
            required_forces = [
                check.required_force_kN
                for check in effective_checks
                if check.required_force_kN is not None
            ]
            available_forces = [
                check.available_force_kN
                for check in effective_checks
                if check.available_force_kN is not None
            ]
            force_utilisations = [
                check.force_utilisation
                for check in effective_checks
                if check.force_utilisation is not None
            ]
            trace_status: Literal[
                "missing",
                "candidate",
                "inadequate",
                "verified",
                "not_required",
            ] = "not_required" if compression == "none" else restraint_status
            traces.append(
                MemberRestraintTrace(
                    id=f"trace-{segment.id}-{combination_id}-{index}",
                    member_id=segment.member_id,
                    combination_id=combination_id,
                    segment_start_m=start_distance,
                    segment_end_m=end_distance,
                    start_position=_position_on_member(declaration, start_distance),
                    end_position=_position_on_member(declaration, end_distance),
                    compression_flange=compression,
                    status=trace_status,
                    start_restraint_candidate_ids=(
                        segment.start_restraint_candidate_ids
                    ),
                    end_restraint_candidate_ids=(segment.end_restraint_candidate_ids),
                    effective_restraint_candidate_ids=effective_ids,
                    governing_candidate_check_ids=effective_check_ids,
                    required_restraint_force_kN=(
                        max(required_forces)
                        if effective_checks
                        and len(required_forces) == len(effective_checks)
                        else None
                    ),
                    available_restraint_force_kN=(
                        min(available_forces)
                        if effective_checks
                        and len(available_forces) == len(effective_checks)
                        else None
                    ),
                    restraint_force_utilisation=(
                        max(force_utilisations)
                        if effective_checks
                        and len(force_utilisations) == len(effective_checks)
                        else None
                    ),
                    basis=(
                        f"Signed PyNite local Mz identifies {compression.replace('_', ' ')} "
                        f"as the compression flange. Restraint state {trace_status} "
                        "requires effective lateral-translation and twist restraint at "
                        "both physical segment boundaries."
                    ),
                )
            )
    return traces


def _member_stability_checks(
    model,
    analysis,
) -> list[MemberStabilityCheck]:
    definition = analysis.member_stability_verification
    if definition is None:
        return []

    members_by_id = {member.id: member for member in analysis.members}
    sections_by_id = {section.id: section for section in analysis.sections}
    candidate_checks_by_combination = {
        combination_id: {
            check.candidate_id: check
            for check in _member_restraint_candidate_checks(
                model,
                analysis,
                combination_id=combination_id,
            )
        }
        for combination_id in definition.combination_ids
    }
    checks: list[MemberStabilityCheck] = []
    for segment in definition.segments:
        declaration = members_by_id[segment.member_id]
        section = sections_by_id[declaration.section_id]
        unbraced_length_m = segment.end_distance_m - segment.start_distance_m
        try:
            capacity = member_compression_capacity(
                definition.pack_id,
                section,
                unbraced_length_m=unbraced_length_m,
                minor_axis_effective_length_factor=(
                    segment.minor_axis_effective_length_factor
                ),
                torsional_effective_length_factor=(
                    segment.torsional_effective_length_factor
                ),
            )
        except CapacityPackError as exc:
            checks.append(
                MemberStabilityCheck(
                    segment_id=segment.id,
                    member_id=segment.member_id,
                    label=f"{declaration.label} member stability",
                    pack_id=definition.pack_id,
                    status="unsupported",
                    segment_start_m=segment.start_distance_m,
                    segment_end_m=segment.end_distance_m,
                    unbraced_length_m=unbraced_length_m,
                    lateral_bending_restraint=(segment.lateral_bending_restraint),
                    restraint_status=segment.restraint_status,
                    distortional_buckling_status=(segment.distortional_buckling_status),
                    basis=f"Member pack could not evaluate this segment: {exc}",
                    assumptions=[
                        "No member resistance has been inferred from incomplete data."
                    ],
                )
            )
            continue

        station_distances = {
            distance
            for distance in _member_station_distances(analysis, declaration)
            if (segment.start_distance_m <= distance <= segment.end_distance_m)
        }
        station_distances.update((segment.start_distance_m, segment.end_distance_m))
        governing: dict[str, Any] | None = None
        off_axis_exceeded = False
        lateral_bending_unverified = False
        restraint_capacity_fails = False
        compression_flange_values: set[str] = set()
        member = model.members[declaration.id]
        for combination_id in definition.combination_ids:
            for distance in sorted(station_distances):
                axial_kN = abs(member.axial(distance, combination_id))
                signed_major_moment_kNm = member.moment("Mz", distance, combination_id)
                major_moment_kNm = abs(signed_major_moment_kNm)
                minor_moment_kNm = abs(member.moment("My", distance, combination_id))
                off_axis_shear_kN = abs(member.shear("Fz", distance, combination_id))
                torsion_kNm = abs(member.torque(distance, combination_id))
                axial_utilisation = (
                    axial_kN / capacity.design_member_compression_capacity_kN
                )
                compression_flange = _compression_flange(
                    signed_major_moment_kNm,
                    definition.off_axis_tolerance,
                )
                if compression_flange != "none":
                    compression_flange_values.add(compression_flange)
                restraint_status, effective_candidate_ids, _candidate_check_ids = (
                    _segment_restraint_state(
                        model,
                        definition,
                        segment,
                        compression_flange,
                        candidate_checks_by_combination[combination_id],
                    )
                )
                has_verified_lateral_restraint = restraint_status == "verified"
                restraint_capacity_fails |= restraint_status == "inadequate"
                if major_moment_kNm > definition.off_axis_tolerance:
                    lateral_bending_unverified |= not has_verified_lateral_restraint
                axial_bending_utilisation = (
                    axial_utilisation
                    + major_moment_kNm / capacity.design_major_bending_capacity_kNm
                )
                utilisation = (
                    axial_bending_utilisation
                    if has_verified_lateral_restraint
                    else axial_utilisation
                )
                off_axis_exceeded |= (
                    max(
                        minor_moment_kNm,
                        off_axis_shear_kN,
                        torsion_kNm,
                    )
                    > definition.off_axis_tolerance
                )
                candidate: dict[str, Any] = {
                    "combination_id": combination_id,
                    "distance": distance,
                    "axial": axial_kN,
                    "major_moment": major_moment_kNm,
                    "compression_flange": compression_flange,
                    "restraint_status": restraint_status,
                    "restraint_candidate_ids": effective_candidate_ids,
                    "axial_utilisation": axial_utilisation,
                    "axial_bending_utilisation": axial_bending_utilisation,
                    "utilisation": utilisation,
                }
                if governing is None or float(candidate["utilisation"]) > float(
                    governing["utilisation"]
                ):
                    governing = candidate

        if governing is None:
            raise StructuralAnalysisError(
                f"member-stability segment {segment.id!r} has no stations"
            )
        axial_fails = float(governing["axial_utilisation"]) > 1.0
        combined_fails = float(governing["axial_bending_utilisation"]) > 1.0
        distortional_buckling_unverified = (
            segment.distortional_buckling_status != "verified"
        )
        status: Literal["pass", "fail", "unsupported"] = (
            "fail"
            if axial_fails or restraint_capacity_fails
            else "unsupported"
            if (
                lateral_bending_unverified
                or distortional_buckling_unverified
                or off_axis_exceeded
            )
            else "fail"
            if combined_fails
            else "pass"
        )
        checks.append(
            MemberStabilityCheck(
                segment_id=segment.id,
                member_id=segment.member_id,
                label=f"{declaration.label} member stability",
                pack_id=definition.pack_id,
                status=status,
                governing_combination_id=str(governing["combination_id"]),
                governing_station_m=float(governing["distance"]),
                segment_start_m=segment.start_distance_m,
                segment_end_m=segment.end_distance_m,
                unbraced_length_m=unbraced_length_m,
                axial_kN=float(governing["axial"]),
                major_moment_kNm=float(governing["major_moment"]),
                elastic_flexural_buckling_stress_MPa=(
                    capacity.elastic_flexural_buckling_stress_MPa
                ),
                elastic_torsional_buckling_stress_MPa=(
                    capacity.elastic_torsional_buckling_stress_MPa
                ),
                elastic_flexural_torsional_buckling_stress_MPa=(
                    capacity.elastic_flexural_torsional_buckling_stress_MPa
                ),
                nominal_global_buckling_stress_MPa=(
                    capacity.nominal_global_buckling_stress_MPa
                ),
                design_member_compression_capacity_kN=(
                    capacity.design_member_compression_capacity_kN
                ),
                design_major_bending_capacity_kNm=(
                    capacity.design_major_bending_capacity_kNm
                ),
                axial_utilisation=float(governing["axial_utilisation"]),
                axial_bending_utilisation=(
                    float(governing["axial_bending_utilisation"])
                    if not lateral_bending_unverified
                    else None
                ),
                governing_utilisation=(
                    float(governing["utilisation"])
                    if not lateral_bending_unverified
                    else None
                ),
                lateral_bending_restraint=segment.lateral_bending_restraint,
                restraint_status=cast(
                    Literal[
                        "missing",
                        "candidate",
                        "inadequate",
                        "assumed",
                        "verified",
                    ],
                    governing["restraint_status"],
                ),
                compression_flange=cast(
                    Literal[
                        "positive_local_y",
                        "negative_local_y",
                        "none",
                        "mixed",
                    ],
                    "mixed"
                    if len(compression_flange_values) > 1
                    else next(iter(compression_flange_values), "none"),
                ),
                restraint_candidate_ids=list(governing["restraint_candidate_ids"]),
                distortional_buckling_status=(segment.distortional_buckling_status),
                section_record_sha256=capacity.section_record_sha256,
                basis=capacity.basis,
                assumptions=[
                    segment.restraint_basis,
                    segment.distortional_buckling_basis,
                    (
                        "Absolute axial demand is treated as compression; tension "
                        "is not used to improve the result."
                    ),
                    *(
                        [
                            "Major-axis bending is present, but restraint to the "
                            "compression flange and twist is not verified for every "
                            "governing combination. Lateral-torsional resistance and "
                            "the combined member utilisation therefore remain "
                            "unsupported."
                        ]
                        if lateral_bending_unverified
                        else []
                    ),
                    *(
                        [
                            "Distortional buckling resistance is not verified; "
                            "the global compression calculation cannot by itself "
                            "complete the member check."
                        ]
                        if distortional_buckling_unverified
                        else []
                    ),
                    *(
                        [
                            "Minor-axis bending, off-axis shear, or torsion exceeds "
                            "the authored tolerance; the member pack does not cover "
                            "that action."
                        ]
                        if off_axis_exceeded
                        else []
                    ),
                ],
            )
        )
    return checks


def _member_max_axial(
    model,
    declaration,
    *,
    combination_id: str,
) -> float:
    member_length = _length(declaration.start, declaration.end)
    member = model.members[declaration.id]
    return max(
        abs(member.axial(member_length * index / STATION_INTERVALS, combination_id))
        for index in range(STATION_INTERVALS + 1)
    )


def _member_global_displacement(
    model,
    *,
    member_id: str,
    distance_m: float,
    combination_id: str,
) -> Vector3:
    member = model.members[member_id]
    rotation = member.T()[:3, :3]
    return _local_to_global(
        rotation,
        (
            member.deflection("dx", distance_m, combination_id),
            member.deflection("dy", distance_m, combination_id),
            member.deflection("dz", distance_m, combination_id),
        ),
    )


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


def _governing_working_combination(
    model,
    analysis,
    combinations: list[LoadCombination],
) -> LoadCombination:
    """Select the worst credible service combination already authored by design.py."""

    excluded_words = ("demo", "deliberate", "illustrative")
    candidates = [
        combination
        for combination in combinations
        if combination.limit_state == "serviceability"
        and not any(
            word in f"{combination.id} {combination.label}".lower()
            for word in excluded_words
        )
    ]
    if not candidates:
        return _select_combination(combinations, None)

    sections = {section.id: section for section in analysis.sections}

    def score(combination: LoadCombination) -> tuple[float, float, float]:
        maximum_ratio = 0.0
        maximum_moment = 0.0
        maximum_displacement = 0.0
        for declaration in analysis.members:
            moment, displacement = _member_extrema(
                model,
                declaration,
                combination_id=combination.id,
                point_loads=[
                    load
                    for load in analysis.member_loads
                    if load.member_id == declaration.id
                ],
                distributed_loads=[
                    load
                    for load in analysis.member_distributed_loads
                    if load.member_id == declaration.id
                ],
            )
            maximum_moment = max(maximum_moment, moment)
            maximum_displacement = max(maximum_displacement, displacement)
            section = sections[declaration.section_id]
            if section.bending_reference_kNm:
                maximum_ratio = max(
                    maximum_ratio,
                    moment / section.bending_reference_kNm,
                )
            member_length_mm = _length(declaration.start, declaration.end) * 1000.0
            displacement_limit = declaration.deflection_limit_mm
            if (
                displacement_limit is None
                and declaration.deflection_limit_ratio is not None
            ):
                displacement_limit = (
                    member_length_mm / declaration.deflection_limit_ratio
                )
            if displacement_limit:
                maximum_ratio = max(
                    maximum_ratio,
                    displacement / displacement_limit,
                )
        return maximum_ratio, maximum_moment, maximum_displacement

    return max(candidates, key=score)


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
    tension_member_checks: list[TensionMemberCheck],
    cross_section_checks: list[MemberCrossSectionCheck],
    member_stability_checks: list[MemberStabilityCheck],
    member_restraint_candidate_checks: list[MemberRestraintCandidateCheck],
    serviceability_checks: list[ServiceabilityCheck],
    equilibrium_status: Literal["pass", "fail"],
    residual: float,
    equilibrium_tolerance: float,
    stability_result: StabilityResult | None,
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
            *(f"{role}: {reference}" for role, reference in basis.standards.items()),
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
    action_inputs: list[CalculationInput] = []
    wind_bases_by_id = {
        wind_basis.id: wind_basis for wind_basis in capture.wind_action_bases
    }
    for wind_basis in capture.wind_action_bases:
        action_inputs.extend(
            [
                CalculationInput(
                    symbol=f"site,{wind_basis.id}",
                    label="Site",
                    value=wind_basis.site_address,
                    source="design.py StructuralModel.wind_action_basis",
                ),
                CalculationInput(
                    symbol=f"region,{wind_basis.id}",
                    label="Wind region",
                    value=(
                        f"{wind_basis.region} — {wind_basis.region_area} "
                        f"({wind_basis.region_status})"
                    ),
                    source=wind_basis.region_source,
                ),
                CalculationInput(
                    symbol=f"TC,{wind_basis.id}",
                    label="Terrain category",
                    value=wind_basis.terrain_category,
                    source="design.py site wind input",
                ),
                CalculationInput(
                    symbol=f"R,{wind_basis.id}",
                    label="Annual recurrence interval",
                    value=wind_basis.annual_recurrence_interval_years,
                    unit="years",
                    source="design.py site wind input",
                ),
                CalculationInput(
                    symbol=f"z,{wind_basis.id}",
                    label="Reference height",
                    value=wind_basis.reference_height_m,
                    unit="m",
                    source="design.py site wind input",
                ),
                CalculationInput(
                    symbol=f"enclosure,{wind_basis.id}",
                    label="Enclosure / openings",
                    value=(
                        f"{wind_basis.enclosure or 'not declared'}; "
                        f"{wind_basis.openings_operating_state or 'not declared'}"
                    ),
                    source="tertius_site.py action envelope",
                ),
                CalculationInput(
                    symbol=f"selection,{wind_basis.id}",
                    label="Coefficient case selection",
                    value=(wind_basis.coefficient_selection_policy or "not declared"),
                    source="tertius_site.py action envelope",
                ),
            ]
        )
        action_equations.extend(
            [
                CalculationEquation(
                    label=f"{wind_basis.id} site wind speed",
                    expression="V_sit = V_R M_c M_d M_z,cat M_s M_t",
                    substitution=(
                        f"{wind_basis.regional_wind_speed_m_s:g} × "
                        f"{wind_basis.climate_change_multiplier:g} × "
                        f"{wind_basis.direction_multiplier:g} × "
                        f"{wind_basis.terrain_height_multiplier:g} × "
                        f"{wind_basis.shielding_multiplier:g} × "
                        f"{wind_basis.topographic_multiplier:g}"
                    ),
                    result=wind_basis.site_wind_speed_m_s,
                    unit="m/s",
                ),
                CalculationEquation(
                    label=f"{wind_basis.id} free-stream dynamic pressure",
                    expression="q_z = 0.5 ρ V_sit²",
                    substitution=(
                        f"0.5 × 1.2 × {wind_basis.site_wind_speed_m_s:g}² / 1000"
                    ),
                    result=wind_basis.q_z_kPa,
                    unit="kPa",
                ),
            ]
        )
    for load in capture.loads:
        if load.wind_basis_id is not None and load.net_pressure_coefficient is not None:
            wind_basis = wind_bases_by_id[load.wind_basis_id]
            action_equations.append(
                CalculationEquation(
                    label=f"{load.label} net surface pressure",
                    expression="p_net = q_z |C_net|",
                    substitution=(
                        f"{wind_basis.q_z_kPa:g} × |{load.net_pressure_coefficient:g}|"
                    ),
                    result=load.pressure_kPa,
                    unit="kPa",
                )
            )
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
            value=check.capacity_kNm
            if check.capacity_kNm is not None
            else "not supplied",
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
    basis_status: Literal["pass", "blocked"] = (
        "pass" if basis is not None else "blocked"
    )
    action_standard_references = [
        reference
        for role, reference in (basis.standards.items() if basis else [])
        if "action" in role or "wind" in role
    ]
    action_basis_ready = bool(action_standard_references) and all(
        "confirm" not in reference.lower() and "not yet active" not in reference.lower()
        for reference in action_standard_references
    )
    wind_surface_loads = [load for load in capture.loads if load.case == "wind"]
    wind_basis_drift = {
        basis.id: verify_site_wind_snapshot(basis.model_dump())
        for basis in capture.wind_action_bases
    }
    unlinked_wind_loads = [
        load.id for load in wind_surface_loads if load.wind_basis_id is None
    ]
    unverified_wind_bases = [
        basis.id
        for basis in capture.wind_action_bases
        if basis.region_status != "verified"
        or basis.table_status != "verified"
        or wind_basis_drift[basis.id]
    ]
    assumed_wind_coefficients = [
        load.id for load in wind_surface_loads if load.coefficient_status == "assumed"
    ]
    working_wind_coefficients = [
        load.id
        for load in wind_surface_loads
        if load.coefficient_status == "working_conservative"
    ]
    wind_actions_ready = not wind_surface_loads or (
        bool(capture.wind_action_bases)
        and not unlinked_wind_loads
        and not unverified_wind_bases
        and not assumed_wind_coefficients
    )
    action_basis_ready = action_basis_ready and wind_actions_ready
    action_assumptions = [
        *(
            ["The project action standard references still require confirmation."]
            if not action_standard_references
            or any(
                "confirm" in reference.lower() or "not yet active" in reference.lower()
                for reference in action_standard_references
            )
            else []
        ),
        *(
            [
                "Wind loads are not linked to a design.py wind action basis: "
                + ", ".join(unlinked_wind_loads)
            ]
            if unlinked_wind_loads
            else []
        ),
        *(
            [
                "Wind site/table verification remains outstanding for: "
                + ", ".join(unverified_wind_bases)
            ]
            if unverified_wind_bases
            else []
        ),
        *(
            [
                "Net surface pressure coefficients remain assumed for: "
                + ", ".join(assumed_wind_coefficients)
            ]
            if assumed_wind_coefficients
            else []
        ),
        *(
            [
                "Working conservative envelope is active for: "
                + ", ".join(working_wind_coefficients)
                + ". The solver selects the worst available credible service "
                "combination; AS/NZS 1170.2 surface-zone verification remains "
                "an evidence item before final design."
            ]
            if working_wind_coefficients
            else []
        ),
        *(
            [
                f"{basis_id}: {message}"
                for basis_id, messages in wind_basis_drift.items()
                for message in messages
            ]
        ),
    ]
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
    stability_definition = analysis.stability
    stability_status: Literal["pass", "fail", "warning", "blocked"]
    stability_direction_evidence_complete = bool(
        stability_result
        and len(stability_result.direction_results) >= 2
        and all(
            result.alpha_cr is not None for result in stability_result.direction_results
        )
    )
    stability_base_model_matches = _analysis_base_model_matches(analysis)
    stability_analysis_basis_ready = bool(
        stability_definition
        and stability_base_model_matches
        and (
            (
                stability_definition.analysis_base_model == "perfectly_pinned"
                and stability_definition.analysis_basis_status
                == "verified_conservative"
            )
            or (
                stability_definition.analysis_basis_status == "verified"
                and (
                    (
                        stability_definition.analysis_base_model == "unspecified"
                        and stability_definition.base_stiffness_status == "verified"
                    )
                    or stability_definition.physical_connection_stiffness_status
                    == "verified"
                )
            )
        )
    )
    if stability_definition is None or stability_result is None:
        stability_status = "blocked"
    elif (
        not stability_result.converged
        or (
            stability_result.minimum_alpha_cr is not None
            and stability_result.minimum_alpha_cr <= 1.0
        )
    ):
        stability_status = "fail"
    elif (
        stability_result.governing_moment_amplification
        > stability_result.amplification_warning_ratio
        or stability_result.governing_displacement_amplification
        > stability_result.amplification_warning_ratio
        or not stability_direction_evidence_complete
        or not stability_analysis_basis_ready
        or stability_result.simplified_alpha_cr_applicable is False
        or actions_status != "pass"
        or combinations_status != "pass"
    ):
        stability_status = "warning"
    else:
        stability_status = "pass"

    cross_section_definition = analysis.cross_section_verification
    cross_section_status: Literal[
        "pass", "fail", "not_checked", "unsupported", "blocked"
    ]
    if cross_section_definition is None:
        cross_section_status = "not_checked"
    elif stability_status != "pass":
        cross_section_status = "blocked"
    elif not cross_section_checks:
        cross_section_status = "not_checked"
    elif any(check.status == "fail" for check in cross_section_checks):
        cross_section_status = "fail"
    elif any(check.status == "unsupported" for check in cross_section_checks):
        cross_section_status = "unsupported"
    elif all(check.status == "pass" for check in cross_section_checks):
        cross_section_status = "pass"
    else:
        cross_section_status = "not_checked"

    cross_section_equations = [
        equation
        for check in cross_section_checks
        if check.governing_utilisation is not None
        for equation in (
            CalculationEquation(
                label=f"{check.member_id} axial + major bending interaction",
                expression="u_NM = N*/(phi_c N_s) + M*/(phi_b M_s)",
                substitution=(
                    f"{check.axial_kN:g}/{check.design_compression_capacity_kN:g} "
                    f"+ {check.major_moment_kNm:g}/"
                    f"{check.design_major_bending_capacity_kNm:g}"
                ),
                result=check.axial_bending_utilisation or 0.0,
            ),
            CalculationEquation(
                label=f"{check.member_id} major bending + web shear interaction",
                expression=("u_MV = sqrt[(M*/(phi_b M_s))² + (V*/(phi_v V_v))²]"),
                substitution=(
                    f"sqrt[({check.major_moment_kNm:g}/"
                    f"{check.design_major_bending_capacity_kNm:g})² + "
                    f"({check.web_shear_kN:g}/"
                    f"{check.design_web_shear_capacity_kN:g})²]"
                ),
                result=check.bending_shear_utilisation or 0.0,
            ),
        )
    ]
    cross_section_equations.extend(
        CalculationEquation(
            label=f"{check.member_id} off-axis support reaction demand",
            expression="R_off-axis* = max |Fz|",
            substitution=f"max |Fz| = {check.off_axis_required_reaction_kN:g} kN",
            result=check.off_axis_required_reaction_kN,
            unit="kN",
        )
        for check in cross_section_checks
        if check.off_axis_required_reaction_kN is not None
    )
    cross_section_outputs = [
        output
        for check in cross_section_checks
        for output in (
            CalculationInput(
                symbol=f"u_gov,{check.member_id}",
                label=f"{check.label} governing utilisation",
                value=(
                    check.governing_utilisation
                    if check.governing_utilisation is not None
                    else check.status
                ),
                source=(
                    f"{check.governing_combination_id or 'no valid envelope'} at "
                    f"{check.governing_station_m or 0:g} m"
                ),
            ),
            CalculationInput(
                symbol=f"phi_b M_s,{check.member_id}",
                label=f"{check.label} design major-bending resistance",
                value=(
                    check.design_major_bending_capacity_kNm
                    if check.design_major_bending_capacity_kNm is not None
                    else "not available"
                ),
                unit=(
                    "kN.m"
                    if check.design_major_bending_capacity_kNm is not None
                    else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"phi_c N_s,{check.member_id}",
                label=f"{check.label} design compression section resistance",
                value=(
                    check.design_compression_capacity_kN
                    if check.design_compression_capacity_kN is not None
                    else "not available"
                ),
                unit=(
                    "kN" if check.design_compression_capacity_kN is not None else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"phi_v V_v,{check.member_id}",
                label=f"{check.label} design web-shear resistance",
                value=(
                    check.design_web_shear_capacity_kN
                    if check.design_web_shear_capacity_kN is not None
                    else "not available"
                ),
                unit=("kN" if check.design_web_shear_capacity_kN is not None else None),
                source=(
                    f"{check.shear_regime or 'unknown'} web; "
                    f"d1/t={check.web_slenderness or 0:g}"
                ),
            ),
        )
    ]
    cross_section_outputs.extend(
        CalculationInput(
            symbol=f"path_off-axis,{check.member_id}",
            label=f"{check.label} off-axis collector path",
            value=(
                " → ".join(check.off_axis_collector_component_ids)
                if check.off_axis_collector_component_ids
                else check.off_axis_load_path_status
            ),
            source=check.off_axis_load_path_basis or "No authored path basis.",
        )
        for check in cross_section_checks
    )

    member_stability_definition = analysis.member_stability_verification
    member_stability_status: Literal[
        "pass", "fail", "not_checked", "unsupported", "blocked"
    ]
    if member_stability_definition is None:
        member_stability_status = "not_checked"
    elif cross_section_status != "pass":
        member_stability_status = "blocked"
    elif not member_stability_checks:
        member_stability_status = "not_checked"
    elif any(check.status == "fail" for check in member_stability_checks):
        member_stability_status = "fail"
    elif any(check.status == "unsupported" for check in member_stability_checks):
        member_stability_status = "unsupported"
    elif all(check.status == "pass" for check in member_stability_checks):
        member_stability_status = "pass"
    else:
        member_stability_status = "not_checked"

    bracing_status: Literal["pass", "fail", "warning", "not_checked", "unsupported"]
    if any(check.status == "fail" for check in tension_member_checks):
        bracing_status = "fail"
    elif any(check.status == "unsupported" for check in tension_member_checks):
        bracing_status = "unsupported"
    elif not member_restraint_candidate_checks and not tension_member_checks:
        bracing_status = "not_checked"
    elif any(check.status == "fail" for check in member_restraint_candidate_checks):
        bracing_status = "fail"
    elif all(check.status == "pass" for check in member_restraint_candidate_checks) and all(
        check.status == "pass" for check in tension_member_checks
    ):
        bracing_status = "pass"
    elif any(
        check.status == "unsupported" for check in member_restraint_candidate_checks
    ):
        bracing_status = "unsupported"
    else:
        bracing_status = "warning"

    member_stability_equations = [
        equation
        for check in member_stability_checks
        if check.design_member_compression_capacity_kN is not None
        for equation in (
            CalculationEquation(
                label=f"{check.segment_id} flexural-torsional elastic buckling",
                expression=(
                    "Fe,FT = (Fey + Fez)/(2β) [1 - sqrt(1 - 4β Fey Fez/(Fey + Fez)²)]"
                ),
                substitution=(
                    f"Fey/Fez from Lb={check.unbraced_length_m:g} m; "
                    f"Fe,FT={check.elastic_flexural_torsional_buckling_stress_MPa:g}"
                ),
                result=(check.elastic_flexural_torsional_buckling_stress_MPa or 0.0),
                unit="MPa",
            ),
            CalculationEquation(
                label=f"{check.segment_id} global compression utilisation",
                expression="u_N = N*/(phi_c N_c)",
                substitution=(
                    f"{check.axial_kN:g}/"
                    f"{check.design_member_compression_capacity_kN:g}"
                ),
                result=check.axial_utilisation or 0.0,
            ),
        )
    ]
    member_stability_equations.extend(
        CalculationEquation(
            label=f"{check.candidate_id} eccentric-load restraint demand",
            expression="P_L = 1.5(e/d)W; M_L = P_L d",
            substitution=(
                f"P_L={check.required_force_kN:g} kN; "
                f"M_L={check.required_moment_kNm:g} kN.m"
            ),
            result=check.required_force_kN,
            unit="kN",
        )
        for check in member_restraint_candidate_checks
        if check.required_force_kN is not None and check.required_moment_kNm is not None
    )
    member_stability_outputs = [
        output
        for check in member_stability_checks
        for output in (
            CalculationInput(
                symbol=f"phi_c N_c,{check.segment_id}",
                label=f"{check.label} design member compression resistance",
                value=(
                    check.design_member_compression_capacity_kN
                    if check.design_member_compression_capacity_kN is not None
                    else "not available"
                ),
                unit=(
                    "kN"
                    if check.design_member_compression_capacity_kN is not None
                    else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"u_N,{check.segment_id}",
                label=f"{check.label} global compression utilisation",
                value=(
                    check.axial_utilisation
                    if check.axial_utilisation is not None
                    else check.status
                ),
                source=(
                    f"{check.governing_combination_id or 'no valid envelope'} at "
                    f"{check.governing_station_m or 0:g} m"
                ),
            ),
            CalculationInput(
                symbol=f"LTB,{check.segment_id}",
                label=f"{check.label} lateral-bending restraint",
                value=(f"{check.lateral_bending_restraint} ({check.restraint_status})"),
                source=check.assumptions[0] if check.assumptions else check.basis,
            ),
            CalculationInput(
                symbol=f"compression_flange,{check.segment_id}",
                label=f"{check.label} signed-moment compression flange",
                value=check.compression_flange,
                source=(
                    f"Signed PyNite local Mz envelope for "
                    f"{check.governing_combination_id or 'no valid envelope'}"
                ),
            ),
            CalculationInput(
                symbol=f"restraint_candidates,{check.segment_id}",
                label=f"{check.label} effective physical restraint candidates",
                value=(
                    ", ".join(check.restraint_candidate_ids)
                    if check.restraint_candidate_ids
                    else "none"
                ),
                source="Builder-authored axes and registered connection handles",
            ),
            CalculationInput(
                symbol=f"distortional,{check.segment_id}",
                label=f"{check.label} distortional buckling",
                value=check.distortional_buckling_status,
                source=(
                    check.assumptions[1] if len(check.assumptions) > 1 else check.basis
                ),
            ),
        )
    ]

    stability_assumptions = (
        [
            "Equivalent horizontal forces/geometric imperfections are not authored.",
            "First-/second-order amplification is not available.",
            "Base rotational stiffness has not been declared.",
        ]
        if stability_definition is None or stability_result is None
        else [
            stability_definition.imperfection_basis,
            stability_definition.base_stiffness_basis,
            *(
                [
                    "The analytical base model is not yet a verified conservative "
                    "idealisation."
                ]
                if not stability_analysis_basis_ready
                else []
            ),
            *(
                [
                    "The declared analytical base model does not match the actual "
                    "design.py member-end restraints."
                ]
                if not stability_base_model_matches
                else []
            ),
            *(
                [
                    "Physical GPB/anchor rotational stiffness is not checked, but "
                    "it is not relied upon by the perfectly pinned analysis model."
                ]
                if stability_definition.analysis_base_model == "perfectly_pinned"
                and stability_definition.physical_connection_stiffness_status
                != "verified"
                else []
            ),
            *(
                [
                    "Both sway directions and their NEd/200 NHF displacement "
                    "checks have not all been authored."
                ]
                if not stability_direction_evidence_complete
                else []
            ),
            *(
                [
                    "The P399 simplified NHF alpha-cr expression is outside its "
                    "rafter axial-force applicability limit; a more accurate "
                    "elastic critical-load analysis is required."
                ]
                if stability_result.simplified_alpha_cr_applicable is False
                else []
            ),
            *(
                [
                    "The linear/P-Delta amplification ratio exceeds the authored "
                    "review threshold. P399 does not define this ratio as a failure "
                    "criterion; the converged second-order design effects remain "
                    "visible for downstream resistance and serviceability checks."
                ]
                if (
                    stability_result.governing_moment_amplification
                    > stability_result.amplification_warning_ratio
                    or stability_result.governing_displacement_amplification
                    > stability_result.amplification_warning_ratio
                )
                else []
            ),
            *(
                [
                    "Action inputs and combinations remain provisional, so this "
                    "stability result cannot become a design pass."
                ]
                if actions_status != "pass" or combinations_status != "pass"
                else []
            ),
        ]
    )
    stability_equations = (
        []
        if stability_result is None
        else [
            equation
            for comparison in stability_result.member_comparisons
            for equation in (
                CalculationEquation(
                    label=f"{comparison.member_id} moment amplification",
                    expression="η_M = M_II / M_I",
                    substitution=(
                        f"{comparison.second_order_max_moment_kNm:g} / "
                        f"{comparison.first_order_max_moment_kNm:g}"
                    ),
                    result=comparison.moment_amplification,
                ),
                CalculationEquation(
                    label=f"{comparison.member_id} displacement amplification",
                    expression="η_δ = δ_II / δ_I",
                    substitution=(
                        f"{comparison.second_order_max_displacement_mm:g} / "
                        f"{comparison.first_order_max_displacement_mm:g}"
                    ),
                    result=comparison.displacement_amplification,
                ),
            )
        ]
    )
    if stability_result is not None:
        stability_equations.extend(
            CalculationEquation(
                label=f"{direction.id} elastic critical load ratio",
                expression="αcr = h / (200 δNHF)",
                substitution=(
                    f"{stability_definition.column_height_m:g} × 1000 / "
                    f"(200 × {direction.nhf_eaves_displacement_mm:g})"
                    if stability_definition is not None
                    and stability_definition.column_height_m is not None
                    and direction.nhf_eaves_displacement_mm > 0
                    else "insufficient NHF displacement evidence"
                ),
                result=(
                    direction.alpha_cr
                    if direction.alpha_cr is not None
                    else "not available"
                ),
            )
            for direction in stability_result.direction_results
        )
        if (
            stability_result.rafter_design_axial_kN is not None
            and stability_result.rafter_elastic_critical_load_kN is not None
            and stability_result.rafter_axial_limit_kN is not None
        ):
            stability_equations.append(
                CalculationEquation(
                    label="Rafter axial-force applicability",
                    expression="NEd ≤ 0.09 Ncr",
                    substitution=(
                        f"{stability_result.rafter_design_axial_kN:g} ≤ "
                        f"0.09 × "
                        f"{stability_result.rafter_elastic_critical_load_kN:g}"
                    ),
                    result=(
                        stability_result.rafter_design_axial_kN
                        / stability_result.rafter_axial_limit_kN
                    ),
                )
            )

    sheets = [
        CalculationSheet(
            id="sheet-p399-geometry",
            stage_id="geometry",
            title="Geometry and analytical scheme",
            status=basis_status,
            p399_reference="SCI P399 Sections 3 and 6.1",
            purpose="Prove which design.py geometry became nodes, members, and supports.",
            assumptions=list(
                dict.fromkeys(member.assumption for member in analysis.members)
            ),
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
                    value=sum(
                        any(node.restraints.model_dump().values()) for node in nodes
                    ),
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
                action_assumptions
                or [
                    "The authored actions remain illustrative until the project/site "
                    "Australian action inputs are confirmed."
                ]
            ),
            inputs=action_inputs,
            equations=action_equations,
            references=[
                *basis_references,
                *(
                    f"{basis.id}: {basis.provenance}"
                    for basis in capture.wind_action_bases
                ),
            ],
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
                ),
                CalculationInput(
                    symbol="r_tol",
                    label="Equilibrium diagnostic tolerance",
                    value=equilibrium_tolerance,
                    unit="kN / kN.m",
                    source=(
                        "Absolute first-order tolerance."
                        if stability_result is None
                        else (
                            "0.1% of the governing action/reaction scale; distributed "
                            "loads are integrated over the displaced member geometry."
                        )
                    ),
                ),
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
            status=stability_status,
            p399_reference="SCI P399 Sections 7.2–7.8",
            purpose=(
                "Compare first-order elastic and iterative P-Delta response for the "
                "authored imperfection combination."
            ),
            assumptions=stability_assumptions,
            inputs=(
                []
                if stability_definition is None
                else [
                    CalculationInput(
                        symbol="method",
                        label="Second-order method",
                        value=stability_definition.method,
                        source="design.py StructuralModel.stability",
                    ),
                    CalculationInput(
                        symbol="combinations",
                        label="Stability combinations",
                        value=(
                            ", ".join(
                                direction.stability_combination_id
                                for direction in stability_definition.direction_cases
                            )
                            or stability_definition.stability_combination_id
                        ),
                        source="design.py StructuralModel.stability",
                    ),
                    CalculationInput(
                        symbol="imperfection_cases",
                        label="Imperfection load cases",
                        value=(
                            ", ".join(
                                direction.imperfection_case_id
                                for direction in stability_definition.direction_cases
                            )
                            or stability_definition.imperfection_case_id
                        ),
                        source=stability_definition.imperfection_basis,
                    ),
                    CalculationInput(
                        symbol="analysis_base_model",
                        label="Analytical base model",
                        value=stability_definition.analysis_base_model,
                        source=stability_definition.base_stiffness_basis,
                    ),
                    CalculationInput(
                        symbol="analysis_basis_status",
                        label="Analytical basis status",
                        value=stability_definition.analysis_basis_status,
                        source="design.py StructuralModel.stability",
                    ),
                    CalculationInput(
                        symbol="analysis_base_match",
                        label="Base model matches member restraints",
                        value=stability_base_model_matches,
                        source=(
                            "Direct comparison with design.py start restraints on "
                            "the declared eaves/column members"
                        ),
                    ),
                    CalculationInput(
                        symbol="physical_base_stiffness",
                        label="Physical connection stiffness",
                        value=(
                            stability_definition.physical_connection_stiffness_status
                        ),
                        source=(
                            "Evidence item retained for P399 Connections/Bases; "
                            "not relied upon by a perfectly pinned model."
                        ),
                    ),
                    CalculationInput(
                        symbol="nhf_combinations",
                        label="NEd/200 NHF combinations",
                        value=", ".join(
                            direction.nhf_combination_id
                            for direction in stability_definition.direction_cases
                        )
                        or "not authored",
                        source="design.py StructuralModel.stability",
                    ),
                    CalculationInput(
                        symbol="η_warning",
                        label="Amplification warning ratio",
                        value=stability_definition.amplification_warning_ratio,
                        source="design.py StructuralModel.stability",
                    ),
                ]
            ),
            equations=stability_equations,
            outputs=(
                []
                if stability_result is None
                else [
                    CalculationInput(
                        symbol="η_M,max",
                        label="Governing moment amplification",
                        value=stability_result.governing_moment_amplification,
                        source=(
                            f"PyNite linear/P-Delta comparison for "
                            f"{stability_result.combination_id}"
                        ),
                    ),
                    CalculationInput(
                        symbol="η_δ,max",
                        label="Governing displacement amplification",
                        value=stability_result.governing_displacement_amplification,
                        source=(
                            f"PyNite linear/P-Delta comparison for "
                            f"{stability_result.combination_id}"
                        ),
                    ),
                    CalculationInput(
                        symbol="converged",
                        label="P-Delta convergence",
                        value=stability_result.converged,
                        source="PyNite iterative P-Delta solve",
                    ),
                    CalculationInput(
                        symbol="αcr,min",
                        label="Governing elastic critical load ratio",
                        value=(
                            stability_result.minimum_alpha_cr
                            if stability_result.minimum_alpha_cr is not None
                            else "not available"
                        ),
                        source="P399 NHF method; minimum of authored sway directions",
                    ),
                    CalculationInput(
                        symbol="direction",
                        label="Governing sway direction",
                        value=(
                            stability_result.governing_direction_id or "not available"
                        ),
                        source="Bidirectional stability envelope",
                    ),
                    CalculationInput(
                        symbol="second_order",
                        label="Second-order analysis required",
                        value=(
                            stability_result.second_order_required
                            if stability_result.second_order_required is not None
                            else "not available"
                        ),
                        source="P399 αcr < 10 threshold",
                    ),
                    CalculationInput(
                        symbol="rafter_axial_significant",
                        label="Rafter axial force significant",
                        value=(
                            stability_result.rafter_axial_force_significant
                            if stability_result.rafter_axial_force_significant
                            is not None
                            else "not available"
                        ),
                        source="P399 NEd > 0.09 Ncr applicability check",
                    ),
                ]
            ),
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
            related_load_case_ids=(
                [
                    direction.imperfection_case_id
                    for direction in stability_definition.direction_cases
                ]
                or [stability_definition.imperfection_case_id]
                if stability_definition is not None
                else []
            ),
            related_combination_ids=(
                [
                    combination_id
                    for direction in stability_definition.direction_cases
                    for combination_id in (
                        direction.stability_combination_id,
                        direction.nhf_combination_id,
                    )
                ]
                or [stability_definition.stability_combination_id]
                if stability_definition is not None
                else [combination.id]
            ),
        ),
        CalculationSheet(
            id="sheet-p399-cross-section",
            stage_id="cross_section",
            title="Cross-section verification",
            status=cross_section_status,
            p399_reference="SCI P399 Section 8.1",
            purpose="Check classification/effective properties and governing force interactions.",
            inputs=(
                []
                if cross_section_definition is None
                else [
                    CalculationInput(
                        symbol="capacity_pack",
                        label="Versioned capacity pack",
                        value=cross_section_definition.pack_id,
                        source="design.py StructuralModel.cross_section_verification",
                    ),
                    CalculationInput(
                        symbol="ULS_envelope",
                        label="Checked ULS combinations",
                        value=", ".join(cross_section_definition.combination_ids),
                        source="design.py StructuralModel.cross_section_verification",
                    ),
                ]
            ),
            equations=cross_section_equations,
            assumptions=(
                [
                    assumption
                    for check in cross_section_checks
                    for assumption in check.assumptions
                ]
                if cross_section_checks
                else [
                    "No versioned Australian cross-section capacity pack is selected."
                ]
            ),
            outputs=(
                cross_section_outputs if cross_section_checks else reference_outputs
            ),
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=(
                cross_section_definition.combination_ids
                if cross_section_definition is not None
                else [combination.id]
            ),
        ),
        CalculationSheet(
            id="sheet-p399-member-stability",
            stage_id="member_stability",
            title="Member stability",
            status=member_stability_status,
            p399_reference="SCI P399 Sections 8.2–8.4",
            purpose="Verify buckling and axial-bending interaction on restraint-defined segments.",
            inputs=(
                []
                if member_stability_definition is None
                else [
                    CalculationInput(
                        symbol="member_capacity_pack",
                        label="Versioned member-capacity pack",
                        value=member_stability_definition.pack_id,
                        source=(
                            "design.py StructuralModel.member_stability_verification"
                        ),
                    ),
                    CalculationInput(
                        symbol="member_ULS_envelope",
                        label="Checked ULS combinations",
                        value=", ".join(member_stability_definition.combination_ids),
                        source=(
                            "design.py StructuralModel.member_stability_verification"
                        ),
                    ),
                ]
            ),
            equations=member_stability_equations,
            assumptions=(
                [
                    assumption
                    for check in member_stability_checks
                    for assumption in check.assumptions
                ]
                if member_stability_checks
                else ["No restraint-defined member-stability segments are authored."]
            ),
            outputs=(
                member_stability_outputs
                if member_stability_checks
                else reference_outputs
            ),
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=(
                member_stability_definition.combination_ids
                if member_stability_definition is not None
                else [combination.id]
            ),
        ),
        CalculationSheet(
            id="sheet-p399-bracing",
            stage_id="bracing",
            title="Bracing and restraint",
            status=bracing_status,
            p399_reference="SCI P399 Section 9",
            purpose="Trace restraint forces and stiffness to a complete resisting system.",
            assumptions=[
                "Cladding and fasteners are not assumed to provide unverified restraint.",
                *[
                    assumption
                    for check in tension_member_checks
                    for assumption in check.assumptions
                ],
                *(
                    [
                        "Physical restraint candidates are active, but their complete "
                        "force/moment resistance, stiffness, and longitudinal load path "
                        "are not all verified."
                    ]
                    if member_restraint_candidate_checks
                    else [
                        "No restraint candidates or bracing stiffness checks are active."
                    ]
                ),
            ],
            inputs=[
                CalculationInput(
                    symbol=f"restraint,{check.candidate_id}",
                    label=f"{check.candidate_id} demand/capacity state",
                    value=check.status,
                    source=(
                        f"{check.combination_id}; identity={check.identity_status}; "
                        f"stiffness={check.stiffness_status}; "
                        f"anchorage={check.anchorage_status}; {check.provenance}; "
                        f"required={check.required_force_kN if check.required_force_kN is not None else 'not defined'} kN; "
                        f"available={check.available_force_kN if check.available_force_kN is not None else 'not verified'} kN"
                    ),
                )
                for check in member_restraint_candidate_checks
            ]
            + [
                CalculationInput(
                    symbol=f"strap,{check.member_id}",
                    label=f"{check.label} ULS tension envelope",
                    value=check.status,
                    source=(
                        f"governing={check.governing_combination_id or 'none'}; "
                        f"demand={check.tension_demand_kN:g} kN; "
                        f"strap capacity={check.tension_capacity_kN if check.tension_capacity_kN is not None else 'not declared'} kN; "
                        f"end capacity={check.end_connection_capacity_kN if check.end_connection_capacity_kN is not None else 'unverified'} kN; "
                        f"per end fastener={check.required_force_per_end_fastener_kN if check.required_force_per_end_fastener_kN is not None else 'not declared'} kN"
                    ),
                )
                for check in tension_member_checks
            ],
            references=list(
                dict.fromkeys(
                    [
                        *basis_references,
                        *(
                            reference
                            for check in member_restraint_candidate_checks
                            for reference in check.evidence_references
                        ),
                    ]
                )
            ),
            equations=[
                CalculationEquation(
                    label=f"{check.label} governing strap utilisation",
                    expression="u = N* / min(phi Nt, phi Nc,end)",
                    substitution=(
                        f"{check.tension_demand_kN:g} / "
                        f"{check.governing_capacity_kN if check.governing_capacity_kN is not None else 'unverified'}"
                    ),
                    result=check.governing_utilisation or 0.0,
                )
                for check in tension_member_checks
                if check.governing_capacity_kN is not None
            ]
            + [
                CalculationEquation(
                    label=f"{check.label} required force per end fastener",
                    expression="F*,fastener = N* / n_end",
                    substitution=(
                        f"{check.tension_demand_kN:g} / {check.end_fastener_count}"
                    ),
                    result=check.required_force_per_end_fastener_kN or 0.0,
                    unit="kN",
                )
                for check in tension_member_checks
                if check.end_fastener_count is not None
            ],
            outputs=[
                CalculationInput(
                    symbol=f"N*,{check.member_id}",
                    label=f"{check.label} governing ULS tension",
                    value=check.tension_demand_kN,
                    unit="kN",
                    source=check.governing_combination_id or "No active ULS tension",
                )
                for check in tension_member_checks
            ],
            related_member_ids=member_ids,
            related_combination_ids=list(
                dict.fromkeys(
                    [
                        combination.id,
                        *(
                            check.governing_combination_id
                            for check in tension_member_checks
                            if check.governing_combination_id is not None
                        ),
                    ]
                )
            ),
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
            summary=(
                f"{len(capture.wind_action_bases)} site basis/bases, "
                f"{len(wind_surface_loads)} wind surface action(s), "
                f"{len(action_equations)} trace equation(s)."
            ),
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
            blocking_stage_ids=[]
            if actions_status in {"pass", "warning"}
            else ["actions"],
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
            status=stability_status,
            summary=(
                "Imperfection load and P-Delta comparison are missing."
                if stability_result is None
                else (
                    f"{len(stability_result.direction_results)} sway directions; "
                    f"ηM={stability_result.governing_moment_amplification:.3f}, "
                    f"ηδ={stability_result.governing_displacement_amplification:.3f}, "
                    f"αcr,min="
                    f"{stability_result.minimum_alpha_cr:.2f}; "
                    f"{stability_definition.analysis_base_model} bases."
                    if stability_result.minimum_alpha_cr is not None
                    else (
                        f"{stability_result.combination_id}: "
                        f"ηM={stability_result.governing_moment_amplification:.3f}, "
                        f"ηδ="
                        f"{stability_result.governing_displacement_amplification:.3f}; "
                        "NHF αcr evidence incomplete."
                    )
                )
            ),
            sheet_ids=["sheet-p399-stability"],
            blocking_stage_ids=[] if analysis_status == "pass" else ["analysis"],
        ),
        VerificationStage(
            id="cross_section",
            order=6,
            label="Cross-section",
            p399_reference="§8.1",
            status=cross_section_status,
            summary=(
                "No versioned Australian capacity pack is selected."
                if cross_section_definition is None
                else (
                    f"{len(cross_section_checks)} members checked across "
                    f"{len(cross_section_definition.combination_ids)} ULS "
                    f"combinations; governing utilisation "
                    f"{max((check.governing_utilisation or 0.0) for check in cross_section_checks):.3f}. "
                    "Cross-section only."
                    if cross_section_checks
                    else "Cross-section envelope produced no member results."
                )
            ),
            sheet_ids=["sheet-p399-cross-section"],
            blocking_stage_ids=["stability"],
        ),
        VerificationStage(
            id="member_stability",
            order=7,
            label="Member stability",
            p399_reference="§8.2–§8.4",
            status=member_stability_status,
            summary=(
                "No restraint-defined member-capacity pack is selected."
                if member_stability_definition is None
                else (
                    f"{len(member_stability_checks)} restraint segment(s); "
                    f"{sum(check.status == 'pass' for check in member_stability_checks)} "
                    "pass, "
                    f"{sum(check.status == 'fail' for check in member_stability_checks)} "
                    "fail, "
                    f"{sum(check.status == 'unsupported' for check in member_stability_checks)} "
                    "need restraint and/or distortional-buckling evidence."
                )
            ),
            sheet_ids=["sheet-p399-member-stability"],
            blocking_stage_ids=["stability", "cross_section"],
        ),
        VerificationStage(
            id="bracing",
            order=8,
            label="Bracing/restraint",
            p399_reference="§9",
            status=bracing_status,
            summary=(
                f"{len(member_restraint_candidate_checks)} active restraint candidate "
                f"check(s): {sum(check.identity_status == 'pass' for check in member_restraint_candidate_checks)} identity pass, "
                f"{sum(check.stiffness_status == 'verified' for check in member_restraint_candidate_checks)} stiffness verified, "
                f"{sum(check.anchorage_status == 'verified' for check in member_restraint_candidate_checks)} anchored."
                if member_restraint_candidate_checks
                else "No verified restraint or bracing load path is active."
            ),
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
    if analysis.cross_section_verification is not None:
        combinations_by_id = {
            combination.id: combination for combination in combinations
        }
        for (
            envelope_combination_id
        ) in analysis.cross_section_verification.combination_ids:
            envelope_combination = combinations_by_id.get(envelope_combination_id)
            if envelope_combination is None:
                raise StructuralAnalysisError(
                    "Cross-section verification references missing combination "
                    f"{envelope_combination_id!r}"
                )
            if envelope_combination.limit_state != "ultimate":
                raise StructuralAnalysisError(
                    "Cross-section verification requires ULS combinations; "
                    f"{envelope_combination_id!r} is "
                    f"{envelope_combination.limit_state!r}"
                )
    combination_selection: Literal[
        "requested", "default", "governing_working_envelope"
    ] = "requested" if combination_id is not None else "default"
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
            tension_only=declaration.tension_only,
            comp_only=declaration.compression_only,
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
    stability_directions: list[dict[str, Any]] = []
    if analysis.stability is not None:
        stability_directions = [
            direction.model_dump() for direction in analysis.stability.direction_cases
        ] or [
            {
                "id": "authored",
                "stability_combination_id": (
                    analysis.stability.stability_combination_id
                ),
                "imperfection_case_id": analysis.stability.imperfection_case_id,
                "nhf_combination_id": "",
                "horizontal_axis": "x",
            }
        ]
    first_order_stability: dict[tuple[str, str], tuple[float, float]] = {}
    first_order_nhf_eaves_displacement_mm: dict[str, float] = {}
    first_order_rafter_axial_kN: dict[str, float] = {}
    stability_result: StabilityResult | None = None
    try:
        if analysis.stability is None:
            model.analyze(check_statics=False, log=False)
        else:
            model.analyze_linear(check_statics=False, log=False)
            members_by_id = {
                declaration.id: declaration for declaration in analysis.members
            }
            for stability_direction in stability_directions:
                stability_combination_id = stability_direction[
                    "stability_combination_id"
                ]
                for declaration in analysis.members:
                    first_order_stability[
                        (stability_direction["id"], declaration.id)
                    ] = _member_extrema(
                        model,
                        declaration,
                        combination_id=stability_combination_id,
                        point_loads=[
                            load
                            for load in analysis.member_loads
                            if load.member_id == declaration.id
                        ],
                        distributed_loads=[
                            load
                            for load in analysis.member_distributed_loads
                            if load.member_id == declaration.id
                        ],
                    )
                first_order_rafter_axial_kN[stability_direction["id"]] = max(
                    (
                        _member_max_axial(
                            model,
                            members_by_id[member_id],
                            combination_id=stability_combination_id,
                        )
                        for member_id in analysis.stability.rafter_member_ids
                    ),
                    default=0.0,
                )
                nhf_combination_id = stability_direction["nhf_combination_id"]
                if nhf_combination_id and analysis.stability.eaves_member_ids:
                    axis_name = stability_direction["horizontal_axis"]
                    first_order_nhf_eaves_displacement_mm[stability_direction["id"]] = (
                        max(
                            abs(
                                getattr(
                                    _member_global_displacement(
                                        model,
                                        member_id=member_id,
                                        distance_m=_length(
                                            members_by_id[member_id].start,
                                            members_by_id[member_id].end,
                                        ),
                                        combination_id=nhf_combination_id,
                                    ),
                                    axis_name,
                                )
                            )
                            * 1000.0
                            for member_id in analysis.stability.eaves_member_ids
                        )
                    )
            model.analyze_PDelta(log=False)
    except Exception as exc:
        raise StructuralAnalysisError(
            f"PyNite could not solve the active design: {exc}"
        ) from exc

    if combination_id is None and any(
        basis.coefficient_selection_policy == "worst_available_credible"
        for basis in capture.wind_action_bases
    ):
        active_combination = _governing_working_combination(
            model,
            analysis,
            combinations,
        )
        combination_selection = "governing_working_envelope"

    if analysis.stability is not None:
        direction_results: list[StabilityDirectionResult] = []
        for stability_direction in stability_directions:
            member_comparisons: list[MemberStabilityComparison] = []
            for declaration in analysis.members:
                point_loads = [
                    load
                    for load in analysis.member_loads
                    if load.member_id == declaration.id
                ]
                distributed_loads = [
                    load
                    for load in analysis.member_distributed_loads
                    if load.member_id == declaration.id
                ]
                second_moment, second_displacement = _member_extrema(
                    model,
                    declaration,
                    combination_id=stability_direction["stability_combination_id"],
                    point_loads=point_loads,
                    distributed_loads=distributed_loads,
                )
                first_moment, first_displacement = first_order_stability[
                    (stability_direction["id"], declaration.id)
                ]
                member_comparisons.append(
                    MemberStabilityComparison(
                        member_id=declaration.id,
                        first_order_max_moment_kNm=first_moment,
                        second_order_max_moment_kNm=second_moment,
                        moment_amplification=_amplification(
                            second_moment, first_moment
                        ),
                        first_order_max_displacement_mm=first_displacement,
                        second_order_max_displacement_mm=second_displacement,
                        displacement_amplification=_amplification(
                            second_displacement, first_displacement
                        ),
                    )
                )
            nhf_displacement_mm = first_order_nhf_eaves_displacement_mm.get(
                stability_direction["id"], 0.0
            )
            alpha_cr = (
                analysis.stability.column_height_m
                * 1000.0
                / (200.0 * nhf_displacement_mm)
                if analysis.stability.column_height_m is not None
                and nhf_displacement_mm > 1e-12
                else None
            )
            governing_comparisons = _stability_scope_comparisons(
                member_comparisons,
                eaves_member_ids=analysis.stability.eaves_member_ids,
                rafter_member_ids=analysis.stability.rafter_member_ids,
            )
            direction_results.append(
                StabilityDirectionResult(
                    id=stability_direction["id"],
                    combination_id=stability_direction["stability_combination_id"],
                    imperfection_case_id=stability_direction["imperfection_case_id"],
                    nhf_combination_id=stability_direction["nhf_combination_id"],
                    horizontal_axis=(
                        "x" if stability_direction["horizontal_axis"] == "x" else "y"
                    ),
                    converged=True,
                    governing_moment_amplification=max(
                        comparison.moment_amplification
                        for comparison in governing_comparisons
                    ),
                    governing_displacement_amplification=max(
                        comparison.displacement_amplification
                        for comparison in governing_comparisons
                    ),
                    nhf_eaves_displacement_mm=nhf_displacement_mm,
                    alpha_cr=alpha_cr,
                    member_comparisons=member_comparisons,
                )
            )
        governing_direction = max(
            direction_results,
            key=lambda result: max(
                result.governing_moment_amplification,
                result.governing_displacement_amplification,
            ),
        )
        alpha_cr_values = [
            result.alpha_cr
            for result in direction_results
            if result.alpha_cr is not None
        ]
        rafter_design_axial_kN = (
            max(first_order_rafter_axial_kN.values())
            if analysis.stability.rafter_member_ids
            else None
        )
        rafter_elastic_critical_load_kN: float | None = None
        if analysis.stability.rafter_member_ids:
            declarations_by_id = {
                declaration.id: declaration for declaration in analysis.members
            }
            sections_by_id = {section.id: section for section in analysis.sections}
            materials_by_id = {material.id: material for material in analysis.materials}
            rafter_length_m = sum(
                _length(
                    declarations_by_id[member_id].start,
                    declarations_by_id[member_id].end,
                )
                for member_id in analysis.stability.rafter_member_ids
            )
            minimum_ei_kNm2 = min(
                materials_by_id[
                    declarations_by_id[member_id].material_id
                ].elastic_modulus_kN_m2
                * max(
                    sections_by_id[declarations_by_id[member_id].section_id].iy_m4,
                    sections_by_id[declarations_by_id[member_id].section_id].iz_m4,
                )
                for member_id in analysis.stability.rafter_member_ids
            )
            rafter_elastic_critical_load_kN = (
                pi**2 * minimum_ei_kNm2 / rafter_length_m**2
            )
        rafter_axial_limit_kN = (
            0.09 * rafter_elastic_critical_load_kN
            if rafter_elastic_critical_load_kN is not None
            else None
        )
        rafter_axial_force_significant = (
            rafter_design_axial_kN > rafter_axial_limit_kN
            if rafter_design_axial_kN is not None and rafter_axial_limit_kN is not None
            else None
        )
        stability_result = StabilityResult(
            method=analysis.stability.method,
            combination_id=governing_direction.combination_id,
            imperfection_case_id=governing_direction.imperfection_case_id,
            converged=all(result.converged for result in direction_results),
            amplification_warning_ratio=(
                analysis.stability.amplification_warning_ratio
            ),
            governing_moment_amplification=max(
                result.governing_moment_amplification for result in direction_results
            ),
            governing_displacement_amplification=max(
                result.governing_displacement_amplification
                for result in direction_results
            ),
            member_comparisons=governing_direction.member_comparisons,
            direction_results=direction_results,
            governing_direction_id=governing_direction.id,
            minimum_alpha_cr=(min(alpha_cr_values) if alpha_cr_values else None),
            second_order_required=(
                min(alpha_cr_values) < 10.0 if alpha_cr_values else None
            ),
            rafter_design_axial_kN=rafter_design_axial_kN,
            rafter_elastic_critical_load_kN=rafter_elastic_critical_load_kN,
            rafter_axial_limit_kN=rafter_axial_limit_kN,
            rafter_axial_force_significant=rafter_axial_force_significant,
            simplified_alpha_cr_applicable=(
                not rafter_axial_force_significant
                if rafter_axial_force_significant is not None
                else None
            ),
        )

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
    tension_member_checks = _tension_member_checks(model, analysis)
    cross_section_checks = _cross_section_checks(
        model,
        analysis,
        components,
        capture.connections,
    )
    cross_section_checks_by_member = {
        check.member_id: check for check in cross_section_checks
    }
    member_stability_checks = _member_stability_checks(model, analysis)
    member_restraint_candidate_checks = _member_restraint_candidate_checks(
        model,
        analysis,
        combination_id=active_combination.id,
    )
    member_restraint_traces = _member_restraint_traces(
        model,
        analysis,
        combination_id=active_combination.id,
    )
    member_stability_checks_by_member: dict[str, list[MemberStabilityCheck]] = {}
    for stability_check in member_stability_checks:
        member_stability_checks_by_member.setdefault(
            stability_check.member_id,
            [],
        ).append(stability_check)
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
                tension_only=declaration.tension_only,
                compression_only=declaration.compression_only,
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
            if declaration.tension_only:
                # Tension-only brace members are axial ties. PyNite retains tiny
                # frame stiffness for numerical stability, so suppress those
                # non-physical bending/shear results and report axial elongation.
                local_moment = (0.0, 0.0, 0.0)
                local_shear = (0.0, 0.0, 0.0)
                local_displacement = (
                    member.deflection("dx", distance, active_combination.id)
                    * 1000.0,
                    0.0,
                    0.0,
                )
            else:
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
                    member.deflection("dx", distance, active_combination.id)
                    * 1000.0,
                    member.deflection("dy", distance, active_combination.id)
                    * 1000.0,
                    member.deflection("dz", distance, active_combination.id)
                    * 1000.0,
                )
            global_moment = _local_to_global(rotation, local_moment)
            global_major_moment = _local_to_global(
                rotation,
                (0.0, 0.0, local_moment[2]),
            )
            global_minor_moment = _local_to_global(
                rotation,
                (0.0, local_moment[1], 0.0),
            )
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
                    major_moment_kNm=global_major_moment,
                    minor_moment_kNm=global_minor_moment,
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
        cross_section_check = cross_section_checks_by_member.get(declaration.id)
        stability_member_checks = member_stability_checks_by_member.get(
            declaration.id,
            [],
        )
        governing_stability_check = max(
            stability_member_checks,
            key=lambda check: (
                check.governing_utilisation
                if check.governing_utilisation is not None
                else check.axial_utilisation
                if check.axial_utilisation is not None
                else -1.0
            ),
            default=None,
        )
        if (
            governing_stability_check is not None
            and governing_stability_check.status in {"pass", "fail"}
        ):
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 7 member stability",
                    demand_kNm=governing_stability_check.major_moment_kNm or 0.0,
                    capacity_kNm=(
                        governing_stability_check.design_major_bending_capacity_kNm
                    ),
                    utilisation=(
                        governing_stability_check.governing_utilisation
                        if governing_stability_check.governing_utilisation is not None
                        else governing_stability_check.axial_utilisation
                    ),
                    status=(
                        "pass" if governing_stability_check.status == "pass" else "fail"
                    ),
                    basis=(
                        f"{governing_stability_check.basis} Governing ULS "
                        f"envelope: "
                        f"{governing_stability_check.governing_combination_id} at "
                        f"{governing_stability_check.governing_station_m:.3f} m. "
                        "Stage 8 bracing resistance and Stage 9 connections "
                        "remain separate."
                    ),
                )
            )
        elif governing_stability_check is not None:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 7 member stability",
                    demand_kNm=governing_stability_check.major_moment_kNm or 0.0,
                    capacity_kNm=None,
                    utilisation=None,
                    status="not_checked",
                    basis=(
                        f"{governing_stability_check.basis} "
                        + " ".join(governing_stability_check.assumptions)
                    ),
                )
            )
        elif cross_section_check is not None and cross_section_check.status in {
            "pass",
            "fail",
        }:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 6 cross-section",
                    demand_kNm=(cross_section_check.major_moment_kNm or 0.0),
                    capacity_kNm=(
                        cross_section_check.design_major_bending_capacity_kNm
                    ),
                    utilisation=cross_section_check.governing_utilisation,
                    status=("pass" if cross_section_check.status == "pass" else "fail"),
                    basis=(
                        f"CROSS-SECTION ONLY — {cross_section_check.basis} "
                        f"Governing ULS envelope: "
                        f"{cross_section_check.governing_combination_id} at "
                        f"{cross_section_check.governing_station_m:.3f} m. "
                        "Stage 7 member stability, restraints, and connections "
                        "remain incomplete."
                    ),
                )
            )
        elif cross_section_check is not None:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 6 cross-section",
                    demand_kNm=max_moment,
                    capacity_kNm=None,
                    utilisation=None,
                    status="not_checked",
                    basis=cross_section_check.basis,
                )
            )
        elif section.bending_reference_kNm is None:
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
        if analysis.stability is not None:
            position = _plus(
                position,
                _member_global_displacement(
                    model,
                    member_id=equilibrium_point_load.member_id,
                    distance_m=equilibrium_point_load.distance_m,
                    combination_id=active_combination.id,
                ),
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
        if analysis.stability is None:
            segment_start = _plus(
                declaration.start,
                _scaled(member_axis, equilibrium_line_load.start_distance_m),
            )
            resultant, line_first_moment = _distributed_resultant(equilibrium_line_load)
            _add(applied_force_sum, resultant, factor)
            _add(applied_moment_sum, _cross(segment_start, resultant), factor)
            _add(
                applied_moment_sum,
                _cross(member_axis, line_first_moment),
                factor,
            )
        else:
            loaded_span = (
                equilibrium_line_load.end_distance_m
                - equilibrium_line_load.start_distance_m
            )
            interval = loaded_span / PDELTA_EQUILIBRIUM_INTERVALS
            for interval_index in range(PDELTA_EQUILIBRIUM_INTERVALS):
                distance = (
                    equilibrium_line_load.start_distance_m
                    + (interval_index + 0.5) * interval
                )
                load_ratio = (
                    distance - equilibrium_line_load.start_distance_m
                ) / loaded_span
                force = Vector3(
                    **{
                        axis_name: (
                            getattr(
                                equilibrium_line_load.start_force_kN_m,
                                axis_name,
                            )
                            + (
                                getattr(
                                    equilibrium_line_load.end_force_kN_m,
                                    axis_name,
                                )
                                - getattr(
                                    equilibrium_line_load.start_force_kN_m,
                                    axis_name,
                                )
                            )
                            * load_ratio
                        )
                        * interval
                        for axis_name in ("x", "y", "z")
                    }
                )
                position = _plus(
                    _plus(
                        declaration.start,
                        _scaled(member_axis, distance),
                    ),
                    _member_global_displacement(
                        model,
                        member_id=equilibrium_line_load.member_id,
                        distance_m=distance,
                        combination_id=active_combination.id,
                    ),
                )
                _add(applied_force_sum, force, factor)
                _add(applied_moment_sum, _cross(position, force), factor)

    force_residual = tuple(
        applied_force_sum[index] + reaction_force_sum[index] for index in range(3)
    )
    moment_residual = tuple(
        applied_moment_sum[index] + reaction_moment_sum[index] for index in range(3)
    )
    residual = max(abs(value) for value in (*force_residual, *moment_residual))
    equilibrium_scale = max(
        (
            abs(value)
            for value in (
                *applied_force_sum,
                *applied_moment_sum,
                *reaction_force_sum,
                *reaction_moment_sum,
            )
        ),
        default=0.0,
    )
    equilibrium_tolerance = (
        RESIDUAL_TOLERANCE
        if analysis.stability is None
        else max(
            RESIDUAL_TOLERANCE,
            equilibrium_scale * PDELTA_RESIDUAL_RELATIVE_TOLERANCE,
        )
    )
    equilibrium_status: Literal["pass", "fail"] = (
        "pass" if residual <= equilibrium_tolerance else "fail"
    )
    summary = _load_summary(analysis, active_combination)
    checked_serviceability = [
        check for check in serviceability_checks if check.status != "not_checked"
    ]
    verification_stages, calculation_sheets = _p399_evidence(
        capture=capture,
        analysis=analysis,
        combination=active_combination,
        nodes=structural_nodes,
        members=structural_members,
        member_results=member_results,
        member_checks=member_checks,
        tension_member_checks=tension_member_checks,
        cross_section_checks=cross_section_checks,
        member_stability_checks=member_stability_checks,
        member_restraint_candidate_checks=member_restraint_candidate_checks,
        serviceability_checks=serviceability_checks,
        equilibrium_status=equilibrium_status,
        residual=residual,
        equilibrium_tolerance=equilibrium_tolerance,
        stability_result=stability_result,
    )
    cross_section_stage = next(
        stage for stage in verification_stages if stage.id == "cross_section"
    )
    if (
        analysis.cross_section_verification is not None
        and cross_section_stage.status not in {"pass", "fail"}
    ):
        member_checks = [
            check.model_copy(
                update={
                    "capacity_kNm": None,
                    "utilisation": None,
                    "status": "not_checked",
                    "basis": (
                        f"{check.basis} Renderer colour remains neutral because "
                        f"Stage 6 is {cross_section_stage.status}."
                    ),
                }
            )
            for check in member_checks
        ]

    return StructuralSnapshot(
        mode="design",
        title=capture.title,
        subtitle=(
            (
                "Multi-member iterative P-Delta analysis"
                if analysis.stability is not None
                else "Multi-member first-order elastic analysis"
            )
            + f" — {active_combination.label}"
        ),
        source=SnapshotSource(
            kind="design",
            label=capture.project_name,
            design_id=capture.project_name,
            design_hash=capture.design_hash,
        ),
        design_basis=capture.design_basis,
        wind_action_bases=capture.wind_action_bases,
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
        tension_member_checks=tension_member_checks,
        cross_section_checks=cross_section_checks,
        member_stability_checks=member_stability_checks,
        member_restraint_candidate_checks=member_restraint_candidate_checks,
        member_restraint_traces=member_restraint_traces,
        serviceability_checks=serviceability_checks,
        load_summary=summary,
        equilibrium=EquilibriumDiagnostic(
            force_residual_kN=_vector(force_residual),
            moment_residual_kNm=_vector(moment_residual),
            tolerance=equilibrium_tolerance,
            status=equilibrium_status,
        ),
        solver=SolverMetadata(
            name="PyNiteFEA",
            version=version("PyNiteFEA"),
            analysis=(
                (
                    "3D iterative P-Delta frame with captured first-order comparison; "
                    if analysis.stability is not None
                    else "3D first-order elastic frame; "
                )
                + "shared-coordinate nodes, point loads, and global distributed loads"
            ),
            combination_id=active_combination.id,
            combination_selection=combination_selection,
        ),
        stability=stability_result,
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
                id="stability",
                label="P-Delta",
                status="online" if stability_result is not None else "pending",
                detail=(
                    (
                        f"{stability_result.combination_id} converged with governing "
                        f"moment amplification "
                        f"{stability_result.governing_moment_amplification:.4f} and "
                        f"displacement amplification "
                        f"{stability_result.governing_displacement_amplification:.4f}."
                    )
                    if stability_result is not None
                    else "No authored imperfection case and stability combination."
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
                detail=(
                    f"Global residual is {residual:.3e} against tolerance "
                    f"{equilibrium_tolerance:.3e}."
                ),
            ),
            CapabilityState(
                id="checks",
                label="Cross-section",
                status=(
                    "online"
                    if cross_section_stage.status == "pass"
                    else "blocked"
                    if cross_section_stage.status == "fail"
                    or analysis.cross_section_verification is None
                    else "pending"
                ),
                detail=(
                    f"{len(cross_section_checks)} catalogue-backed members pass "
                    "the authored ULS section-only envelope; member stability "
                    "remains a separate stage."
                    if cross_section_stage.status == "pass"
                    else (
                        f"{sum(check.status == 'fail' for check in cross_section_checks)} "
                        "members fail the section-only ULS envelope."
                    )
                    if cross_section_stage.status == "fail"
                    else (
                        "A section-capacity pack is selected, but prerequisite "
                        f"evidence leaves Stage 6 {cross_section_stage.status}."
                    )
                    if analysis.cross_section_verification is not None
                    else "No versioned cross-section calculation pack is active."
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
            (
                "CROSS-SECTION EVIDENCE ONLY — NOT FOR CERTIFICATION OR ORDERING. "
                "MEMBER STABILITY, RESTRAINT, BRACING, AND CONNECTIONS REMAIN "
                "INCOMPLETE."
                if analysis.cross_section_verification is not None
                else "ELASTIC MEMBER DEMAND ONLY — NOT FOR DESIGN, CERTIFICATION, "
                "OR ORDERING."
            ),
            *dict.fromkeys(member.assumption for member in analysis.members),
            (
                "Non-steel permanent actions are included only where design.py "
                "authors a traceable distributed or point load."
            ),
            (
                "Stage 6 uses catalogue effective properties and a versioned "
                "AS/NZS 4600:2018 cross-section pack. Member/local/distortional/"
                "lateral-torsional buckling, restraint, connections, anchors, "
                "concrete, impact, and progressive collapse are not checked."
                if analysis.cross_section_verification is not None
                else "The displayed bending threshold is an effective-section "
                "yield reference only. AS/NZS 4600 member capacity, "
                "lateral-torsional buckling, restraint, connections, anchors, "
                "concrete, impact, and progressive collapse are not checked."
            ),
        ],
    )
