from __future__ import annotations

import build123d as bd
import pytest

from core.structural.authoring_runtime import (
    StructuralAuthoringError,
    StructuralConnectorGeometry,
    StructuralMemberGeometry,
    StructuralModel,
    StructuralSurfaceGeometry,
)


def test_component_geometry_contract_keeps_cad_axis_area_and_fasteners_together():
    model = StructuralModel(title="Imported component contracts")
    material = model.material(
        id="steel",
        label="Steel",
        elastic_modulus_kN_m2=200_000_000.0,
        shear_modulus_kN_m2=76_923_000.0,
        poisson_ratio=0.3,
        density_kg_m3=7850.0,
    )
    section = model.section(
        id="cee",
        label="Cee",
        area_m2=0.001,
        iy_m4=1.0e-6,
        iz_m4=2.0e-6,
        torsion_j_m4=1.0e-8,
    )
    member_geometry = StructuralMemberGeometry(
        shape=bd.Box(100, 50, 1000),
        label="Imported Cee",
        part_number="C10012",
        start=(1.0, 2.0, 3.0),
        end=(1.0, 2.0, 4.0),
        rotation_deg=90.0,
    )
    surface_geometry = StructuralSurfaceGeometry(
        shape=bd.Box(1000, 1000, 1),
        label="Imported cladding panel",
        part_number="CUSTOM-ORB",
        area_m2=1.0,
    )
    connector_geometry = StructuralConnectorGeometry(
        shape=bd.Cylinder(3, 20),
        label="Imported screw pattern",
        part_number="12-14X35",
    )

    member = model.member_from_geometry(
        member_geometry,
        component_id="component-member",
        member_id="member-axis",
        section=section,
        material=material,
        start_restraints=(True, True, True, True, True, True),
        assumption="Imported builder axis.",
    )
    surface = model.surface_from_geometry(
        surface_geometry,
        component_id="component-surface",
    )
    connector = model.connector_from_geometry(
        connector_geometry,
        component_id="component-fasteners",
    )
    ground = model.ground(
        bd.Box(200, 200, 100),
        id="component-ground",
        label="Ground",
    )
    model.connect(
        surface,
        member,
        via=[connector],
        id="surface-member",
        label="Sheet fixed to Cee",
        transfers=["wind_normal", "force"],
    )
    model.connect(
        member,
        ground,
        id="member-ground",
        label="Cee reaches ground",
        transfers=["force", "shear", "moment"],
    )
    load = model.surface_load(
        surface,
        id="surface-load",
        label="Panel action",
        case="wind",
        pressure_kPa=1.0,
        area_m2=surface_geometry.area_m2,
        direction=(1.0, 0.0, 0.0),
        provenance="Builder-authored physical panel area.",
    )
    model.distribute_surface_load_uniform(
        load,
        member,
        id="surface-distribution",
        label="Panel to Cee",
        provenance="Uniform fixture distribution.",
    )
    model.assembly(
        [surface, connector, member, ground],
        label="imported-component-contract",
    )

    manifest = model.manifest()
    analytical = manifest["analysis"]["members"][0]
    assert analytical["start"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert analytical["end"] == {"x": 1.0, "y": 2.0, "z": 4.0}
    assert analytical["rotation_deg"] == 90.0
    assert manifest["loads"][0]["area_m2"] == 1.0


def test_structural_model_generates_manifest_from_registered_shape_handles():
    model = StructuralModel(title="Handle-authored model")
    sheet = model.surface(
        bd.Box(100, 2, 100),
        id="sheet",
        label="Roof sheet",
    )
    screws = model.connector(
        bd.Cylinder(2, 10),
        id="screws",
        label="Tek screws",
    )
    block = model.ground(
        bd.Box(100, 100, 100),
        id="block",
        label="Concrete block",
    )
    model.connect(
        sheet,
        block,
        via=[screws],
        id="sheet-ground",
        label="Sheet fixed to ground",
        transfers=["force", "shear"],
    )
    model.surface_load(
        sheet,
        id="wind",
        label="Wind",
        case="wind",
        pressure_kPa=0.8,
        area_m2=0.5,
        direction=(0, -1, 0),
        provenance="Test load",
    )

    assembly = model.assembly([sheet, screws, block], label="structural-model")
    manifest = model.manifest()

    assert sheet.shape.label == "sheet"
    assert manifest["authoring"] == {
        "mode": "generated",
        "assembly_component_ids": ["sheet", "screws", "block"],
    }
    assert manifest["connections"][0]["from_component_id"] == "sheet"
    assert manifest["connections"][0]["connector_component_ids"] == ["screws"]
    assert assembly.tertius_structural_manifest is manifest


def test_structural_model_links_literal_site_dict_without_storing_derived_truth():
    model = StructuralModel(title="Site-linked model")
    basis = model.site_wind_basis(
        {
            "schema_version": "1.0",
            "project_basis": {"importance_level": "2"},
            "location": {
                "address": "14 Porter St",
                "latitude": -34.4125,
                "longitude": 150.8886,
            },
            "wind": {
                "basis_id": "project-site-wind",
                "region": "A2",
                "region_area": "NSW",
                "region_source": "Test source",
                "region_status": "verified",
                "table_status": "verified",
                "terrain_category": "3",
                "reference_height_m": 1.6,
            },
        }
    )

    assert basis.id == "project-site-wind"
    assert model._wind_action_bases[0]["table_version"] == "compile-placeholder-v1"
    assert "replaced by the Structural API" in model._wind_action_bases[0]["provenance"]


def test_structural_model_rejects_raw_unregistered_assembly_shapes():
    model = StructuralModel(title="Fail closed")
    block = model.ground(
        bd.Box(100, 100, 100),
        id="block",
        label="Concrete block",
    )

    with pytest.raises(
        StructuralAuthoringError,
        match="registered StructuralPart handles only",
    ):
        model.assembly([block, bd.Box(10, 10, 10)], label="invalid")  # type: ignore[list-item]


def test_structural_model_rejects_unconnected_registered_members():
    model = StructuralModel(title="Fail closed")
    purlin = model.member(
        bd.Box(10, 10, 100),
        id="purlin",
        label="Purlin",
    )
    block = model.ground(
        bd.Box(100, 100, 100),
        id="block",
        label="Concrete block",
    )
    model.assembly([purlin, block], label="unconnected")

    with pytest.raises(
        StructuralAuthoringError,
        match=r"structural components have no declared connection: \['purlin'\]",
    ):
        model.manifest()


def test_surface_load_distribution_derives_member_loads_from_the_same_load_handle():
    model = StructuralModel(title="Analytical handles")
    sheet = model.surface(bd.Box(100, 2, 100), id="sheet", label="Sheet")
    purlin = model.member(bd.Box(10, 10, 1600), id="purlin", label="Purlin")
    block = model.ground(bd.Box(100, 100, 100), id="block", label="Block")
    steel = model.material(
        id="steel",
        label="Steel",
        elastic_modulus_kN_m2=200_000_000,
        shear_modulus_kN_m2=80_000_000,
        poisson_ratio=0.3,
        density_kg_m3=7850,
    )
    section = model.section(
        id="c100",
        label="C100",
        area_m2=409e-6,
        iy_m4=142000e-12,
        iz_m4=673000e-12,
        torsion_j_m4=492e-12,
    )
    model.member_axis(
        purlin,
        id="purlin-axis",
        label="Purlin",
        start=(0, 0, 0),
        end=(0, 0, 1.6),
        section=section,
        material=steel,
        start_restraints=(True, True, True, True, True, True),
        assumption="Fixed-base test.",
    )
    model.connect(
        sheet,
        purlin,
        id="sheet-purlin",
        label="Sheet to purlin",
        transfers=["force"],
    )
    model.connect(
        purlin,
        block,
        id="purlin-block",
        label="Purlin to block",
        transfers=["force", "moment"],
    )
    wind = model.surface_load(
        sheet,
        id="wind",
        label="Wind",
        case="wind",
        pressure_kPa=0.8,
        area_m2=0.9144,
        direction=(0, -1, 0),
        provenance="Test pressure.",
    )
    model.distribute_surface_load(
        wind,
        purlin,
        id="wind-screws",
        label="Screw load",
        positions_m=(0.35, 0.8, 1.25),
        provenance="Equal screw tributaries.",
    )
    model.assembly([sheet, purlin, block], label="analysis")

    manifest = model.manifest()

    point_loads = manifest["analysis"]["member_loads"]
    assert [load["force"]["y"] for load in point_loads] == pytest.approx(
        [-0.24384, -0.24384, -0.24384]
    )
    assert sum(load["force"]["y"] for load in point_loads) == pytest.approx(-0.73152)


def test_site_wind_basis_is_the_only_pressure_source_for_wind_surface_load():
    model = StructuralModel(title="Site wind handles")
    sheet = model.surface(bd.Box(100, 2, 100), id="sheet", label="Sheet")
    block = model.ground(bd.Box(100, 100, 100), id="block", label="Block")
    model.connect(
        sheet,
        block,
        id="sheet-ground",
        label="Sheet fixed to ground",
        transfers=["wind_normal", "force"],
    )
    basis = model.wind_action_basis(
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
    )
    wind = model.wind_surface_load(
        sheet,
        basis=basis,
        id="wind-inward",
        label="Inward wind",
        case_id="wind-inward",
        case_label="Inward wind",
        net_pressure_coefficient=0.9,
        coefficient_status="assumed",
        area_m2=0.5,
        direction=(0, -1, 0),
        provenance="Explicit assumed net coefficient.",
    )
    model.assembly([sheet, block], label="wind-test")

    manifest = model.manifest()

    assert manifest["wind_action_bases"][0]["q_z_kPa"] == pytest.approx(0.683438)
    load = manifest["loads"][0]
    assert load["id"] == wind.id
    assert load["pressure_kPa"] == pytest.approx(0.6150942)
    assert load["wind_basis_id"] == basis.id
    assert load["net_pressure_coefficient"] == pytest.approx(0.9)
    assert load["coefficient_status"] == "assumed"


def test_authored_point_load_and_stability_basis_are_emitted_together():
    model = StructuralModel(title="P-Delta frame")
    column = model.member(bd.Box(10, 10, 2000), id="column", label="Column")
    block = model.ground(bd.Box(100, 100, 100), id="block", label="Block")
    steel = model.material(
        id="steel",
        label="Steel",
        elastic_modulus_kN_m2=200_000_000,
        shear_modulus_kN_m2=80_000_000,
        poisson_ratio=0.3,
        density_kg_m3=7850,
    )
    section = model.section(
        id="section",
        label="Test section",
        area_m2=409e-6,
        iy_m4=142000e-12,
        iz_m4=673000e-12,
        torsion_j_m4=492e-12,
    )
    model.member_axis(
        column,
        id="column-axis",
        label="Column",
        start=(0, 0, 0),
        end=(0, 0, 2),
        section=section,
        material=steel,
        start_restraints=(True, True, True, True, True, True),
        assumption="Fixed-base test.",
    )
    model.connect(
        column,
        block,
        id="column-ground",
        label="Column to ground",
        transfers=["force", "shear", "moment"],
    )
    model.member_point_load(
        column,
        id="notional",
        label="Notional horizontal force",
        case="imperfection",
        case_id="imperfection-x",
        distance_m=2.0,
        force=(0.001, 0, 0),
        provenance="Explicit test imperfection.",
    )
    model.load_combination(
        id="ULS-STABILITY",
        label="Stability combination",
        limit_state="ultimate",
        factors={"imperfection-x": 1.0},
    )
    model.stability(
        method="p_delta",
        stability_combination_id="ULS-STABILITY",
        imperfection_case_id="imperfection-x",
        imperfection_basis="Explicit test imperfection.",
        base_stiffness_basis="Assumed fixed base.",
        base_stiffness_status="assumed",
        direction_cases=(
            {
                "id": "+X",
                "stability_combination_id": "ULS-STABILITY",
                "imperfection_case_id": "imperfection-x",
                "nhf_combination_id": "ULS-STABILITY",
                "horizontal_axis": "x",
            },
        ),
        eaves_member_ids=("column-axis",),
        column_height_m=2.0,
        analysis_base_model="perfectly_pinned",
        analysis_basis_status="verified_conservative",
        physical_connection_stiffness_status="not_relied_upon",
    )
    model.assembly([column, block], label="frame")

    analysis = model.manifest()["analysis"]

    assert analysis["member_loads"][0]["source_load_id"] is None
    assert analysis["load_cases"][0]["category"] == "imperfection"
    assert analysis["stability"]["stability_combination_id"] == "ULS-STABILITY"
    assert analysis["stability"]["direction_cases"][0] == {
        "id": "+X",
        "stability_combination_id": "ULS-STABILITY",
        "imperfection_case_id": "case-imperfection-x",
        "nhf_combination_id": "ULS-STABILITY",
        "horizontal_axis": "x",
    }
    assert analysis["stability"]["analysis_base_model"] == "perfectly_pinned"


def test_catalogue_section_registers_normalized_solver_data_and_provenance():
    model = StructuralModel(title="Catalogue-backed member")
    resolved = model.section_from_catalog(
        id="section-c10019",
        material_id="material-g450",
        record={
            "schema_version": "1.0",
            "catalog": {
                "id": "lysaght-zc-v2",
                "version": "2.0",
                "section_key": "C10019 (100x1.9)",
                "source": "Lysaght guide p.7-8",
            },
            "label": "C100x1.9 (Lysaght)",
            "solver": {
                "area_m2": 409e-6,
                "iy_m4": 142000e-12,
                "iz_m4": 673000e-12,
                "torsion_j_m4": 492e-12,
                "mass_kg_m": 3.29,
                "bending_reference_kNm": 5.535,
                "bending_reference_axis": "local_z",
                "bending_reference_basis": "Nominal Zxe times fy reference only.",
            },
            "material": {
                "label": "G450 steel",
                "elastic_modulus_kN_m2": 200_000_000,
                "shear_modulus_kN_m2": 80_000_000,
                "poisson_ratio": 0.3,
                "density_kg_m3": 7850,
            },
            "axis_mapping": {
                "local_y_inertia": "Iy_mm4",
                "local_z_inertia": "Ix_mm4",
            },
            "properties": {
                "A_mm2": 409,
                "Ix_mm4": 673000,
                "Iy_mm4": 142000,
                "J_mm4": 492,
                "fy_MPa": 450,
                "Zxe_mm3": 12300,
            },
        },
    )

    assert resolved.section.id == "section-c10019"
    assert resolved.material.id == "material-g450"
    section = model._sections[0]
    assert section["area_m2"] == pytest.approx(409e-6)
    assert section["mass_kg_m"] == pytest.approx(3.29)
    assert section["bending_reference_kNm"] == pytest.approx(5.535)
    assert section["bending_reference_axis"] == "local_z"
    assert section["bending_reference_basis"].startswith("Nominal Zxe")
    assert section["catalog"]["catalog_id"] == "lysaght-zc-v2"
    assert section["catalog"]["axis_mapping"]["local_z_inertia"] == "Ix_mm4"
    assert section["catalog"]["properties"]["Zxe_mm3"] == 12300
    assert len(section["catalog"]["record_sha256"]) == 64


def test_catalogue_member_self_weight_and_service_combination_are_authored():
    model = StructuralModel(title="Gravity member")
    beam = model.member(bd.Box(2000, 10, 10), id="beam", label="Beam")
    block = model.ground(bd.Box(100, 100, 100), id="block", label="Block")
    steel = model.material(
        id="steel",
        label="Steel",
        elastic_modulus_kN_m2=200_000_000,
        shear_modulus_kN_m2=80_000_000,
        poisson_ratio=0.3,
        density_kg_m3=7850,
    )
    section = model.section(
        id="c100",
        label="C100",
        area_m2=409e-6,
        iy_m4=142000e-12,
        iz_m4=673000e-12,
        torsion_j_m4=492e-12,
        mass_kg_m=3.29,
    )
    model.member_axis(
        beam,
        id="beam-axis",
        label="Beam",
        start=(0, 0, 0),
        end=(2, 0, 0),
        section=section,
        material=steel,
        start_restraints=(True, True, True, True, True, True),
        deflection_limit_ratio=250,
        deflection_limit_basis="Project demonstration criterion L/250.",
        assumption="Fixed cantilever demonstration.",
    )
    model.connect(
        beam,
        block,
        id="beam-ground",
        label="Beam to ground",
        transfers=["force", "moment"],
    )
    model.member_self_weight(
        beam,
        id="beam-self-weight",
        label="Beam self-weight",
    )
    model.load_combination(
        id="SLS-G",
        label="Permanent actions",
        limit_state="serviceability",
        factors={"dead": 1.0},
    )
    model.load_combination(
        id="ULS-G",
        label="Factored permanent actions",
        limit_state="ultimate",
        factors={"dead": 1.2},
    )
    model.member_stability_verification(
        pack_id="as_nzs_4600_2018_ewm_member",
        combination_ids=("ULS-G",),
        segments=(
            {
                "id": "beam-whole-length",
                "member_id": "beam-axis",
                "start_distance_m": 0.0,
                "end_distance_m": 2.0,
                "minor_axis_effective_length_factor": 1.0,
                "torsional_effective_length_factor": 1.0,
                "lateral_bending_restraint": "unverified",
                "restraint_status": "assumed",
                "restraint_basis": "Test segment has no credited lateral restraint.",
                "distortional_buckling_status": "unverified",
                "distortional_buckling_basis": (
                    "No distortional capacity is connected in this test."
                ),
            },
        ),
    )
    model.assembly([beam, block], label="gravity")

    manifest = model.manifest()

    line_load = manifest["analysis"]["member_distributed_loads"][0]
    assert line_load["start_force_kN_m"]["z"] == pytest.approx(-3.29 * 9.80665 / 1000)
    assert line_load["end_distance_m"] == pytest.approx(2.0)
    assert line_load["source_kind"] == "self_weight"
    assert manifest["analysis"]["load_combinations"][0]["factors"] == {"case-dead": 1.0}
    member_verification = manifest["analysis"]["member_stability_verification"]
    assert member_verification["pack_id"] == "as_nzs_4600_2018_ewm_member"
    assert member_verification["combination_ids"] == ["ULS-G"]
    assert member_verification["segments"][0]["member_id"] == "beam-axis"


def test_named_opposite_wind_cases_remain_distinct_in_manifest():
    model = StructuralModel(title="Opposite wind cases")
    sheet = model.surface(bd.Box(100, 2, 100), id="sheet", label="Sheet")
    block = model.ground(bd.Box(100, 100, 100), id="block", label="Block")
    model.connect(
        sheet,
        block,
        id="sheet-ground",
        label="Sheet to ground",
        transfers=["force"],
    )
    model.surface_load(
        sheet,
        id="wind-inward",
        label="Inward pressure",
        case="wind",
        case_id="wind-inward",
        case_label="Inward wind pressure",
        pressure_kPa=0.8,
        area_m2=0.5,
        direction=(0, -1, 0),
        provenance="Opposite-direction test.",
    )
    model.surface_load(
        sheet,
        id="wind-outward",
        label="Outward suction",
        case="wind",
        case_id="wind-outward",
        case_label="Outward wind suction",
        pressure_kPa=0.8,
        area_m2=0.5,
        direction=(0, 1, 0),
        provenance="Opposite-direction test.",
    )
    model.load_combination(
        id="SLS-WIN",
        label="Inward",
        limit_state="serviceability",
        factors={"wind-inward": 1.0},
    )
    model.load_combination(
        id="SLS-WOUT",
        label="Outward",
        limit_state="serviceability",
        factors={"wind-outward": 1.0},
    )
    model.assembly([sheet, block], label="opposite-wind")

    manifest = model.manifest()

    assert manifest["loads"][0]["case_id"] == "case-wind-inward"
    assert manifest["loads"][1]["case_id"] == "case-wind-outward"
    assert manifest["analysis"]["load_cases"] == [
        {
            "id": "case-wind-inward",
            "label": "Inward wind pressure",
            "category": "wind",
        },
        {
            "id": "case-wind-outward",
            "label": "Outward wind suction",
            "category": "wind",
        },
    ]
