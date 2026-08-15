from __future__ import annotations

from copy import deepcopy
from math import dist

import pytest

from core.project_templates import (
    default_project_files,
    default_structural_configuration,
)
from core.structural.action_standard_packs import resolve_action_standard_pack
from core.site_definition import default_site_definition
from core.structural.project_analysis import solve_project_structural
from core.structural.project_configuration import StructuralProjectConfiguration
from core.structural.contracts import DesignComponent, ProjectStructuralCapture
from tertius.runner import execute_design
from workflows.structural.structural_server import (
    _capture_from_structural_projection,
    _endpoint_connection_effects,
    _portal_frame_wind_actions,
)


def test_structural_workbench_capture_uses_compiled_projection() -> None:
    projection = {
        "schema_version": "tertius.structural.v1",
        "compiled_design_digest": "d" * 64,
        "components": [
            {"component_id": "C1", "kind": "member", "mark": "C1"},
            {"component_id": "R1", "kind": "member", "mark": "R1"},
            {"component_id": "KB1", "kind": "connector", "mark": "KB1"},
        ],
        "joints": [
            {
                "connection_id": "K1",
                "ports": [
                    {"component_id": "C1", "port": "end"},
                    {"component_id": "R1", "port": "start"},
                ],
                "connector_component_ids": ["KB1"],
                "transfers": ["force", "shear", "moment"],
            }
        ],
        "readiness": {"model_complete": True, "verified": False},
        "diagnostics": [],
    }

    capture = _capture_from_structural_projection(
        projection,
        project_name="managed-frame",
    )

    assert capture.design_hash == "d" * 64
    assert [component.id for component in capture.components] == ["C1", "R1", "KB1"]
    assert capture.connections[0].connector_component_ids == ["KB1"]
    assert capture.analysis is None
    assert {capability.status for capability in capture.capabilities} == {
        "online",
        "blocked",
    }


def test_structural_workbench_rejects_old_manifest_schema() -> None:
    with pytest.raises(ValueError, match="unsupported structural projection"):
        _capture_from_structural_projection(
            {"schema_version": "1.0", "compiled_design_digest": "d" * 64},
            project_name="legacy-frame",
        )


def test_default_mechanical_graph_and_workbench_state_produce_solver_results(
    tmp_path,
) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    configuration = StructuralProjectConfiguration.model_validate(
        default_structural_configuration()
    )

    capture = _capture_from_structural_projection(
        execution.projections["structural"],
        project_name="default-purlin",
        configuration=configuration,
        configuration_revision=1,
        configuration_digest=configuration.configuration_digest,
    )
    snapshot = solve_project_structural(capture)

    assert capture.analysis_configuration_revision == 1
    assert capture.analysis_configuration_digest == configuration.configuration_digest
    assert capture.analysis is not None
    ultimate_ids = [
        combination.id
        for combination in capture.analysis.load_combinations
        if combination.limit_state == "ultimate"
    ]
    assert capture.analysis.action_standard_pack is not None
    assert capture.analysis.action_standard_pack.combination_ids == [
        combination.id for combination in capture.analysis.load_combinations
    ]
    assert capture.analysis.cross_section_verification is not None
    assert capture.analysis.cross_section_verification.combination_ids == ultimate_ids
    assert capture.analysis.member_stability_verification is not None
    assert capture.analysis.member_stability_verification.combination_ids == ultimate_ids
    members = {member.component_id: member for member in capture.analysis.members}
    assert members["C1"].start_restraints.model_dump() == {
        "dx": True,
        "dy": True,
        "dz": True,
        "rx": True,
        "ry": True,
        "rz": True,
    }
    assert members["C1"].start_node_key == "joint:BASE1"
    assert members["C1"].end_node_key == "joint:KNEE1"
    assert members["P1"].start_node_key == "joint:KNEE1"
    assert members["P1"].end_node_key == "endpoint:P1:end"
    assert members["C1"].rotation_deg == pytest.approx(-90.0)
    assert members["P1"].rotation_deg == pytest.approx(-90.0)
    assert [connection.id for connection in capture.connections] == ["BASE1", "KNEE1"]
    assert len(snapshot.nodes) == 3
    assert len(snapshot.member_diagrams) == 2
    assert len(snapshot.reactions) == 1
    assert {result.member_id for result in snapshot.member_results} == {
        "member:C1",
        "member:P1",
    }
    assert max(result.max_moment_kNm for result in snapshot.member_results) > 0
    assert (
        next(
            check
            for check in snapshot.serviceability_checks
            if check.member_id == "member:P1"
        ).status
        == "pass"
    )
    assert snapshot.equilibrium.status == "pass"
    assert snapshot.source.analysis_configuration_revision == 1
    assert any("Connection KNEE1" in warning for warning in capture.warnings)

    sections = {section.label: section for section in snapshot.sections}
    assert sections["C200x2.4 (Lysaght)"].catalog is not None
    assert sections["C100x1.9 (Lysaght)"].catalog is not None
    assert all(
        section.catalog is not None and section.catalog.properties["validated"] is True
        for section in sections.values()
    )
    assert {check.status for check in snapshot.cross_section_checks} == {"pass"}
    assert all(
        check.governing_utilisation is not None
        and check.governing_utilisation < 1.0
        and check.section_record_sha256
        for check in snapshot.cross_section_checks
    )
    assert {check.status for check in snapshot.member_stability_checks} == {
        "unsupported"
    }
    assert all(
        check.distortional_buckling_status == "unverified"
        for check in snapshot.member_stability_checks
    )
    connection_checks = {
        check.connection_id: check for check in snapshot.connection_checks
    }
    assert set(connection_checks) == {"BASE1", "KNEE1"}
    assert all(
        check.status == "unsupported"
        and check.identity_status == "pass"
        and check.evidence_status == "unverified"
        and check.moment_demand_kNm > 0
        for check in connection_checks.values()
    )
    stages = {stage.id: stage.status for stage in snapshot.verification_stages}
    assert stages["cross_section"] == "pass"
    assert stages["member_stability"] == "unsupported"
    assert stages["connections"] == "unsupported"
    assert stages["decision"] == "blocked"

    procurement = execution.projections["procurement"]
    requirement_parts = {
        requirement["part_number"] for requirement in procurement["requirements"]
    }
    assert {
        "C20024",
        "C10019",
        "FAB-BP-150X150X10",
        "M12X100-ANCHOR-DEMO",
        "FAB-KG-180X180X6",
        "M12X30-BOLT-DEMO",
    }.issubset(requirement_parts)


def test_site_basis_without_wind_receivers_blocks_actions_stage(tmp_path) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    configuration = StructuralProjectConfiguration.model_validate(
        default_structural_configuration()
    )

    capture = _capture_from_structural_projection(
        execution.projections["structural"],
        project_name="default-purlin",
        configuration=configuration,
        site=default_site_definition(),
    )
    snapshot = solve_project_structural(capture)

    actions = next(
        stage for stage in snapshot.verification_stages if stage.id == "actions"
    )
    assert capture.wind_action_bases
    assert capture.loads == []
    assert actions.status == "blocked"
    assert (
        "no wind action"
        in next(
            sheet
            for sheet in snapshot.calculation_sheets
            if sheet.stage_id == "actions"
        )
        .assumptions[0]
        .lower()
    )


def test_portal_role_action_model_derives_site_wind_cases_and_line_actions() -> None:
    site = default_site_definition().model_copy(deep=True)
    site.structure.footprint_length_m = 5.0
    site.structure.footprint_width_m = 3.0
    configuration_data = default_structural_configuration()
    configuration_data["portal_frame_wind_actions"] = {
        "coefficient_basis": (
            "Worked transverse portal-frame envelope pending AS/NZS 1170.2 "
            "surface-zone verification."
        )
    }
    configuration = StructuralProjectConfiguration.model_validate(configuration_data)
    components: list[DesignComponent] = []
    analytical_members: list[dict] = []
    for frame_index, y in enumerate((0.0, 5.0), start=1):
        member_specs = (
            (f"F{frame_index}CL", "portal column", (-1.5, y, 0), (-1.5, y, 2.4)),
            (f"F{frame_index}CR", "portal column", (1.5, y, 0), (1.5, y, 2.4)),
            (f"F{frame_index}RL", "portal rafter", (-1.5, y, 2.4), (0, y, 3.0)),
            (f"F{frame_index}RR", "portal rafter", (1.5, y, 2.4), (0, y, 3.0)),
        )
        for component_id, role, start, end in member_specs:
            components.append(
                DesignComponent(
                    id=component_id,
                    label=component_id,
                    kind="member",
                    visual_node_id=component_id,
                    role=role,
                )
            )
            analytical_members.append(
                {
                    "id": f"member:{component_id}",
                    "component_id": component_id,
                    "start_m": list(start),
                    "end_m": list(end),
                    "physical_start_distance_m": 0.0,
                    "physical_end_distance_m": dist(start, end),
                }
            )

    (
        effective_configuration,
        wind_bases,
        surface_loads,
        line_loads,
        surface_sources,
        warnings,
    ) = _portal_frame_wind_actions(
        {"analytical_members": analytical_members},
        components=components,
        configuration=configuration,
        site=site,
    )

    assert warnings == []
    assert len(wind_bases) == 4
    assert len(surface_loads) == 16
    assert len(line_loads) == 16
    assert len(surface_sources) == 16
    assert {load.case_id for load in line_loads} == {
        "wind-plus-x",
        "wind-minus-x",
    }
    assert {load.coefficient_status for load in surface_loads} == {
        "working_conservative"
    }
    assert {case.role for case in effective_configuration.action_cases}.issuperset(
        {"wind_positive_x", "wind_negative_x"}
    )
    assert "load_combinations" not in type(effective_configuration).model_fields
    resolved = resolve_action_standard_pack(
        effective_configuration.action_standard_pack_id,
        effective_configuration.action_cases,
    )
    assert {combination.id for combination in resolved.load_combinations}.issuperset(
        {"SLS-G+WX+", "SLS-G+WX-", "ULS-1.2G+WX+", "ULS-1.2G+WX-"}
    )


def test_split_analytical_segments_share_one_physical_serviceability_check(
    tmp_path,
) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    configuration = StructuralProjectConfiguration.model_validate(
        default_structural_configuration()
    )
    original = _capture_from_structural_projection(
        execution.projections["structural"],
        project_name="split-purlin",
        configuration=configuration,
    ).model_dump(mode="python")
    analysis = original["analysis"]
    assert analysis is not None
    purlin = next(
        member for member in analysis["members"] if member["component_id"] == "P1"
    )
    purlin_length = dist(purlin["start"].values(), purlin["end"].values())
    midpoint = {
        axis: (purlin["start"][axis] + purlin["end"][axis]) / 2.0
        for axis in ("x", "y", "z")
    }
    first = deepcopy(purlin)
    second = deepcopy(purlin)
    first["id"] = "member:P1:segment:01"
    first["end"] = midpoint
    first["end_node_key"] = "physical:P1:split"
    second["id"] = "member:P1:segment:02"
    second["start"] = midpoint
    second["start_node_key"] = "physical:P1:split"
    analysis["members"] = [
        member for member in analysis["members"] if member["component_id"] != "P1"
    ] + [first, second]
    rewritten_line_loads = []
    for load in analysis["member_distributed_loads"]:
        if load["member_id"] != purlin["id"]:
            rewritten_line_loads.append(load)
            continue
        for index, member_id in enumerate(
            (first["id"], second["id"]),
            start=1,
        ):
            segment_load = deepcopy(load)
            segment_load["id"] = f"{load['id']}:segment:{index:02d}"
            segment_load["member_id"] = member_id
            segment_load["start_distance_m"] = 0.0
            segment_load["end_distance_m"] = purlin_length / 2.0
            rewritten_line_loads.append(segment_load)
    analysis["member_distributed_loads"] = rewritten_line_loads
    analysis["cross_section_verification"] = None
    analysis["member_stability_verification"] = None

    snapshot = solve_project_structural(
        ProjectStructuralCapture.model_validate(original)
    )
    purlin_checks = [
        check
        for check in snapshot.serviceability_checks
        if check.physical_member_id == "P1"
    ]

    assert len(purlin_checks) == 1
    assert purlin_checks[0].span_m == pytest.approx(purlin_length)
    assert purlin_checks[0].limit_mm == pytest.approx(purlin_length * 1000 / 250)
    assert purlin_checks[0].analytical_member_ids == [first["id"], second["id"]]


def test_physical_load_stations_are_mapped_onto_trimmed_solver_axis(tmp_path) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    projection = deepcopy(execution.projections["structural"])
    projected_purlin = next(
        member
        for member in projection["analytical_members"]
        if member["component_id"] == "P1"
    )
    analytical_length = dist(projected_purlin["start_m"], projected_purlin["end_m"])
    projected_purlin["physical_end_distance_m"] = analytical_length + 0.025
    configuration_data = default_structural_configuration()
    configuration_data["member_distributed_loads"] = [
        {
            "id": "trimmed-axis-load",
            "label": "Full physical purlin action",
            "component_id": "P1",
            "case_id": "dead",
            "start_distance_m": 0.0,
            "end_distance_m": analytical_length + 0.025,
            "start_force_kN_m": {"x": 0, "y": 0, "z": -0.1},
            "provenance": "Physical station mapping regression.",
        }
    ]
    configuration = StructuralProjectConfiguration.model_validate(configuration_data)

    capture = _capture_from_structural_projection(
        projection,
        project_name="trimmed-axis",
        configuration=configuration,
    )
    assert capture.analysis is not None
    mapped = next(
        load
        for load in capture.analysis.member_distributed_loads
        if load.id == "trimmed-axis-load"
    )

    assert mapped.end_distance_m == pytest.approx(analytical_length)


def test_product_authored_tension_member_behavior_reaches_analysis(tmp_path) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    projection = deepcopy(execution.projections["structural"])
    projected_purlin = next(
        member
        for member in projection["analytical_members"]
        if member["component_id"] == "P1"
    )
    product_key = projected_purlin["product_key"]
    structural_facet = next(
        facet
        for facet in projection["product_facets"]
        if facet["product_key"] == product_key
    )
    structural_facet["properties"].update(
        {
            "tension_only": True,
            "tension_capacity_status": "candidate",
            "tension_capacity_kN": 12.5,
            "tension_capacity_basis": "Candidate product tension evidence.",
            "end_fastener_count": 2,
            "end_connection_capacity_kN": 8.0,
            "end_connection_basis": "Candidate product connection evidence.",
        }
    )
    configuration = StructuralProjectConfiguration.model_validate(
        default_structural_configuration()
    )

    capture = _capture_from_structural_projection(
        projection,
        project_name="tension-member",
        configuration=configuration,
    )

    assert capture.analysis is not None
    declaration = next(
        member for member in capture.analysis.members if member.component_id == "P1"
    )
    assert declaration.tension_only is True
    assert declaration.tension_capacity_status == "candidate"
    assert declaration.tension_capacity_kN == pytest.approx(12.5)
    assert declaration.end_fastener_count == 2
    assert declaration.end_connection_capacity_kN == pytest.approx(8.0)
    assert capture.analysis.cross_section_verification is not None
    assert declaration.id not in capture.analysis.cross_section_verification.member_ids
    assert capture.analysis.member_stability_verification is not None
    assert all(
        segment.member_id != declaration.id
        for segment in capture.analysis.member_stability_verification.segments
    )


def test_pinned_physical_joint_maps_to_member_end_releases() -> None:
    restraints, releases, warnings, node_key = _endpoint_connection_effects(
        component_id="C1",
        endpoint="end",
        component_kinds={"C1": "member", "R1": "member"},
        endpoint_joints={
            ("C1", "end"): {
                "connection_id": "PIN1",
                "ports": [
                    {"component_id": "C1", "port": "end"},
                    {"component_id": "R1", "port": "start"},
                ],
                "transfers": ["force", "shear"],
                "analysis_model": "pinned",
                "stiffness_status": "unverified",
                "stiffness_basis": "Draft pin assumption.",
            }
        },
    )

    assert not any(restraints.model_dump().values())
    assert releases.model_dump() == {
        "dx": False,
        "dy": False,
        "dz": False,
        "rx": False,
        "ry": True,
        "rz": True,
    }
    assert node_key == "joint:PIN1"
    assert warnings == [
        "Connection PIN1 uses its pinned analysis model as a draft assumption "
        "(unverified): Draft pin assumption."
    ]


def test_connection_resistance_fails_closed_when_rendered_part_identity_drifts(
    tmp_path,
) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    projection = deepcopy(execution.projections["structural"])
    changed = next(
        component
        for component in projection["components"]
        if component["component_id"] == "KNEE1-B4"
    )
    changed["part_number"] = "WRONG-BOLT"
    configuration = StructuralProjectConfiguration.model_validate(
        default_structural_configuration()
    )
    capture = _capture_from_structural_projection(
        projection,
        project_name="identity-drift",
        configuration=configuration,
        configuration_revision=1,
        configuration_digest=configuration.configuration_digest,
    )

    snapshot = solve_project_structural(capture)
    check = next(
        item for item in snapshot.connection_checks if item.connection_id == "KNEE1"
    )

    assert check.status == "unsupported"
    assert check.identity_status == "fail"
    assert "WRONG-BOLT" in check.rendered_connector_part_numbers
    assert check.identity_mismatches


def test_explicit_topology_keeps_touching_unconnected_endpoints_separate() -> None:
    capture = ProjectStructuralCapture.model_validate(
        {
            "project_name": "separate-cantilevers",
            "design_hash": "a" * 64,
            "title": "Separate cantilevers",
            "authoring_mode": "generated",
            "components": [
                {
                    "id": "M1",
                    "label": "M1",
                    "kind": "member",
                    "visual_node_id": "M1",
                },
                {
                    "id": "M2",
                    "label": "M2",
                    "kind": "member",
                    "visual_node_id": "M2",
                },
            ],
            "connections": [],
            "loads": [],
            "load_paths": [],
            "analysis": {
                "materials": [
                    {
                        "id": "steel",
                        "label": "Steel",
                        "elastic_modulus_kN_m2": 200_000_000,
                        "shear_modulus_kN_m2": 80_000_000,
                        "poisson_ratio": 0.3,
                        "density_kg_m3": 7850,
                    }
                ],
                "sections": [
                    {
                        "id": "section",
                        "label": "Test section",
                        "area_m2": 0.001,
                        "iy_m4": 1e-6,
                        "iz_m4": 1e-6,
                        "torsion_j_m4": 1e-6,
                    }
                ],
                "members": [
                    {
                        "id": "member:M1",
                        "label": "M1",
                        "component_id": "M1",
                        "start": {"x": 0, "y": 0, "z": 0},
                        "end": {"x": 0, "y": 0, "z": 1},
                        "start_node_key": "ground:M1",
                        "end_node_key": "endpoint:M1:end",
                        "start_restraints": {
                            "dx": True,
                            "dy": True,
                            "dz": True,
                            "rx": True,
                            "ry": True,
                            "rz": True,
                        },
                        "section_id": "section",
                        "material_id": "steel",
                        "assumption": "Explicit topology test.",
                    },
                    {
                        "id": "member:M2",
                        "label": "M2",
                        "component_id": "M2",
                        "start": {"x": 1, "y": 0, "z": 0},
                        "end": {"x": 0, "y": 0, "z": 1},
                        "start_node_key": "ground:M2",
                        "end_node_key": "endpoint:M2:end",
                        "start_restraints": {
                            "dx": True,
                            "dy": True,
                            "dz": True,
                            "rx": True,
                            "ry": True,
                            "rz": True,
                        },
                        "section_id": "section",
                        "material_id": "steel",
                        "assumption": "Explicit topology test.",
                    },
                ],
                "load_cases": [{"id": "live", "label": "Live", "category": "live"}],
                "member_loads": [
                    {
                        "id": "load",
                        "label": "Load",
                        "member_id": "member:M1",
                        "case_id": "live",
                        "distance_m": 0.5,
                        "force": {"x": 0, "y": 1, "z": 0},
                        "provenance": "Topology test.",
                    }
                ],
                "load_combinations": [
                    {
                        "id": "SLS",
                        "label": "SLS",
                        "limit_state": "serviceability",
                        "factors": {"live": 1.0},
                    }
                ],
            },
            "capabilities": [],
            "warnings": [],
        }
    )

    snapshot = solve_project_structural(capture)

    assert len(snapshot.nodes) == 4
    touching_nodes = [
        node
        for node in snapshot.nodes
        if node.position.model_dump() == {"x": 0.0, "y": 0.0, "z": 1.0}
    ]
    assert len(touching_nodes) == 2
