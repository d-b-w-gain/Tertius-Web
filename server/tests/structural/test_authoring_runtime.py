from __future__ import annotations

import build123d as bd
import pytest

from core.structural.authoring_runtime import (
    StructuralAuthoringError,
    StructuralModel,
)


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
    assert section["catalog"]["catalog_id"] == "lysaght-zc-v2"
    assert section["catalog"]["axis_mapping"]["local_z_inertia"] == "Ix_mm4"
    assert section["catalog"]["properties"]["Zxe_mm3"] == 12300
    assert len(section["catalog"]["record_sha256"]) == 64
