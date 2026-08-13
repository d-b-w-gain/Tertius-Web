from __future__ import annotations

import pytest

from core.project_templates import (
    default_project_files,
    default_structural_configuration,
)
from core.structural.project_analysis import solve_project_structural
from core.structural.project_configuration import StructuralProjectConfiguration
from core.structural.contracts import ProjectStructuralCapture
from tertius.runner import execute_design
from workflows.structural.structural_server import (
    _capture_from_structural_projection,
    _endpoint_connection_effects,
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
    assert [connection.id for connection in capture.connections] == ["BASE1", "KNEE1"]
    assert len(snapshot.nodes) == 3
    assert len(snapshot.member_diagrams) == 2
    assert len(snapshot.reactions) == 1
    assert {result.member_id for result in snapshot.member_results} == {
        "member:C1",
        "member:P1",
    }
    assert max(result.max_moment_kNm for result in snapshot.member_results) > 0
    assert next(
        check for check in snapshot.serviceability_checks if check.member_id == "member:P1"
    ).status == "pass"
    assert snapshot.equilibrium.status == "pass"
    assert snapshot.source.analysis_configuration_revision == 1
    assert any("Connection KNEE1" in warning for warning in capture.warnings)

    procurement = execution.projections["procurement"]
    requirement_parts = {
        requirement["part_number"]
        for requirement in procurement["requirements"]
    }
    assert {
        "C20024",
        "C10019",
        "FAB-BP-150X150X10",
        "M12X100-ANCHOR-DEMO",
        "FAB-KG-180X180X6",
        "M12X30-BOLT-DEMO",
    }.issubset(requirement_parts)


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
        "rx": True,
        "ry": True,
        "rz": True,
    }
    assert node_key == "joint:PIN1"
    assert warnings == [
        "Connection PIN1 uses its pinned analysis model as a draft assumption "
        "(unverified): Draft pin assumption."
    ]


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
                "load_cases": [
                    {"id": "live", "label": "Live", "category": "live"}
                ],
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
