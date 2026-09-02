from __future__ import annotations

import json
from math import atan2, degrees, dist, sqrt
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from datetime import datetime
from threading import Lock
from time import perf_counter
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.structural.cantilever_fixture import cantilever_glb, cantilever_snapshot
from core.structural.action_standard_packs import (
    ActionRole,
    ImposedActionProfile,
    StructuralActionCase,
    resolve_action_standard_pack,
)
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
    MemberRestraintCandidateDefinition,
    MemberStabilitySegmentDefinition,
    MemberStabilityVerificationDefinition,
    ProjectStructuralCapture,
    RestraintConfigurationIdentity,
    Restraints,
    SectionCatalogReference,
    SectionProperties,
    StructuralMaterial,
    StructuralSnapshot,
    StructuralWindActionBasis,
    StabilityDefinition,
    StabilityDirectionDefinition,
    UnavailableLoadCombination,
    Vector3,
)
from core.structural.project_configuration import (
    ConfiguredMemberDistributedLoad,
    ConfiguredMemberPointLoad,
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
from core.structural.analysis_cache import (
    StructuralAnalysisCacheIdentity,
    acquire_structural_analysis_lock,
    analysis_cache_identity,
    get_cached_structural_analysis,
    store_structural_analysis,
)
from core.structural.restraint_evidence import match_restraint_evidence_pack
from core.structural.site_wind import (
    REGION_SOURCE,
    REGION_VERIFY_AGAINST,
    SiteWindError,
    compute_site_wind,
    lookup_wind_region,
    wind_region_geojson,
)
from core.structural.wind_surface_action_packs import (
    PACK_ID as WIND_SURFACE_ACTION_PACK_ID,
    internal_pressure_candidates,
    longitudinal_leeward_wall_external_coefficient,
    longitudinal_roof_external_coefficient,
    surface_coefficient_envelope,
    transverse_leeward_wall_external_coefficient,
    transverse_roof_external_coefficients,
)
from core.models import (
    Artifact,
    Project,
    StructuralAnalysisResult,
    StructuralConfigurationRevision,
    UserWorkspaceState,
)
from core.workbench_access import require_structural_workbench

app = FastAPI(
    title="Tertius Structural Design Workbench",
    dependencies=[Depends(require_structural_workbench)],
)


_ALL_OTHER_ROOFS_CONCENTRATED_ACTION_KN = 1.4
_STRUCTURAL_PROGRESS_LOCK = Lock()
_STRUCTURAL_PROGRESS: dict[tuple[str, str, str], dict[str, object]] = {}


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


class StructuralAnalysisCacheInfo(BaseModel):
    status: Literal["hit", "calculated"]
    key_digest: str
    engine_version: str
    calculated_at: datetime
    calculation_duration_seconds: float


class StructuralAnalysisProgress(BaseModel):
    state: Literal["idle", "running", "complete", "failed"]
    stage_id: str = "idle"
    stage_label: str = "Waiting for a structural calculation"
    completed_units: int | None = None
    total_units: int | None = None
    elapsed_seconds: float = 0.0
    engine_version: str | None = None
    key_digest: str | None = None


class ActiveStructuralWorkbenchResponse(BaseModel):
    capture: ProjectStructuralCapture
    analysis: StructuralSnapshot | None = None
    analysis_error: str | None = None
    cache: StructuralAnalysisCacheInfo | None = None


def _structural_progress_scope(
    ctx: AuthContext,
    project: Project,
    combination_id: str | None,
) -> tuple[str, str, str]:
    return (str(ctx.tenant_id), str(project.id), combination_id or "")


def _begin_structural_progress(
    scope: tuple[str, str, str],
    identity: StructuralAnalysisCacheIdentity,
) -> None:
    with _STRUCTURAL_PROGRESS_LOCK:
        if scope not in _STRUCTURAL_PROGRESS and len(_STRUCTURAL_PROGRESS) >= 128:
            _STRUCTURAL_PROGRESS.pop(next(iter(_STRUCTURAL_PROGRESS)))
        _STRUCTURAL_PROGRESS[scope] = {
            "state": "running",
            "stage_id": "preparing",
            "stage_label": "Preparing the structural calculation",
            "completed_units": None,
            "total_units": None,
            "started_monotonic": perf_counter(),
            "elapsed_seconds": 0.0,
            "engine_version": identity.engine_version,
            "key_digest": identity.key_digest[:12],
        }


def _update_structural_progress(
    scope: tuple[str, str, str],
    stage_id: str,
    stage_label: str,
    completed_units: int | None = None,
    total_units: int | None = None,
) -> None:
    with _STRUCTURAL_PROGRESS_LOCK:
        progress = _STRUCTURAL_PROGRESS.get(scope)
        if progress is None or progress.get("state") != "running":
            return
        progress.update(
            {
                "stage_id": stage_id,
                "stage_label": stage_label,
                "completed_units": completed_units,
                "total_units": total_units,
            }
        )


def _finish_structural_progress(
    scope: tuple[str, str, str],
    *,
    state: Literal["complete", "failed"],
    duration_seconds: float,
) -> None:
    with _STRUCTURAL_PROGRESS_LOCK:
        progress = _STRUCTURAL_PROGRESS.get(scope)
        if progress is None:
            return
        progress.update(
            {
                "state": state,
                "stage_id": state,
                "stage_label": (
                    "Structural calculation saved"
                    if state == "complete"
                    else "Structural calculation failed"
                ),
                "completed_units": None,
                "total_units": None,
                "elapsed_seconds": duration_seconds,
            }
        )


def _read_structural_progress(
    scope: tuple[str, str, str],
) -> StructuralAnalysisProgress:
    with _STRUCTURAL_PROGRESS_LOCK:
        stored = _STRUCTURAL_PROGRESS.get(scope)
        if stored is None:
            return StructuralAnalysisProgress(state="idle")
        progress = dict(stored)
    started_monotonic = progress.pop("started_monotonic", None)
    if progress["state"] == "running" and isinstance(started_monotonic, float):
        progress["elapsed_seconds"] = max(0.0, perf_counter() - started_monotonic)
    return StructuralAnalysisProgress.model_validate(progress)


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

    product_facets = {
        str(facet["product_key"]): facet
        for facet in projection.get("product_facets", [])
        if isinstance(facet, dict) and facet.get("product_key")
    }
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
            product_key=(
                str(component["product_key"]) if component.get("product_key") else None
            ),
            product_definition_digest=(
                str(component["product_definition_digest"])
                if component.get("product_definition_digest")
                else None
            ),
            structural_evidence_status=(
                product_facets.get(str(component.get("product_key") or ""), {}).get(
                    "evidence_status"
                )
            ),
            structural_evidence_basis=(
                str(evidence_basis)
                if (
                    evidence_basis := product_facets.get(
                        str(component.get("product_key") or ""), {}
                    ).get("evidence_basis")
                )
                else None
            ),
            structural_properties=dict(
                product_facets.get(str(component.get("product_key") or ""), {}).get(
                    "properties"
                )
                or {}
            ),
            fabrication=dict(component.get("fabrication") or {}),
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
                component_ports={
                    str(port["component_id"]): str(port.get("port") or "")
                    for port in joint.get("ports", [])
                    if isinstance(port, dict)
                    and port.get("component_id") in connected_ids
                },
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


_PRIMARY_RESTRAINT_ROLE_PAIRS: dict[str, frozenset[str]] = {
    "roof/ceiling purlin": frozenset(
        {
            "left roof-plane tension cross brace",
            "right roof-plane tension cross brace",
            "roof purlin solid bridging",
        }
    ),
    "portal rafter": frozenset(
        {
            "roof/ceiling purlin",
            "left roof-plane tension cross brace",
            "right roof-plane tension cross brace",
        }
    ),
    "portal column": frozenset(
        {
            "wall top track",
            "wall bottom track",
            "long-wall tension cross brace",
        }
    ),
    # A solid bridge is itself a flexural member.  Its two bolted web joints
    # must therefore be available as end-restraint candidates, not only as
    # intermediate restraints for the roof purlins that it crosses.
    "roof purlin solid bridging": frozenset({"roof/ceiling purlin"}),
}


def _node_key_connection_ids(node_key: str | None) -> set[str]:
    if not node_key or not node_key.startswith("joint:"):
        return set()
    return {
        connection_id
        for connection_id in node_key.removeprefix("joint:").split("+")
        if connection_id
    }


def _compiled_anchorage_path(
    *,
    start_component_id: str,
    excluded_connection_id: str,
    components_by_id: Mapping[str, DesignComponent],
    joints: Sequence[object],
) -> tuple[list[str], list[str], str | None]:
    """Trace physical topology without treating it as resistance evidence."""

    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for raw_joint in joints:
        if not isinstance(raw_joint, dict):
            continue
        connection_id = str(raw_joint.get("connection_id") or "")
        if not connection_id or connection_id == excluded_connection_id:
            continue
        connected_ids = list(
            dict.fromkeys(
                str(port["component_id"])
                for port in raw_joint.get("ports", [])
                if isinstance(port, dict)
                and port.get("component_id") in components_by_id
            )
        )
        for index, component_id in enumerate(connected_ids):
            for other_id in connected_ids[index + 1 :]:
                adjacency[component_id].append((other_id, connection_id))
                adjacency[other_id].append((component_id, connection_id))

    queue: deque[tuple[str, list[str], list[str]]] = deque(
        [(start_component_id, [start_component_id], [])]
    )
    visited = {start_component_id}
    while queue:
        component_id, component_path, connection_path = queue.popleft()
        component = components_by_id[component_id]
        if component.grounded:
            return component_path, connection_path, component_id
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
    return [start_component_id], [], None


def _derive_member_restraint_candidates(
    projection: Mapping[str, object],
    *,
    components: Sequence[DesignComponent],
    declarations: Sequence[AnalyticalMemberDeclaration],
) -> list[MemberRestraintCandidateDefinition]:
    """Locate restraint candidates from compiled parts and physical joints.

    This deliberately derives only topology and identity. A rendered connection is
    not, by itself, evidence that it has sufficient stiffness, twist restraint,
    anchorage, or resistance, so no candidate produced here is credited as verified.
    """

    components_by_id = {component.id: component for component in components}
    declarations_by_component: dict[str, list[AnalyticalMemberDeclaration]] = (
        defaultdict(list)
    )
    for declaration in declarations:
        declarations_by_component[declaration.component_id].append(declaration)
    joints = projection.get("joints")
    if not isinstance(joints, list):
        return []

    candidates: list[MemberRestraintCandidateDefinition] = []
    candidate_ids: set[str] = set()
    for raw_joint in joints:
        if not isinstance(raw_joint, dict):
            continue
        connection_id = str(raw_joint.get("connection_id") or "")
        ports = [
            port
            for port in raw_joint.get("ports", [])
            if isinstance(port, dict) and port.get("component_id") in components_by_id
        ]
        if not connection_id or len(ports) < 2:
            continue

        for primary_port in ports:
            primary_component_id = str(primary_port["component_id"])
            primary_component = components_by_id[primary_component_id]
            primary_role = (primary_component.role or "").strip().lower()
            bracing_roles = _PRIMARY_RESTRAINT_ROLE_PAIRS.get(primary_role)
            if not bracing_roles:
                continue
            for bracing_port in ports:
                bracing_component_id = str(bracing_port["component_id"])
                if bracing_component_id == primary_component_id:
                    continue
                bracing_component = components_by_id[bracing_component_id]
                bracing_role = (bracing_component.role or "").strip().lower()
                if bracing_role not in bracing_roles:
                    continue

                brace_endpoints: list[Vector3] = []
                for brace_declaration in declarations_by_component.get(
                    bracing_component_id, []
                ):
                    if connection_id in _node_key_connection_ids(
                        brace_declaration.start_node_key
                    ):
                        brace_endpoints.append(brace_declaration.start)
                    if connection_id in _node_key_connection_ids(
                        brace_declaration.end_node_key
                    ):
                        brace_endpoints.append(brace_declaration.end)
                if not brace_endpoints:
                    continue

                anchorage_components, anchorage_connections, grounded_id = (
                    _compiled_anchorage_path(
                        start_component_id=bracing_component_id,
                        excluded_connection_id=connection_id,
                        components_by_id=components_by_id,
                        joints=joints,
                    )
                )
                connector_component_ids = [
                    str(component_id)
                    for component_id in raw_joint.get("connector_component_ids", [])
                    if component_id in components_by_id
                ]
                connector_part_numbers = sorted(
                    part_number
                    for component_id in connector_component_ids
                    if (part_number := components_by_id[component_id].part_number)
                )
                configuration = RestraintConfigurationIdentity(
                    primary_part_number=primary_component.part_number,
                    bracing_part_number=bracing_component.part_number,
                    connector_part_numbers=connector_part_numbers,
                )
                evidence = match_restraint_evidence_pack(configuration)
                for declaration in declarations_by_component.get(
                    primary_component_id, []
                ):
                    endpoint_specs: list[tuple[float, Vector3]] = []
                    declaration_length = dist(
                        declaration.start.model_dump().values(),
                        declaration.end.model_dump().values(),
                    )
                    if connection_id in _node_key_connection_ids(
                        declaration.start_node_key
                    ):
                        endpoint_specs.append((0.0, declaration.start))
                    if connection_id in _node_key_connection_ids(
                        declaration.end_node_key
                    ):
                        endpoint_specs.append((declaration_length, declaration.end))
                    for distance_m, member_position in endpoint_specs:
                        brace_position = min(
                            brace_endpoints,
                            key=lambda position: dist(
                                member_position.model_dump().values(),
                                position.model_dump().values(),
                            ),
                        )
                        axis_separation_m = dist(
                            member_position.model_dump().values(),
                            brace_position.model_dump().values(),
                        )
                        candidate_id = (
                            f"derived-restraint:{connection_id}:{declaration.id}"
                        )
                        if candidate_id in candidate_ids:
                            continue
                        candidate_ids.add(candidate_id)
                        candidates.append(
                            MemberRestraintCandidateDefinition(
                                id=candidate_id,
                                member_id=declaration.id,
                                bracing_component_id=bracing_component_id,
                                connection_id=connection_id,
                                connector_component_ids=connector_component_ids,
                                member_position=member_position,
                                brace_position=brace_position,
                                distance_m=distance_m,
                                axis_separation_m=axis_separation_m,
                                restrains_lateral_translation=(
                                    evidence.restrains_lateral_translation
                                    if evidence is not None
                                    else True
                                ),
                                restrains_twist=(
                                    evidence.restrains_twist
                                    if evidence is not None
                                    else False
                                ),
                                restrained_flange=(
                                    evidence.restrained_flange
                                    if evidence is not None
                                    else "auto"
                                ),
                                demand_model=(
                                    evidence.demand_model
                                    if evidence is not None
                                    else "not_defined"
                                ),
                                stiffness_status=(
                                    evidence.stiffness_status
                                    if evidence is not None
                                    else "unverified"
                                ),
                                evidence_status=(
                                    "verified"
                                    if evidence is not None
                                    and evidence.identity_status == "pass"
                                    and evidence.design_force_capacity_kN is not None
                                    and evidence.design_moment_capacity_kNm is not None
                                    and evidence.stiffness_status == "verified"
                                    else "candidate"
                                ),
                                evidence_basis=(
                                    (
                                        f"Exact rendered products match evidence pack "
                                        f"{evidence.pack_id} v{evidence.pack_version}. "
                                        f"{evidence.capacity_basis}"
                                    )
                                    if evidence is not None
                                    else (
                                        "Tertius derived this possible lateral-restraint "
                                        "location from the compiled physical joint between "
                                        f"{primary_component_id} ({primary_role}) and "
                                        f"{bracing_component_id} ({bracing_role}). Twist "
                                        "restraint and effective-flange engagement are not "
                                        "credited."
                                    )
                                ),
                                capacity_basis=(
                                    evidence.capacity_basis
                                    if evidence is not None
                                    else (
                                        "No verified restraint force, moment, stiffness, or "
                                        "connection resistance is attached to this rendered "
                                        "configuration."
                                    )
                                ),
                                provenance=(
                                    "Compiled Build123d component axes, registered "
                                    "physical joint, connector identities, and Tertius "
                                    "topology traversal."
                                ),
                                evidence_pack_id=(
                                    evidence.pack_id if evidence is not None else None
                                ),
                                configuration=configuration,
                                anchorage_status="unverified",
                                anchorage_component_ids=anchorage_components,
                                anchorage_connection_ids=anchorage_connections,
                                anchorage_grounded_component_id=grounded_id,
                                anchorage_basis=(
                                    "Compiled topology reaches grounded component "
                                    f"{grounded_id}; connection resistance and "
                                    "longitudinal anchorage remain unverified."
                                    if grounded_id is not None
                                    else "No alternate compiled topology path from this "
                                    "bracing component reaches a grounded component."
                                ),
                            )
                        )
    candidates.sort(key=lambda candidate: candidate.id)
    return candidates


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
    """Derive portal-frame wind and roof-imposed actions from roles and Site data."""

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
    roof_imposed_receiver_ids = {
        component_id
        for component_id, role in role_by_component.items()
        if role == configured.roof_imposed_receiver_role.strip().lower()
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
    if configured.surface_action_pack_id != WIND_SURFACE_ACTION_PACK_ID:
        raise ValueError(
            "unsupported portal-frame wind surface action pack "
            f"{configured.surface_action_pack_id!r}"
        )

    column_x_positions: list[float] = []
    eave_elevations: list[float] = []
    ridge_elevations: list[float] = []
    for frame in frame_components.values():
        for component_id in frame["columns"]:
            start, end = physical_endpoints(component_id)
            column_x_positions.append((start.x + end.x) / 2.0)
            eave_elevations.append(max(start.z, end.z))
        for component_id in frame["rafters"]:
            start, end = physical_endpoints(component_id)
            ridge_elevations.append(max(start.z, end.z))
    geometry_width = max(column_x_positions) - min(column_x_positions)
    if len(frame_positions) < 2:
        return (
            analysis_configuration,
            [],
            [],
            [],
            {},
            [
                "Portal-frame wind actions require at least two compiled transverse "
                "frames so the mechanical envelope length and tributary strips can "
                "be derived without using the Site placement footprint."
            ],
        )
    geometry_length = frame_positions[-1] - frame_positions[0]
    eave_height = sum(eave_elevations) / len(eave_elevations)
    ridge_height = max(ridge_elevations)
    roof_rise = ridge_height - eave_height
    if roof_rise <= 0 or geometry_width <= 0:
        raise ValueError("portal-frame wind actions require a pitched gable envelope")
    average_roof_height = (eave_height + ridge_height) / 2.0
    roof_pitch_degrees = degrees(atan2(roof_rise, geometry_width / 2.0))
    transverse_leeward_cpe = transverse_leeward_wall_external_coefficient(
        roof_pitch_degrees
    )
    transverse_upwind_roof_cpe, transverse_downwind_roof_cpe = (
        transverse_roof_external_coefficients(
            roof_pitch_degrees=roof_pitch_degrees,
            average_roof_height_m=average_roof_height,
            building_depth_m=geometry_width,
        )
    )
    longitudinal_leeward_cpe = longitudinal_leeward_wall_external_coefficient(
        building_depth_m=geometry_length,
        average_roof_height_m=average_roof_height,
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
    if any(width <= 0 for width in tributary_widths.values()):
        raise ValueError("compiled portal frames produce a non-positive tributary width")

    potential_opening_roles = {
        "door jamb",
        "door header",
        "window header",
        "window sill",
        "window left jamb",
        "window right jamb",
    }
    potential_opening_frame_positions = {
        round(
            sum(value.y for value in physical_endpoints(component_id)) / 2.0,
            6,
        )
        for component_id, role in role_by_component.items()
        if role in potential_opening_roles and component_id in members_by_component
    }
    envelope = site.wind.action_envelope
    if envelope.enclosure != "enclosed":
        return (
            analysis_configuration,
            [],
            [],
            [],
            {},
            [
                "The selected Tertius wind surface action pack supports enclosed "
                "rectangular gable buildings only; no open-sided wind actions were "
                "generated."
            ],
        )
    if (
        envelope.coefficient_selection_policy == "verified_only"
        and envelope.opening_capacity_status != "verified"
    ):
        return (
            analysis_configuration,
            [],
            [],
            [],
            {},
            [
                "The Site policy requires verified-only wind coefficients, but the "
                "potential opening capacity is unverified. Verify the door/window "
                "envelope or select the worst-available-credible policy."
            ],
        )
    opening_capacity_verified = envelope.opening_capacity_status == "verified"
    openings_normally_open = envelope.openings_operating_state == "normally_open"

    def relative_opening_surfaces(
        *, wind_axis: Literal["transverse", "longitudinal"], wind_sign: float
    ) -> tuple[Literal["windward", "leeward", "side", "roof"], ...]:
        if opening_capacity_verified and not openings_normally_open:
            return ()
        if not potential_opening_frame_positions:
            # The Site declaration says potential openings cannot be credited as
            # pressure-resistant, but the compiled projection has no opening
            # framing. Bound every opening location instead of silently assuming
            # a sealed envelope.
            return ("windward", "leeward", "side", "roof")
        if wind_axis == "transverse":
            return ("side",)
        windward_frame = frame_positions[0] if wind_sign > 0 else frame_positions[-1]
        leeward_frame = frame_positions[-1] if wind_sign > 0 else frame_positions[0]
        surfaces: list[Literal["windward", "leeward", "side", "roof"]] = []
        if windward_frame in potential_opening_frame_positions:
            surfaces.append("windward")
        if leeward_frame in potential_opening_frame_positions:
            surfaces.append("leeward")
        return tuple(surfaces)

    def load_direction_from_surface_normal(
        outward: Vector3, net_coefficient: float
    ) -> Vector3:
        scale = -1.0 if net_coefficient > 0 else 1.0
        return Vector3(
            x=outward.x * scale,
            y=outward.y * scale,
            z=outward.z * scale,
        )

    dead_case = next(
        (
            case
            for case in analysis_configuration.action_cases
            if case.role == "permanent"
        ),
        None,
    )
    if dead_case is None:
        raise ValueError("portal-frame wind actions require a permanent action case")

    def ensure_action_case(
        *,
        case_id: str,
        label: str,
        role: ActionRole,
        imposed_profile: ImposedActionProfile | None = None,
    ) -> StructuralActionCase:
        existing_by_id = next(
            (
                case
                for case in analysis_configuration.action_cases
                if case.id == case_id
            ),
            None,
        )
        if existing_by_id is not None:
            if (
                existing_by_id.role != role
                or existing_by_id.imposed_profile != imposed_profile
            ):
                raise ValueError(
                    f"generated action case {case_id!r} conflicts with configured "
                    f"{existing_by_id.role!r} action"
                )
            return existing_by_id
        if role != "imposed":
            existing_by_role = next(
                (
                    case
                    for case in analysis_configuration.action_cases
                    if case.role == role
                ),
                None,
            )
            if existing_by_role is not None:
                raise ValueError(
                    f"Tertius-derived {role!r} action requires stable case ID "
                    f"{case_id!r}, but the configuration uses "
                    f"{existing_by_role.id!r}"
                )
        elif imposed_profile == "all_other_roofs_distributed":
            existing_distributed = next(
                (
                    case
                    for case in analysis_configuration.action_cases
                    if case.imposed_profile == imposed_profile
                ),
                None,
            )
            if existing_distributed is not None:
                raise ValueError(
                    "Tertius-derived distributed roof action requires stable case ID "
                    f"{case_id!r}, but the configuration uses "
                    f"{existing_distributed.id!r}"
                )
        generated = StructuralActionCase(
            id=case_id,
            label=label,
            role=role,
            imposed_profile=imposed_profile,
        )
        analysis_configuration.action_cases.append(generated)
        return generated

    imposed_case = ensure_action_case(
        case_id="roof-imposed",
        label="R2 roof imposed action",
        role="imposed",
        imposed_profile="all_other_roofs_distributed",
    )
    event_specs: tuple[
        tuple[str, str, str, ActionRole, ActionRole, ActionRole, ActionRole], ...
    ] = (
        (
            "serviceability",
            "sls",
            "Serviceability",
            "wind_serviceability_positive_x",
            "wind_serviceability_negative_x",
            "wind_serviceability_positive_y",
            "wind_serviceability_negative_y",
        ),
        (
            "ultimate",
            "uls",
            "Ultimate",
            "wind_ultimate_positive_x",
            "wind_ultimate_negative_x",
            "wind_ultimate_positive_y",
            "wind_ultimate_negative_y",
        ),
    )
    wind_cases = {
        event: {
            "positive_x": ensure_action_case(
                case_id=f"wind-{case_prefix}-plus-x",
                label=f"{event_label} transverse wind +X",
                role=positive_x_role,
            ),
            "negative_x": ensure_action_case(
                case_id=f"wind-{case_prefix}-minus-x",
                label=f"{event_label} transverse wind -X",
                role=negative_x_role,
            ),
            "positive_y": ensure_action_case(
                case_id=f"wind-{case_prefix}-plus-y",
                label=f"{event_label} longitudinal wind +Y",
                role=positive_y_role,
            ),
            "negative_y": ensure_action_case(
                case_id=f"wind-{case_prefix}-minus-y",
                label=f"{event_label} longitudinal wind -Y",
                role=negative_y_role,
            ),
        }
        for (
            event,
            case_prefix,
            event_label,
            positive_x_role,
            negative_x_role,
            positive_y_role,
            negative_y_role,
        ) in event_specs
    }

    raw_loads: list[dict[str, object]] = []
    load_geometry: dict[str, tuple[str, float, Vector3]] = {}

    def add_surface_action(
        *,
        load_id: str,
        label: str,
        case: str,
        case_id: str,
        component_id: str,
        pressure_kPa: float,
        area_m2: float,
        line_tributary_width_m: float,
        direction: Vector3,
        provenance: str,
        net_pressure_coefficient: float | None = None,
        coefficient_status: str | None = None,
        surface_action_pack_id: str | None = None,
        external_pressure_coefficient: float | None = None,
        internal_pressure_coefficient: float | None = None,
        area_reduction_factor: float | None = None,
    ) -> None:
        load: dict[str, object] = {
            "id": load_id,
            "label": label,
            "case": case,
            "case_id": case_id,
            "component_id": component_id,
            "pressure_kPa": pressure_kPa,
            "area_m2": area_m2,
            "direction": direction.model_dump(),
            "provenance": provenance,
        }
        if net_pressure_coefficient is not None:
            load["net_pressure_coefficient"] = net_pressure_coefficient
        if coefficient_status is not None:
            load["coefficient_status"] = coefficient_status
        if surface_action_pack_id is not None:
            load["surface_action_pack_id"] = surface_action_pack_id
        if external_pressure_coefficient is not None:
            load["external_pressure_coefficient"] = external_pressure_coefficient
        if internal_pressure_coefficient is not None:
            load["internal_pressure_coefficient"] = internal_pressure_coefficient
        if area_reduction_factor is not None:
            load["area_reduction_factor"] = area_reduction_factor
        raw_loads.append(load)
        load_geometry[load_id] = (
            component_id,
            line_tributary_width_m,
            direction,
        )

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
        for case_id, wind_sign in (
            *(
                (wind_cases[event]["positive_x"].id, 1.0)
                for event in ("serviceability", "ultimate")
            ),
            *(
                (wind_cases[event]["negative_x"].id, -1.0)
                for event in ("serviceability", "ultimate")
            ),
        ):
            transverse_internal_candidates = internal_pressure_candidates(
                opening_capacity_verified=opening_capacity_verified,
                openings_normally_open=openings_normally_open,
                potential_opening_surfaces=relative_opening_surfaces(
                    wind_axis="transverse", wind_sign=wind_sign
                ),
                leeward_external_coefficient=transverse_leeward_cpe,
                roof_external_coefficient=min(
                    transverse_upwind_roof_cpe,
                    transverse_downwind_roof_cpe,
                ),
            )
            for column_index, component_id in enumerate(columns):
                is_windward = (wind_sign > 0 and column_index == 0) or (
                    wind_sign < 0 and column_index == 1
                )
                start, end = physical_endpoints(component_id)
                member_length = dist(
                    start.model_dump().values(), end.model_dump().values()
                )
                loaded_area = member_length * tributary_width
                coefficient = surface_coefficient_envelope(
                    external_coefficient=(
                        0.7 if is_windward else transverse_leeward_cpe
                    ),
                    internal_candidates=transverse_internal_candidates,
                    loaded_area_m2=loaded_area,
                    surface=("windward_wall" if is_windward else "leeward_wall"),
                    average_roof_height_m=average_roof_height,
                    detail=(
                        f"transverse {'windward' if is_windward else 'leeward'} "
                        f"wall; h={average_roof_height:.6g} m; "
                        f"roof pitch={roof_pitch_degrees:.6g} degrees; "
                        f"loaded area={loaded_area:.6g} m2"
                    ),
                )
                load_id = f"site:{case_id}:{component_id}:wall"
                outward = Vector3(
                    x=-1.0 if (start.x + end.x) < 0 else 1.0,
                    y=0,
                    z=0,
                )
                direction = load_direction_from_surface_normal(
                    outward, coefficient.net_coefficient
                )
                add_surface_action(
                    load_id=load_id,
                    label=f"{case_id} wall action on {component_id}",
                    case="wind",
                    case_id=case_id,
                    component_id=component_id,
                    pressure_kPa=abs(coefficient.net_coefficient),
                    area_m2=loaded_area,
                    line_tributary_width_m=tributary_width,
                    direction=direction,
                    provenance=coefficient.provenance,
                    net_pressure_coefficient=coefficient.net_coefficient,
                    coefficient_status=coefficient.status,
                    surface_action_pack_id=configured.surface_action_pack_id,
                    external_pressure_coefficient=coefficient.external_coefficient,
                    internal_pressure_coefficient=coefficient.internal_coefficient,
                    area_reduction_factor=coefficient.area_reduction_factor,
                )
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
                is_upwind = (wind_sign > 0 and (start.x + end.x) < 0) or (
                    wind_sign < 0 and (start.x + end.x) > 0
                )
                loaded_area = member_length * tributary_width
                external_coefficient = (
                    transverse_upwind_roof_cpe
                    if is_upwind
                    else transverse_downwind_roof_cpe
                )
                coefficient = surface_coefficient_envelope(
                    external_coefficient=external_coefficient,
                    internal_candidates=transverse_internal_candidates,
                    loaded_area_m2=loaded_area,
                    surface="roof",
                    average_roof_height_m=average_roof_height,
                    detail=(
                        f"transverse {'upwind' if is_upwind else 'downwind'} "
                        f"roof slope; h/d={average_roof_height / geometry_width:.6g}; "
                        f"roof pitch={roof_pitch_degrees:.6g} degrees; "
                        f"loaded area={loaded_area:.6g} m2"
                    ),
                )
                load_id = f"site:{case_id}:{component_id}:roof"
                add_surface_action(
                    load_id=load_id,
                    label=f"{case_id} roof action on {component_id}",
                    case="wind",
                    case_id=case_id,
                    component_id=component_id,
                    pressure_kPa=abs(coefficient.net_coefficient),
                    area_m2=loaded_area,
                    line_tributary_width_m=tributary_width,
                    direction=load_direction_from_surface_normal(
                        outward, coefficient.net_coefficient
                    ),
                    provenance=coefficient.provenance,
                    net_pressure_coefficient=coefficient.net_coefficient,
                    coefficient_status=coefficient.status,
                    surface_action_pack_id=configured.surface_action_pack_id,
                    external_pressure_coefficient=coefficient.external_coefficient,
                    internal_pressure_coefficient=coefficient.internal_coefficient,
                    area_reduction_factor=coefficient.area_reduction_factor,
                )

        for component_id in rafters:
            start, end = physical_endpoints(component_id)
            member_length = dist(start.model_dump().values(), end.model_dump().values())
            plan_area = abs(end.x - start.x) * tributary_width
            if plan_area <= 1e-9:
                raise ValueError(
                    f"portal rafter {component_id!r} has no projected roof plan area"
                )
            imposed_pressure = max(1.8 / plan_area + 0.12, 0.25)
            plan_to_slope = abs(end.x - start.x) / member_length
            load_id = f"site:{imposed_case.id}:{component_id}:roof"
            add_surface_action(
                load_id=load_id,
                label=f"R2 roof imposed action on {component_id}",
                case="live",
                case_id=imposed_case.id,
                component_id=component_id,
                pressure_kPa=imposed_pressure,
                area_m2=plan_area,
                line_tributary_width_m=tributary_width * plan_to_slope,
                direction=Vector3(x=0, y=0, z=-1),
                provenance=(
                    "Tertius AS/NZS 1170.1:2002 Table 3.2 R2 working formula: "
                    "q = max(1.8/A + 0.12, 0.25) kPa; A is the compiled "
                    "member plan tributary area."
                ),
            )

    if not roof_imposed_receiver_ids:
        analysis_configuration.design_basis.standards[
            "permanent_and_imposed_actions"
        ] = (
            "AS/NZS 1170.1:2002 including Amendments 1 and 2 — concentrated "
            "roof-action receiver missing"
        )
        action_warnings = [
            "The concentrated roof action was not generated because no compiled "
            f"member has role {configured.roof_imposed_receiver_role!r}."
        ]
    else:
        action_warnings = []
        existing_load_ids = {load.id for load in analysis_configuration.member_loads}
        for component_id in sorted(roof_imposed_receiver_ids):
            start, end = physical_endpoints(component_id)
            member_length = dist(start.model_dump().values(), end.model_dump().values())
            case_id = f"roof-concentrated:{component_id}"
            point_load_id = f"tertius:{case_id}:midspan"
            if point_load_id in existing_load_ids:
                raise ValueError(
                    f"configured member load {point_load_id!r} uses a reserved "
                    "Tertius-derived roof-action identity"
                )
            concentrated_case = ensure_action_case(
                case_id=case_id,
                label=f"1.4 kN concentrated roof action on {component_id}",
                role="imposed",
                imposed_profile="all_other_roofs_concentrated",
            )
            analysis_configuration.member_loads.append(
                ConfiguredMemberPointLoad(
                    id=point_load_id,
                    label=f"Concentrated roof action on {component_id}",
                    component_id=component_id,
                    case_id=concentrated_case.id,
                    distance_m=member_length / 2.0,
                    force=Vector3(
                        x=0,
                        y=0,
                        z=-_ALL_OTHER_ROOFS_CONCENTRATED_ACTION_KN,
                    ),
                    provenance=(
                        "Tertius AS/NZS 1170.1:2002 Table 3.2 all-other-roofs "
                        "concentrated action. Each compiled roof receiver is an "
                        "alternative 1.4 kN midspan case; the AS/NZS 1170.0 action "
                        "pack prevents simultaneous application with the distributed "
                        "roof action or another concentrated receiver case."
                    ),
                )
            )
            existing_load_ids.add(point_load_id)

    end_frame_positions = (frame_positions[0], frame_positions[-1])
    for case_id, wind_sign in (
        *(
            (wind_cases[event]["positive_y"].id, 1.0)
            for event in ("serviceability", "ultimate")
        ),
        *(
            (wind_cases[event]["negative_y"].id, -1.0)
            for event in ("serviceability", "ultimate")
        ),
    ):
        longitudinal_roof_cpe_by_frame: dict[float, float] = {}
        for frame_y in frame_positions:
            strip_centroid_distance = (
                frame_y - frame_positions[0] + tributary_widths[frame_y] / 2.0
                if wind_sign > 0
                else frame_positions[-1] - frame_y
                + tributary_widths[frame_y] / 2.0
            )
            longitudinal_roof_cpe_by_frame[frame_y] = (
                longitudinal_roof_external_coefficient(
                    distance_from_windward_edge_m=strip_centroid_distance,
                    average_roof_height_m=average_roof_height,
                    building_depth_m=geometry_length,
                )
            )
        longitudinal_internal_candidates = internal_pressure_candidates(
            opening_capacity_verified=opening_capacity_verified,
            openings_normally_open=openings_normally_open,
            potential_opening_surfaces=relative_opening_surfaces(
                wind_axis="longitudinal", wind_sign=wind_sign
            ),
            leeward_external_coefficient=longitudinal_leeward_cpe,
            roof_external_coefficient=min(longitudinal_roof_cpe_by_frame.values()),
        )
        for end_index, frame_y in enumerate(end_frame_positions):
            is_windward = (wind_sign > 0 and end_index == 0) or (
                wind_sign < 0 and end_index == 1
            )
            external_coefficient = 0.7 if is_windward else longitudinal_leeward_cpe
            outward = Vector3(
                x=0,
                y=-1.0 if end_index == 0 else 1.0,
                z=0,
            )
            frame = frame_components[frame_y]
            for component_id in frame["columns"]:
                start, end = physical_endpoints(component_id)
                member_length = dist(
                    start.model_dump().values(), end.model_dump().values()
                )
                tributary_width = geometry_width / 2.0
                loaded_area = member_length * tributary_width
                coefficient = surface_coefficient_envelope(
                    external_coefficient=external_coefficient,
                    internal_candidates=longitudinal_internal_candidates,
                    loaded_area_m2=loaded_area,
                    surface=("windward_wall" if is_windward else "leeward_wall"),
                    average_roof_height_m=average_roof_height,
                    detail=(
                        f"longitudinal {'windward' if is_windward else 'leeward'} "
                        f"gable wall; h={average_roof_height:.6g} m; "
                        f"d/h={geometry_length / average_roof_height:.6g}; "
                        f"loaded area={loaded_area:.6g} m2"
                    ),
                )
                load_id = f"site:{case_id}:{component_id}:gable-wall"
                add_surface_action(
                    load_id=load_id,
                    label=f"{case_id} gable wall action on {component_id}",
                    case="wind",
                    case_id=case_id,
                    component_id=component_id,
                    pressure_kPa=abs(coefficient.net_coefficient),
                    area_m2=loaded_area,
                    line_tributary_width_m=tributary_width,
                    direction=load_direction_from_surface_normal(
                        outward, coefficient.net_coefficient
                    ),
                    provenance=coefficient.provenance,
                    net_pressure_coefficient=coefficient.net_coefficient,
                    coefficient_status=coefficient.status,
                    surface_action_pack_id=configured.surface_action_pack_id,
                    external_pressure_coefficient=coefficient.external_coefficient,
                    internal_pressure_coefficient=coefficient.internal_coefficient,
                    area_reduction_factor=coefficient.area_reduction_factor,
                )
            for component_id in frame["rafters"]:
                start, end = physical_endpoints(component_id)
                member_length = dist(
                    start.model_dump().values(), end.model_dump().values()
                )
                gable_area = abs(end.x - start.x) * abs(end.z - start.z) / 2.0
                if gable_area <= 1e-9:
                    continue
                coefficient = surface_coefficient_envelope(
                    external_coefficient=external_coefficient,
                    internal_candidates=longitudinal_internal_candidates,
                    loaded_area_m2=gable_area,
                    surface=("windward_wall" if is_windward else "leeward_wall"),
                    average_roof_height_m=average_roof_height,
                    detail=(
                        f"longitudinal {'windward' if is_windward else 'leeward'} "
                        f"gable triangle; h={average_roof_height:.6g} m; "
                        f"loaded area={gable_area:.6g} m2"
                    ),
                )
                load_id = f"site:{case_id}:{component_id}:gable"
                add_surface_action(
                    load_id=load_id,
                    label=f"{case_id} gable action on {component_id}",
                    case="wind",
                    case_id=case_id,
                    component_id=component_id,
                    pressure_kPa=abs(coefficient.net_coefficient),
                    area_m2=gable_area,
                    line_tributary_width_m=gable_area / member_length,
                    direction=load_direction_from_surface_normal(
                        outward, coefficient.net_coefficient
                    ),
                    provenance=coefficient.provenance,
                    net_pressure_coefficient=coefficient.net_coefficient,
                    coefficient_status=coefficient.status,
                    surface_action_pack_id=configured.surface_action_pack_id,
                    external_pressure_coefficient=coefficient.external_coefficient,
                    internal_pressure_coefficient=coefficient.internal_coefficient,
                    area_reduction_factor=coefficient.area_reduction_factor,
                )

        for frame_y in frame_positions:
            tributary_width = tributary_widths[frame_y]
            for component_id in frame_components[frame_y]["rafters"]:
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
                loaded_area = member_length * tributary_width
                external_coefficient = longitudinal_roof_cpe_by_frame[frame_y]
                coefficient = surface_coefficient_envelope(
                    external_coefficient=external_coefficient,
                    internal_candidates=longitudinal_internal_candidates,
                    loaded_area_m2=loaded_area,
                    surface="roof",
                    average_roof_height_m=average_roof_height,
                    detail=(
                        "longitudinal crosswind roof strip; "
                        f"Cp,e={external_coefficient:.6g}; "
                        f"loaded area={loaded_area:.6g} m2"
                    ),
                )
                load_id = f"site:{case_id}:{component_id}:roof"
                add_surface_action(
                    load_id=load_id,
                    label=f"{case_id} roof action on {component_id}",
                    case="wind",
                    case_id=case_id,
                    component_id=component_id,
                    pressure_kPa=abs(coefficient.net_coefficient),
                    area_m2=loaded_area,
                    line_tributary_width_m=tributary_width,
                    direction=load_direction_from_surface_normal(
                        outward, coefficient.net_coefficient
                    ),
                    provenance=coefficient.provenance,
                    net_pressure_coefficient=coefficient.net_coefficient,
                    coefficient_status=coefficient.status,
                    surface_action_pack_id=configured.surface_action_pack_id,
                    external_pressure_coefficient=coefficient.external_coefficient,
                    internal_pressure_coefficient=coefficient.internal_coefficient,
                    area_reduction_factor=coefficient.area_reduction_factor,
                )

    ultimate_overlaid = apply_site_definition(
        {
            "design_basis": analysis_configuration.design_basis.model_dump(
                mode="python"
            ),
            "wind_action_bases": [],
            "loads": [
                load
                for load in raw_loads
                if load.get("case") != "wind"
                or str(load.get("case_id") or "").startswith("wind-uls-")
            ],
        },
        site,
        basis_suffix="-uls",
        design_event="ultimate",
    )
    serviceability_overlaid = apply_site_definition(
        {
            "design_basis": analysis_configuration.design_basis.model_dump(
                mode="python"
            ),
            "wind_action_bases": [],
            "loads": [
                load
                for load in raw_loads
                if load.get("case") == "wind"
                and str(load.get("case_id") or "").startswith("wind-sls-")
            ],
        },
        site,
        annual_probability="1/25",
        basis_suffix="-sls",
        design_event="serviceability",
    )
    analysis_configuration.design_basis = type(
        configuration.design_basis
    ).model_validate(ultimate_overlaid["design_basis"])
    wind_reference = analysis_configuration.design_basis.standards.get(
        "wind_actions", site.project_basis.standards.wind
    )
    analysis_configuration.design_basis.standards["wind_actions"] = (
        f"{wind_reference}; Tertius surface action pack "
        f"{configured.surface_action_pack_id}"
    )
    wind_bases = [
        StructuralWindActionBasis.model_validate(value)
        for value in (
            *ultimate_overlaid["wind_action_bases"],
            *serviceability_overlaid["wind_action_bases"],
        )
    ]
    surface_loads = [
        DesignSurfaceLoad.model_validate(value)
        for value in (
            *ultimate_overlaid["loads"],
            *serviceability_overlaid["loads"],
        )
    ]
    surface_sources: dict[str, str] = {}
    distributed_configs: list[ConfiguredMemberDistributedLoad] = []
    for surface_load in surface_loads:
        component_id, line_tributary_width, direction = load_geometry[surface_load.id]
        force = Vector3(
            x=surface_load.pressure_kPa * line_tributary_width * direction.x,
            y=surface_load.pressure_kPa * line_tributary_width * direction.y,
            z=surface_load.pressure_kPa * line_tributary_width * direction.z,
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
                    + "; line tributary width derived from the Site footprint and "
                    "compiled portal-frame envelope."
                ),
            )
        )
    return (
        analysis_configuration,
        wind_bases,
        surface_loads,
        distributed_configs,
        surface_sources,
        action_warnings,
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


def _p399_stability_actions(
    configuration: StructuralProjectConfiguration,
    *,
    components: Sequence[DesignComponent],
    members: Sequence[AnalyticalMemberDeclaration],
    load_combinations: Sequence[LoadCombination],
) -> tuple[
    list[LoadCase],
    list[LoadCombination],
    StabilityDefinition | None,
    list[UnavailableLoadCombination],
    list[str],
]:
    """Plan solver-generated P399 EHF and NEd/200 NHF action cases."""

    roles = {
        component.id: (component.role or "").strip().lower() for component in components
    }
    column_component_ids = sorted(
        component_id for component_id, role in roles.items() if role == "portal column"
    )
    rafter_component_ids = {
        component_id for component_id, role in roles.items() if role == "portal rafter"
    }
    members_by_component: dict[str, list[AnalyticalMemberDeclaration]] = defaultdict(
        list
    )
    for member in members:
        members_by_component[member.component_id].append(member)

    missing_inputs: list[str] = []
    if not column_component_ids or any(
        not members_by_component.get(component_id)
        for component_id in column_component_ids
    ):
        missing_inputs.append("portal_column_axes")
    rafter_member_ids = [
        member.id for member in members if member.component_id in rafter_component_ids
    ]
    if not rafter_member_ids:
        missing_inputs.append("portal_rafter_axes")

    combinations_by_id = {
        combination.id: combination for combination in load_combinations
    }
    default_base = combinations_by_id.get("ULS-1.2G+1.5Q") or combinations_by_id.get(
        "ULS-1.35G"
    )
    if default_base is None:
        missing_inputs.append("uls_vertical_action_combination")

    if missing_inputs:
        reason = (
            "Tertius cannot derive the SCI P399 EHF/NHF actions until the compiled "
            "model supplies "
            + ", ".join(input_id.replace("_", " ") for input_id in missing_inputs)
            + "."
        )
        unavailable = [
            UnavailableLoadCombination(
                id=f"{prefix}{suffix}",
                label=f"P399 {label} {suffix}",
                limit_state="ultimate",
                family="global_stability",
                missing_inputs=missing_inputs,
                reason=reason,
            )
            for suffix in ("+X", "-X", "+Y", "-Y")
            for prefix, label in (
                ("ULS-STABILITY", "global-stability actions"),
                ("NHF-CHECK", "notional-horizontal-force check"),
            )
        ]
        return [], [], None, unavailable, [reason]

    assert default_base is not None
    direction_specs: tuple[
        tuple[str, str, Literal["x", "y"], Literal[-1, 1], str], ...
    ] = (
        ("positive-x", "+X", "x", 1, "WX+"),
        ("negative-x", "-X", "x", -1, "WX-"),
        ("positive-y", "+Y", "y", 1, "WY+"),
        ("negative-y", "-Y", "y", -1, "WY-"),
    )
    generated_cases: list[LoadCase] = []
    generated_combinations: list[LoadCombination] = []
    directions: list[StabilityDirectionDefinition] = []
    for direction_id, suffix, axis, sign, wind_suffix in direction_specs:
        design_base = combinations_by_id.get(f"ULS-1.2G+{wind_suffix}") or default_base
        ehf_case_id = f"p399-ehf-{direction_id}"
        nhf_case_id = f"p399-nhf-{direction_id}"
        stability_combination_id = f"ULS-STABILITY{suffix}"
        nhf_combination_id = f"NHF-CHECK{suffix}"
        generated_cases.extend(
            (
                LoadCase(
                    id=ehf_case_id,
                    label=f"P399 equivalent horizontal force {suffix}",
                    category="imperfection",
                ),
                LoadCase(
                    id=nhf_case_id,
                    label=f"P399 NEd/200 notional horizontal force {suffix}",
                    category="imperfection",
                ),
            )
        )
        generated_combinations.extend(
            (
                LoadCombination(
                    id=stability_combination_id,
                    label=f"{design_base.label} plus P399 EHF {suffix}",
                    limit_state="ultimate",
                    factors={**design_base.factors, ehf_case_id: 1.0},
                ),
                LoadCombination(
                    id=nhf_combination_id,
                    label=f"P399 NEd/200 stiffness probe {suffix}",
                    limit_state="ultimate",
                    factors={nhf_case_id: 1.0},
                    purpose="stability_probe",
                ),
            )
        )
        directions.append(
            StabilityDirectionDefinition(
                id=direction_id,
                base_combination_id=default_base.id,
                stability_combination_id=stability_combination_id,
                imperfection_case_id=ehf_case_id,
                nhf_combination_id=nhf_combination_id,
                horizontal_axis=axis,
                direction_sign=sign,
            )
        )

    eaves_member_ids = [
        max(
            members_by_component[component_id],
            key=lambda member: max(member.start.z, member.end.z),
        ).id
        for component_id in column_component_ids
    ]
    column_members = [
        member
        for component_id in column_component_ids
        for member in members_by_component[component_id]
    ]
    column_height = max(
        max(member.start.z, member.end.z) for member in column_members
    ) - min(min(member.start.z, member.end.z) for member in column_members)
    base_restraints = []
    for component_id in column_component_ids:
        component_members = members_by_component[component_id]
        endpoint_restraints = [
            (member.start.z, member.start_restraints) for member in component_members
        ] + [(member.end.z, member.end_restraints) for member in component_members]
        base_restraints.append(min(endpoint_restraints, key=lambda item: item[0])[1])
    base_model: Literal["unspecified", "perfectly_pinned", "rotational_spring", "fixed"]
    if all(
        restraint.rx and restraint.ry and restraint.rz for restraint in base_restraints
    ):
        base_model = "fixed"
    elif all(
        not restraint.rx and not restraint.ry and not restraint.rz
        for restraint in base_restraints
    ):
        base_model = "perfectly_pinned"
    else:
        base_model = "unspecified"

    first_direction = directions[0]
    stability = StabilityDefinition(
        method="p_delta",
        stability_combination_id=first_direction.stability_combination_id,
        imperfection_case_id=first_direction.imperfection_case_id,
        imperfection_basis=(
            "SCI P399 Sections 7.2-7.6 working implementation: Tertius derives "
            "EHF and NHF at 1/200 of the solved factored vertical base reactions."
        ),
        base_stiffness_basis=(
            "Compiled support restraints; physical rotational stiffness remains "
            "subject to connection evidence."
        ),
        base_stiffness_status="assumed",
        direction_cases=directions,
        column_component_ids=column_component_ids,
        eaves_member_ids=eaves_member_ids,
        rafter_member_ids=rafter_member_ids,
        column_height_m=column_height,
        analysis_base_model=base_model,
        analysis_basis_status="assumed",
        physical_connection_stiffness_status="not_checked",
    )
    return generated_cases, generated_combinations, stability, [], []


def _analysis_from_projection(
    projection: dict,
    *,
    components: list[DesignComponent],
    configuration: StructuralProjectConfiguration,
    derived_distributed_loads: Sequence[ConfiguredMemberDistributedLoad] = (),
    surface_sources: Mapping[str, str] | None = None,
) -> tuple[DesignAnalysisDefinition, list[str]]:
    resolved_action_pack = resolve_action_standard_pack(
        configuration.action_standard_pack_id,
        configuration.action_cases,
    )
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
                tension_width_mm=(
                    float(section_data["tension_width_mm"])
                    if section_data.get("tension_width_mm") is not None
                    else None
                ),
                tension_thickness_mm=(
                    float(section_data["tension_thickness_mm"])
                    if section_data.get("tension_thickness_mm") is not None
                    else None
                ),
                tension_hole_diameter_mm=(
                    float(section_data["tension_hole_diameter_mm"])
                    if section_data.get("tension_hole_diameter_mm") is not None
                    else None
                ),
                tension_holes_in_critical_section=(
                    int(section_data["tension_holes_in_critical_section"])
                    if section_data.get("tension_holes_in_critical_section") is not None
                    else None
                ),
                tension_force_distribution_factor=(
                    float(section_data["tension_force_distribution_factor"])
                    if section_data.get("tension_force_distribution_factor") is not None
                    else None
                ),
                end_fastener_nominal_diameter_mm=(
                    float(section_data["end_fastener_nominal_diameter_mm"])
                    if section_data.get("end_fastener_nominal_diameter_mm") is not None
                    else None
                ),
                end_fastener_spacing_mm=(
                    float(section_data["end_fastener_spacing_mm"])
                    if section_data.get("end_fastener_spacing_mm") is not None
                    else None
                ),
                end_fastener_edge_distance_mm=(
                    float(section_data["end_fastener_edge_distance_mm"])
                    if section_data.get("end_fastener_edge_distance_mm") is not None
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
                yield_strength_MPa=(
                    float(material_data["yield_strength_pa"]) / 1_000_000.0
                    if material_data.get("yield_strength_pa") is not None
                    else None
                ),
                tensile_strength_MPa=(
                    float(material_data["tensile_strength_pa"]) / 1_000_000.0
                    if material_data.get("tensile_strength_pa") is not None
                    else None
                ),
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
                tension_only=bool(
                    structural_properties.get(
                        "tension_only",
                        projected_member.get("tension_only"),
                    )
                ),
                compression_only=bool(
                    structural_properties.get(
                        "compression_only",
                        projected_member.get("compression_only"),
                    )
                ),
                tension_capacity_status=(
                    structural_properties.get(
                        "tension_capacity_status",
                        projected_member.get("tension_capacity_status"),
                    )
                    or "not_checked"
                ),
                tension_capacity_kN=(
                    float(structural_properties["tension_capacity_kN"])
                    if structural_properties.get("tension_capacity_kN") is not None
                    else float(projected_member["tension_capacity_kN"])
                    if projected_member.get("tension_capacity_kN") is not None
                    else None
                ),
                tension_capacity_basis=structural_properties.get(
                    "tension_capacity_basis",
                    projected_member.get("tension_capacity_basis"),
                ),
                end_fastener_count=(
                    int(structural_properties["end_fastener_count"])
                    if structural_properties.get("end_fastener_count") is not None
                    else int(projected_member["end_fastener_count"])
                    if projected_member.get("end_fastener_count") is not None
                    else None
                ),
                end_connection_capacity_kN=(
                    float(structural_properties["end_connection_capacity_kN"])
                    if structural_properties.get("end_connection_capacity_kN")
                    is not None
                    else float(projected_member["end_connection_capacity_kN"])
                    if projected_member.get("end_connection_capacity_kN") is not None
                    else None
                ),
                end_connection_basis=structural_properties.get(
                    "end_connection_basis",
                    projected_member.get("end_connection_basis"),
                ),
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
        analytical_length = _member_length(point_member)
        local_distance = (
            (point_config.distance_m - point_member_start)
            / station_span
            * analytical_length
        )
        point_loads.append(
            MemberPointLoad(
                id=point_config.id,
                label=point_config.label,
                member_id=str(point_member["id"]),
                case_id=point_config.case_id,
                distance_m=min(analytical_length, max(0.0, local_distance)),
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
                mapped = (station - member_start) / station_span * analytical_length
                return min(analytical_length, max(0.0, mapped))

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
            case for case in resolved_action_pack.load_cases if case.category == "dead"
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
    inferred_restraint_candidates = _derive_member_restraint_candidates(
        projection,
        components=components,
        declarations=declarations,
    )
    restraint_candidates_by_member: dict[
        str, list[MemberRestraintCandidateDefinition]
    ] = defaultdict(list)
    for candidate in inferred_restraint_candidates:
        restraint_candidates_by_member[candidate.member_id].append(candidate)
    (
        p399_load_cases,
        p399_load_combinations,
        stability,
        p399_unavailable,
        p399_warnings,
    ) = _p399_stability_actions(
        configuration,
        components=components,
        members=declarations,
        load_combinations=resolved_action_pack.load_combinations,
    )
    load_cases = [*resolved_action_pack.load_cases, *p399_load_cases]
    load_combinations = [
        *resolved_action_pack.load_combinations,
        *p399_load_combinations,
    ]
    unavailable_combinations = [
        *resolved_action_pack.unavailable_combinations,
        *p399_unavailable,
    ]
    warnings.extend(p399_warnings)
    ultimate_combination_ids = [
        combination.id
        for combination in load_combinations
        if combination.limit_state == "ultimate" and combination.purpose == "design"
    ]
    projected_by_id = {str(member["id"]): member for member in projected_members}
    cross_section_verification = None
    if configuration.cross_section_verification is not None:
        configured_cross_section = configuration.cross_section_verification
        selected_member_ids: list[str] = []
        selected_component_ids = configured_cross_section.component_ids
        if not selected_component_ids:
            selected_member_ids = [
                declaration.id
                for declaration in declarations
                if not declaration.tension_only and not declaration.compression_only
            ]
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
            combination_ids=ultimate_combination_ids,
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
                if declaration.tension_only or declaration.compression_only:
                    continue
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
                        start_restraint_candidate_ids=[
                            candidate.id
                            for candidate in restraint_candidates_by_member.get(
                                declaration.id, []
                            )
                            if abs(candidate.distance_m) <= 1e-9
                        ],
                        end_restraint_candidate_ids=[
                            candidate.id
                            for candidate in restraint_candidates_by_member.get(
                                declaration.id, []
                            )
                            if abs(candidate.distance_m - member_length) <= 1e-9
                        ],
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
                local_start = overlap_start - member_start
                local_end = overlap_end - member_start
                segments.append(
                    MemberStabilitySegmentDefinition(
                        id=(
                            configured_segment.id
                            if len(overlapping_declarations) == 1
                            else f"{configured_segment.id}:segment:{segment_index:02d}"
                        ),
                        member_id=segment_declaration.id,
                        start_distance_m=local_start,
                        end_distance_m=local_end,
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
                        start_restraint_candidate_ids=[
                            candidate.id
                            for candidate in restraint_candidates_by_member.get(
                                segment_declaration.id, []
                            )
                            if abs(candidate.distance_m - local_start) <= 1e-9
                        ],
                        end_restraint_candidate_ids=[
                            candidate.id
                            for candidate in restraint_candidates_by_member.get(
                                segment_declaration.id, []
                            )
                            if abs(candidate.distance_m - local_end) <= 1e-9
                        ],
                    )
                )
        active_member_ids = {segment.member_id for segment in segments}
        member_stability_verification = MemberStabilityVerificationDefinition(
            pack_id=configured_member_stability.pack_id,
            combination_ids=ultimate_combination_ids,
            segments=segments,
            restraint_candidates=[
                candidate
                for candidate in inferred_restraint_candidates
                if candidate.member_id in active_member_ids
            ],
            off_axis_tolerance=configured_member_stability.off_axis_tolerance,
        )

    return (
        DesignAnalysisDefinition(
            materials=list(materials.values()),
            sections=list(sections.values()),
            members=declarations,
            load_cases=load_cases,
            member_loads=point_loads,
            member_distributed_loads=distributed_loads,
            load_combinations=load_combinations,
            unavailable_load_combinations=unavailable_combinations,
            action_standard_pack=resolved_action_pack.evidence,
            stability=stability,
            cross_section_verification=cross_section_verification,
            member_stability_verification=member_stability_verification,
        ),
        list(dict.fromkeys(warnings)),
    )


def _load_active_capture_context(
    *,
    ctx: AuthContext,
    db: Session,
) -> tuple[Project, ProjectStructuralCapture, dict[str, object] | None]:
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
        capture = _capture_from_structural_projection(
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
        return (
            project,
            capture,
            site.model_dump(mode="json") if site is not None else None,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Compiled structural projection is invalid: {exc}",
        ) from exc


@app.get("/active/capture", response_model=ProjectStructuralCapture)
def get_active_capture(
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ProjectStructuralCapture:
    _, capture, _ = _load_active_capture_context(ctx=ctx, db=db)
    return capture


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


def _analysis_cache_info(
    identity: StructuralAnalysisCacheIdentity,
    stored: StructuralAnalysisResult,
    *,
    status: Literal["hit", "calculated"],
) -> StructuralAnalysisCacheInfo:
    return StructuralAnalysisCacheInfo(
        status=status,
        key_digest=identity.key_digest,
        engine_version=identity.engine_version,
        calculated_at=stored.created_at,
        calculation_duration_seconds=stored.calculation_duration_seconds,
    )


def _solve_cached_structural_analysis(
    *,
    db: Session,
    ctx: AuthContext,
    project: Project,
    capture: ProjectStructuralCapture,
    site_definition: dict[str, object] | None,
    combination_id: str | None,
) -> tuple[StructuralSnapshot, StructuralAnalysisCacheInfo]:
    identity = analysis_cache_identity(
        tenant_id=ctx.tenant_id,
        project_id=project.id,
        design_digest=capture.design_hash,
        configuration_digest=capture.analysis_configuration_digest,
        site_definition=site_definition,
        combination_id=combination_id,
    )
    cached = get_cached_structural_analysis(db, identity)
    if cached is not None:
        stored, snapshot = cached
        return snapshot, _analysis_cache_info(identity, stored, status="hit")

    acquire_structural_analysis_lock(db, identity)
    cached = get_cached_structural_analysis(db, identity)
    if cached is not None:
        stored, snapshot = cached
        info = _analysis_cache_info(identity, stored, status="hit")
        db.commit()
        return snapshot, info

    progress_scope = _structural_progress_scope(ctx, project, combination_id)
    _begin_structural_progress(progress_scope, identity)
    started_at = perf_counter()
    try:
        snapshot = solve_project_structural(
            capture,
            combination_id=combination_id,
            progress_callback=lambda stage_id, stage_label, completed, total: (
                _update_structural_progress(
                    progress_scope,
                    stage_id,
                    stage_label,
                    completed,
                    total,
                )
            ),
        )
        _update_structural_progress(
            progress_scope,
            "saving",
            "Saving the reusable structural result",
        )
        calculation_duration_seconds = perf_counter() - started_at
        stored = store_structural_analysis(
            db,
            identity,
            snapshot,
            calculation_duration_seconds=calculation_duration_seconds,
        )
        info = _analysis_cache_info(identity, stored, status="calculated")
        db.commit()
        _finish_structural_progress(
            progress_scope,
            state="complete",
            duration_seconds=calculation_duration_seconds,
        )
        return snapshot, info
    except Exception:
        db.rollback()
        _finish_structural_progress(
            progress_scope,
            state="failed",
            duration_seconds=perf_counter() - started_at,
        )
        raise


def _apply_analysis_cache_headers(
    response: Response,
    cache: StructuralAnalysisCacheInfo,
) -> None:
    response.headers["X-Tertius-Structural-Cache"] = cache.status.upper()
    response.headers["X-Tertius-Structural-Cache-Key"] = cache.key_digest[:12]
    response.headers["X-Tertius-Structural-Engine"] = cache.engine_version
    response.headers["X-Tertius-Structural-Calculated-At"] = (
        cache.calculated_at.isoformat()
    )
    response.headers["X-Tertius-Structural-Calculation-Seconds"] = (
        f"{cache.calculation_duration_seconds:.6f}"
    )


@app.get(
    "/active/analysis/progress",
    response_model=StructuralAnalysisProgress,
)
def get_active_analysis_progress(
    combination_id: str | None = Query(default=None),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> StructuralAnalysisProgress:
    project = get_active_project(db, ctx)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project selected")
    return _read_structural_progress(
        _structural_progress_scope(ctx, project, combination_id)
    )


@app.get("/active/workbench", response_model=ActiveStructuralWorkbenchResponse)
def get_active_workbench(
    combination_id: str | None = Query(default=None),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ActiveStructuralWorkbenchResponse:
    project, capture, site_definition = _load_active_capture_context(ctx=ctx, db=db)
    try:
        analysis, cache = _solve_cached_structural_analysis(
            db=db,
            ctx=ctx,
            project=project,
            capture=capture,
            site_definition=site_definition,
            combination_id=combination_id,
        )
        return ActiveStructuralWorkbenchResponse(
            capture=capture,
            analysis=analysis,
            cache=cache,
        )
    except StructuralAnalysisError as exc:
        return ActiveStructuralWorkbenchResponse(
            capture=capture,
            analysis_error=str(exc),
        )


@app.get("/active/analysis", response_model=StructuralSnapshot)
def get_active_analysis(
    response: Response,
    combination_id: str | None = Query(default=None),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> StructuralSnapshot:
    project, capture, site_definition = _load_active_capture_context(ctx=ctx, db=db)
    try:
        analysis, cache = _solve_cached_structural_analysis(
            db=db,
            ctx=ctx,
            project=project,
            capture=capture,
            site_definition=site_definition,
            combination_id=combination_id,
        )
        _apply_analysis_cache_headers(response, cache)
        return analysis
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
