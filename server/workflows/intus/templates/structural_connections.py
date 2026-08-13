"""Project-owned physical connection builders using the generic Tertius SDK."""

from __future__ import annotations

from functools import lru_cache
from math import dist, sqrt

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


def _vector3(values) -> tuple[float, float, float]:
    return (float(values[0]), float(values[1]), float(values[2]))


def _connection_plane(port) -> bd.Plane:
    z_direction = _vector3(port.direction)
    z_length = sqrt(sum(value * value for value in z_direction))
    z_direction = (
        z_direction[0] / z_length,
        z_direction[1] / z_length,
        z_direction[2] / z_length,
    )
    x_direction = _vector3(port.x_direction)
    return bd.Plane(
        origin=bd.Vector(*port.point_mm),
        x_dir=bd.Vector(*x_direction),
        z_dir=bd.Vector(*z_direction),
    )


def _unit(values: tuple[float, float, float]) -> tuple[float, float, float]:
    length = sqrt(sum(value * value for value in values))
    if length <= 1e-9:
        raise ValueError("connection frame requires a non-zero direction")
    return (values[0] / length, values[1] / length, values[2] / length)


def _cross(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _joint_plane(first_port, second_port) -> bd.Plane:
    offset = dist(first_port.point_mm, second_port.point_mm)
    if offset > 1.0:
        raise ValueError(
            f"member connection ports are {offset:g} mm apart; add explicit offset "
            "geometry or move the member endpoints"
        )
    first_axis = _unit(_vector3(first_port.direction))
    second_axis = _unit(_vector3(second_port.direction))
    normal_raw = _cross(first_axis, second_axis)
    if sqrt(sum(value * value for value in normal_raw)) <= 1e-6:
        normal_raw = _vector3(first_port.x_direction)
    normal = _unit(normal_raw)
    point = (
        (first_port.point_mm[0] + second_port.point_mm[0]) / 2.0,
        (first_port.point_mm[1] + second_port.point_mm[1]) / 2.0,
        (first_port.point_mm[2] + second_port.point_mm[2]) / 2.0,
    )
    return bd.Plane(
        origin=bd.Vector(*point),
        x_dir=bd.Vector(*first_axis),
        z_dir=bd.Vector(*normal),
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
                x_direction=port.x_direction,
                engagement_length_mm=20.0,
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


@lru_cache(maxsize=1)
def _knee_bracket_product() -> ProductDefinition:
    return ProductDefinition(
        key="project-fabricated:knee-gusset-180x180x6",
        label="Fabricated knee gusset 180×180×6",
        geometry={
            "shape": "gusset_plate",
            "width_mm": 180,
            "height_mm": 180,
            "thickness_mm": 6,
        },
        procurement=ProcurementFacet(
            part_number="FAB-KG-180X180X6",
            manufacturer="Project fabricated",
            material="G450 steel",
            ordering={
                "basis": "fabricated_each",
                "width_mm": 180,
                "height_mm": 180,
                "thickness_mm": 6,
            },
        ),
        structural=StructuralFacet(
            kind="connector",
            evidence_status="unverified",
            evidence_basis=(
                "Gusset geometry and procurement identity are authoritative; "
                "connection resistance and stiffness require project verification."
            ),
        ),
        drawing=DrawingFacet(
            name="Fabricated knee gusset",
            attributes={"width_mm": 180, "height_mm": 180, "thickness_mm": 6},
        ),
    )


@lru_cache(maxsize=1)
def _knee_bolt_product() -> ProductDefinition:
    return ProductDefinition(
        key="project-fastener:m12x30-knee-bolt-demo",
        label="M12×30 knee bolt demonstration item",
        geometry={"shape": "cylinder", "diameter_mm": 12, "length_mm": 30},
        procurement=ProcurementFacet(
            part_number="M12X30-BOLT-DEMO",
            manufacturer="Project nominated",
            material="Steel",
            ordering={"basis": "each", "diameter_mm": 12, "length_mm": 30},
        ),
        structural=StructuralFacet(
            kind="connector",
            evidence_status="unverified",
            evidence_basis=(
                "Bolt geometry and quantity are authoritative for the draft BoM; "
                "bolt group resistance is not verified."
            ),
        ),
        drawing=DrawingFacet(
            name="M12×30 knee bolt demonstration item",
            attributes={"diameter_mm": 12, "length_mm": 30},
        ),
    )


def bolted_rigid_knee(
    first_member: bd.Shape,
    second_member: bd.Shape,
    *,
    first_port_name: str,
    second_port_name: str,
    connection_family: str,
    mark: str,
) -> bd.Shape:
    """Join two member ports through one rendered and procured draft knee."""

    first_port = first_member.ports[first_port_name]
    second_port = second_member.ports[second_port_name]
    plane = _joint_plane(first_port, second_port)

    plate_shape = bd.Box(
        180,
        180,
        6,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.CENTER),
    ).moved(plane.location)
    plate_shape.label = f"{mark}-PLATE · FAB-KG-180X180X6"
    plate = managed_component(
        plate_shape,
        product=_knee_bracket_product(),
        mark=f"{mark}-PLATE",
        role="knee gusset",
    )

    bolts: list[bd.Shape] = []
    for index, (x_offset, y_offset) in enumerate(
        ((-55, 0), (55, 0), (0, -55), (0, 55)),
        start=1,
    ):
        bolt_shape = (
            bd.Cylinder(
                6,
                30,
                align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.CENTER),
            )
            .moved(bd.Pos(X=x_offset, Y=y_offset))
            .moved(plane.location)
        )
        bolt_shape.label = f"{mark}-B{index} · M12X30-BOLT-DEMO"
        bolts.append(
            managed_component(
                bolt_shape,
                product=_knee_bolt_product(),
                mark=f"{mark}-B{index}",
                role="knee bolt",
            )
        )

    assembly = bd.Compound(  # type: ignore[call-overload]
        children=[plate, *bolts],
        label=f"{mark} · bolted member knee",
    )
    definition = ConnectionDefinition(
        key="project-demo-bolted-rigid-knee",
        label="Bolted member knee (draft analysis model)",
        family=connection_family,
        transfers=("force", "shear", "moment"),
        analysis_model="rigid",
        stiffness_status="unverified",
        stiffness_basis=(
            "Rigid for draft elastic analysis only; gusset, bolts, local member "
            "effects, stiffness, and resistance require verification."
        ),
        maximum_port_offset_mm=1.0,
    )
    return physical_connection(
        assembly,
        definition=definition,
        ports=(first_port, second_port),
        connector_components=(plate, *bolts),
        mark=mark,
    )


__all__ = ["bolted_fixed_base", "bolted_rigid_knee"]
