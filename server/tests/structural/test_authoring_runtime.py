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
