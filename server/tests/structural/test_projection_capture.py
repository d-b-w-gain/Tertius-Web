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
from core.structural.contracts import (
    AnalyticalMemberDeclaration,
    DesignComponent,
    ProjectStructuralCapture,
    Restraints,
    Vector3,
)
from tertius.runner import execute_design
from workflows.structural.structural_server import (
    _capture_from_structural_projection,
    _derive_member_restraint_candidates,
    _endpoint_connection_effects,
    _p399_stability_actions,
    _portal_frame_abcb_protocol_scope,
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
    assert capture.connections[0].component_ports == {"C1": "end", "R1": "start"}
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


def test_restraint_candidates_are_derived_from_compiled_physical_joints() -> None:
    components = [
        DesignComponent(
            id="R1",
            label="Portal rafter",
            kind="member",
            visual_node_id="R1",
            part_number="C10019",
            role="portal rafter",
        ),
        DesignComponent(
            id="P1",
            label="Roof purlin",
            kind="member",
            visual_node_id="P1",
            part_number="C10012",
            role="roof/ceiling purlin",
        ),
        DesignComponent(
            id="AC1",
            label="100AC purlin bracket",
            kind="connector",
            visual_node_id="AC1",
            part_number="100AC",
            role="roof purlin end connection",
        ),
        *[
            DesignComponent(
                id=f"PB{index}",
                label=f"PB1230HS purlin bolt kit {index}",
                kind="connector",
                visual_node_id=f"PB{index}",
                part_number="PB1230HS",
                role="roof purlin end connection",
            )
            for index in (1, 2)
        ],
        DesignComponent(
            id="G1",
            label="Foundation",
            kind="ground",
            visual_node_id="G1",
            grounded=True,
            role="foundation",
        ),
    ]
    declarations = [
        AnalyticalMemberDeclaration(
            id="member:R1:segment:01",
            label="Rafter segment 1",
            component_id="R1",
            start=Vector3(x=0, y=0, z=2),
            end=Vector3(x=1, y=0, z=2),
            start_node_key="joint:KNEE",
            end_node_key="joint:PURLIN-JOINT",
            section_id="C20024",
            material_id="G450",
            assumption="Compiled physical segment.",
        ),
        AnalyticalMemberDeclaration(
            id="member:R1:segment:02",
            label="Rafter segment 2",
            component_id="R1",
            start=Vector3(x=1, y=0, z=2),
            end=Vector3(x=2, y=0, z=2),
            start_node_key="joint:PURLIN-JOINT",
            end_node_key="joint:APEX",
            section_id="C20024",
            material_id="G450",
            assumption="Compiled physical segment.",
        ),
        AnalyticalMemberDeclaration(
            id="member:P1",
            label="Purlin",
            component_id="P1",
            start=Vector3(x=1, y=-2, z=2),
            end=Vector3(x=1, y=0, z=2),
            start_node_key="joint:PURLIN-GROUND",
            end_node_key="joint:PURLIN-JOINT",
            section_id="C10019",
            material_id="G450",
            assumption="Compiled physical purlin.",
        ),
    ]
    projection = {
        "joints": [
            {
                "connection_id": "PURLIN-JOINT",
                "ports": [
                    {"component_id": "P1", "port": "end"},
                    {"component_id": "R1", "port": "roof:1"},
                ],
                "connector_component_ids": ["AC1", "PB1", "PB2"],
                "transfers": ["force", "shear"],
            },
            {
                "connection_id": "PURLIN-GROUND",
                "ports": [
                    {"component_id": "P1", "port": "start"},
                    {"component_id": "G1", "port": "purlin"},
                ],
                "connector_component_ids": [],
                "transfers": ["force"],
            },
        ]
    }

    candidates = _derive_member_restraint_candidates(
        projection,
        components=components,
        declarations=declarations,
    )

    assert len(candidates) == 2
    assert {candidate.member_id for candidate in candidates} == {
        "member:R1:segment:01",
        "member:R1:segment:02",
    }
    assert {candidate.distance_m for candidate in candidates} == {0.0, 1.0}
    assert all(candidate.restrains_lateral_translation for candidate in candidates)
    assert all(candidate.restrains_twist for candidate in candidates)
    assert all(candidate.evidence_status == "candidate" for candidate in candidates)
    assert all(candidate.stiffness_status == "unverified" for candidate in candidates)
    assert all(candidate.anchorage_status == "unverified" for candidate in candidates)
    assert all(
        candidate.anchorage_component_ids == ["P1", "G1"]
        and candidate.anchorage_connection_ids == ["PURLIN-GROUND"]
        and candidate.anchorage_grounded_component_id == "G1"
        for candidate in candidates
    )
    assert all(
        candidate.configuration.primary_part_number == "C10019"
        and candidate.configuration.bracing_part_number == "C10012"
        and candidate.configuration.connector_part_numbers
        == ["100AC", "PB1230HS", "PB1230HS"]
        for candidate in candidates
    )
    assert all(
        candidate.evidence_pack_id
        == "lysaght-zc-2026-08-c10012-100ac-pb1230hs"
        and candidate.demand_model
        == "as_nzs_4600_2005_4_3_2_flange_force"
        for candidate in candidates
    )


@pytest.mark.parametrize(
    "bracing_role",
        (
            "left roof-plane tension cross brace",
            "right roof-plane tension cross brace",
        ),
)
def test_roof_bracing_connection_is_a_purlin_restraint_candidate(
    bracing_role: str,
) -> None:
    components = [
        DesignComponent(
            id="P1",
            label="Roof purlin",
            kind="member",
            visual_node_id="P1",
            part_number="C10012",
            role="roof/ceiling purlin",
        ),
        DesignComponent(
            id="B1",
            label="Roof-plane cross brace",
            kind="member",
            visual_node_id="B1",
            part_number="DESIGN-TBD-BRACE-STRAP-30X1.0",
            role=bracing_role,
        ),
        *[
            DesignComponent(
                id=f"S{index}",
                label=f"Crossing screw {index}",
                kind="connector",
                visual_node_id=f"S{index}",
                part_number="6-311-0695-5MP",
                role="cross-brace connection fastener",
            )
            for index in (1, 2)
        ],
    ]
    declarations = [
        AnalyticalMemberDeclaration(
            id="member:P1:segment:01",
            label="Purlin segment 1",
            component_id="P1",
            start=Vector3(x=0, y=0, z=2),
            end=Vector3(x=0, y=1, z=2),
            start_node_key="PURLIN-START",
            end_node_key="joint:STRAP-PURLIN",
            section_id="C10012",
            material_id="G450",
            assumption="Compiled physical purlin segment.",
        ),
        AnalyticalMemberDeclaration(
            id="member:P1:segment:02",
            label="Purlin segment 2",
            component_id="P1",
            start=Vector3(x=0, y=1, z=2),
            end=Vector3(x=0, y=2, z=2),
            start_node_key="joint:STRAP-PURLIN",
            end_node_key="PURLIN-END",
            section_id="C10012",
            material_id="G450",
            assumption="Compiled physical purlin segment.",
        ),
        AnalyticalMemberDeclaration(
            id="member:B1:segment:01",
            label="Brace segment 1",
            component_id="B1",
            start=Vector3(x=-1, y=0, z=2),
            end=Vector3(x=0, y=1, z=2),
            start_node_key="BRACE-START",
            end_node_key="joint:STRAP-PURLIN",
            section_id="STRAP30X1",
            material_id="G450",
            assumption="Compiled physical brace segment.",
        ),
        AnalyticalMemberDeclaration(
            id="member:B1:segment:02",
            label="Brace segment 2",
            component_id="B1",
            start=Vector3(x=0, y=1, z=2),
            end=Vector3(x=1, y=2, z=2),
            start_node_key="joint:STRAP-PURLIN",
            end_node_key="BRACE-END",
            section_id="STRAP30X1",
            material_id="G450",
            assumption="Compiled physical brace segment.",
        ),
    ]
    projection = {
        "joints": [
            {
                "connection_id": "STRAP-PURLIN",
                "ports": [
                    {"component_id": "P1", "port": "roof-brace:A"},
                    {"component_id": "B1", "port": "purlin:2"},
                ],
                "connector_component_ids": ["S1", "S2"],
                "transfers": ["force", "shear"],
            }
        ]
    }

    candidates = _derive_member_restraint_candidates(
        projection,
        components=components,
        declarations=declarations,
    )

    assert len(candidates) == 2
    assert {candidate.member_id for candidate in candidates} == {
        "member:P1:segment:01",
        "member:P1:segment:02",
    }
    assert all(candidate.bracing_component_id == "B1" for candidate in candidates)
    assert all(candidate.restrains_lateral_translation for candidate in candidates)
    assert all(not candidate.restrains_twist for candidate in candidates)
    assert all(candidate.evidence_status == "candidate" for candidate in candidates)


def test_solid_bridge_joint_restraints_are_derived_in_both_directions() -> None:
    components = [
        DesignComponent(
            id="P1",
            label="Roof purlin",
            kind="member",
            visual_node_id="P1",
            part_number="C10012",
            role="roof/ceiling purlin",
        ),
        DesignComponent(
            id="B1",
            label="Full-depth solid bridge",
            kind="member",
            visual_node_id="B1",
            part_number="C10012",
            role="roof purlin solid bridging",
        ),
        DesignComponent(
            id="AC1",
            label="100AC angle",
            kind="connector",
            visual_node_id="AC1",
            part_number="100AC",
            role="roof purlin solid bridging connection",
        ),
        *[
            DesignComponent(
                id=f"PB{index}",
                label=f"PB1230HS bolt {index}",
                kind="connector",
                visual_node_id=f"PB{index}",
                part_number="PB1230HS",
                role="roof purlin solid bridging connection",
            )
            for index in range(1, 5)
        ],
    ]
    declarations = [
        AnalyticalMemberDeclaration(
            id="member:P1:left",
            label="Purlin left segment",
            component_id="P1",
            start=Vector3(x=0, y=0, z=0),
            end=Vector3(x=1, y=0, z=0),
            start_node_key="PURLIN-START",
            end_node_key="joint:BRIDGE-JOINT",
            section_id="C10012",
            material_id="G500",
            assumption="Compiled physical purlin segment.",
        ),
        AnalyticalMemberDeclaration(
            id="member:P1:right",
            label="Purlin right segment",
            component_id="P1",
            start=Vector3(x=1, y=0, z=0),
            end=Vector3(x=2, y=0, z=0),
            start_node_key="joint:BRIDGE-JOINT",
            end_node_key="PURLIN-END",
            section_id="C10012",
            material_id="G500",
            assumption="Compiled physical purlin segment.",
        ),
        AnalyticalMemberDeclaration(
            id="member:B1",
            label="Solid bridge",
            component_id="B1",
            start=Vector3(x=1, y=-1, z=0),
            end=Vector3(x=1, y=0, z=0),
            start_node_key="BRIDGE-START",
            end_node_key="joint:BRIDGE-JOINT",
            section_id="C10012",
            material_id="G500",
            assumption="Compiled full-depth bridge.",
        ),
    ]
    projection = {
        "joints": [
            {
                "connection_id": "BRIDGE-JOINT",
                "ports": [
                    {"component_id": "P1", "port": "solid-bridge"},
                    {"component_id": "B1", "port": "end"},
                ],
                "connector_component_ids": [
                    "AC1",
                    "PB1",
                    "PB2",
                    "PB3",
                    "PB4",
                ],
                "transfers": ["force", "shear", "moment"],
            }
        ]
    }

    candidates = _derive_member_restraint_candidates(
        projection,
        components=components,
        declarations=declarations,
    )

    assert {candidate.member_id for candidate in candidates} == {
        "member:P1:left",
        "member:P1:right",
        "member:B1",
    }
    assert all(candidate.restrains_lateral_translation for candidate in candidates)
    assert all(candidate.restrains_twist for candidate in candidates)
    assert all(candidate.restrained_flange == "both" for candidate in candidates)
    assert all(
        candidate.evidence_pack_id
        == "lysaght-zc-2026-09-c10012-solid-bridge-100ac-pb1230hs"
        for candidate in candidates
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
    assert (
        capture.analysis.member_stability_verification.combination_ids == ultimate_ids
    )
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
    assert {check.status for check in snapshot.member_stability_checks} == {"pass"}
    assert all(
        check.distortional_buckling_status == "verified"
        and check.design_lateral_torsional_bending_capacity_kNm is not None
        and check.design_distortional_bending_capacity_kNm is not None
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
    assert stages["member_stability"] == "pass"
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
    # The Site footprint is a map-placement aid and deliberately differs from
    # the compiled 5 m x 3 m mechanical frame envelope.
    site.structure.footprint_length_m = 5.5
    site.structure.footprint_width_m = 3.5
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
    for purlin_index, (x, z) in enumerate(((-0.75, 2.7), (0.75, 2.7)), start=1):
        component_id = f"RP{purlin_index}"
        components.append(
            DesignComponent(
                id=component_id,
                label=component_id,
                kind="member",
                visual_node_id=component_id,
                role="roof/ceiling purlin",
            )
        )
        analytical_members.append(
            {
                "id": f"member:{component_id}",
                "component_id": component_id,
                "start_m": [x, 0.0, z],
                "end_m": [x, 5.0, z],
                "physical_start_distance_m": 0.0,
                "physical_end_distance_m": 5.0,
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
    assert effective_configuration.portal_frame_wind_actions is not None
    normalized_wind_configuration = (
        effective_configuration.portal_frame_wind_actions.model_dump(mode="json")
    )
    assert normalized_wind_configuration == {
        "model_id": "transverse_portal_frame_strip_v1",
        "column_role": "portal column",
        "rafter_role": "portal rafter",
        "roof_imposed_receiver_role": "roof/ceiling purlin",
        "surface_action_pack_id": (
            "as_nzs_1170_2_rectangular_enclosed_main_frame_v1"
        ),
    }
    assert len(wind_bases) == 8
    assert len(surface_loads) == 84
    assert len(line_loads) == 84
    assert len(surface_sources) == 84
    protocol_scope = _portal_frame_abcb_protocol_scope(
        {"analytical_members": analytical_members},
        components=components,
        configuration=configuration,
    )
    assert protocol_scope is not None
    assert protocol_scope.status == "outside_scope"
    assert protocol_scope.geometry.eaves_height_m == pytest.approx(2.4)
    assert protocol_scope.geometry.roof_height_m == pytest.approx(3.0)
    assert protocol_scope.geometry.building_width_m == pytest.approx(3.0)
    assert protocol_scope.geometry.building_length_m == pytest.approx(5.0)
    assert protocol_scope.geometry.length_width_ratio == pytest.approx(5.0 / 3.0)
    assert protocol_scope.geometry.roof_pitch_degrees == pytest.approx(
        21.80140948635181
    )
    first_frame_wall = next(
        load
        for load in surface_loads
        if load.id == "site:wind-uls-plus-x:F1CL:wall"
    )
    assert first_frame_wall.area_m2 == pytest.approx(2.4 * 2.5)
    assert len(effective_configuration.member_loads) == 2
    assert {
        load.case_id for load in effective_configuration.member_loads
    } == {"roof-concentrated:RP1", "roof-concentrated:RP2"}
    assert all(
        load.distance_m == pytest.approx(2.5)
        and load.force.z == pytest.approx(-1.4)
        for load in effective_configuration.member_loads
    )
    bases_by_event = {
        event: [basis for basis in wind_bases if basis.design_event == event]
        for event in ("serviceability", "ultimate")
    }
    assert {event: len(bases) for event, bases in bases_by_event.items()} == {
        "serviceability": 4,
        "ultimate": 4,
    }
    assert {
        basis.annual_recurrence_interval_years
        for basis in bases_by_event["serviceability"]
    } == {25}
    assert {
        basis.annual_recurrence_interval_years for basis in bases_by_event["ultimate"]
    } == {500}
    assert max(basis.q_z_kPa for basis in bases_by_event["serviceability"]) < max(
        basis.q_z_kPa for basis in bases_by_event["ultimate"]
    )
    assert {load.case_id for load in line_loads} == {
        "roof-imposed",
        "wind-sls-plus-x",
        "wind-sls-minus-x",
        "wind-sls-plus-y",
        "wind-sls-minus-y",
        "wind-uls-plus-x",
        "wind-uls-minus-x",
        "wind-uls-plus-y",
        "wind-uls-minus-y",
    }
    assert {
        load.coefficient_status for load in surface_loads if load.case == "wind"
    } == {"verified"}
    assert all(
        "as_nzs_1170_2_rectangular_enclosed_main_frame_v1" in load.provenance
        for load in surface_loads
        if load.case == "wind"
    )
    assert {case.role for case in effective_configuration.action_cases}.issuperset(
        {
            "imposed",
            "wind_serviceability_positive_x",
            "wind_serviceability_negative_x",
            "wind_serviceability_positive_y",
            "wind_serviceability_negative_y",
            "wind_ultimate_positive_x",
            "wind_ultimate_negative_x",
            "wind_ultimate_positive_y",
            "wind_ultimate_negative_y",
        }
    )
    assert "load_combinations" not in type(effective_configuration).model_fields
    resolved = resolve_action_standard_pack(
        effective_configuration.action_standard_pack_id,
        effective_configuration.action_cases,
    )
    assert {combination.id for combination in resolved.load_combinations}.issuperset(
        {
            "SLS-G+WX+",
            "SLS-G+WX-",
            "ULS-1.2G+WX+",
            "ULS-1.2G+WX-",
            "ULS-0.9G+WX+",
            "ULS-0.9G+WX-",
            "SLS-G+WY+",
            "SLS-G+WY-",
            "ULS-1.2G+WY+",
            "ULS-1.2G+WY-",
            "ULS-0.9G+WY+",
            "ULS-0.9G+WY-",
            "SLS-G+Q",
            "ULS-1.2G+1.5Q",
            "SLS-G+Qc:roof-concentrated:RP1",
            "ULS-1.2G+1.5Qc:roof-concentrated:RP1",
            "SLS-G+Qc:roof-concentrated:RP2",
            "ULS-1.2G+1.5Qc:roof-concentrated:RP2",
        }
    )
    assert resolved.unavailable_combinations == []
    p399_members = [
        AnalyticalMemberDeclaration(
            id=str(member["id"]),
            label=str(member["component_id"]),
            component_id=str(member["component_id"]),
            start=Vector3.model_validate(
                dict(zip(("x", "y", "z"), member["start_m"], strict=True))
            ),
            end=Vector3.model_validate(
                dict(zip(("x", "y", "z"), member["end_m"], strict=True))
            ),
            start_restraints=(
                Restraints(dx=True, dy=True, dz=True, rx=True, ry=True, rz=True)
                if str(member["component_id"]).endswith(("CL", "CR"))
                else Restraints()
            ),
            section_id="section",
            material_id="material",
            assumption="P399 action-planning fixture.",
        )
        for member in analytical_members
    ]
    (
        p399_cases,
        p399_combinations,
        p399_stability,
        p399_unavailable,
        p399_warnings,
    ) = _p399_stability_actions(
        effective_configuration,
        components=components,
        members=p399_members,
        load_combinations=resolved.load_combinations,
    )
    assert len(p399_cases) == 8
    assert len(p399_combinations) == 8
    assert p399_stability is not None
    assert len(p399_stability.direction_cases) == 4
    assert p399_stability.column_component_ids == [
        "F1CL",
        "F1CR",
        "F2CL",
        "F2CR",
    ]
    assert p399_stability.analysis_base_model == "fixed"
    assert p399_stability.analysis_basis_status == "assumed"
    assert p399_unavailable == []
    assert p399_warnings == []

    pinned_members = [
        member.model_copy(
            update={
                "start_restraints": Restraints(dx=True, dy=True, dz=True)
                if member.component_id.endswith(("CL", "CR"))
                else member.start_restraints
            }
        )
        for member in p399_members
    ]
    _, _, pinned_stability, _, _ = _p399_stability_actions(
        effective_configuration,
        components=components,
        members=pinned_members,
        load_combinations=resolved.load_combinations,
    )
    assert pinned_stability is not None
    assert pinned_stability.analysis_base_model == "perfectly_pinned"
    assert pinned_stability.base_stiffness_status == "verified"
    assert pinned_stability.analysis_basis_status == "verified_conservative"
    assert pinned_stability.physical_connection_stiffness_status == "not_relied_upon"


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


def test_split_member_boundary_load_is_clamped_to_solver_axis(tmp_path) -> None:
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
    midpoint_distance = analytical_length / 2.0
    midpoint = [
        (start + end) / 2.0
        for start, end in zip(
            projected_purlin["start_m"], projected_purlin["end_m"], strict=True
        )
    ]
    first = deepcopy(projected_purlin)
    second = deepcopy(projected_purlin)
    first.update(
        {
            "id": "member:P1:segment:01",
            "end_m": midpoint,
            "physical_start_distance_m": 0.0,
            # Reproduce independent station calculations straddling a shared
            # endpoint by less than the declared numerical tolerance.
            "physical_end_distance_m": midpoint_distance - 5e-10,
        }
    )
    second.update(
        {
            "id": "member:P1:segment:02",
            "start_m": midpoint,
            "physical_start_distance_m": midpoint_distance,
            "physical_end_distance_m": analytical_length,
        }
    )
    projection["analytical_members"] = [
        member
        for member in projection["analytical_members"]
        if member["component_id"] != "P1"
    ] + [first, second]
    configuration_data = default_structural_configuration()
    configuration_data["member_loads"] = [
        {
            "id": "split-boundary-load",
            "label": "Split-boundary point action",
            "component_id": "P1",
            "case_id": "dead",
            "distance_m": midpoint_distance,
            "force": {"x": 0, "y": 0, "z": -0.1},
            "provenance": "Floating-point endpoint mapping regression.",
        }
    ]

    capture = _capture_from_structural_projection(
        projection,
        project_name="split-boundary-load",
        configuration=StructuralProjectConfiguration.model_validate(configuration_data),
    )
    assert capture.analysis is not None
    mapped = next(
        load
        for load in capture.analysis.member_loads
        if load.id == "split-boundary-load"
    )
    first_length = dist(first["start_m"], first["end_m"])

    assert mapped.member_id == first["id"]
    assert mapped.distance_m == first_length


def test_capture_accepts_machine_precision_load_endpoint_roundoff(tmp_path) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    configuration = StructuralProjectConfiguration.model_validate(
        default_structural_configuration()
    )
    capture_data = _capture_from_structural_projection(
        execution.projections["structural"],
        project_name="load-endpoint-roundoff",
        configuration=configuration,
    ).model_dump(mode="python")
    analysis = capture_data["analysis"]
    assert analysis is not None
    self_weight = next(
        load
        for load in analysis["member_distributed_loads"]
        if load["source_kind"] == "self_weight"
    )
    member = next(
        item for item in analysis["members"] if item["id"] == self_weight["member_id"]
    )
    member_length = dist(member["start"].values(), member["end"].values())
    self_weight["end_distance_m"] = member_length + 5e-10

    validated = ProjectStructuralCapture.model_validate(capture_data)

    assert validated.analysis is not None
    validated_load = next(
        load
        for load in validated.analysis.member_distributed_loads
        if load.id == self_weight["id"]
    )
    assert validated_load.end_distance_m == pytest.approx(member_length, abs=1e-9)


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
    structural_facet["section"].update(
        {
            "tension_width_mm": 30.0,
            "tension_thickness_mm": 1.0,
            "tension_hole_diameter_mm": 5.5,
            "tension_holes_in_critical_section": 2,
            "tension_force_distribution_factor": 1.0,
            "end_fastener_nominal_diameter_mm": 5.0,
            "end_fastener_spacing_mm": 15.0,
            "end_fastener_edge_distance_mm": 20.0,
        }
    )
    structural_facet["material"].update(
        {
            "yield_strength_pa": 450e6,
            "tensile_strength_pa": 480e6,
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
    section = next(
        item for item in capture.analysis.sections if item.id == declaration.section_id
    )
    material = next(
        item for item in capture.analysis.materials if item.id == declaration.material_id
    )
    assert section.tension_width_mm == pytest.approx(30.0)
    assert section.tension_hole_diameter_mm == pytest.approx(5.5)
    assert section.end_fastener_spacing_mm == pytest.approx(15.0)
    assert material.yield_strength_MPa == pytest.approx(450.0)
    assert material.tensile_strength_MPa == pytest.approx(480.0)
    component = next(item for item in capture.components if item.id == "P1")
    assert component.product_key == product_key
    assert component.product_definition_digest == projected_purlin[
        "product_definition_digest"
    ]
    assert component.structural_properties["end_fastener_count"] == 2
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


def test_rendered_connection_without_resistance_still_exposes_uls_demand(
    tmp_path,
) -> None:
    for filename, content in default_project_files().items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    execution = execute_design(tmp_path)
    projection = deepcopy(execution.projections["structural"])
    knee = next(
        joint for joint in projection["joints"] if joint["connection_id"] == "KNEE1"
    )
    knee["resistance"] = None
    configuration = StructuralProjectConfiguration.model_validate(
        default_structural_configuration()
    )

    snapshot = solve_project_structural(
        _capture_from_structural_projection(
            projection,
            project_name="demand-only-connection",
            configuration=configuration,
        )
    )
    check = next(
        item for item in snapshot.connection_checks if item.connection_id == "KNEE1"
    )

    assert check.status == "unsupported"
    assert check.evidence_status == "unverified"
    assert check.identity_status == "not_declared"
    assert check.pack_id == "unverified-rendered-connection"
    assert check.rendered_connector_part_numbers
    assert check.governing_combination_id is not None
    assert check.governing_member_id is not None
    assert check.moment_demand_kNm > 0
    assert check.design_moment_capacity_kNm is None
    assert "no resistance evidence pack" in check.assumptions[0].lower()


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
