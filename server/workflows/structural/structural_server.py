from __future__ import annotations

import json
from math import atan2, degrees, dist, sqrt
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.structural.cantilever_fixture import cantilever_glb, cantilever_snapshot
from core.structural.contracts import (
    AnalyticalMemberDeclaration,
    CapabilityState,
    CrossSectionVerificationDefinition,
    DesignAnalysisDefinition,
    DesignComponent,
    DesignConnection,
    DesignLoadPath,
    DesignSurfaceLoad,
    LoadCase,
    LoadCombination,
    MemberDistributedLoad,
    MemberPointLoad,
    MemberStabilitySegmentDefinition,
    MemberStabilityVerificationDefinition,
    ProjectStructuralCapture,
    Restraints,
    SectionCatalogReference,
    SectionProperties,
    StructuralMaterial,
    StructuralSnapshot,
    StructuralWindActionBasis,
    Vector3,
)
from core.structural.project_configuration import (
    ConfiguredMemberDistributedLoad,
    StructuralConfigurationRevisionResponse,
    StructuralProjectConfiguration,
    fixed_restraints,
    pinned_restraints,
)
from core.repositories import ProjectRepository
from core.site_definition import (
    SITE_DEFINITION_FILENAME,
    SiteDefinition,
    apply_site_definition,
    parse_site_definition,
)
from core.structural.project_analysis import (
    StructuralAnalysisError,
    solve_project_structural,
)
from core.structural.site_wind import (
    REGION_SOURCE,
    REGION_VERIFY_AGAINST,
    SiteWindError,
    compute_site_wind,
    lookup_wind_region,
    wind_region_geojson,
)
from core.models import (
    Artifact,
    Project,
    StructuralConfigurationRevision,
    UserWorkspaceState,
)
from core.workbench_access import require_structural_workbench

app = FastAPI(
    title="Tertius Structural Design Workbench",
    dependencies=[Depends(require_structural_workbench)],
)


class WindSiteRequest(BaseModel):
    site_address: str
    latitude: float
    longitude: float
    region: str = ""
    terrain_category: str
    importance_level: str = "2"
    annual_probability_uls: str = "1/500"
    reference_height_m: float
    direction_multiplier: float = 1.0
    shielding_multiplier: float = 1.0
    topographic_multiplier: float = 1.0
    climate_change_multiplier: float | None = None


def get_active_project(db: Session, ctx: AuthContext) -> Project | None:
    state = db.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == ctx.user_id,
            UserWorkspaceState.tenant_id == ctx.tenant_id,
        )
    )
    if state is None or state.active_project_id is None:
        return None
    return db.scalar(
        select(Project).where(
            Project.tenant_id == ctx.tenant_id,
            Project.id == state.active_project_id,
        )
    )


def get_latest_structural_projection_artifact(
    db: Session,
    ctx: AuthContext,
    project: Project,
) -> Artifact | None:
    return db.scalar(
        select(Artifact)
        .where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind == "structural",
        )
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1)
    )


def get_latest_structural_configuration(
    db: Session,
    ctx: AuthContext,
    project: Project,
) -> StructuralConfigurationRevision | None:
    return db.scalar(
        select(StructuralConfigurationRevision)
        .where(
            StructuralConfigurationRevision.tenant_id == ctx.tenant_id,
            StructuralConfigurationRevision.project_id == project.id,
        )
        .order_by(StructuralConfigurationRevision.revision.desc())
        .limit(1)
    )


def _capture_from_structural_projection(
    projection: dict,
    *,
    project_name: str,
    configuration: StructuralProjectConfiguration | None = None,
    configuration_revision: int | None = None,
    configuration_digest: str | None = None,
    site: SiteDefinition | None = None,
) -> ProjectStructuralCapture:
    if projection.get("schema_version") != "tertius.structural.v1":
        raise ValueError("unsupported structural projection schema")
    design_digest = str(projection.get("compiled_design_digest") or "")
    if len(design_digest) != 64:
        raise ValueError("structural projection is missing its compiled-design digest")

    components = [
        DesignComponent(
            id=str(component["component_id"]),
            label=str(component.get("mark") or component["component_id"]),
            kind=component["kind"],
            visual_node_id=str(component["component_id"]),
            grounded=component["kind"] == "ground",
            part_number=(
                str(component["part_number"]) if component.get("part_number") else None
            ),
            role=(str(component["role"]) if component.get("role") else None),
        )
        for component in projection.get("components", [])
        if isinstance(component, dict)
        and component.get("component_id")
        and component.get("kind")
    ]
    component_ids = {component.id for component in components}
    connections = []
    for joint in projection.get("joints", []):
        if not isinstance(joint, dict):
            continue
        connected_ids = [
            str(port["component_id"])
            for port in joint.get("ports", [])
            if isinstance(port, dict) and port.get("component_id") in component_ids
        ]
        if len(connected_ids) < 2:
            continue
        connections.append(
            DesignConnection(
                id=str(joint["connection_id"]),
                label=str(joint["connection_id"]),
                from_component_id=connected_ids[0],
                to_component_id=connected_ids[1],
                connector_component_ids=[
                    str(component_id)
                    for component_id in joint.get("connector_component_ids", [])
                    if component_id in component_ids
                ],
                transfers=joint.get("transfers") or [],
                resistance=joint.get("resistance"),
            )
        )

    diagnostics = [
        str(diagnostic.get("message"))
        for diagnostic in projection.get("diagnostics", [])
        if isinstance(diagnostic, dict) and diagnostic.get("message")
    ]
    readiness = projection.get("readiness") or {}
    analysis = None
    wind_action_bases: list[StructuralWindActionBasis] = []
    surface_loads: list[DesignSurfaceLoad] = []
    effective_configuration = configuration
    derived_distributed_loads: list[ConfiguredMemberDistributedLoad] = []
    surface_sources: dict[str, str] = {}
    if configuration is not None:
        (
            effective_configuration,
            wind_action_bases,
            surface_loads,
            derived_distributed_loads,
            surface_sources,
            wind_warnings,
        ) = _portal_frame_wind_actions(
            projection,
            components=components,
            configuration=configuration,
            site=site,
        )
        diagnostics.extend(wind_warnings)
        if site is not None and configuration.portal_frame_wind_actions is None:
            overlaid = apply_site_definition(
                {
                    "design_basis": configuration.design_basis.model_dump(
                        mode="python"
                    ),
                    "wind_action_bases": [],
                    "loads": [],
                },
                site,
            )
            effective_configuration = configuration.model_copy(deep=True)
            effective_configuration.design_basis = type(
                configuration.design_basis
            ).model_validate(overlaid["design_basis"])
            wind_action_bases = [
                StructuralWindActionBasis.model_validate(value)
                for value in overlaid["wind_action_bases"]
            ]
    if configuration is not None and readiness.get("model_complete"):
        analysis, analysis_warnings = _analysis_from_projection(
            projection,
            components=components,
            configuration=effective_configuration or configuration,
            derived_distributed_loads=derived_distributed_loads,
            surface_sources=surface_sources,
        )
        diagnostics.extend(analysis_warnings)
    load_paths = _trace_generated_load_paths(components, connections, surface_loads)
    capabilities = [
        CapabilityState(
            id="mechanical-topology",
            label="Mechanical structural topology",
            status="online" if readiness.get("model_complete") else "blocked",
            detail=(
                "Members and joints were projected from physical components."
                if readiness.get("model_complete")
                else "Resolve the structural component and connection diagnostics."
            ),
        ),
        CapabilityState(
            id="derived-action-load-paths",
            label="Derived action load paths",
            status=(
                "online"
                if load_paths and all(path.status == "complete" for path in load_paths)
                else "blocked"
            ),
            detail=(
                f"{len(load_paths)} derived wind actions reach a grounded component."
                if load_paths and all(path.status == "complete" for path in load_paths)
                else "Wind actions have no complete path to ground."
                if surface_loads
                else "No derived wind actions are active."
            ),
        ),
        CapabilityState(
            id="project-analysis-context",
            label="Project analysis context",
            status="online" if analysis is not None else "blocked",
            detail=(
                f"Structural configuration revision {configuration_revision} is active."
                if analysis is not None
                else "Site, loads, standards, combinations, and approval policy must be "
                "configured in the Structural workbench before analysis."
            ),
        ),
    ]
    return ProjectStructuralCapture(
        project_name=project_name,
        design_hash=design_digest,
        analysis_configuration_revision=configuration_revision,
        analysis_configuration_digest=configuration_digest,
        title=project_name,
        authoring_mode="generated",
        design_basis=(
            effective_configuration.design_basis
            if effective_configuration is not None
            else None
        ),
        wind_action_bases=wind_action_bases,
        components=components,
        connections=connections,
        loads=surface_loads,
        load_paths=load_paths,
        analysis=analysis,
        capabilities=capabilities,
        warnings=diagnostics,
    )


def _vector(values: object, *, label: str) -> Vector3:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError(f"{label} requires three coordinates")
    return Vector3(x=float(values[0]), y=float(values[1]), z=float(values[2]))


def _member_length(member: dict) -> float:
    start = member.get("start_m")
    end = member.get("end_m")
    if not isinstance(start, list) or not isinstance(end, list):
        raise ValueError(f"analytical member {member.get('id')!r} has no endpoints")
    return float(dist(start, end))


def _trace_generated_load_paths(
    components: Sequence[DesignComponent],
    connections: Sequence[DesignConnection],
    loads: Sequence[DesignSurfaceLoad],
) -> list[DesignLoadPath]:
    components_by_id = {component.id: component for component in components}
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for connection in connections:
        adjacency[connection.from_component_id].append(
            (connection.to_component_id, connection.id)
        )
        adjacency[connection.to_component_id].append(
            (connection.from_component_id, connection.id)
        )
    paths: list[DesignLoadPath] = []
    for load in loads:
        queue: deque[tuple[str, list[str], list[str]]] = deque(
            [(load.component_id, [load.component_id], [])]
        )
        visited = {load.component_id}
        complete: DesignLoadPath | None = None
        while queue:
            component_id, component_path, connection_path = queue.popleft()
            component = components_by_id[component_id]
            if component.grounded:
                complete = DesignLoadPath(
                    load_id=load.id,
                    status="complete",
                    component_ids=component_path,
                    connection_ids=connection_path,
                    grounded_component_id=component_id,
                    detail=f"Derived action reaches {component.label}.",
                )
                break
            for next_id, connection_id in adjacency.get(component_id, []):
                if next_id in visited:
                    continue
                visited.add(next_id)
                queue.append(
                    (
                        next_id,
                        [*component_path, next_id],
                        [*connection_path, connection_id],
                    )
                )
        paths.append(
            complete
            or DesignLoadPath(
                load_id=load.id,
                status="blocked",
                component_ids=[load.component_id],
                connection_ids=[],
                detail="No compiled physical connection path reaches ground.",
            )
        )
    return paths


def _portal_frame_wind_actions(
    projection: dict,
    *,
    components: list[DesignComponent],
    configuration: StructuralProjectConfiguration,
    site: SiteDefinition | None,
) -> tuple[
    StructuralProjectConfiguration,
    list[StructuralWindActionBasis],
    list[DesignSurfaceLoad],
    list[ConfiguredMemberDistributedLoad],
    dict[str, str],
    list[str],
]:
    """Derive a transverse portal-frame strip model from roles and Site data."""

    configured = configuration.portal_frame_wind_actions
    if configured is None:
        return configuration, [], [], [], {}, []

    analysis_configuration = configuration.model_copy(deep=True)
    if site is None:
        analysis_configuration.design_basis.standards["wind_actions"] = (
            "Site workbench wind basis missing — unconfirmed for this project"
        )
        return (
            analysis_configuration,
            [],
            [],
            [],
            {},
            [
                f"{SITE_DEFINITION_FILENAME} is required by the configured portal-frame "
                "wind action model. Wind actions were not generated."
            ],
        )

    projected_members = [
        member
        for member in projection.get("analytical_members", [])
        if isinstance(member, dict) and member.get("component_id")
    ]
    members_by_component: dict[str, list[dict]] = defaultdict(list)
    for member in projected_members:
        members_by_component[str(member["component_id"])].append(member)
    for members in members_by_component.values():
        members.sort(
            key=lambda member: float(member.get("physical_start_distance_m") or 0.0)
        )

    role_by_component = {
        component.id: (component.role or "").strip().lower() for component in components
    }
    column_ids = {
        component_id
        for component_id, role in role_by_component.items()
        if role == configured.column_role.strip().lower()
    }
    rafter_ids = {
        component_id
        for component_id, role in role_by_component.items()
        if role == configured.rafter_role.strip().lower()
    }

    def physical_endpoints(component_id: str) -> tuple[Vector3, Vector3]:
        members = members_by_component.get(component_id, [])
        if not members:
            raise ValueError(
                f"portal wind role component {component_id!r} has no analytical axis"
            )
        return (
            _vector(members[0].get("start_m"), label="physical member start"),
            _vector(members[-1].get("end_m"), label="physical member end"),
        )

    frame_components: dict[float, dict[str, list[str]]] = defaultdict(
        lambda: {"columns": [], "rafters": []}
    )
    for component_id, key in (
        *((component_id, "columns") for component_id in column_ids),
        *((component_id, "rafters") for component_id in rafter_ids),
    ):
        start, end = physical_endpoints(component_id)
        frame_y = round((start.y + end.y) / 2.0, 6)
        if abs(start.y - end.y) > 1e-5:
            return (
                analysis_configuration,
                [],
                [],
                [],
                {},
                [
                    "Portal-frame wind actions were not generated because role "
                    f"component {component_id!r} is not in a constant-Y transverse plane."
                ],
            )
        frame_components[frame_y][key].append(component_id)

    invalid_frames = {
        frame_y: values
        for frame_y, values in frame_components.items()
        if len(values["columns"]) != 2 or len(values["rafters"]) != 2
    }
    if not frame_components or invalid_frames:
        analysis_configuration.design_basis.standards["wind_actions"] = (
            f"{site.project_basis.standards.wind} — portal frame roles incomplete"
        )
        return (
            analysis_configuration,
            [],
            [],
            [],
            {},
            [
                "Portal-frame wind actions require exactly two portal columns and "
                "two portal rafters in every transverse frame; no partial wind model "
                "was generated."
            ],
        )

    frame_positions = sorted(frame_components)
    if len(frame_positions) == 1:
        tributary_widths = {frame_positions[0]: site.structure.footprint_length_m}
    else:
        geometry_length = frame_positions[-1] - frame_positions[0]
        site_length = site.structure.footprint_length_m
        if abs(geometry_length - site_length) > max(0.1, site_length * 0.1):
            analysis_configuration.design_basis.standards["wind_actions"] = (
                f"{site.project_basis.standards.wind} — geometry/site footprint mismatch"
            )
            return (
                analysis_configuration,
                [],
                [],
                [],
                {},
                [
                    "Portal-frame wind actions were not generated because the compiled "
                    f"frame length ({geometry_length:.3f} m) does not match the Site "
                    f"footprint length ({site_length:.3f} m)."
                ],
            )
        tributary_widths = {
            frame_y: (
                (frame_positions[1] - frame_positions[0]) / 2.0
                if index == 0
                else (frame_positions[-1] - frame_positions[-2]) / 2.0
                if index == len(frame_positions) - 1
                else (frame_positions[index + 1] - frame_positions[index - 1]) / 2.0
            )
            for index, frame_y in enumerate(frame_positions)
        }
        end_strip_adjustment = (site_length - geometry_length) / 2.0
        tributary_widths[frame_positions[0]] += end_strip_adjustment
        tributary_widths[frame_positions[-1]] += end_strip_adjustment
        if any(width <= 0 for width in tributary_widths.values()):
            raise ValueError(
                "Site footprint produces a non-positive portal-frame tributary width"
            )

    dead_case = next(
        (case for case in analysis_configuration.load_cases if case.category == "dead"),
        None,
    )
    if dead_case is None:
        raise ValueError("portal-frame wind actions require a permanent action case")
    generated_cases = (
        LoadCase(id="wind-plus-x", label="Transverse wind +X", category="wind"),
        LoadCase(id="wind-minus-x", label="Transverse wind -X", category="wind"),
    )
    existing_cases = {case.id: case for case in analysis_configuration.load_cases}
    for generated_case in generated_cases:
        existing = existing_cases.get(generated_case.id)
        if existing is not None and existing.category != "wind":
            raise ValueError(
                f"generated wind case {generated_case.id!r} conflicts with a "
                f"configured {existing.category!r} case"
            )
        if existing is None:
            analysis_configuration.load_cases.append(generated_case)

    generated_combinations = (
        LoadCombination(
            id="SLS-G+WX+",
            label="Permanent plus transverse wind +X",
            limit_state="serviceability",
            factors={dead_case.id: 1.0, "wind-plus-x": 1.0},
        ),
        LoadCombination(
            id="SLS-G+WX-",
            label="Permanent plus transverse wind -X",
            limit_state="serviceability",
            factors={dead_case.id: 1.0, "wind-minus-x": 1.0},
        ),
        LoadCombination(
            id="ULS-1.2G+WX+",
            label="ULS permanent plus transverse wind +X",
            limit_state="ultimate",
            factors={dead_case.id: 1.2, "wind-plus-x": 1.0},
        ),
        LoadCombination(
            id="ULS-1.2G+WX-",
            label="ULS permanent plus transverse wind -X",
            limit_state="ultimate",
            factors={dead_case.id: 1.2, "wind-minus-x": 1.0},
        ),
    )
    combination_ids = {
        combination.id for combination in analysis_configuration.load_combinations
    }
    for generated_combination in generated_combinations:
        if generated_combination.id not in combination_ids:
            analysis_configuration.load_combinations.append(generated_combination)
    uls_wind_ids = ["ULS-1.2G+WX+", "ULS-1.2G+WX-"]
    for verification in (
        analysis_configuration.cross_section_verification,
        analysis_configuration.member_stability_verification,
    ):
        if verification is not None:
            verification.combination_ids = list(
                dict.fromkeys([*verification.combination_ids, *uls_wind_ids])
            )

    raw_loads: list[dict[str, object]] = []
    load_geometry: dict[str, tuple[str, float, Vector3]] = {}
    for frame_y in frame_positions:
        tributary_width = tributary_widths[frame_y]
        frame = frame_components[frame_y]
        columns = sorted(
            frame["columns"],
            key=lambda component_id: sum(
                value.x for value in physical_endpoints(component_id)
            ),
        )
        rafters = sorted(
            frame["rafters"],
            key=lambda component_id: sum(
                value.x for value in physical_endpoints(component_id)
            ),
        )
        for case_id, wind_sign in (("wind-plus-x", 1.0), ("wind-minus-x", -1.0)):
            for column_index, component_id in enumerate(columns):
                is_windward = (wind_sign > 0 and column_index == 0) or (
                    wind_sign < 0 and column_index == 1
                )
                coefficient = (
                    configured.windward_wall_coefficient
                    if is_windward
                    else configured.leeward_wall_coefficient
                )
                start, end = physical_endpoints(component_id)
                member_length = dist(
                    start.model_dump().values(), end.model_dump().values()
                )
                load_id = f"site:{case_id}:{component_id}:wall"
                direction = Vector3(x=wind_sign, y=0, z=0)
                raw_loads.append(
                    {
                        "id": load_id,
                        "label": f"{case_id} wall action on {component_id}",
                        "case": "wind",
                        "case_id": case_id,
                        "component_id": component_id,
                        "pressure_kPa": abs(coefficient),
                        "area_m2": member_length * tributary_width,
                        "direction": direction.model_dump(),
                        "provenance": configured.coefficient_basis,
                        "net_pressure_coefficient": coefficient,
                        "coefficient_status": configured.coefficient_status,
                    }
                )
                load_geometry[load_id] = (component_id, tributary_width, direction)
            for component_id in rafters:
                start, end = physical_endpoints(component_id)
                member_length = dist(
                    start.model_dump().values(), end.model_dump().values()
                )
                delta_x = end.x - start.x
                delta_z = end.z - start.z
                slope_length = sqrt(delta_x**2 + delta_z**2)
                outward = Vector3(
                    x=(
                        -abs(delta_z) / slope_length
                        if (start.x + end.x) < 0
                        else abs(delta_z) / slope_length
                    ),
                    y=0,
                    z=abs(delta_x) / slope_length,
                )
                load_id = f"site:{case_id}:{component_id}:roof"
                raw_loads.append(
                    {
                        "id": load_id,
                        "label": f"{case_id} roof suction on {component_id}",
                        "case": "wind",
                        "case_id": case_id,
                        "component_id": component_id,
                        "pressure_kPa": abs(configured.roof_suction_coefficient),
                        "area_m2": member_length * tributary_width,
                        "direction": outward.model_dump(),
                        "provenance": configured.coefficient_basis,
                        "net_pressure_coefficient": configured.roof_suction_coefficient,
                        "coefficient_status": configured.coefficient_status,
                    }
                )
                load_geometry[load_id] = (component_id, tributary_width, outward)

    overlaid = apply_site_definition(
        {
            "design_basis": analysis_configuration.design_basis.model_dump(
                mode="python"
            ),
            "wind_action_bases": [],
            "loads": raw_loads,
        },
        site,
    )
    analysis_configuration.design_basis = type(
        configuration.design_basis
    ).model_validate(overlaid["design_basis"])
    wind_bases = [
        StructuralWindActionBasis.model_validate(value)
        for value in overlaid["wind_action_bases"]
    ]
    surface_loads = [
        DesignSurfaceLoad.model_validate(value) for value in overlaid["loads"]
    ]
    surface_sources: dict[str, str] = {}
    distributed_configs: list[ConfiguredMemberDistributedLoad] = []
    for surface_load in surface_loads:
        component_id, tributary_width, direction = load_geometry[surface_load.id]
        force = Vector3(
            x=surface_load.pressure_kPa * tributary_width * direction.x,
            y=surface_load.pressure_kPa * tributary_width * direction.y,
            z=surface_load.pressure_kPa * tributary_width * direction.z,
        )
        distributed_id = f"distribution:{surface_load.id}"
        surface_sources[distributed_id] = surface_load.id
        distributed_configs.append(
            ConfiguredMemberDistributedLoad(
                id=distributed_id,
                label=f"{surface_load.label} line action",
                component_id=component_id,
                case_id=str(surface_load.case_id),
                start_force_kN_m=force,
                end_force_kN_m=force,
                provenance=(
                    surface_load.provenance
                    + "; tributary width derived from the Site footprint and "
                    "compiled portal-frame spacing."
                ),
            )
        )
    return (
        analysis_configuration,
        wind_bases,
        surface_loads,
        distributed_configs,
        surface_sources,
        [],
    )


def _cross3(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _unit3(values: tuple[float, float, float]) -> tuple[float, float, float]:
    length = sqrt(sum(value * value for value in values))
    if length <= 1e-12:
        raise ValueError("member frame requires a non-zero direction")
    return (values[0] / length, values[1] / length, values[2] / length)


def _pynite_section_rotation(
    start: Vector3,
    end: Vector3,
    section_x_direction: object,
) -> float:
    """Rotate PyNite local z onto the rendered profile's local x/major axis."""

    desired_raw = _vector(section_x_direction, label="section x direction")
    desired_z = _unit3((desired_raw.x, desired_raw.y, desired_raw.z))
    delta = (end.x - start.x, end.y - start.y, end.z - start.z)
    axis = _unit3(delta)
    tolerance = 1e-9
    if abs(delta[0]) <= tolerance and abs(delta[2]) <= tolerance:
        default_z = (0.0, 0.0, 1.0)
    elif abs(delta[1]) <= tolerance:
        default_z = _unit3(_cross3(axis, (0.0, 1.0, 0.0)))
    else:
        projection = (delta[0], 0.0, delta[2])
        default_z = _unit3(
            _cross3(projection, axis) if delta[1] > 0 else _cross3(axis, projection)
        )
    sine = sum(axis[index] * _cross3(default_z, desired_z)[index] for index in range(3))
    cosine = sum(default_z[index] * desired_z[index] for index in range(3))
    rotation = degrees(atan2(sine, cosine))
    return 0.0 if abs(rotation) <= 1e-9 else rotation


def _endpoint_joint_index(
    projection: dict,
) -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    for joint in projection.get("joints", []):
        if not isinstance(joint, dict):
            continue
        for port in joint.get("ports", []):
            if not isinstance(port, dict):
                continue
            component_id = str(port.get("component_id") or "")
            port_name = str(port.get("port") or "")
            if not component_id or not port_name:
                continue
            key = (component_id, port_name)
            if key in index:
                raise ValueError(
                    f"component port {component_id}.{port_name} belongs to more than "
                    "one physical connection"
                )
            index[key] = joint
    return index


def _endpoint_connection_effects(
    *,
    component_id: str,
    endpoint: str,
    component_kinds: Mapping[str, str],
    endpoint_joints: Mapping[tuple[str, str], dict],
    port_names: Sequence[str] | None = None,
    projected_node_key: str | None = None,
) -> tuple[Restraints, Restraints, list[str], str]:
    warnings: list[str] = []
    selected_port_names = tuple(port_names or (endpoint,))
    joints: list[dict] = []
    seen_joint_ids: set[str] = set()
    for port_name in selected_port_names:
        joint = endpoint_joints.get((component_id, port_name))
        if joint is None:
            continue
        joint_id = str(joint.get("connection_id") or joint.get("id") or "")
        if joint_id not in seen_joint_ids:
            joints.append(joint)
            seen_joint_ids.add(joint_id)
    node_key = projected_node_key or (
        f"joint:{'+'.join(sorted(seen_joint_ids))}"
        if joints
        else f"endpoint:{component_id}:{endpoint}"
    )
    restraints = Restraints()
    releases = Restraints()

    def merged(left: Restraints, right: Restraints) -> Restraints:
        return Restraints(
            **{
                field: bool(getattr(left, field) or getattr(right, field))
                for field in ("dx", "dy", "dz", "rx", "ry", "rz")
            }
        )

    for joint in joints:
        connection_id = str(joint.get("connection_id") or joint.get("id") or "")
        ports = [port for port in joint.get("ports", []) if isinstance(port, dict)]
        other_component_ids = {
            str(port.get("component_id"))
            for port in ports
            if str(port.get("component_id")) != component_id
        }
        other_kinds = {
            component_kinds[other_id]
            for other_id in other_component_ids
            if other_id in component_kinds
        }
        model = str(joint.get("analysis_model") or "")
        status = str(joint.get("stiffness_status") or "unverified")
        basis = str(joint.get("stiffness_basis") or "")
        if status != "verified":
            warnings.append(
                f"Connection {connection_id} uses its {model or 'declared'} analysis "
                f"model as a draft assumption ({status}): {basis}"
            )

        if "ground" in other_kinds:
            if model in {"rigid", "rigid_zone"}:
                restraints = merged(restraints, fixed_restraints())
                continue
            if model == "pinned":
                restraints = merged(restraints, pinned_restraints())
                continue
            raise ValueError(
                f"ground connection {connection_id!r} uses unsupported analysis model "
                f"{model!r}"
            )

        if "member" in other_kinds:
            if model in {"rigid", "rigid_zone"}:
                continue
            if model == "pinned":
                if "moment" in set(joint.get("transfers") or []):
                    raise ValueError(
                        f"pinned connection {connection_id!r} cannot declare moment transfer"
                    )
                # A connection at a fabricated/intermediate port does not hinge the
                # continuous host member. The connected member's primary end is
                # released instead.
                joint_component_port_names = {
                    str(port.get("port") or "")
                    for port in ports
                    if str(port.get("component_id") or "") == component_id
                }
                if joint_component_port_names.intersection({"start", "end"}):
                    releases = merged(
                        releases,
                        # Release the two bending rotations. Retaining the
                        # member-axis torsional DOF avoids the singular
                        # free-twist mode produced by a member pinned at both
                        # ends; explicit torsional connection stiffness can
                        # replace this draft idealisation when evidence exists.
                        Restraints(ry=True, rz=True),
                    )
                continue
            raise ValueError(
                f"member connection {connection_id!r} uses unsupported analysis model "
                f"{model!r}"
            )

        raise ValueError(
            f"connection {connection_id!r} does not join {component_id}.{endpoint} "
            "to a structural member or ground reference"
        )

    return restraints, releases, warnings, node_key


def _analysis_from_projection(
    projection: dict,
    *,
    components: list[DesignComponent],
    configuration: StructuralProjectConfiguration,
    derived_distributed_loads: Sequence[ConfiguredMemberDistributedLoad] = (),
    surface_sources: Mapping[str, str] | None = None,
) -> tuple[DesignAnalysisDefinition, list[str]]:
    product_facets = {
        str(facet.get("product_key")): facet
        for facet in projection.get("product_facets", [])
        if isinstance(facet, dict) and facet.get("product_key")
    }
    component_kinds = {component.id: component.kind for component in components}
    endpoint_joints = _endpoint_joint_index(projection)
    projected_members = [
        member
        for member in projection.get("analytical_members", [])
        if isinstance(member, dict) and member.get("component_id")
    ]
    members_by_component: dict[str, list[dict]] = defaultdict(list)
    for projected_member in projected_members:
        members_by_component[str(projected_member["component_id"])].append(
            projected_member
        )
    for component_members in members_by_component.values():
        component_members.sort(
            key=lambda member: float(member.get("physical_start_distance_m") or 0.0)
        )
    if not projected_members:
        raise ValueError("structural projection has no analytical members")

    materials: dict[str, StructuralMaterial] = {}
    sections: dict[str, SectionProperties] = {}
    declarations: list[AnalyticalMemberDeclaration] = []
    criteria_by_component = {
        criterion.component_id: criterion
        for criterion in configuration.member_criteria
        if criterion.component_id is not None
    }
    default_criterion = next(
        (
            criterion
            for criterion in configuration.member_criteria
            if criterion.component_id is None
        ),
        None,
    )
    warnings: list[str] = []
    has_ground_restraint = False
    component_labels = {component.id: component.label for component in components}
    component_spans = {
        component_id: max(
            float(member.get("physical_end_distance_m") or _member_length(member))
            for member in component_members
        )
        - min(
            float(member.get("physical_start_distance_m") or 0.0)
            for member in component_members
        )
        for component_id, component_members in members_by_component.items()
    }

    for projected_member in projected_members:
        component_id = str(projected_member["component_id"])
        product_key = str(projected_member.get("product_key") or "")
        facet = product_facets.get(product_key)
        if facet is None:
            raise ValueError(
                f"analytical member {projected_member.get('id')!r} references missing product facet"
            )
        section_data = facet.get("section") or projected_member.get("section") or {}
        material_data = facet.get("material") or projected_member.get("material") or {}
        catalogue_data = facet.get("catalogue") or {}
        catalogue_row = catalogue_data.get("row") or {}
        structural_properties = facet.get("properties") or {}
        section_id = f"section:{product_key}"
        material_id = f"material:{product_key}"
        sections.setdefault(
            section_id,
            SectionProperties(
                id=section_id,
                label=str(facet.get("label") or product_key),
                area_m2=float(section_data["area_m2"]),
                iy_m4=float(section_data["iy_m4"]),
                iz_m4=float(section_data["iz_m4"]),
                torsion_j_m4=float(section_data["torsion_j_m4"]),
                mass_kg_m=(
                    float(section_data["mass_kg_m"])
                    if section_data.get("mass_kg_m") is not None
                    else None
                ),
                bending_reference_kNm=(
                    float(section_data["effective_section_modulus_m3"])
                    * float(material_data["yield_strength_pa"])
                    / 1000.0
                    if section_data.get("effective_section_modulus_m3") is not None
                    and material_data.get("yield_strength_pa") is not None
                    else None
                ),
                bending_reference_axis=(
                    "local_z"
                    if section_data.get("effective_section_modulus_m3") is not None
                    and material_data.get("yield_strength_pa") is not None
                    else None
                ),
                bending_reference_basis=(
                    "Catalogue Zxe × fy major-axis yield reference; the selected "
                    "capacity pack applies its own design factor and interactions."
                    if section_data.get("effective_section_modulus_m3") is not None
                    and material_data.get("yield_strength_pa") is not None
                    else None
                ),
                catalog=(
                    SectionCatalogReference(
                        catalog_id=str(catalogue_data["id"]),
                        catalog_version=str(catalogue_data["revision"]),
                        section_key=str(catalogue_row["key"]),
                        source=str(
                            catalogue_row.get("source")
                            or structural_properties.get("catalogue_source")
                        ),
                        record_sha256=str(catalogue_data["row_digest"]),
                        axis_mapping={
                            str(key): str(value)
                            for key, value in (
                                structural_properties.get("axis_mapping") or {}
                            ).items()
                        },
                        properties=dict(catalogue_row),
                    )
                    if catalogue_data.get("id")
                    and catalogue_data.get("revision")
                    and catalogue_data.get("row_digest")
                    and catalogue_row.get("key")
                    else None
                ),
            ),
        )
        materials.setdefault(
            material_id,
            StructuralMaterial(
                id=material_id,
                label=str(material_data.get("label") or product_key),
                elastic_modulus_kN_m2=float(material_data["elastic_modulus_pa"])
                / 1000.0,
                shear_modulus_kN_m2=float(material_data["shear_modulus_pa"]) / 1000.0,
                poisson_ratio=float(material_data["poisson_ratio"]),
                density_kg_m3=float(material_data["density_kg_m3"]),
            ),
        )
        (
            start_restraints,
            start_releases,
            start_warnings,
            start_node_key,
        ) = _endpoint_connection_effects(
            component_id=component_id,
            endpoint="start",
            component_kinds=component_kinds,
            endpoint_joints=endpoint_joints,
            port_names=projected_member.get("start_port_names"),
            projected_node_key=projected_member.get("start_node_key"),
        )
        (
            end_restraints,
            end_releases,
            end_warnings,
            end_node_key,
        ) = _endpoint_connection_effects(
            component_id=component_id,
            endpoint="end",
            component_kinds=component_kinds,
            endpoint_joints=endpoint_joints,
            port_names=projected_member.get("end_port_names"),
            projected_node_key=projected_member.get("end_node_key"),
        )
        warnings.extend((*start_warnings, *end_warnings))
        has_ground_restraint = has_ground_restraint or any(
            (
                start_restraints.dx,
                start_restraints.dy,
                start_restraints.dz,
                end_restraints.dx,
                end_restraints.dy,
                end_restraints.dz,
            )
        )
        criterion = criteria_by_component.get(component_id, default_criterion)
        start_vector = _vector(
            projected_member.get("start_m"),
            label="member start",
        )
        end_vector = _vector(
            projected_member.get("end_m"),
            label="member end",
        )
        declarations.append(
            AnalyticalMemberDeclaration(
                id=str(projected_member["id"]),
                label=(
                    next(
                        (
                            component.label
                            for component in components
                            if component.id == component_id
                        ),
                        component_id,
                    )
                    + (
                        f" segment {projected_member['segment_index']}/"
                        f"{projected_member['segment_count']}"
                        if int(projected_member.get("segment_count") or 1) > 1
                        else ""
                    )
                ),
                component_id=component_id,
                start=start_vector,
                end=end_vector,
                start_node_key=start_node_key,
                end_node_key=end_node_key,
                start_restraints=start_restraints,
                end_restraints=end_restraints,
                start_releases=start_releases,
                end_releases=end_releases,
                section_id=section_id,
                material_id=material_id,
                rotation_deg=_pynite_section_rotation(
                    start_vector,
                    end_vector,
                    projected_member.get("section_x_direction"),
                ),
                deflection_limit_ratio=(
                    criterion.deflection_limit_ratio if criterion else None
                ),
                deflection_limit_mm=(
                    criterion.deflection_limit_mm if criterion else None
                ),
                deflection_limit_basis=(
                    criterion.deflection_limit_basis if criterion else None
                ),
                serviceability_group_id=component_id,
                serviceability_group_label=component_labels.get(
                    component_id, component_id
                ),
                serviceability_span_m=component_spans[component_id],
                assumption=(
                    "Axis, section, material, and physical restraints are projected "
                    "from the compiled mechanical graph. PyNite local z is rotated "
                    "onto the rendered profile x/major axis."
                ),
            )
        )

    if not has_ground_restraint:
        raise ValueError(
            "compiled mechanical topology has no member endpoint connected to ground"
        )

    point_loads: list[MemberPointLoad] = []
    distributed_loads: list[MemberDistributedLoad] = []
    for point_config in configuration.member_loads:
        point_component_members = members_by_component.get(
            point_config.component_id,
            [],
        )
        if not point_component_members:
            raise ValueError(
                f"configured load {point_config.id!r} references missing component "
                f"{point_config.component_id!r}"
            )
        physical_length = max(
            float(member.get("physical_end_distance_m") or _member_length(member))
            for member in point_component_members
        )
        if point_config.distance_m > physical_length + 1e-9:
            raise ValueError(
                f"configured load {point_config.id!r} lies beyond member length "
                f"{physical_length:g}m"
            )
        point_member = next(
            (
                member
                for member in point_component_members
                if float(member.get("physical_start_distance_m") or 0.0) - 1e-9
                <= point_config.distance_m
                <= float(
                    member.get("physical_end_distance_m") or _member_length(member)
                )
                + 1e-9
            ),
            point_component_members[-1],
        )
        point_member_start = float(point_member.get("physical_start_distance_m") or 0.0)
        point_member_end = float(
            point_member.get("physical_end_distance_m")
            or (point_member_start + _member_length(point_member))
        )
        station_span = point_member_end - point_member_start
        local_distance = (
            (point_config.distance_m - point_member_start)
            / station_span
            * _member_length(point_member)
        )
        point_loads.append(
            MemberPointLoad(
                id=point_config.id,
                label=point_config.label,
                member_id=str(point_member["id"]),
                case_id=point_config.case_id,
                distance_m=max(0.0, local_distance),
                force=point_config.force,
                moment=point_config.moment,
                source_load_id=None,
                provenance=point_config.provenance,
            )
        )
    surface_sources = surface_sources or {}
    for distributed_config in (
        *configuration.member_distributed_loads,
        *derived_distributed_loads,
    ):
        distributed_component_members = members_by_component.get(
            distributed_config.component_id,
            [],
        )
        if not distributed_component_members:
            raise ValueError(
                f"configured load {distributed_config.id!r} references missing component "
                f"{distributed_config.component_id!r}"
            )
        physical_length = max(
            float(member.get("physical_end_distance_m") or _member_length(member))
            for member in distributed_component_members
        )
        end_distance = distributed_config.end_distance_m or physical_length
        if (
            end_distance > physical_length + 1e-9
            or distributed_config.start_distance_m >= end_distance
        ):
            raise ValueError(
                f"configured load {distributed_config.id!r} has invalid member stations"
            )
        start_force = distributed_config.start_force_kN_m
        end_force = distributed_config.end_force_kN_m or start_force
        loaded_segments: list[tuple[dict, float, float]] = []
        for member in distributed_component_members:
            member_start = float(member.get("physical_start_distance_m") or 0.0)
            member_end = float(
                member.get("physical_end_distance_m") or _member_length(member)
            )
            overlap_start = max(distributed_config.start_distance_m, member_start)
            overlap_end = min(end_distance, member_end)
            if overlap_end > overlap_start + 1e-9:
                loaded_segments.append((member, overlap_start, overlap_end))

        def interpolated_force(station: float) -> Vector3:
            fraction = (station - distributed_config.start_distance_m) / (
                end_distance - distributed_config.start_distance_m
            )
            return Vector3(
                x=start_force.x + fraction * (end_force.x - start_force.x),
                y=start_force.y + fraction * (end_force.y - start_force.y),
                z=start_force.z + fraction * (end_force.z - start_force.z),
            )

        for load_index, (distributed_member, overlap_start, overlap_end) in enumerate(
            loaded_segments,
            start=1,
        ):
            member_start = float(
                distributed_member.get("physical_start_distance_m") or 0.0
            )
            member_end = float(
                distributed_member.get("physical_end_distance_m")
                or (member_start + _member_length(distributed_member))
            )
            analytical_length = _member_length(distributed_member)
            station_span = member_end - member_start

            def solver_distance(station: float) -> float:
                return (station - member_start) / station_span * analytical_length

            distributed_loads.append(
                MemberDistributedLoad(
                    id=(
                        distributed_config.id
                        if len(loaded_segments) == 1
                        else f"{distributed_config.id}:segment:{load_index:02d}"
                    ),
                    label=distributed_config.label,
                    member_id=str(distributed_member["id"]),
                    case_id=distributed_config.case_id,
                    start_distance_m=solver_distance(overlap_start),
                    end_distance_m=solver_distance(overlap_end),
                    start_force_kN_m=interpolated_force(overlap_start),
                    end_force_kN_m=interpolated_force(overlap_end),
                    source_kind=(
                        "surface"
                        if distributed_config.id in surface_sources
                        else "authored"
                    ),
                    source_load_id=surface_sources.get(distributed_config.id),
                    provenance=distributed_config.provenance,
                )
            )

    if configuration.include_self_weight:
        dead_cases = [
            case for case in configuration.load_cases if case.category == "dead"
        ]
        if not dead_cases:
            raise ValueError("self-weight requires a dead load case")
        dead_case = dead_cases[0]
        for declaration in declarations:
            section = sections[declaration.section_id]
            if section.mass_kg_m is None:
                raise ValueError(
                    f"member {declaration.id!r} has no mass per metre for self-weight"
                )
            load = -section.mass_kg_m * 9.80665 / 1000.0
            length = dist(
                declaration.start.model_dump().values(),
                declaration.end.model_dump().values(),
            )
            distributed_loads.append(
                MemberDistributedLoad(
                    id=f"self-weight:{declaration.id}",
                    label=f"{declaration.label} self-weight",
                    member_id=declaration.id,
                    case_id=dead_case.id,
                    start_distance_m=0,
                    end_distance_m=length,
                    start_force_kN_m=Vector3(x=0, y=0, z=load),
                    end_force_kN_m=Vector3(x=0, y=0, z=load),
                    source_kind="self_weight",
                    source_load_id=None,
                    provenance=(
                        "Derived from the product section mass and standard gravity."
                    ),
                )
            )

    declarations_by_component: dict[str, list[AnalyticalMemberDeclaration]] = (
        defaultdict(list)
    )
    for declaration in declarations:
        declarations_by_component[declaration.component_id].append(declaration)
    projected_by_id = {str(member["id"]): member for member in projected_members}
    cross_section_verification = None
    if configuration.cross_section_verification is not None:
        configured_cross_section = configuration.cross_section_verification
        selected_member_ids: list[str] = []
        selected_component_ids = configured_cross_section.component_ids
        if not selected_component_ids:
            selected_member_ids = [declaration.id for declaration in declarations]
        for component_id in selected_component_ids:
            selected_declarations = declarations_by_component.get(component_id)
            if not selected_declarations:
                raise ValueError(
                    "cross-section verification references missing component "
                    f"{component_id!r}"
                )
            selected_member_ids.extend(
                declaration.id for declaration in selected_declarations
            )
        cross_section_verification = CrossSectionVerificationDefinition(
            pack_id=configured_cross_section.pack_id,
            combination_ids=configured_cross_section.combination_ids,
            member_ids=selected_member_ids,
            off_axis_tolerance=configured_cross_section.off_axis_tolerance,
        )

    member_stability_verification = None
    if configuration.member_stability_verification is not None:
        configured_member_stability = configuration.member_stability_verification
        segments: list[MemberStabilitySegmentDefinition] = []
        configured_segments = list(configured_member_stability.segments)
        if not configured_segments:
            for declaration in declarations:
                member_length = dist(
                    declaration.start.model_dump().values(),
                    declaration.end.model_dump().values(),
                )
                segments.append(
                    MemberStabilitySegmentDefinition(
                        id=f"{declaration.id}:full-length",
                        member_id=declaration.id,
                        start_distance_m=0.0,
                        end_distance_m=member_length,
                        minor_axis_effective_length_factor=1.0,
                        torsional_effective_length_factor=1.0,
                        lateral_bending_restraint="unverified",
                        restraint_status="assumed",
                        restraint_basis=(
                            "Automatically selected from the compiled analytical segment; "
                            "no cladding, bridging, or connection restraint is credited."
                        ),
                        distortional_buckling_status="unverified",
                        distortional_buckling_basis=(
                            "No configuration-specific distortional-buckling evidence "
                            "has been attached to this compiled segment."
                        ),
                    )
                )
        for configured_segment in configured_segments:
            component_declarations = declarations_by_component.get(
                configured_segment.component_id,
                [],
            )
            if not component_declarations:
                raise ValueError(
                    "member-stability verification references missing component "
                    f"{configured_segment.component_id!r}"
                )
            physical_length = max(
                float(
                    projected_by_id[declaration.id].get("physical_end_distance_m")
                    or _member_length(projected_by_id[declaration.id])
                )
                for declaration in component_declarations
            )
            end_distance = configured_segment.end_distance_m or physical_length
            if end_distance > physical_length + 1e-9:
                raise ValueError(
                    f"member-stability segment {configured_segment.id!r} extends "
                    f"beyond {configured_segment.component_id!r}"
                )
            overlapping_declarations: list[
                tuple[AnalyticalMemberDeclaration, float, float, float]
            ] = []
            for declaration in component_declarations:
                projected_member = projected_by_id[declaration.id]
                member_start = float(
                    projected_member.get("physical_start_distance_m") or 0.0
                )
                member_end = float(
                    projected_member.get("physical_end_distance_m")
                    or (member_start + _member_length(projected_member))
                )
                overlap_start = max(configured_segment.start_distance_m, member_start)
                overlap_end = min(end_distance, member_end)
                if overlap_end > overlap_start + 1e-9:
                    overlapping_declarations.append(
                        (declaration, member_start, overlap_start, overlap_end)
                    )
            for segment_index, (
                segment_declaration,
                member_start,
                overlap_start,
                overlap_end,
            ) in enumerate(overlapping_declarations, start=1):
                segments.append(
                    MemberStabilitySegmentDefinition(
                        id=(
                            configured_segment.id
                            if len(overlapping_declarations) == 1
                            else f"{configured_segment.id}:segment:{segment_index:02d}"
                        ),
                        member_id=segment_declaration.id,
                        start_distance_m=overlap_start - member_start,
                        end_distance_m=overlap_end - member_start,
                        minor_axis_effective_length_factor=(
                            configured_segment.minor_axis_effective_length_factor
                        ),
                        torsional_effective_length_factor=(
                            configured_segment.torsional_effective_length_factor
                        ),
                        lateral_bending_restraint=(
                            configured_segment.lateral_bending_restraint
                        ),
                        restraint_status=configured_segment.restraint_status,
                        restraint_basis=configured_segment.restraint_basis,
                        distortional_buckling_status=(
                            configured_segment.distortional_buckling_status
                        ),
                        distortional_buckling_basis=(
                            configured_segment.distortional_buckling_basis
                        ),
                    )
                )
        member_stability_verification = MemberStabilityVerificationDefinition(
            pack_id=configured_member_stability.pack_id,
            combination_ids=configured_member_stability.combination_ids,
            segments=segments,
            off_axis_tolerance=configured_member_stability.off_axis_tolerance,
        )

    return (
        DesignAnalysisDefinition(
            materials=list(materials.values()),
            sections=list(sections.values()),
            members=declarations,
            load_cases=[
                LoadCase.model_validate(case) for case in configuration.load_cases
            ],
            member_loads=point_loads,
            member_distributed_loads=distributed_loads,
            load_combinations=[
                LoadCombination.model_validate(combination)
                for combination in configuration.load_combinations
            ],
            cross_section_verification=cross_section_verification,
            member_stability_verification=member_stability_verification,
        ),
        list(dict.fromkeys(warnings)),
    )


@app.get("/active/capture", response_model=ProjectStructuralCapture)
def get_active_capture(
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ProjectStructuralCapture:
    project = get_active_project(db, ctx)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project")
    artifact = get_latest_structural_projection_artifact(db, ctx, project)
    if artifact is None or artifact.content is None:
        raise HTTPException(
            status_code=404,
            detail="Compile the active project to create its structural projection.",
        )
    try:
        projection = json.loads(artifact.content)
        if not isinstance(projection, dict):
            raise ValueError("structural projection root must be an object")
        stored_configuration = get_latest_structural_configuration(db, ctx, project)
        configuration = (
            StructuralProjectConfiguration.model_validate(stored_configuration.content)
            if stored_configuration is not None
            else None
        )
        site = None
        if configuration is not None:
            site_source = ProjectRepository(db, ctx.tenant_id).get_code(
                project.name,
                SITE_DEFINITION_FILENAME,
            )
            if site_source is not None:
                site = parse_site_definition(site_source)
        return _capture_from_structural_projection(
            projection,
            project_name=project.name,
            configuration=configuration,
            configuration_revision=(
                stored_configuration.revision
                if stored_configuration is not None
                else None
            ),
            configuration_digest=(
                stored_configuration.digest
                if stored_configuration is not None
                else None
            ),
            site=site,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Compiled structural projection is invalid: {exc}",
        ) from exc


@app.get(
    "/active/configuration",
    response_model=StructuralConfigurationRevisionResponse,
)
def get_active_configuration(
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> StructuralConfigurationRevisionResponse:
    project = get_active_project(db, ctx)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project")
    stored = get_latest_structural_configuration(db, ctx, project)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail="The active project has no Structural workbench configuration.",
        )
    return StructuralConfigurationRevisionResponse(
        revision=stored.revision,
        digest=stored.digest,
        configuration=StructuralProjectConfiguration.model_validate(stored.content),
    )


@app.put(
    "/active/configuration",
    response_model=StructuralConfigurationRevisionResponse,
)
def put_active_configuration(
    configuration: StructuralProjectConfiguration,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> StructuralConfigurationRevisionResponse:
    project = get_active_project(db, ctx)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project")
    latest = get_latest_structural_configuration(db, ctx, project)
    digest = configuration.configuration_digest
    if latest is not None and latest.digest == digest:
        return StructuralConfigurationRevisionResponse(
            revision=latest.revision,
            digest=latest.digest,
            configuration=configuration,
        )
    stored = StructuralConfigurationRevision(
        tenant_id=ctx.tenant_id,
        project_id=project.id,
        revision=(latest.revision + 1 if latest is not None else 1),
        digest=digest,
        content=configuration.model_dump(mode="json"),
        created_by=ctx.user_id,
    )
    db.add(stored)
    db.commit()
    return StructuralConfigurationRevisionResponse(
        revision=stored.revision,
        digest=stored.digest,
        configuration=configuration,
    )


@app.get("/active/analysis", response_model=StructuralSnapshot)
def get_active_analysis(
    combination_id: str | None = Query(default=None),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> StructuralSnapshot:
    capture = get_active_capture(ctx=ctx, db=db)
    try:
        return solve_project_structural(
            capture,
            combination_id=combination_id,
        )
    except StructuralAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/wind/region")
def get_wind_region(
    latitude: float,
    longitude: float,
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        result = lookup_wind_region(
            latitude=latitude,
            longitude=longitude,
        )
    except SiteWindError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is not None:
        return result
    return {
        "region": None,
        "area": None,
        "approximate": True,
        "source": REGION_SOURCE,
        "verify_against": REGION_VERIFY_AGAINST,
        "detail": "Coordinates are outside the deployed Australian region overlay.",
    }


@app.get("/wind/regions.geojson")
def get_wind_regions_geojson(
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        return JSONResponse(content=wind_region_geojson())
    except SiteWindError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/wind/site")
def calculate_site_wind(
    request: WindSiteRequest,
    _ctx: AuthContext = Depends(get_auth_context),
):
    try:
        suggested = lookup_wind_region(
            latitude=request.latitude,
            longitude=request.longitude,
        )
        selected_region = request.region.strip().upper()
        if not selected_region:
            if suggested is None or not suggested.get("region"):
                raise SiteWindError(
                    "site coordinates do not resolve to a wind region; "
                    "select one manually"
                )
            selected_region = str(suggested["region"])
        calculation = compute_site_wind(
            region=selected_region,
            terrain_category=request.terrain_category,
            importance_level=request.importance_level,
            annual_probability_uls=request.annual_probability_uls,
            reference_height_m=request.reference_height_m,
            direction_multiplier=request.direction_multiplier,
            shielding_multiplier=request.shielding_multiplier,
            topographic_multiplier=request.topographic_multiplier,
            climate_change_multiplier=request.climate_change_multiplier,
        )
    except SiteWindError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    suggested_region = (
        str(suggested.get("region"))
        if suggested is not None and suggested.get("region")
        else None
    )
    return {
        "site_address": request.site_address,
        "latitude": request.latitude,
        "longitude": request.longitude,
        "region_area": (
            str(suggested.get("area") or "") if suggested is not None else ""
        ),
        "region_source": REGION_SOURCE,
        "region_approximate": True,
        "region_status": "suggested",
        "suggested_region": suggested_region,
        "selected_region": selected_region,
        "region_conflict": bool(
            suggested_region and suggested_region != selected_region
        ),
        "region_detail": (
            suggested.get("detail")
            if suggested is not None
            else "No overlay suggestion is available."
        ),
        **calculation,
    }


@app.get("/fixture/cantilever", response_model=StructuralSnapshot)
def get_cantilever_fixture(
    _ctx: AuthContext = Depends(get_auth_context),
) -> StructuralSnapshot:
    return cantilever_snapshot()


@app.get("/fixture/cantilever/model")
def get_cantilever_model(
    _ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    return Response(content=cantilever_glb(), media_type="model/gltf-binary")
