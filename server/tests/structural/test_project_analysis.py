from __future__ import annotations

import pytest

from core.structural.design_capture import parse_project_structural_capture
from core.structural.project_analysis import solve_project_structural


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
    framework_id="SCI-P399",
    framework_label="SCI P399 verification process",
    framework_reference="Table 3.1 and Sections 4-12",
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
    assert service.design_basis.framework_id == "SCI-P399"
    assert len(service.calculation_sheets) == 11
    actions_sheet = next(
        sheet for sheet in service.calculation_sheets if sheet.stage_id == "actions"
    )
    assert actions_sheet.equations
    assert actions_sheet.related_member_ids == ["column-axis", "beam-axis"]


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
    amplification_warning_ratio=1.10,
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
