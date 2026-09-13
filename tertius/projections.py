from __future__ import annotations

from collections import defaultdict
from math import sqrt
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


def _connected_port_names(
    compiled_design: dict[str, Any],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for connection in compiled_design.get("connections", []):
        if not isinstance(connection, dict):
            continue
        for port in connection.get("ports", []):
            if not isinstance(port, dict):
                continue
            component_id = str(port.get("component_id") or "")
            port_name = str(port.get("port") or "")
            if component_id and port_name:
                result[component_id].add(port_name)
    return result


def _member_stations(
    *,
    ports: dict[str, dict[str, Any]],
    connected_port_names: set[str],
) -> list[dict[str, Any]]:
    """Return ordered analytical stations from the physical member's ports."""

    start = ports["start"]
    end = ports["end"]
    start_point = tuple(float(value) for value in start["point_mm"])
    end_point = tuple(float(value) for value in end["point_mm"])
    axis = tuple(end_point[index] - start_point[index] for index in range(3))
    length_squared = sum(value * value for value in axis)
    if length_squared <= 1e-12:
        return []
    length_mm = sqrt(length_squared)
    candidates: list[tuple[float, str, dict[str, Any]]] = [
        (0.0, "start", start),
        (length_mm, "end", end),
    ]
    for name in sorted(connected_port_names - {"start", "end"}):
        port = ports.get(name)
        if port is None:
            continue
        point = tuple(float(value) for value in port["point_mm"])
        offset = tuple(point[index] - start_point[index] for index in range(3))
        parameter = sum(offset[index] * axis[index] for index in range(3)) / length_squared
        projected = tuple(
            start_point[index] + parameter * axis[index] for index in range(3)
        )
        perpendicular_offset = sqrt(
            sum((point[index] - projected[index]) ** 2 for index in range(3))
        )
        station_mm = parameter * length_mm
        if perpendicular_offset <= 0.1 and -0.1 <= station_mm <= length_mm + 0.1:
            candidates.append((min(length_mm, max(0.0, station_mm)), name, port))

    candidates.sort(key=lambda item: (item[0], item[1]))
    stations: list[dict[str, Any]] = []
    for station_mm, name, port in candidates:
        if stations and abs(station_mm - float(stations[-1]["station_mm"])) <= 0.1:
            stations[-1]["port_names"].append(name)
            continue
        stations.append(
            {
                "station_mm": station_mm,
                "point_mm": [
                    start_point[index] + (station_mm / length_mm) * axis[index]
                    for index in range(3)
                ],
                "port_names": [name],
                "section_x_direction": list(port["x_direction"]),
            }
        )
    return stations


class _PortTopology:
    def __init__(self) -> None:
        self._parent: dict[tuple[str, str], tuple[str, str]] = {}
        self._joint_ids: dict[tuple[str, str], set[str]] = defaultdict(set)

    def _find(self, item: tuple[str, str]) -> tuple[str, str]:
        self._parent.setdefault(item, item)
        parent = self._parent[item]
        if parent != item:
            self._parent[item] = self._find(parent)
        return self._parent[item]

    def union(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        root = self._find(items[0])
        for item in items[1:]:
            other_root = self._find(item)
            if other_root != root:
                self._parent[other_root] = root

    def add_joint(self, items: list[tuple[str, str]], connection_id: str) -> None:
        self.union(items)
        for item in items:
            self._joint_ids[item].add(connection_id)

    def node_key(self, component_id: str, port_names: list[str]) -> str:
        items = [(component_id, name) for name in port_names]
        self.union(items)
        root = self._find(items[0])
        joint_ids: set[str] = set()
        for item in self._parent:
            if self._find(item) == root:
                joint_ids.update(self._joint_ids.get(item, set()))
        if joint_ids:
            return "joint:" + "+".join(sorted(joint_ids))
        return f"endpoint:{component_id}:{port_names[0]}"


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
    connected_port_names = _connected_port_names(compiled_design)
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
                "fabrication": component.get("fabrication") or {},
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
        stations = _member_stations(
            ports=ports,
            connected_port_names=connected_port_names.get(str(component["id"]), set()),
        )
        if len(stations) < 2:
            continue
        segment_count = len(stations) - 1
        for segment_index, (segment_start, segment_end) in enumerate(
            zip(stations, stations[1:]),
            start=1,
        ):
            base_member_id = f"member:{component['id']}"
            structural_properties = structural.get("properties") or {}
            analytical_members.append({
                "id": (
                    base_member_id
                    if segment_count == 1
                    else f"{base_member_id}:segment:{segment_index:02d}"
                ),
                "physical_member_id": base_member_id,
                "component_id": component["id"],
                "product_key": component["product_key"],
                "product_definition_digest": component[
                    "product_definition_digest"
                ],
                "segment_index": segment_index,
                "segment_count": segment_count,
                "physical_start_distance_m": float(segment_start["station_mm"]) / 1000.0,
                "physical_end_distance_m": float(segment_end["station_mm"]) / 1000.0,
                "start_m": [
                    float(value) / 1000.0 for value in segment_start["point_mm"]
                ],
                "end_m": [
                    float(value) / 1000.0 for value in segment_end["point_mm"]
                ],
                "start_port_names": list(segment_start["port_names"]),
                "end_port_names": list(segment_end["port_names"]),
                "section_x_direction": list(segment_start["section_x_direction"]),
                "section": structural.get("section") or {},
                "material": structural.get("material") or {},
                "evidence_status": structural.get("evidence_status"),
                "evidence_basis": structural.get("evidence_basis"),
                "tension_only": bool(structural_properties.get("tension_only")),
                "compression_only": bool(
                    structural_properties.get("compression_only")
                ),
                "tension_capacity_status": structural_properties.get(
                    "tension_capacity_status",
                    "not_checked",
                ),
                "tension_capacity_kN": structural_properties.get(
                    "tension_capacity_kN"
                ),
                "tension_capacity_basis": structural_properties.get(
                    "tension_capacity_basis"
                ),
                "end_fastener_count": structural_properties.get(
                    "end_fastener_count"
                ),
                "end_connection_capacity_kN": structural_properties.get(
                    "end_connection_capacity_kN"
                ),
                "end_connection_basis": structural_properties.get(
                    "end_connection_basis"
                ),
                "profile_rotation_deg": float(
                    (component.get("fabrication") or {}).get("rotation_deg") or 0.0
                ),
            })

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

    topology = _PortTopology()
    for joint in joints:
        joint_ports = [
            (str(port["component_id"]), str(port["port"]))
            for port in joint["ports"]
            if isinstance(port, dict) and port.get("component_id") and port.get("port")
        ]
        topology.add_joint(joint_ports, str(joint["connection_id"]))
    for member in analytical_members:
        component_id = str(member["component_id"])
        topology.union(
            [(component_id, str(name)) for name in member["start_port_names"]]
        )
        topology.union(
            [(component_id, str(name)) for name in member["end_port_names"]]
        )
    for member in analytical_members:
        component_id = str(member["component_id"])
        member["start_node_key"] = topology.node_key(
            component_id,
            list(member["start_port_names"]),
        )
        member["end_node_key"] = topology.node_key(
            component_id,
            list(member["end_port_names"]),
        )

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
