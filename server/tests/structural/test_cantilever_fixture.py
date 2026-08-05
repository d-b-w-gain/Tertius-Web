from __future__ import annotations

import json
import struct
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.workbench_access import STRUCTURAL_WORKBENCH_ROLE
from core.structural.cantilever_fixture import (
    BASE_NODE_ID,
    FREE_NODE_ID,
    MEMBER_ID,
    cantilever_glb,
    cantilever_snapshot,
    solve_cantilever,
)
from core.structural.contracts import StructuralSnapshot
from workflows.structural.structural_server import app as structural_app


def test_pynite_cantilever_matches_independent_beam_solution():
    solution = solve_cantilever()

    assert solution.reaction_fx_kN == pytest.approx(-1.0)
    assert solution.reaction_my_kNm == pytest.approx(-2.0)
    assert solution.base_my_kNm == pytest.approx(2.0)
    assert solution.base_shear_kN == pytest.approx(1.0)
    assert solution.tip_dx_mm == pytest.approx(1.600064, rel=1e-5)


def test_fixture_contract_links_graph_results_and_visual_identity():
    snapshot = cantilever_snapshot()

    assert snapshot.schema_version == "1.0"
    assert snapshot.mode == "fixture"
    assert {node.id for node in snapshot.nodes} == {BASE_NODE_ID, FREE_NODE_ID}
    assert snapshot.members[0].id == MEMBER_ID
    assert snapshot.members[0].visual_node_id == MEMBER_ID
    assert snapshot.member_results[0].member_id == MEMBER_ID
    assert snapshot.member_checks[0].member_id == MEMBER_ID
    assert snapshot.member_checks[0].utilisation == pytest.approx(0.8)
    assert snapshot.equilibrium.status == "pass"
    assert snapshot.solver.name == "PyNiteFEA"
    assert snapshot.solver.version == "2.4.1"
    assert snapshot.warnings[0].startswith("DEMONSTRATION FIXTURE")


def test_structural_contract_rejects_dangling_member_reference():
    payload = cantilever_snapshot().model_dump(mode="json")
    payload["members"][0]["end_node_id"] = "missing-node"

    with pytest.raises(ValidationError, match="member end node references missing ID"):
        StructuralSnapshot.model_validate(payload)


def test_fixture_glb_contains_the_same_stable_visual_ids():
    content = cantilever_glb()
    json_chunk_length, chunk_type = struct.unpack("<I4s", content[12:20])
    assert content[:4] == b"glTF"
    assert chunk_type == b"JSON"

    document = json.loads(content[20 : 20 + json_chunk_length].decode("utf-8"))
    names = {node.get("name") for node in document["nodes"]}
    assert {
        BASE_NODE_ID,
        FREE_NODE_ID,
        MEMBER_ID,
        "fixture-load-tip-x",
    }.issubset(names)


def test_structural_fixture_api_returns_contract_and_binary_model():
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="structural-fixture-test",
        email="test@example.com",
        roles=frozenset({STRUCTURAL_WORKBENCH_ROLE}),
    )
    structural_app.dependency_overrides[get_auth_context] = lambda: context
    try:
        with TestClient(structural_app) as client:
            response = client.get("/fixture/cantilever")
            model_response = client.get("/fixture/cantilever/model")
    finally:
        structural_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["members"][0]["visual_node_id"] == MEMBER_ID
    assert model_response.status_code == 200
    assert model_response.headers["content-type"] == "model/gltf-binary"
    assert model_response.content[:4] == b"glTF"


def test_structural_site_picker_exposes_region_conflict_and_qz_derivation():
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="structural-site-test",
        email="test@example.com",
        roles=frozenset({STRUCTURAL_WORKBENCH_ROLE}),
    )
    structural_app.dependency_overrides[get_auth_context] = lambda: context
    try:
        with TestClient(structural_app) as client:
            region_response = client.get(
                "/wind/region",
                params={
                    "latitude": -34.4125046,
                    "longitude": 150.8885637,
                },
            )
            site_response = client.post(
                "/wind/site",
                json={
                    "site_address": "14 Porter St, North Wollongong NSW 2500",
                    "latitude": -34.4125046,
                    "longitude": 150.8885637,
                    "region": "C",
                    "terrain_category": "3",
                    "importance_level": "2",
                    "annual_probability_uls": "1/500",
                    "reference_height_m": 1.6,
                    "direction_multiplier": 1.0,
                    "shielding_multiplier": 1.0,
                    "topographic_multiplier": 1.0,
                },
            )
            overlay_response = client.get("/wind/regions.geojson")
    finally:
        structural_app.dependency_overrides.clear()

    assert region_response.status_code == 200
    assert region_response.json()["region"] == "A2"
    assert site_response.status_code == 200
    payload = site_response.json()
    assert payload["selected_region"] == "C"
    assert payload["suggested_region"] == "A2"
    assert payload["region_conflict"] is True
    assert payload["q_z_kPa"] == pytest.approx(1.62084)
    assert overlay_response.status_code == 200
    assert overlay_response.json()["type"] == "FeatureCollection"
