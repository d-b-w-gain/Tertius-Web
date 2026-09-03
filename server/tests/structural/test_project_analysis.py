from __future__ import annotations

from copy import deepcopy
from math import pi, sqrt
from types import SimpleNamespace

import pytest
import build123d as bd

from core.site_definition import apply_site_definition, default_site_definition
from core.structural.authoring_runtime import (
    StructuralMemberGeometry,
    StructuralModel,
)
from core.structural.contracts import (
    AnalyticalMemberDeclaration,
    AnchorGroupCheck,
    BoltedSheetInterfaceCheck,
    ConnectionCheck,
    DesignComponent,
    DesignConnection,
    MemberStabilityComparison,
    ProjectStructuralCapture,
    Restraints,
    SectionCatalogReference,
    SectionProperties,
    StructuralMaterial,
    TensionMemberCheck,
    Vector3,
)
from core.structural.design_capture import (
    capture_project_structural_declaration,
    parse_project_structural_capture,
)
from core.structural.project_analysis import (
    _analysis_base_model_matches,
    _bracing_load_path_traces,
    _calculated_anchored_fixture_resistance,
    _calculated_direct_anchored_sheet_resistance,
    _connection_checks,
    _off_axis_load_path,
    _released_rotational_datum_restraints,
    _released_node_rotational_axes,
    _relative_transverse_deflection_mm,
    _rafter_stability_applicability_checks,
    _select_calculated_connection_resistance,
    _stability_scope_comparisons,
    _tension_member_checks,
    solve_project_structural,
)


def _test_member(
    *,
    member_id: str,
    component_id: str,
    start_z: float,
    end_z: float,
    start_restraints: Restraints | None = None,
    end_restraints: Restraints | None = None,
) -> AnalyticalMemberDeclaration:
    return AnalyticalMemberDeclaration(
        id=member_id,
        label=member_id,
        component_id=component_id,
        start=Vector3(x=0, y=0, z=start_z),
        end=Vector3(x=0, y=0, z=end_z),
        start_restraints=start_restraints or Restraints(),
        end_restraints=end_restraints or Restraints(),
        section_id="section",
        material_id="material",
        assumption="Structural regression fixture.",
    )


def test_segmented_column_base_match_uses_lowest_physical_endpoint() -> None:
    pinned = Restraints(dx=True, dy=True, dz=True)
    analysis = SimpleNamespace(
        stability=SimpleNamespace(
            analysis_base_model="perfectly_pinned",
            column_component_ids=["column"],
            eaves_member_ids=["column-segment-2"],
        ),
        members=[
            _test_member(
                member_id="column-segment-1",
                component_id="column",
                start_z=0,
                end_z=1,
                start_restraints=pinned,
            ),
            _test_member(
                member_id="column-segment-2",
                component_id="column",
                start_z=1,
                end_z=2,
            ),
        ],
    )

    assert _analysis_base_model_matches(analysis)

    analysis.members[0] = analysis.members[0].model_copy(
        update={"start_restraints": Restraints(dx=True, dy=True)}
    )
    assert not _analysis_base_model_matches(analysis)


def test_rafter_applicability_keeps_independent_physical_lengths_separate() -> None:
    members = [
        _test_member(
            member_id="rafter-a-1",
            component_id="rafter-a",
            start_z=0,
            end_z=1,
        ),
        _test_member(
            member_id="rafter-a-2",
            component_id="rafter-a",
            start_z=1,
            end_z=2,
        ),
        _test_member(
            member_id="rafter-b-1",
            component_id="rafter-b",
            start_z=0,
            end_z=1,
        ),
        _test_member(
            member_id="rafter-b-2",
            component_id="rafter-b",
            start_z=1,
            end_z=2,
        ),
    ]
    analysis = SimpleNamespace(
        stability=SimpleNamespace(rafter_member_ids=[member.id for member in members]),
        members=members,
        sections=[
            SectionProperties(
                id="section",
                label="Test section",
                area_m2=1e-3,
                iy_m4=1e-6,
                iz_m4=2e-6,
                torsion_j_m4=1e-8,
            )
        ],
        materials=[
            StructuralMaterial(
                id="material",
                label="Test steel",
                elastic_modulus_kN_m2=200_000_000,
                shear_modulus_kN_m2=80_000_000,
                poisson_ratio=0.3,
                density_kg_m3=7850,
            )
        ],
    )
    demands = {
        ("positive-x", "rafter-a"): 10.0,
        ("negative-x", "rafter-a"): 12.0,
        ("positive-x", "rafter-b"): 8.0,
        ("negative-x", "rafter-b"): 9.0,
    }

    checks = _rafter_stability_applicability_checks(analysis, demands)

    assert [check.component_id for check in checks] == ["rafter-a", "rafter-b"]
    assert [check.length_m for check in checks] == pytest.approx([2.0, 2.0])
    expected_ncr = pi**2 * (200_000_000 * 2e-6) / 2.0**2
    assert [check.elastic_critical_load_kN for check in checks] == pytest.approx(
        [expected_ncr, expected_ncr]
    )
    assert checks[0].design_axial_kN == pytest.approx(12.0)


def test_relative_transverse_deflection_removes_rigid_body_chord_motion() -> None:
    assert _relative_transverse_deflection_mm(
        (3.0, 6.0, -2.0),
        (1.0, 5.0, -3.0),
        (5.0, 7.0, -1.0),
        0.5,
    ) == pytest.approx(0.0)

    assert _relative_transverse_deflection_mm(
        (3.0, 8.0, 2.0),
        (1.0, 5.0, -3.0),
        (5.0, 7.0, -1.0),
        0.5,
    ) == pytest.approx(sqrt(20.0))


def test_shared_pin_stabilizes_only_rotations_with_zero_member_stiffness() -> None:
    pinned = Restraints(ry=True, rz=True)
    local_x_along_global_z = (
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    local_x_along_global_y = (
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, -1.0),
    )

    assert _released_node_rotational_axes(
        [
            (local_x_along_global_z, pinned),
            (local_x_along_global_y, pinned),
        ]
    ) == ("rx",)

    identity = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    assert _released_node_rotational_axes(
        [(identity, Restraints())]
    ) == ()


def test_axis_aligned_torsion_chain_gets_one_zero_energy_rotation_datum() -> None:
    identity = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    pinned_bending = Restraints(ry=True, rz=True)
    free = {
        "left": {axis: False for axis in ("dx", "dy", "dz", "rx", "ry", "rz")},
        "right": {axis: False for axis in ("dx", "dy", "dz", "rx", "ry", "rz")},
    }

    assert _released_rotational_datum_restraints(
        [("left", "right", identity, pinned_bending, pinned_bending)],
        free,
    ) == (("left", "rx"),)

    free["right"]["rx"] = True
    assert _released_rotational_datum_restraints(
        [("left", "right", identity, pinned_bending, pinned_bending)],
        free,
    ) == ()


def test_bracing_load_path_traces_both_rendered_ends_to_ground() -> None:
    components = [
        DesignComponent(
            id="foundation-left",
            label="Left footing",
            kind="ground",
            visual_node_id="foundation-left-node",
            grounded=True,
        ),
        DesignComponent(
            id="collector-left",
            label="Left column",
            kind="member",
            visual_node_id="collector-left-node",
        ),
        DesignComponent(
            id="brace",
            label="Wall strap",
            kind="member",
            visual_node_id="brace-node",
        ),
        DesignComponent(
            id="collector-right",
            label="Right column",
            kind="member",
            visual_node_id="collector-right-node",
        ),
        DesignComponent(
            id="foundation-right",
            label="Right footing",
            kind="ground",
            visual_node_id="foundation-right-node",
            grounded=True,
        ),
        DesignComponent(
            id="screws-left",
            label="Left screws",
            kind="connector",
            visual_node_id="screws-left-node",
        ),
        DesignComponent(
            id="screws-right",
            label="Right screws",
            kind="connector",
            visual_node_id="screws-right-node",
        ),
    ]
    connections = [
        DesignConnection(
            id="brace-left",
            label="Brace left end",
            from_component_id="brace",
            to_component_id="collector-left",
            connector_component_ids=["screws-left"],
            transfers=["force", "shear"],
        ),
        DesignConnection(
            id="brace-right",
            label="Brace right end",
            from_component_id="brace",
            to_component_id="collector-right",
            connector_component_ids=["screws-right"],
            transfers=["force", "shear"],
        ),
        DesignConnection(
            id="left-ground",
            label="Left base",
            from_component_id="collector-left",
            to_component_id="foundation-left",
            transfers=["force", "shear", "moment"],
        ),
        DesignConnection(
            id="right-ground",
            label="Right base",
            from_component_id="collector-right",
            to_component_id="foundation-right",
            transfers=["force", "shear", "moment"],
        ),
    ]
    capture = ProjectStructuralCapture(
        project_name="test",
        design_hash="0" * 64,
        title="Bracing load path test",
        authoring_mode="generated",
        components=components,
        connections=connections,
        loads=[],
        load_paths=[],
        capabilities=[],
        warnings=[],
    )
    analysis = SimpleNamespace(
        members=[
            SimpleNamespace(
                id="brace-axis",
                component_id="brace",
                tension_only=True,
            )
        ]
    )
    tension_check = TensionMemberCheck(
        member_id="brace-axis",
        label="Brace axis",
        capacity_status="verified",
        member_capacity_status="verified",
        connection_capacity_status="verified",
        governing_combination_id="ULS-1.2G+WX+",
        tension_demand_kN=4.2,
        status="pass",
        basis="Typed Stage 8 load-path test fixture.",
    )

    passing_connections = [
        ConnectionCheck(
            connection_id=connection.id,
            label=connection.label,
            status="pass",
            evidence_status="verified",
            pack_id="typed-load-path-fixture",
            pack_version="1",
            identity_status="pass",
            axial_demand_kN=1.0,
            shear_demand_kN=1.0,
            moment_demand_kNm=0.0,
            basis="Typed passing connection fixture.",
        )
        for connection in connections
    ]
    unverified_trace = _bracing_load_path_traces(
        capture,
        analysis,
        [tension_check],
    )[0]
    assert unverified_trace.status == "candidate"
    assert any("no demand/resistance check" in item for item in unverified_trace.blockers)

    traces = _bracing_load_path_traces(
        capture,
        analysis,
        [tension_check],
        passing_connections,
    )

    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == "pass"
    assert trace.component_ids == [
        "foundation-left",
        "collector-left",
        "screws-left",
        "brace",
        "screws-right",
        "collector-right",
        "foundation-right",
    ]
    assert trace.connection_ids == [
        "left-ground",
        "brace-left",
        "brace-right",
        "right-ground",
    ]
    assert trace.grounded_component_ids == [
        "foundation-left",
        "foundation-right",
    ]
    assert trace.blockers == []


def test_tension_connection_uses_rendered_fastener_product_test_evidence() -> None:
    brace_section = SectionProperties(
        id="strap",
        label="30 x 1 mm strap",
        area_m2=30e-6,
        iy_m4=2.5e-12,
        iz_m4=2.25e-9,
        torsion_j_m4=2.5e-12,
        tension_width_mm=30.0,
        tension_thickness_mm=1.0,
        tension_hole_diameter_mm=5.5,
        tension_holes_in_critical_section=2,
        tension_force_distribution_factor=1.0,
        end_fastener_nominal_diameter_mm=5.0,
        end_fastener_spacing_mm=15.0,
        end_fastener_edge_distance_mm=20.0,
    )
    support_section = SectionProperties(
        id="support",
        label="1.2 mm support sheet",
        area_m2=258e-6,
        iy_m4=4.32e-7,
        iz_m4=8.92e-8,
        torsion_j_m4=1.24e-10,
        tension_thickness_mm=1.2,
    )
    brace_material = StructuralMaterial(
        id="g450",
        label="G450",
        elastic_modulus_kN_m2=200e6,
        shear_modulus_kN_m2=76.923e6,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        yield_strength_MPa=450.0,
        tensile_strength_MPa=480.0,
    )
    support_material = StructuralMaterial(
        id="g500",
        label="G500",
        elastic_modulus_kN_m2=200e6,
        shear_modulus_kN_m2=76.923e6,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        yield_strength_MPa=500.0,
        tensile_strength_MPa=550.0,
    )
    declaration = SimpleNamespace(
        id="brace-axis",
        label="Brace axis",
        component_id="brace",
        tension_only=True,
        section_id="strap",
        material_id="g450",
        end_fastener_count=2,
        assumption="Tension-only physical strap.",
        start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=1.0, y=0.0, z=0.0),
        start_node_key="joint:brace-left",
        end_node_key="joint:brace-right",
    )
    support_left = SimpleNamespace(
        id="support-left-axis",
        component_id="support-left",
        tension_only=False,
        section_id="support",
        material_id="g500",
        start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=0.0, y=0.0, z=1.0),
        start_node_key=None,
        end_node_key=None,
    )
    support_right = SimpleNamespace(
        id="support-right-axis",
        component_id="support-right",
        tension_only=False,
        section_id="support",
        material_id="g500",
        start=SimpleNamespace(x=1.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=1.0, y=0.0, z=1.0),
        start_node_key=None,
        end_node_key=None,
    )
    analysis = SimpleNamespace(
        load_combinations=[
            SimpleNamespace(id="ULS-WIND", limit_state="ultimate", purpose="design")
        ],
        members=[declaration, support_left, support_right],
        sections=[brace_section, support_section],
        materials=[brace_material, support_material],
    )
    model = SimpleNamespace(
        members={"brace-axis": SimpleNamespace(axial=lambda _x, _combo: -0.4)}
    )
    fastener_ids = ("s1", "s2", "e1", "e2")
    fastener_components = {
        component_id: DesignComponent(
            id=component_id,
            label="Buildex Smooth Top Tek",
            kind="connector",
            visual_node_id=component_id,
            part_number="6-311-0695-5MP",
            product_key="buildex:smooth-top-tek:6-311-0695-5mp",
            product_definition_digest="d" * 64,
            structural_evidence_status="verified",
            structural_evidence_basis="Buildex PDS 31195-PDS Issue 2.",
            structural_properties={
                "nominal_diameter_mm": 5.0,
                "tested_single_shear_strength_kN": 5.75,
                "test_evidence_source": "Buildex PDS 31195-PDS, Issue 2",
                "test_evidence_revision": "Issue 2, 5 July 2017",
                "test_evidence_url": "https://example.test/buildex-pds.pdf",
            },
        )
        for component_id in fastener_ids
    }
    connections = [
        DesignConnection(
            id="brace-left",
            label="Brace left",
            from_component_id="brace",
            to_component_id="support-left",
            connector_component_ids=["s1", "s2"],
            transfers=["force"],
        ),
        DesignConnection(
            id="brace-right",
            label="Brace right",
            from_component_id="brace",
            to_component_id="support-right",
            connector_component_ids=["e1", "e2"],
            transfers=["force"],
        ),
    ]

    check = _tension_member_checks(
        model,
        analysis,
        connections,
        fastener_components,
    )[0]

    assert check.status == "pass"
    assert check.connection_capacity_status == "verified"
    assert check.fastener_shear_qualification_status == "pass"
    assert check.fastener_tested_single_shear_strength_kN == pytest.approx(5.75)
    assert check.fastener_required_single_shear_strength_kN is not None
    assert check.fastener_required_single_shear_strength_kN < 5.75
    assert check.end_fastener_part_numbers == ["6-311-0695-5MP"]
    assert check.end_connection_capacity_kN is not None

    connection_checks = _connection_checks(
        model,
        analysis,
        connections,
        fastener_components,
        [check],
    )
    assert [connection.status for connection in connection_checks] == ["pass", "pass"]
    assert all(
        connection.pack_id == "as_nzs_4600_2005_a1_tension_end_connection"
        for connection in connection_checks
    )
    assert all(
        connection.design_axial_capacity_kN
        == pytest.approx(check.end_connection_capacity_kN)
        for connection in connection_checks
    )


def test_tension_segment_uses_its_two_node_connections_not_every_component_joint():
    brace_section = SectionProperties(
        id="strap",
        label="38 x 1.2 strap",
        area_m2=45.6e-6,
        iy_m4=5.472e-12,
        iz_m4=5.4872e-9,
        torsion_j_m4=2.1888e-11,
        tension_width_mm=38.0,
        tension_thickness_mm=1.2,
        tension_hole_diameter_mm=6.3,
        tension_holes_in_critical_section=2,
        tension_force_distribution_factor=1.0,
        end_fastener_nominal_diameter_mm=6.3,
        end_fastener_spacing_mm=19.0,
        end_fastener_edge_distance_mm=30.0,
    )
    support_section = SectionProperties(
        id="support",
        label="1.2 mm support sheet",
        area_m2=258e-6,
        iy_m4=4.32e-7,
        iz_m4=8.92e-8,
        torsion_j_m4=1.24e-10,
        tension_thickness_mm=1.2,
    )
    material = StructuralMaterial(
        id="g450",
        label="G450",
        elastic_modulus_kN_m2=200e6,
        shear_modulus_kN_m2=76.923e6,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        yield_strength_MPa=450.0,
        tensile_strength_MPa=480.0,
    )
    declaration = SimpleNamespace(
        id="brace-segment",
        label="Brace segment",
        component_id="brace",
        tension_only=True,
        section_id="strap",
        material_id="g450",
        end_fastener_count=2,
        assumption="Tension-only physical strap segment.",
        start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=0.5, y=0.0, z=0.0),
        start_node_key="joint:brace-left",
        end_node_key="joint:brace-web",
    )
    supports = [
        SimpleNamespace(
            id=f"support-{name}-axis",
            component_id=f"support-{name}",
            tension_only=False,
            section_id="support",
            material_id="g450",
            start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            end=SimpleNamespace(x=0.0, y=0.0, z=1.0),
            start_node_key=None,
            end_node_key=None,
        )
        for name in ("left", "web", "right")
    ]
    analysis = SimpleNamespace(
        load_combinations=[
            SimpleNamespace(id="ULS-WIND", limit_state="ultimate", purpose="design")
        ],
        members=[declaration, *supports],
        sections=[brace_section, support_section],
        materials=[material],
    )
    model = SimpleNamespace(
        members={"brace-segment": SimpleNamespace(axial=lambda _x, _combo: -0.4)}
    )
    connection_specs = (
        ("brace-left", "support-left", ("l1", "l2")),
        ("brace-web", "support-web", ("w1", "w2")),
        ("brace-right", "support-right", ("r1", "r2")),
    )
    connections = [
        DesignConnection(
            id=connection_id,
            label=connection_id,
            from_component_id="brace",
            to_component_id=support_id,
            connector_component_ids=list(fastener_ids),
            transfers=["force"],
        )
        for connection_id, support_id, fastener_ids in connection_specs
    ]
    fasteners = {
        fastener_id: DesignComponent(
            id=fastener_id,
            label="Buildex 14-20x22 Metal Tek",
            kind="connector",
            visual_node_id=fastener_id,
            part_number="6-311-3038-5C4",
            product_key="buildex:metal-tek:6-311-3038-5c4",
            product_definition_digest="d" * 64,
            structural_evidence_status="verified",
            structural_evidence_basis="Buildex 31293-PDS.",
            structural_properties={
                "nominal_diameter_mm": 6.3,
                "tested_single_shear_strength_kN": 11.2,
                "test_evidence_source": "Buildex 31293-PDS",
                "test_evidence_revision": "31293-PDS",
                "test_evidence_url": (
                    "https://www.buildex.com.au/products/roofing-cladding/"
                    "24-fixing-to-metal-teks/110-metal-teks-cladding"
                ),
            },
        )
        for _connection_id, _support_id, fastener_ids in connection_specs
        for fastener_id in fastener_ids
    }

    check = _tension_member_checks(
        model,
        analysis,
        connections,
        fasteners,
    )[0]

    assert check.status == "pass", check.model_dump()
    assert check.rendered_end_connection_count == 2
    assert check.rendered_end_fastener_counts == [2, 2]


def test_off_axis_load_path_traces_surface_fasteners_and_collector_to_ground():
    components = {
        component.id: component
        for component in (
            DesignComponent(
                id="sheet",
                label="Roof sheet",
                kind="surface",
                visual_node_id="sheet-node",
            ),
            DesignComponent(
                id="screws",
                label="Roof screws",
                kind="connector",
                visual_node_id="screw-node",
            ),
            DesignComponent(
                id="purlin",
                label="Roof purlin",
                kind="member",
                visual_node_id="purlin-node",
            ),
            DesignComponent(
                id="portal",
                label="Portal",
                kind="member",
                visual_node_id="portal-node",
            ),
            DesignComponent(
                id="foundation",
                label="Foundation",
                kind="ground",
                visual_node_id="foundation-node",
                grounded=True,
            ),
        )
    }
    connections = [
        DesignConnection(
            id="sheet-purlin",
            label="Sheet screw line",
            from_component_id="sheet",
            to_component_id="purlin",
            connector_component_ids=["screws"],
            transfers=["wind_normal", "force", "shear"],
        ),
        DesignConnection(
            id="purlin-portal",
            label="Purlin cleat",
            from_component_id="purlin",
            to_component_id="portal",
            transfers=["force", "shear"],
        ),
        DesignConnection(
            id="portal-foundation",
            label="Portal base",
            from_component_id="portal",
            to_component_id="foundation",
            transfers=["force", "shear", "moment"],
        ),
    ]

    path = _off_axis_load_path("purlin", components, connections)

    assert path["status"] == "candidate"
    assert path["source_component_ids"] == ["sheet"]
    assert path["source_connection_ids"] == ["sheet-purlin"]
    assert path["collector_component_ids"] == [
        "purlin",
        "portal",
        "foundation",
    ]
    assert path["collector_connection_ids"] == [
        "purlin-portal",
        "portal-foundation",
    ]
    assert path["grounded_component_id"] == "foundation"


@pytest.mark.parametrize(
    ("signed_axial_kN", "expected_anchor_status", "expected_connection_status"),
    [(-0.40, "pass", "unsupported"), (-1.20, "fail", "fail")],
)
def test_ground_connection_resolves_exact_anchor_product_and_signed_uplift(
    signed_axial_kN: float,
    expected_anchor_status: str,
    expected_connection_status: str,
) -> None:
    anchor_properties = {
        "anchor_resistance_pack_id": "manufacturer_working_load_anchor_group",
        "anchor_resistance_pack_version": "1",
        "anchor_product_part_number": "AS12100WGM",
        "anchor_source_status": "verified",
        "anchor_source": "Ramset SARB ANZ Edition 3 brick and block anchoring",
        "anchor_source_sha256": "b" * 64,
        "anchor_reference_substrate_type": "concrete_block",
        "anchor_reference_embedment_mm": 60.0,
        "anchor_single_tension_capacity_kN": 1.15,
        "anchor_single_shear_capacity_kN": 2.10,
        "anchor_required_edge_distance_mm": 35.0,
        "anchor_required_spacing_mm": 35.0,
    }
    components = {
        "column": DesignComponent(
            id="column",
            label="Column",
            kind="member",
            visual_node_id="column",
        ),
        "foundation": DesignComponent(
            id="foundation",
            label="Concrete block foundation",
            kind="ground",
            visual_node_id="foundation",
            grounded=True,
            structural_properties={
                "anchor_substrate_type": "concrete_block",
                "anchor_substrate_status": "verified",
            },
        ),
        **{
            f"anchor-{index}": DesignComponent(
                id=f"anchor-{index}",
                label="Ramset anchor",
                kind="connector",
                visual_node_id=f"anchor-{index}",
                part_number="AS12100WGM",
                product_key="ramset:wercs-ankascrew:as12100wgm",
                product_definition_digest="d" * 64,
                structural_evidence_status="verified",
                structural_properties=anchor_properties,
                fabrication={
                    "anchor_installed_effective_embedment_mm": 88.0,
                    "anchor_minimum_edge_distance_mm": 50.0,
                    "anchor_minimum_spacing_mm": 35.0,
                },
            )
            for index in (1, 2)
        },
    }
    declaration = SimpleNamespace(
        id="column-axis",
        component_id="column",
        tension_only=False,
        start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=0.0, y=0.0, z=2.4),
        start_node_key="joint:base",
        end_node_key=None,
    )
    analysis = SimpleNamespace(
        load_combinations=[
            SimpleNamespace(id="ULS-WIND", limit_state="ultimate", purpose="design")
        ],
        members=[declaration],
    )
    member = SimpleNamespace(
        axial=lambda _x, _combo: signed_axial_kN,
        shear=lambda _axis, _x, _combo: 0.20,
    )
    connection = DesignConnection(
        id="base",
        label="Column base",
        from_component_id="column",
        to_component_id="foundation",
        connector_component_ids=["anchor-1", "anchor-2"],
        transfers=["force", "shear"],
    )

    check = _connection_checks(
        SimpleNamespace(members={"column-axis": member}),
        analysis,
        [connection],
        components,
    )[0]

    assert check.anchor_group is not None
    assert check.anchor_group.status == expected_anchor_status
    assert check.status == expected_connection_status
    assert check.anchor_group.anchor_count == 2
    assert check.anchor_group.tension_demand_kN == pytest.approx(
        max(0.0, -signed_axial_kN)
    )
    assert check.anchor_group.interaction_utilisation == pytest.approx(
        max(0.0, -signed_axial_kN) / 1.15 + sqrt(0.20**2 + 0.20**2) / 2.10
    )


def test_complete_pinned_anchored_fixture_closes_verified_force_path() -> None:
    fixture = DesignComponent(
        id="fixture",
        label="Specified-grade steel base fixture",
        kind="connector",
        visual_node_id="fixture",
        part_number="SHED-C100-ONS-BASE-6",
        product_key="shed:base-fixture",
        product_definition_digest="f" * 64,
        structural_evidence_status="verified",
        structural_properties={
            "anchored_fixture_capacity_pack_id": (
                "specified_grade_pinned_steel_fixture"
            ),
            "anchored_fixture_capacity_pack_version": "1",
            "anchored_fixture_thickness_mm": 6.0,
            "anchored_fixture_effective_width_mm": 40.0,
            "anchored_fixture_yield_strength_MPa": 250.0,
            "anchored_fixture_tensile_strength_MPa": 410.0,
            "source": "Specified-grade fabricated base fixture source",
            "source_sha256": "a" * 64,
        },
    )
    connection = DesignConnection(
        id="base",
        label="Pinned column base",
        from_component_id="column",
        to_component_id="foundation",
        connector_component_ids=["fixture"],
        transfers=["force", "shear"],
    )
    anchor_group = AnchorGroupCheck(
        status="pass",
        evidence_status="verified",
        pack_id="manufacturer_working_load_anchor_group",
        pack_version="1",
        anchor_part_number="AS12100WGM",
        anchor_count=2,
        effective_anchor_count=1.0,
        substrate_type="concrete_block",
        substrate_status="verified",
        tension_demand_kN=0.4,
        shear_demand_kN=0.2,
        tension_capacity_kN=1.15,
        shear_capacity_kN=2.1,
        interaction_utilisation=0.45,
        source="Ramset verified product data",
        source_sha256="b" * 64,
        basis="Exact installed anchor group passes.",
    )
    bolted_sheet_interface = BoltedSheetInterfaceCheck(
        status="pass",
        evidence_status="verified",
        pack_id="as_nzs_4600_2005_a1_bolted_sheet_interface",
        pack_version="1",
        bolt_part_number="PB1230HS",
        bolt_count=4,
        connected_member_id="column",
        connected_sheet_part_number="C10019",
        fixture_part_number="SHED-C100-ONS-BASE-6",
        fixture_capacity_status="verified",
        resultant_shear_demand_kN=0.5,
        design_bolt_shear_capacity_kN=12.0,
        design_sheet_bearing_capacity_kN=8.0,
        design_sheet_tearout_capacity_kN=6.0,
        governing_capacity_kN=6.0,
        governing_utilisation=0.5 / 6.0,
        source="AS/NZS 4600 bolted-sheet calculation",
        source_sha256="c" * 64,
        basis="Exact PB1230HS connected-sheet interface passes.",
    )

    result = _calculated_anchored_fixture_resistance(
        connection=connection,
        components={"fixture": fixture},
        anchor_group=anchor_group,
        bolted_sheet_interface=bolted_sheet_interface,
        resultant_force_demand_kN=0.5,
        moment_demand_kNm=0.0,
    )

    assert result is not None
    assert result["status"] == "pass"
    assert result["evidence_status"] == "verified"
    assert result["stiffness_status"] == "verified"
    assert result["design_force_capacity_kN"] == pytest.approx(6.0)
    assert result["governing_utilisation"] == pytest.approx(0.45)

    moment_result = _calculated_anchored_fixture_resistance(
        connection=connection,
        components={"fixture": fixture},
        anchor_group=anchor_group,
        bolted_sheet_interface=bolted_sheet_interface,
        resultant_force_demand_kN=0.5,
        moment_demand_kNm=0.1,
    )
    assert moment_result is not None
    assert moment_result["status"] == "unsupported"
    assert any("cannot be credited with moment" in item for item in moment_result["blockers"])


def test_direct_anchor_checks_cold_formed_web_and_anchor_path() -> None:
    anchor = DesignComponent(
        id="anchor",
        label="Ramset AS12100WGM",
        kind="connector",
        visual_node_id="anchor",
        part_number="AS12100WGM",
        product_key="ramset:wercs-ankascrew:as12100wgm",
        product_definition_digest="d" * 64,
        structural_evidence_status="verified",
        structural_properties={
            "anchor_resistance_pack_id": "manufacturer_working_load_anchor_group",
            "anchor_nominal_diameter_mm": 12.0,
            "anchor_source": "Ramset verified anchor source",
            "anchor_source_sha256": "b" * 64,
        },
        fabrication={
            "sheet_hole_type": "standard_round",
            "sheet_hole_diameter_mm": 14.0,
            "minimum_sheet_edge_distance_mm": 50.0,
            "minimum_sheet_spacing_mm": 2000.0,
        },
    )
    components = {
        "ledger": DesignComponent(
            id="ledger",
            label="C10019 ledger",
            kind="member",
            visual_node_id="ledger",
        ),
        "ground": DesignComponent(
            id="ground",
            label="Block foundation",
            kind="ground",
            visual_node_id="ground",
            grounded=True,
        ),
        "anchor": anchor,
    }
    connection = DesignConnection(
        id="ledger-ground",
        label="Direct floor-ledger anchor",
        from_component_id="ledger",
        to_component_id="ground",
        connector_component_ids=["anchor"],
        transfers=["force", "shear"],
    )
    section = SectionProperties(
        id="c10019",
        label="C10019",
        area_m2=409e-6,
        iy_m4=1.0e-7,
        iz_m4=1.0e-6,
        torsion_j_m4=4.92e-10,
        tension_thickness_mm=1.9,
    )
    material = StructuralMaterial(
        id="g450",
        label="G450",
        elastic_modulus_kN_m2=200e6,
        shear_modulus_kN_m2=80e6,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        yield_strength_MPa=450.0,
        tensile_strength_MPa=480.0,
    )
    analysis = SimpleNamespace(
        members=[
            SimpleNamespace(
                component_id="ledger",
                section_id="c10019",
                material_id="g450",
                analytical_role="physical",
            )
        ],
        sections=[section],
        materials=[material],
    )
    anchor_group = AnchorGroupCheck(
        status="pass",
        evidence_status="verified",
        pack_id="manufacturer_working_load_anchor_group",
        pack_version="1",
        anchor_part_number="AS12100WGM",
        anchor_count=1,
        effective_anchor_count=1.0,
        substrate_type="concrete_block",
        substrate_status="verified",
        tension_demand_kN=0.0,
        shear_demand_kN=0.2,
        tension_capacity_kN=1.15,
        shear_capacity_kN=2.1,
        interaction_utilisation=0.2 / 2.1,
        source="Ramset verified anchor source",
        source_sha256="b" * 64,
        basis="Exact anchor interaction passes.",
    )

    result = _calculated_direct_anchored_sheet_resistance(
        connection=connection,
        components=components,
        analysis=analysis,
        anchor_group=anchor_group,
        resultant_force_demand_kN=0.2,
        moment_demand_kNm=0.0,
    )

    assert result is not None
    assert result["status"] == "pass"
    assert result["design_force_capacity_kN"] == pytest.approx(2.1)
    assert result["stiffness_status"] == "verified"


def test_base_connection_resolves_bolted_cold_formed_sheet_interface() -> None:
    bolt_properties = {
        "bolted_sheet_fastener_pack_id": (
            "as_nzs_4600_2005_a1_bolted_sheet_interface"
        ),
        "bolted_sheet_fastener_pack_version": "1",
        "nominal_diameter_mm": 12.0,
        "bolt_tensile_strength_MPa": 830.0,
        "bolt_minor_area_mm2": 76.2,
        "washers_under_head_and_nut": True,
        "source": "Lysaght Zeds and Cees guide",
        "source_sha256": "a" * 64,
    }
    components = {
        "column": DesignComponent(
            id="column",
            label="C10019 column",
            kind="member",
            visual_node_id="column",
            part_number="C10019",
        ),
        "foundation": DesignComponent(
            id="foundation",
            label="Foundation",
            kind="ground",
            visual_node_id="foundation",
            grounded=True,
        ),
        "fixture": DesignComponent(
            id="fixture",
            label="Fabricated on-slab cleat",
            kind="connector",
            visual_node_id="fixture",
            part_number="SHED-C100-ONS-BASE-6",
            structural_properties={
                "anchored_fixture_capacity_pack_id": (
                    "specified_grade_pinned_steel_fixture"
                ),
            },
        ),
        **{
            f"bolt-{index}": DesignComponent(
                id=f"bolt-{index}",
                label="PB1230HS bolt kit",
                kind="connector",
                visual_node_id=f"bolt-{index}",
                part_number="PB1230HS",
                product_key="lysaght:pb1230hs",
                product_definition_digest="d" * 64,
                structural_evidence_status="verified",
                structural_properties=bolt_properties,
                fabrication={
                    "sheet_hole_diameter_mm": 14.0,
                    "sheet_hole_type": "standard_round",
                    "minimum_bolt_spacing_mm": 40.0,
                    "minimum_sheet_edge_distance_mm": 31.0,
                },
            )
            for index in range(1, 5)
        },
    }
    declaration = SimpleNamespace(
        id="column-axis",
        component_id="column",
        tension_only=False,
        analytical_role="physical",
        section_id="c10019",
        material_id="g450",
        start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=0.0, y=0.0, z=2.4),
        start_node_key="joint:base",
        end_node_key=None,
    )
    upper_segment = deepcopy(declaration)
    upper_segment.id = "column-axis-upper"
    upper_segment.start_node_key = "joint:knee"
    section = SectionProperties(
        id="c10019",
        label="C10019",
        area_m2=409e-6,
        iy_m4=142000e-12,
        iz_m4=673000e-12,
        torsion_j_m4=492e-12,
        catalog=SectionCatalogReference(
            catalog_id="lysaght-zc-v2",
            catalog_version="2",
            section_key="C10019",
            source="Lysaght guide",
            record_sha256="c" * 64,
            axis_mapping={"local_y_inertia": "Iy", "local_z_inertia": "Ix"},
            properties={"validated": True, "t_mm": 1.9},
        ),
    )
    material = StructuralMaterial(
        id="g450",
        label="G450",
        elastic_modulus_kN_m2=200e6,
        shear_modulus_kN_m2=80e6,
        poisson_ratio=0.3,
        density_kg_m3=7850.0,
        yield_strength_MPa=450.0,
        tensile_strength_MPa=480.0,
    )
    analysis = SimpleNamespace(
        load_combinations=[
            SimpleNamespace(id="ULS-WIND", limit_state="ultimate", purpose="design")
        ],
        members=[declaration, upper_segment],
        sections=[section],
        materials=[material],
    )
    member = SimpleNamespace(
        axial=lambda _x, _combo: 1.0,
        shear=lambda _axis, _x, _combo: 0.20,
    )
    connection = DesignConnection(
        id="base",
        label="Column base",
        from_component_id="column",
        to_component_id="foundation",
        connector_component_ids=[
            "fixture",
            "bolt-1",
            "bolt-2",
            "bolt-3",
            "bolt-4",
        ],
        transfers=["force", "shear"],
    )

    check = _connection_checks(
        SimpleNamespace(members={"column-axis": member}),
        analysis,
        [connection],
        components,
    )[0]

    assert check.status == "unsupported"
    assert check.bolted_sheet_interface is not None
    assert check.bolted_sheet_interface.status == "pass"
    assert check.bolted_sheet_interface.bolt_count == 4
    assert check.bolted_sheet_interface.connected_sheet_part_number == "C10019"
    assert check.bolted_sheet_interface.fixture_capacity_status == "not_checked"
    assert check.bolted_sheet_interface.resultant_shear_demand_kN == pytest.approx(
        sqrt(1.0**2 + 0.20**2 + 0.20**2)
    )
    assert check.bolted_sheet_interface.sheet_bearing_status == "pass"
    assert check.bolted_sheet_interface.sheet_tearout_status == "pass"


def test_complete_bolted_cleat_calculates_eccentric_group_and_stiffness() -> None:
    bolt_properties = {
        "bolted_sheet_fastener_pack_id": (
            "as_nzs_4600_2005_a1_bolted_sheet_interface"
        ),
        "bolted_sheet_fastener_pack_version": "1",
        "nominal_diameter_mm": 12.0,
        "bolt_tensile_strength_MPa": 830.0,
        "bolt_minor_area_mm2": 76.2,
        "washers_under_head_and_nut": False,
        "source": "Lysaght Zeds and Cees guide",
        "source_sha256": "a" * 64,
    }
    components = {
        "column": DesignComponent(
            id="column",
            label="C10019 column",
            kind="member",
            visual_node_id="column",
            part_number="C10019",
        ),
        "support": DesignComponent(
            id="support",
            label="C10019 support",
            kind="member",
            visual_node_id="support",
            part_number="C10019",
        ),
        "fixture": DesignComponent(
            id="fixture",
            label="100AC",
            kind="connector",
            visual_node_id="fixture",
            part_number="100AC",
            product_key="lysaght:100ac",
            product_definition_digest="f" * 64,
            structural_evidence_status="verified",
            structural_properties={
                "connection_capacity_pack_id": (
                    "as_nzs_4600_2005_a1_bolted_cleat"
                ),
                "connection_capacity_pack_version": "1",
                "source": "Lysaght Zeds and Cees guide, pages 17-20",
                "source_sha256": "a" * 64,
                "fasteners_per_interface": 2,
                "fastener_coordinates_mm": [[-20.0, 0.0], [20.0, 0.0]],
                "connected_sheet_hole_diameter_mm": 14.0,
                "connected_sheet_hole_type": "standard_round",
                "minimum_bolt_spacing_mm": 40.0,
                "minimum_sheet_edge_distance_mm": 35.0,
                "maximum_connection_slip_mm": 2.0,
                "fixture_thickness_mm": 8.0,
                "fixture_yield_strength_MPa": 250.0,
                "fixture_tensile_strength_MPa": 410.0,
            },
        ),
        **{
            f"bolt-{index}": DesignComponent(
                id=f"bolt-{index}",
                label="PB1230HS bolt kit",
                kind="connector",
                visual_node_id=f"bolt-{index}",
                part_number="PB1230HS",
                product_key="lysaght:pb1230hs",
                product_definition_digest="d" * 64,
                structural_evidence_status="verified",
                structural_properties=bolt_properties,
            )
            for index in range(1, 5)
        },
    }
    declaration = SimpleNamespace(
        id="column-axis",
        component_id="column",
        tension_only=False,
        analytical_role="physical",
        section_id="c10019",
        material_id="g450",
        start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=0.0, y=0.0, z=2.4),
        start_node_key="joint:cleat",
        end_node_key=None,
    )
    support_declaration = SimpleNamespace(
        id="support-axis",
        component_id="support",
        tension_only=False,
        analytical_role="physical",
        section_id="c10019",
        material_id="g450",
        start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        end=SimpleNamespace(x=1.0, y=0.0, z=0.0),
        start_node_key="joint:cleat",
        end_node_key=None,
    )
    analysis = SimpleNamespace(
        load_combinations=[
            SimpleNamespace(id="ULS-WIND", limit_state="ultimate", purpose="design")
        ],
        members=[declaration, support_declaration],
        sections=[
            SectionProperties(
                id="c10019",
                label="C10019",
                area_m2=409e-6,
                iy_m4=142000e-12,
                iz_m4=673000e-12,
                torsion_j_m4=492e-12,
                catalog=SectionCatalogReference(
                    catalog_id="lysaght-zc-v2",
                    catalog_version="2",
                    section_key="C10019",
                    source="Lysaght guide",
                    record_sha256="c" * 64,
                    axis_mapping={"local_y_inertia": "Iy", "local_z_inertia": "Ix"},
                    properties={"validated": True, "t_mm": 1.9},
                ),
            )
        ],
        materials=[
            StructuralMaterial(
                id="g450",
                label="G450",
                elastic_modulus_kN_m2=200e6,
                shear_modulus_kN_m2=80e6,
                poisson_ratio=0.3,
                density_kg_m3=7850.0,
                yield_strength_MPa=450.0,
                tensile_strength_MPa=480.0,
            )
        ],
    )
    member = SimpleNamespace(
        axial=lambda _x, _combo: 1.0,
        shear=lambda _axis, _x, _combo: 0.20,
        moment=lambda _axis, _x, _combo: 0.10,
    )
    connection = DesignConnection(
        id="cleat",
        label="Calculated 100AC cleat",
        from_component_id="column",
        to_component_id="support",
        connector_component_ids=[
            "fixture",
            "bolt-1",
            "bolt-2",
            "bolt-3",
            "bolt-4",
        ],
        transfers=["force", "shear", "moment"],
    )

    check = _connection_checks(
        SimpleNamespace(members={"column-axis": member, "support-axis": member}),
        analysis,
        [connection],
        components,
    )[0]

    assert check.status == "pass"
    assert check.evidence_status == "verified"
    assert check.pack_id == "as_nzs_4600_2005_a1_bolted_cleat"
    assert check.design_shear_capacity_kN is not None
    assert check.design_moment_capacity_kNm is not None
    assert check.governing_utilisation is not None
    assert check.governing_utilisation < 1.0
    assert check.stiffness_status == "verified"


def test_exact_self_drilling_screw_group_calculates_connected_sheet_bearing() -> None:
    components = {
        component.id: component
        for component in (
            DesignComponent(
                id="stud",
                label="C10012 stud",
                kind="member",
                visual_node_id="stud",
            ),
            DesignComponent(
                id="track",
                label="C10012 track",
                kind="member",
                visual_node_id="track",
            ),
            *(
                DesignComponent(
                    id=f"screw-{index}",
                    label="Buildex 14-20x22 Metal Tek",
                    kind="connector",
                    visual_node_id=f"screw-{index}",
                    part_number="6-311-3038-5C4",
                    product_key="buildex:metal-tek:6-311-3038-5c4",
                    product_definition_digest="b" * 64,
                    structural_evidence_status="verified",
                    structural_evidence_basis="Buildex 31293-PDS.",
                    structural_properties={
                        "fastener_type": "self_drilling_screw",
                        "nominal_diameter_mm": 6.3,
                        "tested_single_shear_strength_kN": 11.2,
                        "test_evidence_source": "Buildex 31293-PDS",
                        "test_evidence_revision": "31293-PDS",
                        "test_evidence_url": (
                            "https://www.buildex.com.au/products/roofing-cladding/"
                            "24-fixing-to-metal-teks/110-metal-teks-cladding"
                        ),
                    },
                )
                for index in range(2)
            ),
        )
    }
    section = SectionProperties(
        id="c10012",
        label="C10012",
        area_m2=258e-6,
        iy_m4=4.32e-7,
        iz_m4=8.92e-8,
        torsion_j_m4=1.24e-10,
        tension_thickness_mm=1.2,
    )
    material = StructuralMaterial(
        id="g450",
        label="G450",
        elastic_modulus_kN_m2=200e6,
        shear_modulus_kN_m2=76.923e6,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        yield_strength_MPa=450.0,
        tensile_strength_MPa=480.0,
    )
    declarations = [
        SimpleNamespace(
            id=f"{component_id}-axis",
            component_id=component_id,
            section_id="c10012",
            material_id="g450",
            tension_only=False,
            start=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            end=SimpleNamespace(x=1.0, y=0.0, z=0.0),
            start_node_key="joint:screw-joint",
            end_node_key=None,
        )
        for component_id in ("stud", "track")
    ]
    analysis = SimpleNamespace(
        load_combinations=[
            SimpleNamespace(id="ULS-WIND", limit_state="ultimate", purpose="design")
        ],
        members=declarations,
        sections=[section],
        materials=[material],
    )
    member = SimpleNamespace(
        axial=lambda _x, _combo: -0.4,
        shear=lambda _axis, _x, _combo: 0.2,
        moment=lambda _axis, _x, _combo: 0.0,
    )
    connection = DesignConnection(
        id="screw-joint",
        label="Two-screw sheet connection",
        from_component_id="stud",
        to_component_id="track",
        connector_component_ids=["screw-0", "screw-1"],
        transfers=["force", "shear"],
    )

    check = _connection_checks(
        SimpleNamespace(
            members={declaration.id: member for declaration in declarations}
        ),
        analysis,
        [connection],
        components,
    )[0]

    assert check.status == "pass"
    assert check.evidence_status == "verified"
    assert check.pack_id == "as_nzs_4600_2005_a1_screwed_sheet_connection"
    assert check.design_shear_capacity_kN is not None
    assert check.governing_utilisation is not None
    assert check.governing_utilisation < 1.0
    assert check.stiffness_status == "unverified"


def _calculated_connection_result(
    status: str,
    *,
    pack_id: str,
) -> dict[str, object]:
    return {
        "status": status,
        "evidence_status": "verified" if status != "unsupported" else "candidate",
        "pack_id": pack_id,
        "pack_version": "test",
        "design_force_capacity_kN": 1.0 if status != "unsupported" else None,
        "design_moment_capacity_kNm": 1.0 if status != "unsupported" else None,
        "governing_utilisation": 0.5 if status != "unsupported" else None,
        "stiffness_status": "verified" if status != "unsupported" else "unverified",
        "stiffness_basis": "test",
        "source": "test",
        "source_sha256": None,
        "basis": "test",
        "blockers": [],
    }


def test_non_ground_connection_uses_complete_cleat_instead_of_partial_fixture() -> None:
    partial_fixture = _calculated_connection_result(
        "unsupported", pack_id="partial-fixture"
    )
    verified_cleat = _calculated_connection_result("pass", pack_id="verified-cleat")

    selected = _select_calculated_connection_resistance(
        grounded=False,
        anchored_fixture=partial_fixture,  # type: ignore[arg-type]
        direct_anchor=None,
        cleat=verified_cleat,  # type: ignore[arg-type]
        screw=None,
        gusset=None,
    )

    assert selected is verified_cleat


def test_ground_connection_does_not_drop_incomplete_foundation_path() -> None:
    partial_fixture = _calculated_connection_result(
        "unsupported", pack_id="partial-fixture"
    )
    verified_cleat = _calculated_connection_result("pass", pack_id="verified-cleat")

    selected = _select_calculated_connection_resistance(
        grounded=True,
        anchored_fixture=partial_fixture,  # type: ignore[arg-type]
        direct_anchor=None,
        cleat=verified_cleat,  # type: ignore[arg-type]
        screw=None,
        gusset=None,
    )

    assert selected is partial_fixture


@pytest.mark.parametrize(
    ("sheet_thickness_mm", "expected_status"),
    [(1.9, "pass"), (3.0, "unsupported")],
)
def test_fabricated_portal_gusset_checks_strength_and_rotational_stiffness(
    sheet_thickness_mm: float,
    expected_status: str,
) -> None:
    bolt_properties = {
        "bolted_sheet_fastener_pack_id": (
            "as_nzs_4600_2005_a1_bolted_sheet_interface"
        ),
        "nominal_diameter_mm": 12.0,
        "bolt_tensile_strength_MPa": 830.0,
        "bolt_minor_area_mm2": 76.2,
        "washers_under_head_and_nut": False,
    }
    components = {
        "column": DesignComponent(
            id="column", label="column", kind="member", visual_node_id="column"
        ),
        "rafter": DesignComponent(
            id="rafter", label="rafter", kind="member", visual_node_id="rafter"
        ),
        "plate": DesignComponent(
            id="plate",
            label="3 mm knee gusset",
            kind="connector",
            visual_node_id="plate",
            part_number="SHED-KNEE-BRACKET-3MM",
            product_key="shed:knee",
            product_definition_digest="f" * 64,
            structural_evidence_status="verified",
            structural_properties={
                "connection_capacity_pack_id": (
                    "as_nzs_4600_2005_a1_fabricated_gusset"
                ),
                "connection_capacity_pack_version": "1",
                "source": "AS/NZS 4600:2005+A1 project calculation",
                "source_sha256": "a" * 64,
                "fasteners_per_interface": 4,
                "fastener_coordinates_mm": [
                    [-20.0, -75.0],
                    [20.0, -75.0],
                    [-20.0, 75.0],
                    [20.0, 75.0],
                ],
                "connected_sheet_hole_diameter_mm": 14.0,
                "connected_sheet_hole_type": "standard_round",
                "minimum_bolt_spacing_mm": 40.0,
                "minimum_sheet_edge_distance_mm": 35.0,
                "maximum_connection_slip_mm": 2.0,
                "fixture_thickness_mm": 3.0,
                "fixture_yield_strength_MPa": 250.0,
                "fixture_tensile_strength_MPa": 410.0,
            },
        ),
        **{
            f"bolt-{index}": DesignComponent(
                id=f"bolt-{index}",
                label="PB1230HS",
                kind="connector",
                visual_node_id=f"bolt-{index}",
                part_number="PB1230HS",
                product_key="lysaght:pb1230hs",
                product_definition_digest="b" * 64,
                structural_evidence_status="verified",
                structural_properties=bolt_properties,
            )
            for index in range(8)
        },
    }
    section = SectionProperties(
        id="c10019",
        label="C10019",
        area_m2=409e-6,
        iy_m4=142000e-12,
        iz_m4=673000e-12,
        torsion_j_m4=492e-12,
        tension_thickness_mm=sheet_thickness_mm,
    )
    material = StructuralMaterial(
        id="g450",
        label="G450",
        elastic_modulus_kN_m2=200e6,
        shear_modulus_kN_m2=80e6,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        yield_strength_MPa=450.0,
        tensile_strength_MPa=480.0,
    )
    declarations = [
        SimpleNamespace(
            id=f"{component_id}-axis-{segment_index}",
            component_id=component_id,
            section_id="c10019",
            material_id="g450",
            tension_only=False,
            analytical_role="physical",
            start=SimpleNamespace(x=0.0, y=0.0, z=start_z),
            end=SimpleNamespace(x=0.0, y=0.0, z=end_z),
            start_node_key="joint:knee" if segment_index == 1 else None,
            end_node_key=None,
        )
        for component_id in ("column", "rafter")
        for segment_index, (start_z, end_z) in enumerate(
            ((0.0, 0.15), (0.15, 2.4)),
            start=1,
        )
    ]
    analysis = SimpleNamespace(
        load_combinations=[
            SimpleNamespace(id="ULS-WIND", limit_state="ultimate", purpose="design")
        ],
        members=declarations,
        sections=[section],
        materials=[material],
    )
    member = SimpleNamespace(
        axial=lambda _x, _combo: 4.0,
        shear=lambda _axis, _x, _combo: 2.0,
        moment=lambda _axis, _x, _combo: 0.6,
    )
    connection = DesignConnection(
        id="knee",
        label="Portal knee",
        from_component_id="column",
        to_component_id="rafter",
        connector_component_ids=["plate", *[f"bolt-{index}" for index in range(8)]],
        transfers=["force", "shear", "moment"],
    )

    check = _connection_checks(
        SimpleNamespace(
            members={declaration.id: member for declaration in declarations}
        ),
        analysis,
        [connection],
        components,
    )[0]

    assert check.status == expected_status
    assert check.pack_id == "as_nzs_4600_2005_a1_fabricated_gusset"
    if expected_status == "unsupported":
        assert "could not be evaluated" in check.basis
        return
    assert check.design_moment_capacity_kNm is not None
    assert check.design_moment_capacity_kNm > 0.6
    assert check.stiffness_status == "verified"


def test_global_stability_scope_excludes_secondary_member_numerical_noise():
    primary = MemberStabilityComparison(
        member_id="primary-rafter",
        first_order_max_moment_kNm=1.0,
        second_order_max_moment_kNm=1.02,
        moment_amplification=1.02,
        first_order_max_displacement_mm=2.0,
        second_order_max_displacement_mm=2.04,
        displacement_amplification=1.02,
    )
    secondary = MemberStabilityComparison(
        member_id="secondary-stud",
        first_order_max_moment_kNm=1e-16,
        second_order_max_moment_kNm=2e-4,
        moment_amplification=2e8,
        first_order_max_displacement_mm=0.2,
        second_order_max_displacement_mm=0.21,
        displacement_amplification=1.05,
    )

    scoped = _stability_scope_comparisons(
        [primary, secondary],
        eaves_member_ids=(),
        rafter_member_ids=("primary-rafter",),
    )

    assert scoped == [primary]


ANALYTICAL_DESIGN = """
from tertius_structural import StructuralModel

sheet_width = 762.0
sheet_length = 1200.0
sheet_area = sheet_width * sheet_length / 1000000.0

structure = StructuralModel(title="C100 structural microcosm")
steel = structure.material(
    id="steel",
    label="G450 steel",
    elastic_modulus_kN_m2=200000000.0,
    shear_modulus_kN_m2=80000000.0,
    poisson_ratio=0.3,
    density_kg_m3=7850.0,
)
c100 = structure.section(
    id="c10019",
    label="C10019 gross section",
    area_m2=409e-6,
    iy_m4=142000e-12,
    iz_m4=673000e-12,
    torsion_j_m4=492e-12,
)
sheet = structure.surface(sheet_shape, id="sheet", label="Roof sheet")
screws = structure.connector(screw_shape, id="screws", label="Tek screws")
purlin = structure.member(purlin_shape, id="purlin", label="C100 purlin")
block = structure.ground(block_shape, id="block", label="Concrete block")
structure.member_axis(
    purlin,
    id="purlin-axis",
    label="C100 purlin",
    start=(0, 0, 0),
    end=(0, 0, 1.6),
    section=c100,
    material=steel,
    start_restraints=(True, True, True, True, True, True),
    assumption="Idealised fixed GPB base; connection capacity is not checked.",
)
structure.connect(
    sheet,
    purlin,
    via=[screws],
    id="sheet-purlin",
    label="Sheet to purlin",
    transfers=["wind_normal", "force", "shear"],
)
structure.connect(
    purlin,
    block,
    id="purlin-ground",
    label="Purlin to ground",
    transfers=["force", "shear", "moment"],
)
wind = structure.surface_load(
    sheet,
    id="wind",
    label="Wind pressure",
    case="wind",
    pressure_kPa=0.8,
    area_m2=sheet_area,
    direction=(0, -1, 0),
    provenance="Test pressure",
)
structure.distribute_surface_load(
    wind,
    purlin,
    id="wind-to-purlin",
    label="Wind at screw",
    positions_m=(0.35, 0.8, 1.25),
    provenance="Equal tributary load at three screws.",
)
structural_assembly = structure.assembly(
    [sheet, screws, purlin, block],
    label="structural-test",
)
TERTIUS_STRUCTURAL = structure.manifest()
"""


GRAVITY_FRAME_DESIGN = """
from tertius_structural import StructuralModel

structure = StructuralModel(title="Two-member gravity frame")
structure.design_basis(
    framework_id="AU-NCC-2022",
    framework_label="NCC 2022 Amendment 2 Australian structural verification",
    framework_reference="NCC 2022 Amendment 2, Volume Two Part H1",
    jurisdiction="Australia",
    analysis_method="3D first-order elastic frame analysis",
    standards={
        "actions": "AS/NZS 1170 test mapping",
        "members": "AS/NZS 4600 test mapping",
    },
)
steel = structure.material(
    id="steel",
    label="G450 steel",
    elastic_modulus_kN_m2=200000000.0,
    shear_modulus_kN_m2=80000000.0,
    poisson_ratio=0.3,
    density_kg_m3=7850.0,
)
c100 = structure.section(
    id="c10019",
    label="C10019 gross section",
    area_m2=409e-6,
    iy_m4=142000e-12,
    iz_m4=673000e-12,
    torsion_j_m4=492e-12,
    mass_kg_m=3.29,
)
column = structure.member(column_shape, id="column", label="Column")
beam = structure.member(beam_shape, id="beam", label="Beam")
block = structure.ground(block_shape, id="block", label="Concrete")
structure.member_axis(
    column,
    id="column-axis",
    label="Column",
    start=(0, 0, 0),
    end=(0, 0, 2),
    section=c100,
    material=steel,
    start_restraints=(True, True, True, True, True, True),
    deflection_limit_ratio=250,
    deflection_limit_basis="Project demonstration criterion L/250.",
    assumption="Rigid knee and fixed base demonstration.",
)
structure.member_axis(
    beam,
    id="beam-axis",
    label="Beam",
    start=(0, 0, 2),
    end=(2, 0, 2),
    section=c100,
    material=steel,
    deflection_limit_ratio=250,
    deflection_limit_basis="Project demonstration criterion L/250.",
    assumption="Rigid knee and fixed base demonstration.",
)
structure.connect(
    beam,
    column,
    id="beam-column",
    label="Rigid knee",
    transfers=["force", "shear", "moment"],
)
structure.connect(
    column,
    block,
    id="column-ground",
    label="Fixed base",
    transfers=["force", "shear", "moment"],
)
structure.member_self_weight(
    column,
    id="column-self-weight",
    label="Column self-weight",
)
structure.member_self_weight(
    beam,
    id="beam-self-weight",
    label="Beam self-weight",
)
structure.member_distributed_load(
    beam,
    id="beam-service-load",
    label="Beam imposed service load",
    case="live",
    start_force_kN_m=(0, 0, -1),
    provenance="Authored one kN per metre demonstration service action.",
)
structure.load_combination(
    id="SLS-G",
    label="Permanent actions",
    limit_state="serviceability",
    factors={"dead": 1.0},
)
structure.load_combination(
    id="SLS-G+Q",
    label="Permanent plus imposed actions",
    limit_state="serviceability",
    factors={"dead": 1.0, "live": 1.0},
)
structural_assembly = structure.assembly([column, beam, block], label="gravity-frame")
TERTIUS_STRUCTURAL = structure.manifest()
"""


def test_surface_pressure_is_distributed_to_pynite_and_matches_hand_equilibrium():
    capture = parse_project_structural_capture(
        ANALYTICAL_DESIGN,
        project_name="structural_test",
    )
    assert capture.analysis is not None
    assert [load.force.y for load in capture.analysis.member_loads] == pytest.approx(
        [-0.24384, -0.24384, -0.24384]
    )

    snapshot = solve_project_structural(capture)

    result = snapshot.member_results[0]
    reaction = snapshot.reactions[0]
    assert result.max_shear_kN == pytest.approx(0.73152, abs=1e-10)
    assert result.max_moment_kNm == pytest.approx(0.585216, abs=1e-10)
    assert result.max_displacement_mm == pytest.approx(2.61231263, abs=1e-8)
    assert reaction.force.y == pytest.approx(0.73152, abs=1e-10)
    assert reaction.moment.x == pytest.approx(-0.585216, abs=1e-10)
    assert snapshot.equilibrium.status == "pass"
    assert snapshot.equilibrium.force_residual_kN.x == pytest.approx(0, abs=1e-10)
    assert snapshot.equilibrium.force_residual_kN.y == pytest.approx(0, abs=1e-10)
    assert snapshot.equilibrium.force_residual_kN.z == pytest.approx(0, abs=1e-10)
    assert snapshot.member_checks[0].status == "not_checked"
    assert snapshot.member_checks[0].capacity_kNm is None


def test_moment_diagram_is_solver_output_with_load_stations_and_zero_free_end():
    snapshot = solve_project_structural(
        parse_project_structural_capture(
            ANALYTICAL_DESIGN,
            project_name="structural_test",
        )
    )
    stations = snapshot.member_diagrams[0].stations
    by_distance = {round(station.distance_m, 6): station for station in stations}

    assert by_distance[0].moment_kNm.x == pytest.approx(-0.585216, abs=1e-10)
    assert by_distance[0].major_moment_kNm.x == pytest.approx(-0.585216, abs=1e-10)
    assert by_distance[0].minor_moment_kNm.x == pytest.approx(0, abs=1e-10)
    assert by_distance[0.35].moment_kNm.x == pytest.approx(-0.329184, abs=1e-10)
    assert by_distance[0.8].moment_kNm.x == pytest.approx(-0.109728, abs=1e-10)
    assert by_distance[1.25].moment_kNm.x == pytest.approx(0, abs=1e-10)
    assert by_distance[1.6].moment_kNm.x == pytest.approx(0, abs=1e-10)


def test_multi_member_frame_solves_catalogue_self_weight_and_service_loads():
    capture = parse_project_structural_capture(
        GRAVITY_FRAME_DESIGN,
        project_name="gravity_frame",
    )

    gravity = solve_project_structural(capture, combination_id="SLS-G")
    service = solve_project_structural(capture, combination_id="SLS-G+Q")

    member_weight_kN_m = 3.29 * 9.80665 / 1000.0
    assert len(service.members) == 2
    assert len(service.nodes) == 3
    assert service.load_summary.member_mass_kg == pytest.approx(13.16)
    assert service.load_summary.self_weight_kN == pytest.approx(member_weight_kN_m * 4)
    assert service.load_summary.imposed_load_kN == pytest.approx(2.0)
    assert gravity.load_summary.imposed_load_kN == pytest.approx(0.0)
    assert gravity.reactions[0].force.z == pytest.approx(member_weight_kN_m * 4)
    assert service.reactions[0].force.z == pytest.approx(member_weight_kN_m * 4 + 2.0)
    assert service.equilibrium.status == "pass"
    assert service.member_results[1].max_displacement_mm > 0
    assert service.serviceability_checks[1].limit_mm == pytest.approx(8.0)
    assert service.serviceability_checks[1].status in {"pass", "fail"}
    stages = {stage.id: stage for stage in service.verification_stages}
    assert stages["geometry"].status == "pass"
    assert stages["actions"].status == "pass"
    assert stages["analysis"].status == "pass"
    assert stages["stability"].status == "blocked"
    assert stages["cross_section"].status == "not_checked"
    assert stages["decision"].status == "blocked"
    assert service.design_basis is not None
    assert service.design_basis.framework_id == "AU-NCC-2022"
    assert len(service.calculation_sheets) == 11
    assert service.certification_readiness is not None
    assert service.certification_readiness.ready_for_engineering_review is True
    assert service.certification_readiness.ready_for_certificate_draft is False
    assert service.certification_readiness.document_status == "engineering_review_draft"
    assert service.certification_readiness.model_coverage.status == "complete"
    assert service.certification_readiness.model_coverage.compiled_member_count == 2
    assert service.certification_readiness.model_coverage.solved_member_count == 2
    assert service.certification_readiness.model_coverage.missing_result_member_ids == []
    assert "DRAFT ENGINEERING REVIEW REPORT" in (
        service.certification_readiness.draft_document_label
    )
    actions_sheet = next(
        sheet for sheet in service.calculation_sheets if sheet.stage_id == "actions"
    )
    assert actions_sheet.equations
    assert actions_sheet.related_member_ids == ["column-axis", "beam-axis"]


def test_orphaned_released_node_dofs_do_not_make_solver_singular():
    capture = parse_project_structural_capture(
        GRAVITY_FRAME_DESIGN,
        project_name="released_free_end",
    )
    assert capture.analysis is not None
    beam = next(member for member in capture.analysis.members if member.id == "beam-axis")
    beam.end_releases = Restraints(ry=True, rz=True)

    snapshot = solve_project_structural(capture, combination_id="SLS-G+Q")

    assert len(snapshot.member_results) == 2
    assert snapshot.equilibrium.status == "pass"


def test_axial_only_tie_is_not_applicable_as_a_bending_member():
    capture = parse_project_structural_capture(
        GRAVITY_FRAME_DESIGN,
        project_name="axial_tie_serviceability",
    )
    assert capture.analysis is not None
    beam = next(member for member in capture.analysis.members if member.id == "beam-axis")
    beam.tension_only = True
    beam_load = next(
        load
        for load in capture.analysis.member_distributed_loads
        if load.id == "beam-service-load"
    )
    beam_load.start_force_kN_m = Vector3(x=1, y=0, z=0)
    beam_load.end_force_kN_m = Vector3(x=1, y=0, z=0)

    snapshot = solve_project_structural(capture, combination_id="SLS-G+Q")
    check = next(
        item for item in snapshot.serviceability_checks if item.member_id == "beam-axis"
    )

    assert check.status == "not_applicable"
    assert check.utilisation is None
    assert check.displacement_mm == 0
    assert "axial-only" in check.basis


def test_site_policy_auto_selects_governing_credible_service_combination():
    capture = parse_project_structural_capture(
        GRAVITY_FRAME_DESIGN,
        project_name="gravity_frame",
    )
    site = default_site_definition()
    working_capture = ProjectStructuralCapture.model_validate(
        apply_site_definition(capture.model_dump(mode="python"), site)
    )

    snapshot = solve_project_structural(working_capture)

    assert snapshot.solver.combination_id == "SLS-G+Q"
    assert snapshot.solver.combination_selection == "governing_working_envelope"


def test_unknown_load_combination_fails_closed():
    capture = parse_project_structural_capture(
        GRAVITY_FRAME_DESIGN,
        project_name="gravity_frame",
    )

    with pytest.raises(ValueError, match="Unknown load combination"):
        solve_project_structural(capture, combination_id="SLS-missing")


def test_authored_imperfection_runs_linear_and_pdelta_stability_comparison():
    stability_source = GRAVITY_FRAME_DESIGN.replace(
        "structural_assembly = structure.assembly",
        """structure.member_point_load(
    column,
    id="notional-horizontal-load",
    label="Notional horizontal load",
    case="imperfection",
    case_id="imperfection-x",
    distance_m=2.0,
    force=(0.001, 0, 0),
    provenance="Explicit test equivalent horizontal force.",
)
structure.load_combination(
    id="ULS-STABILITY",
    label="Permanent action plus imperfection",
    limit_state="ultimate",
    factors={"dead": 1.35, "imperfection-x": 1.0},
)
structure.stability(
    method="p_delta",
    stability_combination_id="ULS-STABILITY",
    imperfection_case_id="imperfection-x",
    imperfection_basis="Explicit test equivalent horizontal force.",
    base_stiffness_basis="Fixed base is an unverified test assumption.",
    base_stiffness_status="assumed",
    amplification_warning_ratio=1.000001,
)
structural_assembly = structure.assembly""",
    )
    capture = parse_project_structural_capture(
        stability_source,
        project_name="stability_frame",
    )

    snapshot = solve_project_structural(
        capture,
        combination_id="ULS-STABILITY",
    )

    assert snapshot.stability is not None
    assert snapshot.stability.converged
    assert snapshot.stability.governing_moment_amplification > 1.0
    assert snapshot.stability.governing_displacement_amplification > 1.0
    assert "P-Delta" in snapshot.solver.analysis
    assert snapshot.equilibrium.status == "pass"
    assert snapshot.equilibrium.tolerance > 1e-8
    stages = {stage.id: stage for stage in snapshot.verification_stages}
    assert stages["stability"].status == "warning"
    stability_sheet = next(
        sheet for sheet in snapshot.calculation_sheets if sheet.stage_id == "stability"
    )
    assert stability_sheet.status == "warning"
    assert any(
        equation.expression == "η_M = M_II / M_I"
        for equation in stability_sheet.equations
    )
    assert any(output.symbol == "converged" for output in stability_sheet.outputs)
    assert any(
        "P399 does not define this ratio as a failure criterion" in assumption
        for assumption in stability_sheet.assumptions
    )


def test_tertius_generates_p399_ehf_and_nhf_from_solved_base_reactions():
    capture = parse_project_structural_capture(
        GRAVITY_FRAME_DESIGN,
        project_name="generated_stability_frame",
    )
    capture_data = capture.model_dump(mode="python")
    components = {
        component["id"]: component for component in capture_data["components"]
    }
    components["column"]["role"] = "portal column"
    components["beam"]["role"] = "portal rafter"
    analysis = capture_data["analysis"]
    dead_case_id = next(
        case["id"] for case in analysis["load_cases"] if case["category"] == "dead"
    )
    analysis["load_cases"].extend(
        [
            {
                "id": "p399-ehf-positive-x",
                "label": "P399 EHF +X",
                "category": "imperfection",
            },
            {
                "id": "p399-nhf-positive-x",
                "label": "P399 NHF +X",
                "category": "imperfection",
            },
            {
                "id": "p399-ehf-negative-x",
                "label": "P399 EHF -X",
                "category": "imperfection",
            },
            {
                "id": "p399-nhf-negative-x",
                "label": "P399 NHF -X",
                "category": "imperfection",
            },
        ]
    )
    analysis["load_combinations"].extend(
        [
            {
                "id": "ULS-1.35G",
                "label": "ULS permanent actions",
                "limit_state": "ultimate",
                "factors": {dead_case_id: 1.35},
            },
            {
                "id": "ULS-STABILITY+X",
                "label": "ULS permanent plus EHF +X",
                "limit_state": "ultimate",
                "factors": {
                    dead_case_id: 1.35,
                    "p399-ehf-positive-x": 1.0,
                },
            },
            {
                "id": "NHF-CHECK+X",
                "label": "NHF probe +X",
                "limit_state": "ultimate",
                "factors": {"p399-nhf-positive-x": 1.0},
                "purpose": "stability_probe",
            },
            {
                "id": "ULS-STABILITY-X",
                "label": "ULS permanent plus EHF -X",
                "limit_state": "ultimate",
                "factors": {
                    dead_case_id: 1.35,
                    "p399-ehf-negative-x": 1.0,
                },
            },
            {
                "id": "NHF-CHECK-X",
                "label": "NHF probe -X",
                "limit_state": "ultimate",
                "factors": {"p399-nhf-negative-x": 1.0},
                "purpose": "stability_probe",
            },
        ]
    )
    analysis["stability"] = {
        "method": "p_delta",
        "stability_combination_id": "ULS-STABILITY+X",
        "imperfection_case_id": "p399-ehf-positive-x",
        "imperfection_basis": "Tertius-generated P399 EHF/NHF test.",
        "base_stiffness_basis": "Fixed fixture base.",
        "base_stiffness_status": "verified",
        "direction_cases": [
            {
                "id": "positive-x",
                "base_combination_id": "ULS-1.35G",
                "stability_combination_id": "ULS-STABILITY+X",
                "imperfection_case_id": "p399-ehf-positive-x",
                "nhf_combination_id": "NHF-CHECK+X",
                "horizontal_axis": "x",
                "direction_sign": 1,
            },
            {
                "id": "negative-x",
                "base_combination_id": "ULS-1.35G",
                "stability_combination_id": "ULS-STABILITY-X",
                "imperfection_case_id": "p399-ehf-negative-x",
                "nhf_combination_id": "NHF-CHECK-X",
                "horizontal_axis": "x",
                "direction_sign": -1,
            },
        ],
        "column_component_ids": ["column"],
        "eaves_member_ids": ["column-axis"],
        "rafter_member_ids": ["beam-axis"],
        "column_height_m": 2.0,
        "analysis_base_model": "fixed",
        "analysis_basis_status": "verified",
        "physical_connection_stiffness_status": "verified",
    }
    generated_capture = ProjectStructuralCapture.model_validate(capture_data)

    snapshot = solve_project_structural(
        generated_capture,
        combination_id="ULS-STABILITY+X",
    )

    assert snapshot.stability is not None
    assert snapshot.stability.converged
    assert len(snapshot.loads) == 4
    ehf_positive = next(
        load for load in snapshot.loads if load.case_id == "p399-ehf-positive-x"
    )
    nhf_positive = next(
        load for load in snapshot.loads if load.case_id == "p399-nhf-positive-x"
    )
    ehf_negative = next(
        load for load in snapshot.loads if load.case_id == "p399-ehf-negative-x"
    )
    assert ehf_positive.force.x > 0
    assert ehf_negative.force.x == pytest.approx(-ehf_positive.force.x)
    assert nhf_positive.force.x == pytest.approx(ehf_positive.force.x)
    assert "1/200 of the solved vertical base reaction" in (ehf_positive.provenance or "")
    assert snapshot.equilibrium.status == "pass"


def test_bidirectional_nhf_evidence_completes_global_stability_stage():
    stability_source = GRAVITY_FRAME_DESIGN.replace(
        "structural_assembly = structure.assembly",
        """structure.member_point_load(
    column,
    id="ehf-plus",
    label="Equivalent horizontal force +X",
    case="imperfection",
    case_id="ehf-plus-x",
    distance_m=2.0,
    force=(0.001, 0, 0),
    provenance="Explicit test EHF +X.",
)
structure.member_point_load(
    column,
    id="ehf-minus",
    label="Equivalent horizontal force -X",
    case="imperfection",
    case_id="ehf-minus-x",
    distance_m=2.0,
    force=(-0.001, 0, 0),
    provenance="Explicit test EHF -X.",
)
structure.member_point_load(
    column,
    id="nhf-plus",
    label="Notional horizontal force +X",
    case="imperfection",
    case_id="nhf-plus-x",
    distance_m=2.0,
    force=(0.001, 0, 0),
    provenance="Explicit test NEd/200 +X.",
)
structure.member_point_load(
    column,
    id="nhf-minus",
    label="Notional horizontal force -X",
    case="imperfection",
    case_id="nhf-minus-x",
    distance_m=2.0,
    force=(-0.001, 0, 0),
    provenance="Explicit test NEd/200 -X.",
)
structure.load_combination(
    id="ULS-STABILITY+X",
    label="Permanent action plus EHF +X",
    limit_state="ultimate",
    factors={"dead": 1.35, "ehf-plus-x": 1.0},
)
structure.load_combination(
    id="ULS-STABILITY-X",
    label="Permanent action plus EHF -X",
    limit_state="ultimate",
    factors={"dead": 1.35, "ehf-minus-x": 1.0},
)
structure.load_combination(
    id="NHF-CHECK+X",
    label="NEd/200 check +X",
    limit_state="ultimate",
    factors={"nhf-plus-x": 1.0},
)
structure.load_combination(
    id="NHF-CHECK-X",
    label="NEd/200 check -X",
    limit_state="ultimate",
    factors={"nhf-minus-x": 1.0},
)
structure.stability(
    method="p_delta",
    stability_combination_id="ULS-STABILITY+X",
    imperfection_case_id="ehf-plus-x",
    imperfection_basis="Mirrored EHF and NEd/200 NHF test cases.",
    base_stiffness_basis="Fixed base is verified for this solver fixture.",
    base_stiffness_status="verified",
    direction_cases=(
        {
            "id": "+X",
            "stability_combination_id": "ULS-STABILITY+X",
            "imperfection_case_id": "ehf-plus-x",
            "nhf_combination_id": "NHF-CHECK+X",
            "horizontal_axis": "x",
        },
        {
            "id": "-X",
            "stability_combination_id": "ULS-STABILITY-X",
            "imperfection_case_id": "ehf-minus-x",
            "nhf_combination_id": "NHF-CHECK-X",
            "horizontal_axis": "x",
        },
    ),
    eaves_member_ids=("column-axis",),
    rafter_member_ids=("beam-axis",),
    column_height_m=2.0,
    analysis_base_model="fixed",
    analysis_basis_status="verified",
    physical_connection_stiffness_status="verified",
)
structural_assembly = structure.assembly""",
    )
    capture = parse_project_structural_capture(
        stability_source,
        project_name="bidirectional_stability_frame",
    )
    capture_data = capture.model_dump()
    for member_data in capture_data["analysis"]["members"]:
        member_data["rotation_deg"] = 90.0
    section_data = capture_data["analysis"]["sections"][0]
    section_data.update(
        {
            "bending_reference_kNm": 5.535,
            "bending_reference_axis": "local_z",
            "bending_reference_basis": "Catalogue Zxe times fy reference.",
            "catalog": {
                "catalog_id": "lysaght-zc-v2",
                "catalog_version": "2.0",
                "section_key": "C10019 (100x1.9)",
                "source": "Lysaght guide p.7-8",
                "record_sha256": "a" * 64,
                "axis_mapping": {
                    "local_y_inertia": "Iy",
                    "local_z_inertia": "Ix",
                },
                "properties": {
                    "type": "C",
                    "validated": True,
                    "lip": 14.5,
                    "fy": 450,
                    "E": 200000,
                        "G": 80000,
                        "A": 409,
                        "Ae": 329,
                        "Zxe": 12300,
                        "Zx": 13200,
                        "Zy": 4210,
                        "flange": 51,
                        "d1": 92.5,
                    "t": 1.9,
                    "rx": 40.6,
                    "ry": 18.7,
                    "x0": 40.4,
                        "ro2": 3630,
                        "beta_y": 122,
                    "J": 492,
                    "Iw": 311000000,
                },
            },
        }
    )
    capture_data["analysis"]["cross_section_verification"] = {
        "pack_id": "as_nzs_4600_2005_a1_ewm",
        "combination_ids": ["ULS-STABILITY+X", "ULS-STABILITY-X"],
        "off_axis_tolerance": 1e-6,
    }
    capture_data["analysis"]["member_stability_verification"] = {
        "pack_id": "as_nzs_4600_2005_a1_member",
        "combination_ids": ["ULS-STABILITY+X", "ULS-STABILITY-X"],
        "segments": [
            {
                "id": "column-segment",
                "member_id": "column-axis",
                "start_distance_m": 0.0,
                "end_distance_m": 2.0,
                "minor_axis_effective_length_factor": 1.0,
                "torsional_effective_length_factor": 1.0,
                "lateral_bending_restraint": "continuous_compression_flange",
                "restraint_status": "verified",
                "restraint_basis": "Verified test restraint.",
                "distortional_buckling_status": "verified",
                "distortional_buckling_basis": (
                    "Verified distortional resistance for the test."
                ),
            },
            {
                "id": "beam-segment",
                "member_id": "beam-axis",
                "start_distance_m": 0.0,
                "end_distance_m": 2.0,
                "minor_axis_effective_length_factor": 1.0,
                "torsional_effective_length_factor": 1.0,
                "lateral_bending_restraint": "continuous_compression_flange",
                "restraint_status": "verified",
                "restraint_basis": "Verified test restraint.",
                "distortional_buckling_status": "verified",
                "distortional_buckling_basis": (
                    "Verified distortional resistance for the test."
                ),
            },
        ],
        "off_axis_tolerance": 1e-6,
    }
    capture = ProjectStructuralCapture.model_validate(capture_data)

    snapshot = solve_project_structural(
        capture,
        combination_id="ULS-STABILITY+X",
    )

    assert snapshot.stability is not None
    assert len(snapshot.stability.direction_results) == 2
    assert snapshot.stability.minimum_alpha_cr is not None
    assert snapshot.stability.minimum_alpha_cr > 1
    assert snapshot.stability.simplified_alpha_cr_applicable
    stages = {stage.id: stage for stage in snapshot.verification_stages}
    assert stages["stability"].status == "pass"
    assert stages["cross_section"].status == "pass"
    assert stages["member_stability"].status == "pass"
    assert len(snapshot.cross_section_checks) == 2
    assert all(check.status == "pass" for check in snapshot.cross_section_checks)
    assert all(
        check.section_record_sha256 == "a" * 64
        for check in snapshot.cross_section_checks
    )
    assert all(
        check.governing_combination_id in {"ULS-STABILITY+X", "ULS-STABILITY-X"}
        for check in snapshot.cross_section_checks
    )
    assert all(
        check.governing_utilisation is not None and check.governing_utilisation < 1
        for check in snapshot.cross_section_checks
    )
    beam_cross_section = next(
        check for check in snapshot.cross_section_checks if check.member_id == "beam-axis"
    )
    assert beam_cross_section.off_axis_load_path_status == "candidate"
    assert beam_cross_section.off_axis_collector_component_ids == [
        "beam",
        "column",
        "block",
    ]
    assert beam_cross_section.off_axis_collector_connection_ids == [
        "beam-column",
        "column-ground",
    ]
    assert beam_cross_section.off_axis_grounded_component_id == "block"
    assert all(check.status == "pass" for check in snapshot.member_checks)
    assert len(snapshot.member_stability_checks) == 2
    assert all(check.status == "pass" for check in snapshot.member_stability_checks)
    assert all(
        check.design_member_compression_capacity_kN is not None
        and check.design_member_compression_capacity_kN > 0
        for check in snapshot.member_stability_checks
    )
    stability_sheet = next(
        sheet for sheet in snapshot.calculation_sheets if sheet.stage_id == "stability"
    )
    assert stability_sheet.status == "pass"
    assert (
        sum(
            equation.expression == "αcr = h / (200 δNHF)"
            for equation in stability_sheet.equations
        )
        == 2
    )
    assert any(
        equation.expression == "NEd ≤ 0.09 Ncr"
        for equation in stability_sheet.equations
    )
    cross_section_sheet = next(
        sheet
        for sheet in snapshot.calculation_sheets
        if sheet.stage_id == "cross_section"
    )
    assert cross_section_sheet.status == "pass"
    assert any(
        equation.expression.startswith("u_NMM = N*/(phi_c N_s)")
        for equation in cross_section_sheet.equations
    )
    assert any(
        equation.expression == "u_T = T*/[phi_v (0.60 fy J/t)]"
        for equation in cross_section_sheet.equations
    )
    assert any(
        equation.expression == "u_gov=max(u_NMM, u_MzVy, u_MyVz) + u_T"
        for equation in cross_section_sheet.equations
    )
    assert any(
        equation.expression == "R_off-axis* = max |Fz|"
        for equation in cross_section_sheet.equations
    )
    member_stability_sheet = next(
        sheet
        for sheet in snapshot.calculation_sheets
        if sheet.stage_id == "member_stability"
    )
    assert member_stability_sheet.status == "pass"
    assert any(
        equation.expression == "u_N = N*/(phi_c N_c)"
        for equation in member_stability_sheet.equations
    )
    assert all(
        check.design_minor_bending_capacity_kNm is not None
        and check.design_off_axis_shear_capacity_kN is not None
        and check.design_st_venant_torsion_capacity_kNm is not None
        and check.governing_minor_bending_mode is not None
        for check in snapshot.member_stability_checks
    )

    unrestrained_data = deepcopy(capture_data)
    for segment in unrestrained_data["analysis"]["member_stability_verification"][
        "segments"
    ]:
        segment["lateral_bending_restraint"] = "unverified"
        segment["restraint_status"] = "assumed"
    unrestrained_snapshot = solve_project_structural(
        ProjectStructuralCapture.model_validate(unrestrained_data),
        combination_id="ULS-STABILITY+X",
    )
    unrestrained_stages = {
        stage.id: stage for stage in unrestrained_snapshot.verification_stages
    }
    assert unrestrained_stages["member_stability"].status == "pass"
    assert all(
        check.status == "pass"
        for check in unrestrained_snapshot.member_stability_checks
    )
    assert all(
        any("complete physical component" in item for item in check.assumptions)
        for check in unrestrained_snapshot.member_stability_checks
    )
    assert all(
        check.design_member_compression_capacity_kN is not None
        for check in unrestrained_snapshot.member_stability_checks
    )
    assert all(
        check.distortional_buckling_status == "verified"
        and check.governing_bending_mode is not None
        and check.standard_reference
        == "AS/NZS 4600:2005 incorporating Amendment No. 1"
        for check in unrestrained_snapshot.member_stability_checks
    )
    assert {check.status for check in unrestrained_snapshot.member_checks} == {"pass"}

    overloaded_data = deepcopy(capture_data)
    overloaded_properties = overloaded_data["analysis"]["sections"][0]["catalog"][
        "properties"
    ]
    overloaded_properties["Ae"] = 1.0
    overloaded_properties["Zxe"] = 10.0
    overloaded_capture = ProjectStructuralCapture.model_validate(overloaded_data)
    overloaded_snapshot = solve_project_structural(
        overloaded_capture,
        combination_id="ULS-STABILITY+X",
    )
    overloaded_stages = {
        stage.id: stage for stage in overloaded_snapshot.verification_stages
    }
    assert overloaded_stages["cross_section"].status == "fail"
    assert any(
        check.status == "fail" for check in overloaded_snapshot.cross_section_checks
    )
    assert any(check.status == "fail" for check in overloaded_snapshot.member_checks)

    mismatched_capture = parse_project_structural_capture(
        stability_source.replace(
            'analysis_base_model="fixed"',
            'analysis_base_model="perfectly_pinned"',
        ),
        project_name="mismatched_stability_frame",
    )
    mismatched_snapshot = solve_project_structural(
        mismatched_capture,
        combination_id="ULS-STABILITY+X",
    )
    mismatched_stages = {
        stage.id: stage for stage in mismatched_snapshot.verification_stages
    }
    assert mismatched_stages["stability"].status == "warning"
    mismatched_sheet = next(
        sheet
        for sheet in mismatched_snapshot.calculation_sheets
        if sheet.stage_id == "stability"
    )
    assert any(
        "does not match" in assumption for assumption in mismatched_sheet.assumptions
    )


def test_catalogue_yield_reference_is_renderer_only_not_a_design_pass():
    source = GRAVITY_FRAME_DESIGN.replace(
        "mass_kg_m=3.29,\n)",
        """mass_kg_m=3.29,
    bending_reference_kNm=1.0,
    bending_reference_axis="resultant",
    bending_reference_basis="Nominal effective-section yield reference only.",
)""",
    ).replace(
        "structural_assembly = structure.assembly",
        """structure.load_combination(
    id="DEMO-OVERLOAD",
    label="Deliberate overload",
    limit_state="ultimate",
    factors={"dead": 1.0, "live": 5.0},
)
structural_assembly = structure.assembly""",
    )
    capture = parse_project_structural_capture(
        source,
        project_name="yield_reference_frame",
    )

    gravity = solve_project_structural(capture, combination_id="SLS-G")
    overload = solve_project_structural(capture, combination_id="DEMO-OVERLOAD")

    assert all(check.status == "not_checked" for check in gravity.member_checks)
    assert all(check.status == "not_checked" for check in overload.member_checks)
    assert all((check.utilisation or 0) <= 1 for check in gravity.member_checks)
    assert any((check.utilisation or 0) > 1 for check in overload.member_checks)
    assert all(
        "RENDERER REFERENCE ONLY" in check.basis
        for check in (*gravity.member_checks, *overload.member_checks)
        if check.capacity_kNm is not None
    )
    assert overload.load_summary.imposed_load_kN == pytest.approx(10.0)
    assert overload.equilibrium.status == "pass"
    assert any(
        capability.id == "checks" and capability.status == "blocked"
        for capability in overload.capabilities
    )


def test_signed_moment_flips_effective_compression_flange_restraint_trace():
    model = StructuralModel(title="Signed restraint trace")
    steel = model.material(
        id="steel",
        label="Steel",
        elastic_modulus_kN_m2=200_000_000.0,
        shear_modulus_kN_m2=80_000_000.0,
        poisson_ratio=0.3,
        density_kg_m3=7850.0,
    )
    section = model.section(
        id="cee",
        label="Cee",
        area_m2=409e-6,
        iy_m4=142000e-12,
        iz_m4=673000e-12,
        torsion_j_m4=492e-12,
        catalog={
            "catalog_id": "test-c100",
            "catalog_version": "1.0",
            "section_key": "C10019",
            "source": "Unit-test section record.",
            "record_sha256": "b" * 64,
            "axis_mapping": {},
            "properties": {"depth_mm": 100.0},
        },
    )
    primary = model.member_from_geometry(
        StructuralMemberGeometry(
            shape=bd.Box(2000, 100, 100),
            label="Primary",
            part_number="C10019",
            start=(0.0, 0.0, 0.0),
            end=(2.0, 0.0, 0.0),
        ),
        component_id="primary",
        member_id="primary-axis",
        section=section,
        material=steel,
        start_restraints=(True, True, True, True, True, True),
        assumption="Fixed cantilever for signed-moment trace test.",
    )
    braces = []
    for index, distance in enumerate((0.5, 1.5), start=1):
        brace = model.member_component_from_geometry(
            StructuralMemberGeometry(
                shape=bd.Box(100, 1000, 100),
                label=f"Brace {index}",
                part_number="C10012",
                start=(distance, 0.1, -0.5),
                end=(distance, 0.1, 0.5),
            ),
            component_id=f"brace-{index}",
        )
        connection = model.connect(
            brace,
            primary,
            id=f"brace-{index}-primary",
            label=f"Brace {index} to primary",
            transfers=["force", "shear"],
        )
        model.member_restraint_from_connection(
            primary,
            brace,
            connection=connection,
            restrains_lateral_translation=True,
            restrains_twist=True,
            evidence_status="verified",
            evidence_basis="Connected geometry with unit-test capacity evidence.",
            design_force_capacity_kN=10.0,
            design_moment_capacity_kNm=1.0,
            stiffness_status="verified",
            capacity_basis="Deliberately sufficient unit-test capacity.",
            provenance="Versioned unit-test restraint record.",
        )
        braces.append(brace)
    ground = model.ground(bd.Box(100, 100, 100), id="ground", label="Ground")
    model.connect(
        primary,
        ground,
        id="primary-ground",
        label="Fixed base",
        transfers=["force", "shear", "moment"],
    )
    for case_id, direction in (("inward", -1.0), ("outward", 1.0)):
        model.member_point_load(
            primary,
            id=f"load-{case_id}",
            label=f"{case_id.title()} load",
            case="wind",
            case_id=case_id,
            case_label=f"{case_id.title()} load case",
            distance_m=2.0,
            force=(0.0, direction, 0.0),
            provenance="Opposite signed-moment trace test.",
        )
        model.load_combination(
            id=f"ULS-{case_id}",
            label=f"ULS {case_id}",
            limit_state="ultimate",
            factors={case_id: 1.0},
        )
    model.member_stability_verification(
        pack_id="as_nzs_4600_2005_a1_member",
        combination_ids=("ULS-inward", "ULS-outward"),
        members=(primary,),
        distortional_buckling_basis="Distortional evidence is outside this trace test.",
    )
    model.assembly([primary, *braces, ground], label="signed-restraint")
    manifest = model.manifest()
    capture = capture_project_structural_declaration(
        manifest,
        project_name="signed_restraint",
        design_hash="a" * 64,
    )

    inward = solve_project_structural(capture, combination_id="ULS-inward")
    outward = solve_project_structural(capture, combination_id="ULS-outward")
    inward_middle = next(
        trace
        for trace in inward.member_restraint_traces
        if trace.segment_start_m == pytest.approx(0.5)
        and trace.segment_end_m == pytest.approx(1.5)
    )
    outward_middle = next(
        trace
        for trace in outward.member_restraint_traces
        if trace.segment_start_m == pytest.approx(0.5)
        and trace.segment_end_m == pytest.approx(1.5)
    )

    assert {inward_middle.compression_flange, outward_middle.compression_flange} == {
        "positive_local_y",
        "negative_local_y",
    }
    assert {inward_middle.status, outward_middle.status} == {"verified", "missing"}
    candidate_trace = (
        inward_middle if inward_middle.status == "verified" else outward_middle
    )
    assert len(candidate_trace.effective_restraint_candidate_ids) == 2
    loaded_candidate = max(
        inward.member_restraint_candidate_checks,
        key=lambda check: check.transferred_load_kN or 0.0,
    )
    assert loaded_candidate.required_force_kN == pytest.approx(1.5)
    assert loaded_candidate.required_moment_kNm == pytest.approx(0.15)
    assert loaded_candidate.status == "pass"

    standard_manifest = deepcopy(manifest)
    for candidate in standard_manifest["analysis"][
        "member_stability_verification"
    ]["restraint_candidates"]:
        candidate["demand_model"] = (
            "as_nzs_4600_2005_4_3_2_flange_force"
        )
    standard_capture = capture_project_structural_declaration(
        standard_manifest,
        project_name="as_nzs_restraint_demand",
        design_hash="b" * 64,
    )
    standard_result = solve_project_structural(
        standard_capture,
        combination_id=inward.solver.combination_id,
    )
    standard_candidate = max(
        standard_result.member_restraint_candidate_checks,
        key=lambda check: check.required_force_kN or 0.0,
    )
    assert standard_candidate.required_force_kN == pytest.approx(
        0.025 * (standard_candidate.transferred_load_kN or 0.0)
    )
    assert standard_candidate.required_moment_kNm == pytest.approx(
        (standard_candidate.required_force_kN or 0.0)
        * (standard_candidate.member_depth_m or 0.0)
    )
    assert "AS/NZS 4600:2005 clauses 4.3.2.2-4.3.2.3" in (
        standard_candidate.mechanism
    )

    inadequate_manifest = deepcopy(manifest)
    for candidate in inadequate_manifest["analysis"]["member_stability_verification"][
        "restraint_candidates"
    ]:
        candidate["design_force_capacity_kN"] = 1.0
        candidate["design_moment_capacity_kNm"] = 0.1
    inadequate_capture = capture_project_structural_declaration(
        inadequate_manifest,
        project_name="inadequate_signed_restraint",
        design_hash="c" * 64,
    )
    inadequate = solve_project_structural(
        inadequate_capture,
        combination_id=inward.solver.combination_id,
    )
    inadequate_middle = next(
        trace
        for trace in inadequate.member_restraint_traces
        if trace.segment_start_m == pytest.approx(0.5)
        and trace.segment_end_m == pytest.approx(1.5)
    )
    assert inadequate_middle.status == "inadequate"
    assert inadequate_middle.restraint_force_utilisation == pytest.approx(1.5)

    evidence_manifest = deepcopy(manifest)
    for candidate in evidence_manifest["analysis"]["member_stability_verification"][
        "restraint_candidates"
    ]:
        candidate["evidence_pack_id"] = "lysaght-zc-2026-08-c10012-100ac-pb1230hs"
        candidate["configuration"] = {
            "primary_part_number": "C10019",
            "bracing_part_number": "C10012",
            "connector_part_numbers": ["100AC", "DIN-6921-M12X25"],
        }
        candidate["anchorage_basis"] = "No grounded test anchorage exists."
    evidence_capture = capture_project_structural_declaration(
        evidence_manifest,
        project_name="identity_failed_restraint",
        design_hash="d" * 64,
    )
    evidence_result = solve_project_structural(
        evidence_capture,
        combination_id=inward.solver.combination_id,
    )
    identity_failed = next(
        check
        for check in evidence_result.member_restraint_candidate_checks
        if check.required_force_kN is not None
    )
    assert identity_failed.status == "unsupported"
    assert identity_failed.identity_status == "fail"
    assert identity_failed.available_force_kN is None
    assert identity_failed.anchorage_status == "unverified"
    assert any("M12X25" in mismatch for mismatch in identity_failed.identity_mismatches)
