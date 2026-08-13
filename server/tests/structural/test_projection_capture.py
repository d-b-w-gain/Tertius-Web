from __future__ import annotations

import pytest

from core.project_templates import (
    default_project_files,
    default_structural_configuration,
)
from core.structural.project_analysis import solve_project_structural
from core.structural.project_configuration import StructuralProjectConfiguration
from tertius.runner import execute_design
from workflows.structural.structural_server import _capture_from_structural_projection


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
    assert capture.analysis.members[0].start_restraints.model_dump() == {
        "dx": True,
        "dy": True,
        "dz": True,
        "rx": True,
        "ry": True,
        "rz": True,
    }
    assert [connection.id for connection in capture.connections] == ["BASE1"]
    assert len(snapshot.nodes) == 2
    assert len(snapshot.member_diagrams) == 1
    assert len(snapshot.reactions) == 1
    assert snapshot.member_results[0].max_moment_kNm == pytest.approx(0.72)
    assert snapshot.equilibrium.status == "pass"
    assert snapshot.source.analysis_configuration_revision == 1
