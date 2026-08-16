from __future__ import annotations

import json
from pathlib import Path

import build123d as bd
import pytest

from tertius import (
    ConnectionDefinition,
    ConnectionResistanceDefinition,
    DrawingFacet,
    PortPlacement,
    ProcurementFacet,
    ProductDefinition,
    StructuralFacet,
    TertiusRuntimeError,
    all_workbench_projections,
    managed_component,
    physical_connection,
)
from tertius.runner import execute_design, write_design_bundle
from tertius.session import compile_session


def member_product(part_number: str = "TEST-C100") -> ProductDefinition:
    return ProductDefinition(
        key=f"test-sections:{part_number}",
        label=f"Test Cee {part_number}",
        catalogue_id="test-sections",
        catalogue_revision="2026-08-13",
        catalogue_row={
            "part_number": part_number,
            "depth_mm": 100.0,
            "thickness_mm": 1.9,
            "area_mm2": 409.0,
        },
        geometry={"depth_mm": 100.0, "thickness_mm": 1.9},
        procurement=ProcurementFacet(
            part_number=part_number,
            manufacturer="Test Steel",
            material="G450 cold-formed steel",
        ),
        structural=StructuralFacet(
            kind="member",
            material={"grade": "G450", "fy_mpa": 450.0},
            section={"area_m2": 409e-6, "iy_m4": 142000e-12},
            evidence_status="verified",
            evidence_basis="Test catalogue fixture.",
        ),
        drawing=DrawingFacet(name=part_number, attributes={"section": "Cee"}),
        port_families={
            "start": ["test-bolted"],
            "end": ["test-bolted"],
            "*": ["test-bolted"],
        },
    )


def connector_product(part_number: str = "TEST-KB01") -> ProductDefinition:
    return ProductDefinition(
        key=f"test-connections:{part_number}",
        label="Test knee bracket",
        geometry={"plate_thickness_mm": 3.0},
        procurement=ProcurementFacet(
            part_number=part_number,
            manufacturer="Test Steel",
        ),
        structural=StructuralFacet(
            kind="connector",
            evidence_status="candidate",
            evidence_basis="Test connector fixture.",
        ),
        drawing=DrawingFacet(name=part_number),
    )


def managed_member(
    *,
    product: ProductDefinition,
    mark: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    extra_ports: dict[str, PortPlacement] | None = None,
) -> bd.Shape:
    length = sum((end[index] - start[index]) ** 2 for index in range(3)) ** 0.5
    shape = bd.Box(
        10, 10, length, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    shape = shape.moved(bd.Pos(X=start[0], Y=start[1], Z=start[2]))
    shape.label = mark
    return managed_component(
        shape,
        product=product,
        mark=mark,
        fabrication={"cut_length_mm": length, "ordered_length_mm": length},
        ports={
            "start": PortPlacement(start, (0, 0, -1)),
            "end": PortPlacement(end, (0, 0, 1)),
            **(extra_ports or {}),
        },
    )


def test_catalogue_product_is_deeply_immutable_and_digest_changes_with_identity() -> (
    None
):
    c100 = member_product("TEST-C100")
    with pytest.raises(TypeError):
        c100.geometry["depth_mm"] = 150.0  # type: ignore[index]
    with pytest.raises(TypeError):
        c100.catalogue_row["part_number"] = "TEST-C150"  # type: ignore[index]

    c150 = member_product("TEST-C150")
    assert c100.definition_digest != c150.definition_digest
    assert c100.catalogue_row_digest != c150.catalogue_row_digest
    payload = c100.payload()
    assert payload["catalogue"]["row"] == {
        "part_number": "TEST-C100",
        "depth_mm": 100.0,
        "thickness_mm": 1.9,
        "area_mm2": 409.0,
    }


def test_managed_component_requires_runner_owned_session() -> None:
    with pytest.raises(TertiusRuntimeError, match="active Tertius compile session"):
        managed_component(bd.Box(1, 1, 1), product=member_product())


def test_product_wildcard_accepts_fabricated_instance_ports() -> None:
    product = ProductDefinition(
        key="test.fabricated-member",
        label="Fabricated member",
        geometry={"kind": "member"},
        procurement=ProcurementFacet(part_number="TEST-FAB", unit="each"),
        structural=StructuralFacet(
            kind="member",
            section={
                "area_m2": 0.001,
                "iy_m4": 1e-6,
                "iz_m4": 1e-6,
                "torsion_j_m4": 1e-8,
            },
            material={
                "elastic_modulus_pa": 200e9,
                "shear_modulus_pa": 77e9,
                "poisson_ratio": 0.3,
                "density_kg_m3": 7850,
            },
        ),
        port_families={
            "start": ("test.connection",),
            "end": ("test.connection",),
            "*": ("test.connection",),
        },
    )

    with compile_session() as session:
        component = managed_component(
            bd.Box(10, 10, 100),
            product=product,
            ports={
                "start": PortPlacement((0, 0, 0), (0, 0, -1)),
                "end": PortPlacement((0, 0, 100), (0, 0, 1)),
                "fabricated:knee": PortPlacement((0, 0, 50), (1, 0, 0)),
            },
        )
        assert component.ports["fabricated:knee"].compatible_families == (
            "test.connection",
        )
        session.finalize(component)


def test_component_and_physical_connection_build_one_linked_graph() -> None:
    with compile_session() as session:
        column = managed_member(
            product=member_product(),
            mark="C1",
            start=(0, 0, 0),
            end=(0, 0, 1000),
        )
        rafter = managed_member(
            product=member_product(),
            mark="R1",
            start=(0, 0, 1000),
            end=(0, 0, 2000),
        )
        bracket = managed_component(
            bd.Box(30, 3, 100).moved(bd.Pos(Z=950)),
            product=connector_product(),
            mark="KB1",
        )
        bracket.label = "KB1 bracket"
        connection_shape = bd.Compound(  # type: ignore[call-overload]
            children=[bracket],
            label="C1-R1 knee",
        )
        connection = physical_connection(
            connection_shape,
            definition=ConnectionDefinition(
                key="test-knee",
                label="Test bolted knee",
                family="test-bolted",
                transfers=("force", "shear", "moment"),
                analysis_model="rigid_zone",
                stiffness_status="candidate",
                stiffness_basis="Test connection definition.",
            ),
            ports=(column.ports.end, rafter.ports.start),
            connector_components=(bracket,),
            mark="K1",
        )
        model = bd.Compound(  # type: ignore[call-overload]
            children=[column, rafter, connection],
            label="test-frame",
        )
        graph = session.finalize(model)
        projections = all_workbench_projections(graph, model=model)

    assert graph["schema_version"] == "1.0"
    assert [component["id"] for component in graph["components"]] == ["C1", "R1", "KB1"]
    column_end = next(
        port for port in graph["components"][0]["ports"] if port["name"] == "end"
    )
    assert column_end["x_direction"] == [1.0, 0.0, 0.0]
    assert column_end["engagement_length_mm"] == 0.0
    assert graph["connections"][0]["ports"] == [
        {"component_id": "C1", "port": "end"},
        {"component_id": "R1", "port": "start"},
    ]
    assert graph["connections"][0]["connector_component_ids"] == ["KB1"]
    assert graph["readiness"]["procurement_complete"] is True
    assert graph["readiness"]["structural_model_complete"] is True
    assert graph["compiled_design_digest"]
    assert model.tertius_compiled_design is graph

    procurement = projections["procurement"]
    structural = projections["structural"]
    drawing = projections["drawing"]
    assert {item["component_id"] for item in procurement["requirements"]} == {
        "C1",
        "R1",
        "KB1",
    }
    assert {item["compiled_design_digest"] for item in projections.values()} == {
        graph["compiled_design_digest"]
    }
    assert structural["analytical_members"][0]["start_m"] == [0.0, 0.0, 0.0]
    assert structural["joints"][0]["connection_id"] == "K1"
    assert structural["joints"][0]["connector_component_ids"] == ["KB1"]
    assert [item["mark"] for item in drawing["items"]] == ["C1", "R1", "KB1"]


def test_connected_fabricated_port_splits_the_analytical_member() -> None:
    with compile_session() as session:
        host = managed_member(
            product=member_product(),
            mark="HOST",
            start=(0, 0, 0),
            end=(0, 0, 1000),
            extra_ports={
                "fabricated:mid": PortPlacement((0, 0, 500), (1, 0, 0)),
            },
        )
        branch = managed_member(
            product=member_product(),
            mark="BRANCH",
            start=(0, 0, 500),
            end=(500, 0, 500),
        )
        bracket = managed_component(
            bd.Box(20, 20, 20).moved(bd.Pos(Z=490)),
            product=connector_product(),
            mark="MID-BRACKET",
        )
        connection = physical_connection(
            bd.Compound(children=[bracket]),  # type: ignore[call-overload]
            definition=ConnectionDefinition(
                key="test-mid",
                label="Test intermediate connection",
                family="test-bolted",
                transfers=("force", "shear"),
                analysis_model="pinned",
                stiffness_status="candidate",
                stiffness_basis="Test intermediate connection.",
            ),
            ports=(host.ports["fabricated:mid"], branch.ports.start),
            connector_components=(bracket,),
            mark="MID",
        )
        model = bd.Compound(  # type: ignore[call-overload]
            children=[host, branch, connection]
        )
        graph = session.finalize(model)
        structural = all_workbench_projections(graph, model=model)["structural"]

    host_segments = [
        member
        for member in structural["analytical_members"]
        if member["component_id"] == "HOST"
    ]
    assert [member["id"] for member in host_segments] == [
        "member:HOST:segment:01",
        "member:HOST:segment:02",
    ]
    assert host_segments[0]["end_m"] == [0.0, 0.0, 0.5]
    assert host_segments[0]["end_node_key"] == "joint:MID"
    assert host_segments[1]["start_node_key"] == "joint:MID"
    branch_member = next(
        member
        for member in structural["analytical_members"]
        if member["component_id"] == "BRANCH"
    )
    assert branch_member["start_node_key"] == "joint:MID"


def test_product_change_propagates_to_every_workbench_projection() -> None:
    projection_sets: list[dict[str, dict]] = []
    for part_number in ("TEST-C100", "TEST-C150"):
        with compile_session() as session:
            member = managed_member(
                product=member_product(part_number),
                mark="M1",
                start=(0, 0, 0),
                end=(0, 0, 1000),
            )
            model = bd.Compound(children=[member])  # type: ignore[call-overload]
            graph = session.finalize(model)
            projection_sets.append(all_workbench_projections(graph, model=model))

    first, second = projection_sets
    assert first["procurement"]["requirements"][0]["part_number"] == "TEST-C100"
    assert second["procurement"]["requirements"][0]["part_number"] == "TEST-C150"
    assert (
        first["structural"]["components"][0]["product_key"]
        != second["structural"]["components"][0]["product_key"]
    )
    for projection_name in ("procurement", "structural", "drawing", "bounds"):
        assert (
            first[projection_name]["projection_digest"]
            != second[projection_name]["projection_digest"]
        )


def test_touching_members_do_not_implicitly_connect() -> None:
    with compile_session() as session:
        column = managed_member(
            product=member_product(),
            mark="C1",
            start=(0, 0, 0),
            end=(0, 0, 1000),
        )
        rafter = managed_member(
            product=member_product(),
            mark="R1",
            start=(0, 0, 1000),
            end=(0, 0, 2000),
        )
        graph = session.finalize(
            bd.Compound(children=[column, rafter])  # type: ignore[call-overload]
        )

    assert graph["readiness"]["structural_model_complete"] is False
    assert [item["component_id"] for item in graph["diagnostics"]] == ["C1", "R1"]


def test_unmanaged_geometry_renders_but_blocks_workbench_completeness() -> None:
    with compile_session() as session:
        raw = bd.Box(10, 10, 10)
        graph = session.finalize(raw)

    assert graph["components"] == []
    assert len(graph["unmanaged_geometry"]) == 1
    assert graph["readiness"]["mechanical_graph_valid"] is True
    assert graph["readiness"]["procurement_complete"] is False
    assert graph["readiness"]["structural_model_complete"] is False


def test_unmanaged_nonstructural_geometry_does_not_invalidate_managed_structure() -> (
    None
):
    with compile_session() as session:
        column = managed_member(
            product=member_product(),
            mark="C1",
            start=(0, 0, 0),
            end=(0, 0, 1000),
        )
        ground_product = ProductDefinition(
            key="test:ground",
            label="Test ground",
            classification="reference",
            geometry={"kind": "ground"},
            structural=StructuralFacet(kind="ground"),
            port_families={"base": ["test-bolted"]},
        )
        ground = managed_component(
            bd.Box(10, 10, 10),
            product=ground_product,
            mark="G1",
            ports={"base": PortPlacement((0, 0, 0), (0, 0, 1))},
        )
        connector = managed_component(
            bd.Box(5, 5, 5),
            product=connector_product(),
            mark="B1",
        )
        connection = physical_connection(
            bd.Compound(children=[connector]),  # type: ignore[call-overload]
            definition=ConnectionDefinition(
                key="test-base",
                label="Test base",
                family="test-bolted",
                transfers=("force", "shear"),
                analysis_model="pinned",
                stiffness_status="candidate",
                stiffness_basis="Test fixture.",
            ),
            ports=(column.ports.start, ground.ports.base),
            connector_components=(connector,),
        )
        raw_furniture = bd.Box(20, 20, 20).moved(bd.Pos(X=100))
        graph = session.finalize(
            bd.Compound(  # type: ignore[call-overload]
                children=[column, ground, connection, raw_furniture]
            )
        )

    assert graph["readiness"]["structural_model_complete"] is True
    assert graph["readiness"]["procurement_complete"] is False


def test_registered_component_must_appear_once_in_model() -> None:
    with compile_session() as session:
        member = managed_member(
            product=member_product(),
            mark="C1",
            start=(0, 0, 0),
            end=(0, 0, 1000),
        )
        with pytest.raises(TertiusRuntimeError, match="missing from model"):
            session.finalize(bd.Box(2, 2, 2))

    assert member.ports.end.name == "end"


def test_registered_component_cannot_be_reused_twice_in_model() -> None:
    with compile_session() as session:
        member = managed_member(
            product=member_product(),
            mark="C1",
            start=(0, 0, 0),
            end=(0, 0, 1000),
        )
        duplicate = bd.Box(1, 1, 1).moved(bd.Pos(X=100))
        duplicate.tertius_component_token = member.tertius_component_token
        with pytest.raises(TertiusRuntimeError, match="more than once"):
            session.finalize(
                bd.Compound(  # type: ignore[call-overload]
                    children=[member, duplicate]
                )
            )


def test_connection_rejects_incompatible_ports() -> None:
    incompatible = ProductDefinition(
        key="test:incompatible",
        label="Incompatible member",
        geometry={"depth_mm": 100},
        procurement=ProcurementFacet(part_number="TEST-X"),
        structural=StructuralFacet(
            kind="member",
            section={"area_m2": 1e-4},
        ),
        port_families={"start": ["welded"], "end": ["welded"]},
    )
    with compile_session():
        first = managed_member(
            product=incompatible,
            mark="M1",
            start=(0, 0, 0),
            end=(0, 0, 100),
        )
        second = managed_member(
            product=incompatible,
            mark="M2",
            start=(0, 0, 100),
            end=(0, 0, 200),
        )
        connector = managed_component(
            bd.Box(1, 1, 1),
            product=connector_product(),
            mark="K1",
        )
        with pytest.raises(TertiusRuntimeError, match="incompatible"):
            physical_connection(
                bd.Compound(children=[connector]),  # type: ignore[call-overload]
                definition=ConnectionDefinition(
                    key="bolted",
                    label="Bolted",
                    family="test-bolted",
                    transfers=("force",),
                    analysis_model="pinned",
                ),
                ports=(first.ports.end, second.ports.start),
                connector_components=(connector,),
            )


def test_connection_rejects_unexplained_member_endpoint_gap() -> None:
    with compile_session():
        first = managed_member(
            product=member_product(),
            mark="M1",
            start=(0, 0, 0),
            end=(0, 0, 100),
        )
        second = managed_member(
            product=member_product(),
            mark="M2",
            start=(0, 0, 110),
            end=(0, 0, 210),
        )
        connector = managed_component(
            bd.Box(1, 1, 1),
            product=connector_product(),
            mark="K1",
        )
        with pytest.raises(TertiusRuntimeError, match="10 mm apart"):
            physical_connection(
                bd.Compound(children=[connector]),  # type: ignore[call-overload]
                definition=ConnectionDefinition(
                    key="bolted",
                    label="Bolted",
                    family="test-bolted",
                    transfers=("force", "shear", "moment"),
                    analysis_model="rigid",
                    maximum_port_offset_mm=1.0,
                ),
                ports=(first.ports.end, second.ports.start),
                connector_components=(connector,),
            )


def test_component_port_can_belong_to_only_one_physical_connection() -> None:
    with compile_session():
        first = managed_member(
            product=member_product(),
            mark="M1",
            start=(0, 0, 0),
            end=(0, 0, 100),
        )
        second = managed_member(
            product=member_product(),
            mark="M2",
            start=(0, 0, 100),
            end=(0, 0, 200),
        )
        third = managed_member(
            product=member_product(),
            mark="M3",
            start=(0, 0, 100),
            end=(100, 0, 100),
        )
        first_connector = managed_component(
            bd.Box(1, 1, 1),
            product=connector_product("TEST-KB01"),
            mark="K1",
        )
        second_connector = managed_component(
            bd.Box(1, 1, 1),
            product=connector_product("TEST-KB02"),
            mark="K2",
        )
        definition = ConnectionDefinition(
            key="bolted",
            label="Bolted",
            family="test-bolted",
            transfers=("force", "shear", "moment"),
            analysis_model="rigid",
        )
        physical_connection(
            bd.Compound(children=[first_connector]),  # type: ignore[call-overload]
            definition=definition,
            ports=(first.ports.end, second.ports.start),
            connector_components=(first_connector,),
        )
        with pytest.raises(TertiusRuntimeError, match="already belongs"):
            physical_connection(
                bd.Compound(children=[second_connector]),  # type: ignore[call-overload]
                definition=definition,
                ports=(first.ports.end, third.ports.start),
                connector_components=(second_connector,),
            )


def test_port_frame_rejects_parallel_x_direction() -> None:
    with pytest.raises(ValueError, match="perpendicular"):
        PortPlacement(
            (0, 0, 0),
            (0, 0, 1),
            x_direction=(0, 0, 2),
            engagement_length_mm=20,
        )


def test_verified_connection_resistance_requires_hashed_complete_capacities() -> None:
    with pytest.raises(ValueError, match="hashed source"):
        ConnectionResistanceDefinition(
            pack_id="test-pack",
            version="1",
            status="verified",
            basis="Test evidence.",
            connector_part_numbers=("TEST-KB01",),
            source="Test source",
            design_axial_capacity_kN=10,
            design_shear_capacity_kN=10,
            design_moment_capacity_kNm=1,
        )

    resistance = ConnectionResistanceDefinition(
        pack_id="test-pack",
        version="1",
        status="verified",
        basis="Test evidence.",
        connector_part_numbers=("TEST-KB01",),
        source="Test source",
        source_sha256="a" * 64,
        design_axial_capacity_kN=10,
        design_shear_capacity_kN=10,
        design_moment_capacity_kNm=1,
    )
    with pytest.raises(ValueError, match="missing capacities for moment"):
        ConnectionDefinition(
            key="test-knee",
            label="Test knee",
            family="test-bolted",
            transfers=("force", "shear", "moment"),
            analysis_model="rigid",
            resistance=ConnectionResistanceDefinition(
                pack_id="incomplete-pack",
                version="1",
                status="verified",
                basis="Incomplete test evidence.",
                connector_part_numbers=("TEST-KB01",),
                source="Test source",
                source_sha256="b" * 64,
                design_axial_capacity_kN=10,
                design_shear_capacity_kN=10,
            ),
        )
    payload = ConnectionDefinition(
        key="test-knee",
        label="Test knee",
        family="test-bolted",
        transfers=("force", "shear", "moment"),
        analysis_model="rigid",
        resistance=resistance,
    ).payload()
    resistance_payload = payload["resistance"]
    assert isinstance(resistance_payload, dict)
    assert resistance_payload["pack_id"] == "test-pack"


def test_runner_requires_model_and_rejects_removed_manifest_exports(
    tmp_path: Path,
) -> None:
    (tmp_path / "design.py").write_text("part = bd.Box(1, 1, 1)\n", encoding="utf-8")
    with pytest.raises(TertiusRuntimeError, match="must assign.*model"):
        execute_design(tmp_path)

    (tmp_path / "design.py").write_text(
        "model = bd.Box(1, 1, 1)\nTERTIUS_STRUCTURAL = {}\n",
        encoding="utf-8",
    )
    with pytest.raises(TertiusRuntimeError, match="removed Tertius manifest exports"):
        execute_design(tmp_path)


def test_structural_projection_carries_product_authored_tension_member_behavior(
    tmp_path: Path,
) -> None:
    (tmp_path / "design.py").write_text(
        """import build123d as bd
from tertius import PortPlacement, ProcurementFacet, ProductDefinition, StructuralFacet, managed_component

product = ProductDefinition(
    key="test:strap",
    label="Test strap",
    geometry={"profile": "flat_strip"},
    procurement=ProcurementFacet(part_number="TEST-STRAP"),
    structural=StructuralFacet(
        kind="member",
        material={"label": "steel"},
        section={"area_m2": 3e-5},
        properties={
            "tension_only": True,
            "tension_capacity_status": "candidate",
            "end_fastener_count": 2,
            "tension_capacity_basis": "Candidate strap evidence.",
            "end_connection_basis": "Candidate end connection.",
        },
    ),
    port_families={"start": ["bolted"], "end": ["bolted"]},
)
model = managed_component(
    bd.Box(30, 1, 1000),
    product=product,
    mark="BR1",
    role="cross brace strap",
    ports={
        "start": PortPlacement((0, 0, 0), (0, 0, -1)),
        "end": PortPlacement((0, 0, 1000), (0, 0, 1)),
    },
)
""",
        encoding="utf-8",
    )

    execution = execute_design(tmp_path)
    member = execution.projections["structural"]["analytical_members"][0]

    assert member["tension_only"] is True
    assert member["compression_only"] is False
    assert member["tension_capacity_status"] == "candidate"
    assert member["end_fastener_count"] == 2
    assert member["tension_capacity_basis"] == "Candidate strap evidence."
    assert member["end_connection_basis"] == "Candidate end connection."


def test_runner_uses_only_model_and_ignores_other_global_shapes(tmp_path: Path) -> None:
    (tmp_path / "design.py").write_text(
        "helper = bd.Box(50, 50, 50)\nmodel = bd.Box(1, 2, 3)\n",
        encoding="utf-8",
    )
    execution = execute_design(tmp_path)

    bounds = execution.model.bounding_box()
    assert bounds.max.X - bounds.min.X == pytest.approx(1.0)
    assert execution.compiled_design["unmanaged_geometry"]
    assert set(execution.projections) == {
        "procurement",
        "structural",
        "drawing",
        "bounds",
    }
    written = write_design_bundle(tmp_path / "bundle", execution)
    assert set(written) == {
        "compiled_design",
        "procurement",
        "structural",
        "drawing",
        "bounds",
    }
    assert all(path.is_file() for path in written.values())
    json.dumps(execution.compiled_design, allow_nan=False)


def test_runner_reserves_tertius_namespace(tmp_path: Path) -> None:
    (tmp_path / "design.py").write_text("model = bd.Box(1, 1, 1)\n", encoding="utf-8")
    (tmp_path / "tertius.py").write_text("# shadow\n", encoding="utf-8")
    with pytest.raises(TertiusRuntimeError, match="reserved Tertius runtime namespace"):
        execute_design(tmp_path)
