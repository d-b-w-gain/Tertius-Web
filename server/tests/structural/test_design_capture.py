from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.structural.design_capture import (
    StructuralDeclarationError,
    parse_project_structural_capture,
)
from workflows.structural import structural_server


DESIGN = """
sheet_width = 762.0
sheet_length = 1200.0
wind_pressure = 0.8
sheet_area = sheet_width * sheet_length / 1000000.0

TERTIUS_STRUCTURAL = {
    "title": "Structural connection microcosm",
    "components": [
        {
            "id": "sheet",
            "label": "Roof sheet",
            "kind": "surface",
            "visual_node_id": "sheet",
        },
        {
            "id": "screws",
            "label": "Tek screws",
            "kind": "connector",
            "visual_node_id": "screws",
        },
        {
            "id": "purlin",
            "label": "C100 purlin",
            "kind": "member",
            "visual_node_id": "purlin",
        },
        {
            "id": "block",
            "label": "Concrete block",
            "kind": "ground",
            "visual_node_id": "block",
            "grounded": True,
        },
    ],
    "connections": [
        {
            "id": "sheet-purlin",
            "label": "Sheet to purlin",
            "from_component_id": "sheet",
            "to_component_id": "purlin",
            "connector_component_ids": ["screws"],
            "transfers": ["wind_normal", "force"],
        },
        {
            "id": "purlin-ground",
            "label": "Purlin to ground",
            "from_component_id": "purlin",
            "to_component_id": "block",
            "transfers": ["force", "shear", "moment"],
        },
    ],
    "loads": [
        {
            "id": "wind",
            "label": "Illustrative wind pressure",
            "case": "wind",
            "component_id": "sheet",
            "pressure_kPa": wind_pressure,
            "area_m2": sheet_area,
            "direction": {"x": 0, "y": -1, "z": 0},
            "provenance": "Illustrative parser fixture",
        },
    ],
}
"""


def test_static_capture_traces_wind_load_to_ground_without_running_design():
    capture = parse_project_structural_capture(DESIGN, project_name="structural_test")

    assert capture.project_name == "structural_test"
    assert capture.loads[0].area_m2 == pytest.approx(0.9144)
    assert capture.load_paths[0].status == "complete"
    assert capture.load_paths[0].component_ids == ["sheet", "purlin", "block"]
    assert capture.load_paths[0].connection_ids == ["sheet-purlin", "purlin-ground"]
    assert capture.load_paths[0].grounded_component_id == "block"
    assert capture.capabilities[2].status == "pending"
    assert capture.design_hash


def test_static_capture_reports_a_disconnected_load_path():
    source = DESIGN.replace(
        """        {
            "id": "purlin-ground",
            "label": "Purlin to ground",
            "from_component_id": "purlin",
            "to_component_id": "block",
            "transfers": ["force", "shear", "moment"],
        },
""",
        "",
    )

    capture = parse_project_structural_capture(source, project_name="structural_test")

    assert capture.load_paths[0].status == "blocked"
    assert capture.load_paths[0].grounded_component_id is None
    assert capture.capabilities[1].status == "blocked"


def test_static_capture_rejects_missing_component_references():
    source = DESIGN.replace('"to_component_id": "block"', '"to_component_id": "missing"')

    with pytest.raises(StructuralDeclarationError, match="missing ID 'missing'"):
        parse_project_structural_capture(source, project_name="structural_test")


def test_static_capture_rejects_executable_structural_declarations():
    source = "TERTIUS_STRUCTURAL = build_structural_model()"

    with pytest.raises(StructuralDeclarationError, match="unsupported expression Call"):
        parse_project_structural_capture(source, project_name="structural_test")


def test_active_capture_api_uses_the_authenticated_active_project(monkeypatch):
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="structural-capture-test",
        email="test@example.com",
    )
    project = type("ProjectStub", (), {"name": "structural_test"})()

    class RepositoryStub:
        def __init__(self, _db, tenant_id):
            assert tenant_id == context.tenant_id

        def files_for_runtime(self, project_name):
            assert project_name == "structural_test"
            return {"design.py": DESIGN}

    monkeypatch.setattr(structural_server, "get_active_project", lambda _db, _ctx: project)
    monkeypatch.setattr(structural_server, "ProjectRepository", RepositoryStub)
    structural_server.app.dependency_overrides[get_auth_context] = lambda: context
    structural_server.app.dependency_overrides[get_db] = lambda: object()
    try:
        with TestClient(structural_server.app) as client:
            response = client.get("/active/capture")
    finally:
        structural_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["project_name"] == "structural_test"
    assert response.json()["load_paths"][0]["status"] == "complete"
