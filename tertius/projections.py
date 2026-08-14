from __future__ import annotations

from collections import defaultdict
from typing import Any

import build123d as bd

from ._canonical import canonical_digest


PROCUREMENT_SCHEMA = "tertius.procurement.v1"
STRUCTURAL_SCHEMA = "tertius.structural.v1"
DRAWING_SCHEMA = "tertius.drawing.v1"
BOUNDS_SCHEMA = "tertius.bounds.v1"


def _with_projection_digest(payload: dict[str, Any]) -> dict[str, Any]:
    payload["projection_digest"] = canonical_digest(payload)
    return payload


def _product_map(compiled_design: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(product["key"]): product
        for product in compiled_design.get("products", [])
        if isinstance(product, dict) and product.get("key")
    }


def procurement_projection(compiled_design: dict[str, Any]) -> dict[str, Any]:
    products = _product_map(compiled_design)
    requirements: list[dict[str, Any]] = []
    for component in compiled_design.get("components", []):
        if not isinstance(component, dict):
            continue
        product = products.get(str(component.get("product_key") or ""))
        procurement = product.get("procurement") if product else None
        if not isinstance(procurement, dict):
            continue
        assert product is not None
        if product.get("classification") != "orderable":
            continue
        fabrication = component.get("fabrication")
        requirements.append(
            {
                "id": f"requirement:{component['id']}",
                "component_id": component["id"],
                "product_key": component["product_key"],
                "product_definition_digest": component["product_definition_digest"],
                "part_number": procurement["part_number"],
                "quantity": 1,
                "unit": procurement["unit"],
                "manufacturer": procurement.get("manufacturer"),
                "material": procurement.get("material"),
                "finish": procurement.get("finish"),
                "standard": procurement.get("standard"),
                "dimensions": fabrication if isinstance(fabrication, dict) else {},
                "ordering": procurement.get("ordering") or {},
                "orderable": True,
            }
        )
    readiness = compiled_design.get("readiness") or {}
    return _with_projection_digest(
        {
            "schema_version": PROCUREMENT_SCHEMA,
            "compiled_design_digest": compiled_design["compiled_design_digest"],
            "assemblies": [],
            "components": [
                {
                    "id": component["id"],
                    "label": component.get("mark") or component["id"],
                    "mark": component.get("mark"),
                    "role": component.get("role") or "component",
                    "scope_id": None,
                    "visual_node_ids": [component["id"]],
                    "product_key": component["product_key"],
                    "product_definition_digest": component[
                        "product_definition_digest"
                    ],
                }
                for component in compiled_design.get("components", [])
                if isinstance(component, dict)
            ],
            "requirements": requirements,
            "readiness": {
                "complete": bool(readiness.get("procurement_complete")),
                "order_release_allowed": bool(readiness.get("release_ready")),
            },
            "diagnostics": [
                diagnostic
                for diagnostic in compiled_design.get("diagnostics", [])
                if isinstance(diagnostic, dict)
            ],
        }
    )


def structural_projection(compiled_design: dict[str, Any]) -> dict[str, Any]:
    products = _product_map(compiled_design)
    product_facets: list[dict[str, Any]] = []
    for product in products.values():
        structural = product.get("structural")
        if not isinstance(structural, dict):
            continue
        product_facets.append(
            {
                "product_key": product["key"],
                "product_definition_digest": product["definition_digest"],
                "label": product.get("label") or product["key"],
                "catalogue": product.get("catalogue"),
                "procurement": product.get("procurement"),
                **structural,
            }
        )

    analytical_members: list[dict[str, Any]] = []
    structural_components: list[dict[str, Any]] = []
    for component in compiled_design.get("components", []):
        if not isinstance(component, dict):
            continue
        selected_product = products.get(str(component.get("product_key") or ""))
        structural = selected_product.get("structural") if selected_product else None
        if not isinstance(structural, dict):
            continue
        structural_components.append(
            {
                "component_id": component["id"],
                "product_key": component["product_key"],
                "product_definition_digest": component[
                    "product_definition_digest"
                ],
                "kind": structural["kind"],
                "mark": component.get("mark"),
                "role": component.get("role"),
                "part_number": (
                    (selected_product.get("procurement") or {}).get("part_number")
                    if selected_product
                    else None
                ),
                "ports": component.get("ports") or [],
            }
        )
        if structural.get("kind") != "member":
            continue
        ports = {
            str(port.get("name")): port
            for port in component.get("ports", [])
            if isinstance(port, dict) and port.get("name")
        }
        start = ports.get("start")
        end = ports.get("end")
        if start is None or end is None:
            continue
        analytical_members.append(
            {
                "id": f"member:{component['id']}",
                "component_id": component["id"],
                "product_key": component["product_key"],
                "product_definition_digest": component[
                    "product_definition_digest"
                ],
                "start_m": [float(value) / 1000.0 for value in start["point_mm"]],
                "end_m": [float(value) / 1000.0 for value in end["point_mm"]],
                "section_x_direction": list(start["x_direction"]),
                "section": structural.get("section") or {},
                "material": structural.get("material") or {},
                "evidence_status": structural.get("evidence_status"),
                "evidence_basis": structural.get("evidence_basis"),
                "profile_rotation_deg": float(
                    (component.get("fabrication") or {}).get("rotation_deg") or 0.0
                ),
            }
        )

    joints: list[dict[str, Any]] = []
    connectivity: dict[str, list[str]] = defaultdict(list)
    for connection in compiled_design.get("connections", []):
        if not isinstance(connection, dict):
            continue
        definition = connection.get("definition") or {}
        connected_ports = connection.get("ports") or []
        joint = {
            "id": f"joint:{connection['id']}",
            "connection_id": connection["id"],
            "connection_definition_digest": definition.get("definition_digest"),
            "ports": connected_ports,
            "connector_component_ids": connection.get("connector_component_ids") or [],
            "transfers": definition.get("transfers") or [],
            "analysis_model": definition.get("analysis_model"),
            "stiffness_status": definition.get("stiffness_status"),
            "stiffness_basis": definition.get("stiffness_basis"),
            "maximum_port_offset_mm": definition.get("maximum_port_offset_mm"),
            "resistance": definition.get("resistance"),
        }
        joints.append(joint)
        for port in connected_ports:
            if isinstance(port, dict) and port.get("component_id"):
                connectivity[str(port["component_id"])].append(joint["id"])

    readiness = compiled_design.get("readiness") or {}
    return _with_projection_digest(
        {
            "schema_version": STRUCTURAL_SCHEMA,
            "compiled_design_digest": compiled_design["compiled_design_digest"],
            "product_facets": product_facets,
            "components": structural_components,
            "analytical_members": analytical_members,
            "joints": joints,
            "connectivity": dict(sorted(connectivity.items())),
            "readiness": {
                "model_complete": bool(readiness.get("structural_model_complete")),
                "verified": bool(readiness.get("structural_verified")),
                "analysis_context_required": not bool(
                    readiness.get("structural_verified")
                ),
            },
            "diagnostics": [
                diagnostic
                for diagnostic in compiled_design.get("diagnostics", [])
                if isinstance(diagnostic, dict)
            ],
        }
    )


def drawing_projection(compiled_design: dict[str, Any]) -> dict[str, Any]:
    products = _product_map(compiled_design)
    items: list[dict[str, Any]] = []
    for component in compiled_design.get("components", []):
        if not isinstance(component, dict):
            continue
        product = products.get(str(component.get("product_key") or ""))
        drawing = product.get("drawing") if product else None
        items.append(
            {
                "component_id": component["id"],
                "mark": component.get("mark"),
                "role": component.get("role"),
                "product_key": component["product_key"],
                "product_definition_digest": component[
                    "product_definition_digest"
                ],
                "name": (
                    drawing.get("name")
                    if isinstance(drawing, dict)
                    else product.get("label") if product else component["id"]
                ),
                "attributes": (
                    drawing.get("attributes") or {}
                    if isinstance(drawing, dict)
                    else {}
                ),
                "fabrication": component.get("fabrication") or {},
                "ports": component.get("ports") or [],
            }
        )
    return _with_projection_digest(
        {
            "schema_version": DRAWING_SCHEMA,
            "compiled_design_digest": compiled_design["compiled_design_digest"],
            "items": items,
            "connections": [
                {
                    "connection_id": connection["id"],
                    "mark": connection.get("mark"),
                    "ports": connection.get("ports") or [],
                    "connector_component_ids": connection.get(
                        "connector_component_ids"
                    )
                    or [],
                }
                for connection in compiled_design.get("connections", [])
                if isinstance(connection, dict)
            ],
        }
    )


def bounds_projection(
    compiled_design: dict[str, Any],
    model: bd.Shape,
) -> dict[str, Any]:
    bounds = model.bounding_box()
    minimum = [float(bounds.min.X), float(bounds.min.Y), float(bounds.min.Z)]
    maximum = [float(bounds.max.X), float(bounds.max.Y), float(bounds.max.Z)]
    size = [maximum[index] - minimum[index] for index in range(3)]
    return _with_projection_digest(
        {
            "schema_version": BOUNDS_SCHEMA,
            "compiled_design_digest": compiled_design["compiled_design_digest"],
            "minimum_mm": minimum,
            "maximum_mm": maximum,
            "size_mm": size,
            "max_dimension_mm": max(size),
        }
    )


def all_workbench_projections(
    compiled_design: dict[str, Any],
    *,
    model: bd.Shape,
) -> dict[str, dict[str, Any]]:
    return {
        "procurement": procurement_projection(compiled_design),
        "structural": structural_projection(compiled_design),
        "drawing": drawing_projection(compiled_design),
        "bounds": bounds_projection(compiled_design, model),
    }
