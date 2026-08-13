"""Project-owned physical connection builders using the generic Tertius SDK."""

from __future__ import annotations

from functools import lru_cache
from math import sqrt

import build123d as bd

from tertius import (
    ConnectionDefinition,
    DrawingFacet,
    PortPlacement,
    ProcurementFacet,
    ProductDefinition,
    StructuralFacet,
    managed_component,
    physical_connection,
)


def _connection_plane(port) -> bd.Plane:
    z_direction = tuple(float(value) for value in port.direction)
    z_length = sqrt(sum(value * value for value in z_direction))
    z_direction = tuple(value / z_length for value in z_direction)
    reference = (1.0, 0.0, 0.0)
    if abs(sum(z_direction[index] * reference[index] for index in range(3))) > 0.95:
        reference = (0.0, 1.0, 0.0)
    projection = sum(
        z_direction[index] * reference[index] for index in range(3)
    )
    x_direction = tuple(
        reference[index] - projection * z_direction[index] for index in range(3)
    )
    x_length = sqrt(sum(value * value for value in x_direction))
    x_direction = tuple(value / x_length for value in x_direction)
    return bd.Plane(
        origin=bd.Vector(*port.point_mm),
        x_dir=bd.Vector(*x_direction),
        z_dir=bd.Vector(*z_direction),
    )


@lru_cache(maxsize=1)
def _ground_product(connection_family: str) -> ProductDefinition:
    return ProductDefinition(
        key=f"project-reference:ground:{connection_family}",
        label="Ground/reference foundation",
        classification="reference",
        geometry={"shape": "foundation_block", "width_mm": 300, "depth_mm": 150},
        structural=StructuralFacet(
            kind="ground",
            evidence_status="unverified",
            evidence_basis=(
                "Ground is a project reference boundary. Foundation and anchor "
                "capacity are not verified by this demonstration definition."
            ),
        ),
        drawing=DrawingFacet(name="Ground/reference foundation"),
        port_families={"top": [connection_family]},
    )


@lru_cache(maxsize=1)
def _base_plate_product() -> ProductDefinition:
    return ProductDefinition(
        key="project-fabricated:base-plate-150x150x10",
        label="Fabricated base plate 150×150×10",
        geometry={"shape": "plate", "width_mm": 150, "height_mm": 150, "thickness_mm": 10},
        procurement=ProcurementFacet(
            part_number="FAB-BP-150X150X10",
            manufacturer="Project fabricated",
            material="G450 steel",
            ordering={
                "basis": "fabricated_each",
                "width_mm": 150,
                "height_mm": 150,
                "thickness_mm": 10,
            },
        ),
        structural=StructuralFacet(
            kind="connector",
            evidence_status="unverified",
            evidence_basis=(
                "Rendered and included in procurement; connection resistance and "
                "stiffness require project verification."
            ),
        ),
        drawing=DrawingFacet(
            name="Fabricated base plate",
            attributes={"width_mm": 150, "height_mm": 150, "thickness_mm": 10},
        ),
    )


@lru_cache(maxsize=1)
def _anchor_product() -> ProductDefinition:
    return ProductDefinition(
        key="project-fastener:m12x100-anchor-demo",
        label="M12×100 anchor demonstration item",
        geometry={"shape": "cylinder", "diameter_mm": 12, "length_mm": 100},
        procurement=ProcurementFacet(
            part_number="M12X100-ANCHOR-DEMO",
            manufacturer="Project nominated",
            material="Steel",
            ordering={"basis": "each", "diameter_mm": 12, "length_mm": 100},
        ),
        structural=StructuralFacet(
            kind="connector",
            evidence_status="unverified",
            evidence_basis=(
                "Anchor geometry and quantity are authoritative for the draft BoM; "
                "anchor capacity is not verified."
            ),
        ),
        drawing=DrawingFacet(
            name="M12×100 anchor demonstration item",
            attributes={"diameter_mm": 12, "length_mm": 100},
        ),
    )


def bolted_fixed_base(
    member: bd.Shape,
    *,
    port_name: str,
    connection_family: str,
    mark: str,
) -> bd.Shape:
    """Render and register a member-to-ground fixed-base draft connection."""

    port = member.ports[port_name]
    plane = _connection_plane(port)

    ground_shape = bd.Box(
        300,
        300,
        150,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    ).moved(bd.Location((0, 0, 10))).moved(plane.location)
    ground_shape.label = f"{mark}-GROUND · reference foundation"
    ground = managed_component(
        ground_shape,
        product=_ground_product(connection_family),
        mark=f"{mark}-GROUND",
        role="ground reference",
        ports={
            "top": PortPlacement(
                port.point_mm,
                tuple(-value for value in port.direction),
                (connection_family,),
            )
        },
    )

    plate_shape = bd.Box(
        150,
        150,
        10,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    ).moved(plane.location)
    plate_shape.label = f"{mark}-PLATE · FAB-BP-150X150X10"
    plate = managed_component(
        plate_shape,
        product=_base_plate_product(),
        mark=f"{mark}-PLATE",
        role="base plate",
    )

    anchors: list[bd.Shape] = []
    for index, (x_offset, y_offset) in enumerate(
        ((-50, -50), (50, -50), (50, 50), (-50, 50)),
        start=1,
    ):
        anchor_shape = bd.Cylinder(
            6,
            100,
            align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
        ).moved(bd.Location((x_offset, y_offset, 0))).moved(plane.location)
        anchor_shape.label = f"{mark}-A{index} · M12X100-ANCHOR-DEMO"
        anchors.append(
            managed_component(
                anchor_shape,
                product=_anchor_product(),
                mark=f"{mark}-A{index}",
                role="anchor bolt",
            )
        )

    assembly = bd.Compound(  # type: ignore[call-overload]
        children=[ground, plate, *anchors],
        label=f"{mark} · bolted fixed base",
    )
    definition = ConnectionDefinition(
        key="project-demo-bolted-fixed-base",
        label="Bolted fixed base (draft analysis model)",
        family=connection_family,
        transfers=("force", "shear", "moment"),
        analysis_model="rigid",
        stiffness_status="unverified",
        stiffness_basis=(
            "Fixed for draft elastic analysis only; plate, anchors, concrete, "
            "stiffness, and resistance require verification."
        ),
    )
    return physical_connection(
        assembly,
        definition=definition,
        ports=(port, ground.ports.top),
        connector_components=(plate, *anchors),
        mark=mark,
    )


__all__ = ["bolted_fixed_base"]
