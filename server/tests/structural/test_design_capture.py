from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.compile_runtime import runtime_files_hash
from core.db import get_db
from core.structural.contracts import CompiledStructuralManifest
from core.structural.design_capture import (
    StructuralDeclarationError,
    _structural_declaration,
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

GENERATED_DESIGN = """
from tertius_structural import StructuralModel

sheet_width = 762.0
sheet_length = 1200.0
wind_pressure = 0.8
sheet_area = sheet_width * sheet_length / 1000000.0

structure = StructuralModel(title="Generated structural connection microcosm")
sheet = structure.surface(
    sheet_shape,
    id="sheet",
    label="Roof sheet",
)
screws = structure.connector(
    screw_shape,
    id="screws",
    label="Tek screws",
)
purlin = structure.member(
    purlin_shape,
    id="purlin",
    label="C100 purlin",
    part_number="C10019",
)
block = structure.ground(
    block_shape,
    id="block",
    label="Concrete block",
)
structure.connect(
    sheet,
    purlin,
    via=[screws],
    id="sheet-purlin",
    label="Sheet to purlin",
    transfers=["wind_normal", "force"],
)
structure.connect(
    purlin,
    block,
    id="purlin-ground",
    label="Purlin to ground",
    transfers=["force", "shear", "moment"],
)
structure.surface_load(
    sheet,
    id="wind",
    label="Illustrative wind pressure",
    case="wind",
    case_id="wind-inward",
    case_label="Inward wind pressure",
    pressure_kPa=wind_pressure,
    area_m2=sheet_area,
    direction=(0, -1, 0),
    provenance="Illustrative parser fixture",
)
structural_assembly = structure.assembly(
    [sheet, screws, purlin, block],
    label="structural-test",
)
TERTIUS_STRUCTURAL = structure.manifest()
"""


def test_static_capture_traces_wind_load_to_ground_without_running_design():
    capture = parse_project_structural_capture(DESIGN, project_name="structural_test")

    assert capture.project_name == "structural_test"
    assert capture.authoring_mode == "legacy"
    assert capture.loads[0].area_m2 == pytest.approx(0.9144)
    assert capture.load_paths[0].status == "complete"
    assert capture.load_paths[0].component_ids == ["sheet", "purlin", "block"]
    assert capture.load_paths[0].connection_ids == ["sheet-purlin", "purlin-ground"]
    assert capture.load_paths[0].grounded_component_id == "block"
    assert capture.capabilities[2].status == "pending"
    assert capture.design_hash


def test_generated_capture_uses_object_handles_and_traces_load_to_ground():
    capture = parse_project_structural_capture(
        GENERATED_DESIGN,
        project_name="structural_test",
    )

    assert capture.title == "Generated structural connection microcosm"
    assert capture.authoring_mode == "generated"
    assert [component.id for component in capture.components] == [
        "sheet",
        "screws",
        "purlin",
        "block",
    ]
    assert capture.connections[0].connector_component_ids == ["screws"]
    assert capture.loads[0].area_m2 == pytest.approx(0.9144)
    assert capture.loads[0].case_id == "case-wind-inward"
    assert capture.analysis is not None
    assert capture.analysis.load_cases[0].label == "Inward wind pressure"
    assert capture.load_paths[0].component_ids == ["sheet", "purlin", "block"]
    assert capture.capabilities[0].detail.startswith(
        "Generated structural authoring calls"
    )
    assert any(
        "UNREGISTERED ASSEMBLY MEMBERS FAIL" in item for item in capture.warnings
    )


def test_generated_capture_recomputes_site_wind_snapshot_and_links_coefficient():
    source = GENERATED_DESIGN.replace(
        'structure = StructuralModel(title="Generated structural connection microcosm")',
        '''structure = StructuralModel(title="Generated structural connection microcosm")
site_wind = structure.wind_action_basis(
    id="porter-wind",
    site_address="14 Porter St, North Wollongong NSW 2500",
    latitude=-34.4125046,
    longitude=150.8885637,
    region="A2",
    region_area="NSW",
    region_source="Geoscience Australia test fixture",
    region_approximate=True,
    region_status="suggested",
    standard="AS/NZS 1170.2:2021",
    table_version="AS1170.2-2021-starter-v1",
    table_status="starter",
    importance_level="2",
    annual_recurrence_interval_years=500,
    terrain_category="3",
    reference_height_m=1.6,
    regional_wind_speed_m_s=45.0,
    climate_change_multiplier=1.0,
    direction_multiplier=1.0,
    terrain_height_multiplier=0.75,
    shielding_multiplier=1.0,
    topographic_multiplier=1.0,
    site_wind_speed_m_s=33.75,
    q_z_kPa=0.683438,
    verifier_hash="6fd0fef70f0f",
    provenance="FBD site-wind calculation test fixture.",
)''',
    ).replace(
        '''structure.surface_load(
    sheet,
    id="wind",
    label="Illustrative wind pressure",
    case="wind",
    case_id="wind-inward",
    case_label="Inward wind pressure",
    pressure_kPa=wind_pressure,
    area_m2=sheet_area,
    direction=(0, -1, 0),
    provenance="Illustrative parser fixture",
)''',
        '''wind = structure.wind_surface_load(
    sheet,
    basis=site_wind,
    id="wind",
    label="Wind pressure",
    case_id="wind-inward",
    case_label="Inward wind pressure",
    net_pressure_coefficient=0.9,
    coefficient_status="assumed",
    area_m2=sheet_area,
    direction=(0, -1, 0),
    provenance="Test site pressure and explicit coefficient.",
)''',
    )

    capture = parse_project_structural_capture(
        source,
        project_name="structural_test",
    )

    assert capture.wind_action_bases[0].region == "A2"
    assert capture.wind_action_bases[0].q_z_kPa == pytest.approx(0.683438)
    assert capture.loads[0].pressure_kPa == pytest.approx(0.6150942)
    assert capture.loads[0].wind_basis_id == "porter-wind"
    assert capture.loads[0].net_pressure_coefficient == pytest.approx(0.9)
    assert not any("WIND ACTION BASIS DRIFT" in item for item in capture.warnings)


def test_generated_capture_rejects_unregistered_assembly_handles():
    source = GENERATED_DESIGN.replace(
        "[sheet, screws, purlin, block]",
        "[sheet, screws, purlin, new_purlin, block]",
    )

    with pytest.raises(
        StructuralDeclarationError,
        match="unregistered structural handle 'new_purlin'",
    ):
        parse_project_structural_capture(source, project_name="structural_test")


def test_generated_capture_rejects_registered_but_unconnected_members():
    source = GENERATED_DESIGN.replace(
        "block = structure.ground(",
        """orphan = structure.member(
    orphan_shape,
    id="orphan",
    label="Unconnected purlin",
)
block = structure.ground(""",
    ).replace(
        "[sheet, screws, purlin, block]",
        "[sheet, screws, purlin, orphan, block]",
    )

    with pytest.raises(
        StructuralDeclarationError,
        match=r"structural components have no declared connection: \['orphan'\]",
    ):
        parse_project_structural_capture(source, project_name="structural_test")


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
    source = DESIGN.replace(
        '"to_component_id": "block"', '"to_component_id": "missing"'
    )

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

    monkeypatch.setattr(
        structural_server, "get_active_project", lambda _db, _ctx: project
    )
    monkeypatch.setattr(
        structural_server,
        "get_latest_structural_manifest_artifact",
        lambda _db, _ctx, _project: None,
    )
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


def test_active_capture_api_prefers_current_compiled_structural_manifest(monkeypatch):
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="compiled-structural-capture-test",
        email="test@example.com",
    )
    project = SimpleNamespace(id=uuid4(), name="structural_test")
    files = {
        "catalog.json.py": '{"version":"2.0"}',
        "design.py": DESIGN,
    }
    compiled = CompiledStructuralManifest(
        source_hash=runtime_files_hash(files),
        design_hash=sha256(DESIGN.encode("utf-8")).hexdigest(),
        declaration=_structural_declaration(DESIGN),
    )

    class RepositoryStub:
        def __init__(self, _db, tenant_id):
            assert tenant_id == context.tenant_id

        def files_for_runtime(self, project_name):
            assert project_name == "structural_test"
            return files

    monkeypatch.setattr(
        structural_server, "get_active_project", lambda _db, _ctx: project
    )
    monkeypatch.setattr(
        structural_server,
        "get_latest_structural_manifest_artifact",
        lambda _db, _ctx, _project: SimpleNamespace(
            content=compiled.model_dump_json().encode("utf-8")
        ),
    )
    monkeypatch.setattr(structural_server, "ProjectRepository", RepositoryStub)
    structural_server.app.dependency_overrides[get_auth_context] = lambda: context
    structural_server.app.dependency_overrides[get_db] = lambda: object()
    try:
        with TestClient(structural_server.app) as client:
            response = client.get("/active/capture")
    finally:
        structural_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["capabilities"][0]["detail"].startswith(
        "Structural manifest resolved from the compiled design.py source closure"
    )


def test_active_capture_api_rejects_stale_compiled_structural_manifest(monkeypatch):
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="stale-structural-capture-test",
        email="test@example.com",
    )
    project = SimpleNamespace(id=uuid4(), name="structural_test")
    compiled = CompiledStructuralManifest(
        source_hash="a" * 64,
        design_hash="b" * 64,
        declaration=_structural_declaration(DESIGN),
    )

    class RepositoryStub:
        def __init__(self, _db, tenant_id):
            assert tenant_id == context.tenant_id

        def files_for_runtime(self, project_name):
            assert project_name == "structural_test"
            return {"design.py": DESIGN + "\n# changed\n"}

    monkeypatch.setattr(
        structural_server, "get_active_project", lambda _db, _ctx: project
    )
    monkeypatch.setattr(
        structural_server,
        "get_latest_structural_manifest_artifact",
        lambda _db, _ctx, _project: SimpleNamespace(
            content=compiled.model_dump_json().encode("utf-8")
        ),
    )
    monkeypatch.setattr(structural_server, "ProjectRepository", RepositoryStub)
    structural_server.app.dependency_overrides[get_auth_context] = lambda: context
    structural_server.app.dependency_overrides[get_db] = lambda: object()
    try:
        with TestClient(structural_server.app) as client:
            response = client.get("/active/capture")
    finally:
        structural_server.app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "Compile the active project" in response.json()["detail"]
