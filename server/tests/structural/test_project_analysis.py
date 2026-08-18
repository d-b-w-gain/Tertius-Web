from __future__ import annotations

from copy import deepcopy

import pytest
import build123d as bd

from core.site_definition import apply_site_definition, default_site_definition
from core.structural.authoring_runtime import (
    StructuralMemberGeometry,
    StructuralModel,
)
from core.structural.contracts import (
    DesignComponent,
    DesignConnection,
    MemberStabilityComparison,
    ProjectStructuralCapture,
)
from core.structural.design_capture import (
    capture_project_structural_declaration,
    parse_project_structural_capture,
)
from core.structural.project_analysis import (
    _off_axis_load_path,
    _stability_scope_comparisons,
    solve_project_structural,
)


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
    assert service.certification_readiness.ready_for_certificate is False
    assert service.certification_readiness.document_status == "engineering_review_draft"
    assert "DRAFT ENGINEERING REVIEW REPORT" in (
        service.certification_readiness.draft_document_label
    )
    actions_sheet = next(
        sheet for sheet in service.calculation_sheets if sheet.stage_id == "actions"
    )
    assert actions_sheet.equations
    assert actions_sheet.related_member_ids == ["column-axis", "beam-axis"]


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
        candidate["evidence_pack_id"] = "lysaght-zc-2026-07-c10012-100ac-pb1230hs"
        candidate["configuration"] = {
            "primary_part_number": "C10019",
            "bracing_part_number": "C10012",
            "connector_part_numbers": ["100AC-M12X25-END-JOINTS"],
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
