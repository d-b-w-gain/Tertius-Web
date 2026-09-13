from __future__ import annotations

from collections import Counter
from typing import Any

import build123d as bd

from ._canonical import canonical_digest, thaw_json
from .session import CompileSession, TertiusRuntimeError


SCHEMA_VERSION = "1.0"


def _shape_label(shape: bd.Shape) -> str:
    return str(getattr(shape, "label", "") or "")[:200]


def _shape_kind(shape: bd.Shape) -> str:
    try:
        kind = shape.geom_type() if callable(shape.geom_type) else shape.geom_type
        return str(getattr(kind, "name", kind)).lower()
    except Exception:
        return type(shape).__name__


def _unmanaged_leaves(model: bd.Shape) -> list[dict[str, str]]:
    unmanaged: list[dict[str, str]] = []

    def visit(shape: bd.Shape, managed_ancestor: bool) -> None:
        managed = managed_ancestor or bool(getattr(shape, "tertius_component_token", None))
        children = [child for child in (getattr(shape, "children", ()) or ()) if isinstance(child, bd.Shape)]
        if not children:
            if not managed:
                unmanaged.append({"label": _shape_label(shape), "geometry_kind": _shape_kind(shape)})
            return
        for child in children:
            visit(child, managed)

    visit(model, False)
    return unmanaged[:1000]


def build_compiled_design_graph(session: CompileSession, model: bd.Shape) -> dict[str, Any]:
    if not isinstance(model, bd.Shape):
        raise TypeError("compiled design model must be a Build123D Shape")
    component_counts: Counter[str] = Counter()
    connection_counts: Counter[str] = Counter()
    for node in bd.PreOrderIter(model):
        if token := getattr(node, "tertius_component_token", None):
            component_counts[str(token)] += 1
        if token := getattr(node, "tertius_connection_token", None):
            connection_counts[str(token)] += 1

    missing = [component.instance_id for component in session.components if component_counts[component.token] == 0]
    repeated = [component.instance_id for component in session.components if component_counts[component.token] > 1]
    if missing:
        raise TertiusRuntimeError(
            f"managed components are missing from model: {sorted(missing)}"
        )
    if repeated:
        raise TertiusRuntimeError(
            f"managed components appear more than once in model: {sorted(repeated)}"
        )
    missing_connections = [
        connection.connection_id
        for connection in session.connections
        if connection_counts[connection.token] == 0
    ]
    if missing_connections:
        raise TertiusRuntimeError(
            f"physical connections are missing from model: {sorted(missing_connections)}"
        )

    component_id_by_token = {
        component.token: component.instance_id for component in session.components
    }
    connected_component_tokens = {
        port.component_token
        for connection in session.connections
        for port in connection.ports
    }
    used_connector_tokens = {
        token
        for connection in session.connections
        for token in connection.connector_component_tokens
    }
    unused_connectors = [
        component.instance_id
        for component in session.components
        if component.product.structural is not None
        and component.product.structural.kind == "connector"
        and component.token not in used_connector_tokens
    ]
    if unused_connectors:
        raise TertiusRuntimeError(
            f"managed connector components are unused: {sorted(unused_connectors)}"
        )

    components: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    procurement_complete = True
    structural_complete = True
    has_structural_components = False
    for component in session.components:
        product = component.product
        if product.classification == "orderable" and product.procurement is None:
            procurement_complete = False
        structural = product.structural
        if structural is not None and structural.kind in {"member", "surface", "support"}:
            has_structural_components = True
            if component.token not in connected_component_tokens:
                structural_complete = False
                diagnostics.append(
                    {
                        "code": "structural_component_unconnected",
                        "severity": "error",
                        "component_id": component.instance_id,
                        "message": "Structural component has no declared physical connection.",
                    }
                )
        components.append(
            {
                "id": component.instance_id,
                "mark": component.mark,
                "role": component.role,
                "product_key": product.key,
                "product_definition_digest": product.definition_digest,
                "fabrication": thaw_json(component.fabrication),
                "ports": [
                    {
                        "name": port.name,
                        "point_mm": list(port.point_mm),
                        "direction": list(port.direction),
                        "x_direction": list(port.x_direction),
                        "compatible_families": list(port.compatible_families),
                        "engagement_length_mm": port.engagement_length_mm,
                    }
                    for _, port in sorted(component.ports.items())
                ],
                "visual": {"label": _shape_label(component.shape)},
            }
        )

    if not has_structural_components:
        structural_complete = False

    connections: list[dict[str, Any]] = []
    for connection in session.connections:
        connections.append(
            {
                "id": connection.connection_id,
                "mark": connection.mark,
                "definition": connection.definition.payload(),
                "ports": [
                    {
                        "component_id": component_id_by_token[port.component_token],
                        "port": port.name,
                    }
                    for port in connection.ports
                ],
                "connector_component_ids": [
                    component_id_by_token[token]
                    for token in connection.connector_component_tokens
                ],
                "visual": {"label": _shape_label(connection.shape)},
            }
        )

    unmanaged = _unmanaged_leaves(model)
    if unmanaged:
        diagnostics.append(
            {
                "code": "unmanaged_geometry_present",
                "severity": "warning",
                "count": len(unmanaged),
                "message": (
                    "Raw Build123D geometry can render but is not authoritative for "
                    "procurement or drawings. Structural analysis includes only "
                    "explicitly managed structural components."
                ),
            }
        )
        procurement_complete = False

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "products": [product.payload() for product in session.products],
        "components": components,
        "connections": connections,
        "unmanaged_geometry": unmanaged,
        "readiness": {
            "mechanical_graph_valid": True,
            "procurement_complete": procurement_complete,
            "structural_model_complete": structural_complete,
            "structural_verified": False,
            "release_ready": False,
        },
        "diagnostics": diagnostics,
    }
    payload["compiled_design_digest"] = canonical_digest(payload)
    return payload
