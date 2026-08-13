from __future__ import annotations

import json
from math import dist
from collections.abc import Mapping

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
    DesignAnalysisDefinition,
    DesignComponent,
    DesignConnection,
    LoadCase,
    LoadCombination,
    MemberDistributedLoad,
    MemberPointLoad,
    ProjectStructuralCapture,
    Restraints,
    SectionProperties,
    StructuralMaterial,
    StructuralSnapshot,
    Vector3,
)
from core.structural.project_configuration import (
    StructuralConfigurationRevisionResponse,
    StructuralProjectConfiguration,
    fixed_restraints,
    pinned_restraints,
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
                str(component["part_number"])
                if component.get("part_number")
                else None
            ),
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
            )
        )

    diagnostics = [
        str(diagnostic.get("message"))
        for diagnostic in projection.get("diagnostics", [])
        if isinstance(diagnostic, dict) and diagnostic.get("message")
    ]
    readiness = projection.get("readiness") or {}
    analysis = None
    if configuration is not None and readiness.get("model_complete"):
        analysis, analysis_warnings = _analysis_from_projection(
            projection,
            components=components,
            configuration=configuration,
        )
        diagnostics.extend(analysis_warnings)
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
        design_basis=configuration.design_basis if configuration else None,
        components=components,
        connections=connections,
        loads=[],
        load_paths=[],
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
) -> tuple[Restraints, Restraints, list[str], str]:
    warnings: list[str] = []
    joint = endpoint_joints.get((component_id, endpoint))
    if joint is None:
        return Restraints(), Restraints(), warnings, f"endpoint:{component_id}:{endpoint}"

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
    node_key = f"joint:{connection_id}"

    if "ground" in other_kinds:
        if model in {"rigid", "rigid_zone"}:
            return fixed_restraints(), Restraints(), warnings, node_key
        if model == "pinned":
            return pinned_restraints(), Restraints(), warnings, node_key
        raise ValueError(
            f"ground connection {joint.get('connection_id')!r} uses unsupported "
            f"analysis model {model!r}"
        )

    if "member" in other_kinds:
        if model in {"rigid", "rigid_zone"}:
            return Restraints(), Restraints(), warnings, node_key
        if model == "pinned":
            if "moment" in set(joint.get("transfers") or []):
                raise ValueError(
                    f"pinned connection {connection_id!r} cannot declare moment transfer"
                )
            releases = Restraints(rx=True, ry=True, rz=True)
            return Restraints(), releases, warnings, node_key
        raise ValueError(
            f"member connection {connection_id!r} uses unsupported analysis model "
            f"{model!r}"
        )

    raise ValueError(
        f"connection {connection_id!r} does not join {component_id}.{endpoint} to "
        "a structural member or ground reference"
    )


def _analysis_from_projection(
    projection: dict,
    *,
    components: list[DesignComponent],
    configuration: StructuralProjectConfiguration,
) -> tuple[DesignAnalysisDefinition, list[str]]:
    product_facets = {
        str(facet.get("product_key")): facet
        for facet in projection.get("product_facets", [])
        if isinstance(facet, dict) and facet.get("product_key")
    }
    component_kinds = {component.id: component.kind for component in components}
    endpoint_joints = _endpoint_joint_index(projection)
    members_by_component = {
        str(member.get("component_id")): member
        for member in projection.get("analytical_members", [])
        if isinstance(member, dict) and member.get("component_id")
    }
    if not members_by_component:
        raise ValueError("structural projection has no analytical members")

    materials: dict[str, StructuralMaterial] = {}
    sections: dict[str, SectionProperties] = {}
    declarations: list[AnalyticalMemberDeclaration] = []
    criteria_by_component = {
        criterion.component_id: criterion for criterion in configuration.member_criteria
    }
    warnings: list[str] = []
    has_ground_restraint = False

    for component_id, projected_member in members_by_component.items():
        product_key = str(projected_member.get("product_key") or "")
        facet = product_facets.get(product_key)
        if facet is None:
            raise ValueError(
                f"analytical member {projected_member.get('id')!r} references missing product facet"
            )
        section_data = facet.get("section") or projected_member.get("section") or {}
        material_data = facet.get("material") or projected_member.get("material") or {}
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
            ),
        )
        materials.setdefault(
            material_id,
            StructuralMaterial(
                id=material_id,
                label=str(material_data.get("label") or product_key),
                elastic_modulus_kN_m2=float(material_data["elastic_modulus_pa"])
                / 1000.0,
                shear_modulus_kN_m2=float(material_data["shear_modulus_pa"])
                / 1000.0,
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
        criterion = criteria_by_component.get(component_id)
        declarations.append(
            AnalyticalMemberDeclaration(
                id=str(projected_member["id"]),
                label=next(
                    (
                        component.label
                        for component in components
                        if component.id == component_id
                    ),
                    component_id,
                ),
                component_id=component_id,
                start=_vector(projected_member.get("start_m"), label="member start"),
                end=_vector(projected_member.get("end_m"), label="member end"),
                start_node_key=start_node_key,
                end_node_key=end_node_key,
                start_restraints=start_restraints,
                end_restraints=end_restraints,
                start_releases=start_releases,
                end_releases=end_releases,
                section_id=section_id,
                material_id=material_id,
                rotation_deg=float(projected_member.get("rotation_deg") or 0),
                deflection_limit_ratio=(
                    criterion.deflection_limit_ratio if criterion else None
                ),
                deflection_limit_mm=(
                    criterion.deflection_limit_mm if criterion else None
                ),
                deflection_limit_basis=(
                    criterion.deflection_limit_basis if criterion else None
                ),
                assumption=(
                    "Axis, section, material, and physical restraints are projected "
                    "from the compiled mechanical graph."
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
        point_member = members_by_component.get(point_config.component_id)
        if point_member is None:
            raise ValueError(
                f"configured load {point_config.id!r} references missing component "
                f"{point_config.component_id!r}"
            )
        length = _member_length(point_member)
        if point_config.distance_m > length:
            raise ValueError(
                f"configured load {point_config.id!r} lies beyond member length {length:g}m"
            )
        point_loads.append(
            MemberPointLoad(
                id=point_config.id,
                label=point_config.label,
                member_id=str(point_member["id"]),
                case_id=point_config.case_id,
                distance_m=point_config.distance_m,
                force=point_config.force,
                moment=point_config.moment,
                source_load_id=None,
                provenance=point_config.provenance,
            )
        )
    for distributed_config in configuration.member_distributed_loads:
        distributed_member = members_by_component.get(distributed_config.component_id)
        if distributed_member is None:
            raise ValueError(
                f"configured load {distributed_config.id!r} references missing component "
                f"{distributed_config.component_id!r}"
            )
        length = _member_length(distributed_member)
        end_distance = distributed_config.end_distance_m or length
        if end_distance > length or distributed_config.start_distance_m >= end_distance:
            raise ValueError(
                f"configured load {distributed_config.id!r} has invalid member stations"
            )
        distributed_loads.append(
            MemberDistributedLoad(
                id=distributed_config.id,
                label=distributed_config.label,
                member_id=str(distributed_member["id"]),
                case_id=distributed_config.case_id,
                start_distance_m=distributed_config.start_distance_m,
                end_distance_m=end_distance,
                start_force_kN_m=distributed_config.start_force_kN_m,
                end_force_kN_m=(
                    distributed_config.end_force_kN_m
                    or distributed_config.start_force_kN_m
                ),
                source_kind="authored",
                source_load_id=None,
                provenance=distributed_config.provenance,
            )
        )

    if configuration.include_self_weight:
        dead_cases = [case for case in configuration.load_cases if case.category == "dead"]
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

    return (
        DesignAnalysisDefinition(
            materials=list(materials.values()),
            sections=list(sections.values()),
            members=declarations,
            load_cases=[LoadCase.model_validate(case) for case in configuration.load_cases],
            member_loads=point_loads,
            member_distributed_loads=distributed_loads,
            load_combinations=[
                LoadCombination.model_validate(combination)
                for combination in configuration.load_combinations
            ],
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
            StructuralProjectConfiguration.model_validate(
                stored_configuration.content
            )
            if stored_configuration is not None
            else None
        )
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
