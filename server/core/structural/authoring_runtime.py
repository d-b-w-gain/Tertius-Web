from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import build123d as bd


ComponentKind = Literal["ground", "member", "surface", "connector", "support"]
TransferKind = Literal["force", "shear", "moment", "wind_normal"]


class StructuralAuthoringError(ValueError):
    """Raised when structural CAD authoring would create an ambiguous manifest."""


@dataclass(frozen=True)
class StructuralPart:
    """A registered Build123D shape and its structural identity."""

    shape: bd.Shape
    component_id: str
    kind: ComponentKind


class StructuralModel:
    """Build a structural manifest from the same handles used to assemble CAD."""

    def __init__(self, *, title: str) -> None:
        self.title = _required_text("model title", title)
        self._components: list[dict[str, Any]] = []
        self._parts_by_id: dict[str, StructuralPart] = {}
        self._connections: list[dict[str, Any]] = []
        self._loads: list[dict[str, Any]] = []
        self._assembled_ids: list[str] | None = None
        self._assembly: bd.Compound | None = None

    def ground(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="ground",
            visual_node_id=visual_node_id,
            part_number=part_number,
            grounded=True,
        )

    def member(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="member",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def surface(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="surface",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def connector(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="connector",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def support(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="support",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def connect(
        self,
        from_component: StructuralPart,
        to_component: StructuralPart,
        *,
        via: Sequence[StructuralPart] = (),
        id: str,
        label: str,
        transfers: Sequence[TransferKind],
    ) -> None:
        source = self._require_registered(from_component)
        target = self._require_registered(to_component)
        if source.component_id == target.component_id:
            raise StructuralAuthoringError(
                f"connection {id!r} connects a component to itself"
            )
        connection_id = _required_text("connection ID", id)
        if any(item["id"] == connection_id for item in self._connections):
            raise StructuralAuthoringError(
                f"connection ID {connection_id!r} is already registered"
            )

        connector_ids: list[str] = []
        for connector in via:
            registered = self._require_registered(connector)
            if registered.kind != "connector":
                raise StructuralAuthoringError(
                    f"connection {connection_id!r} via component "
                    f"{registered.component_id!r} is not a connector"
                )
            if registered.component_id in connector_ids:
                raise StructuralAuthoringError(
                    f"connection {connection_id!r} repeats connector "
                    f"{registered.component_id!r}"
                )
            connector_ids.append(registered.component_id)

        transfer_values = [_required_text("connection transfer", item) for item in transfers]
        allowed_transfers = {"force", "shear", "moment", "wind_normal"}
        invalid_transfers = sorted(set(transfer_values) - allowed_transfers)
        if invalid_transfers:
            raise StructuralAuthoringError(
                f"connection {connection_id!r} has unsupported transfers "
                f"{invalid_transfers}"
            )
        if not transfer_values:
            raise StructuralAuthoringError(
                f"connection {connection_id!r} must declare transferred actions"
            )

        self._connections.append(
            {
                "id": connection_id,
                "label": _required_text("connection label", label),
                "from_component_id": source.component_id,
                "to_component_id": target.component_id,
                "connector_component_ids": connector_ids,
                "transfers": transfer_values,
            }
        )

    def surface_load(
        self,
        component: StructuralPart,
        *,
        id: str,
        label: str,
        case: Literal["dead", "live", "wind"],
        pressure_kPa: float,
        area_m2: float,
        direction: Sequence[float] | dict[str, float],
        provenance: str,
    ) -> None:
        registered = self._require_registered(component)
        if registered.kind != "surface":
            raise StructuralAuthoringError(
                f"surface load component {registered.component_id!r} is not a surface"
            )
        load_id = _required_text("load ID", id)
        if any(item["id"] == load_id for item in self._loads):
            raise StructuralAuthoringError(f"load ID {load_id!r} is already registered")
        if case not in {"dead", "live", "wind"}:
            raise StructuralAuthoringError(f"unsupported load case {case!r}")
        pressure = float(pressure_kPa)
        area = float(area_m2)
        if pressure == 0:
            raise StructuralAuthoringError(f"load {load_id!r} has zero pressure")
        if area <= 0:
            raise StructuralAuthoringError(f"load {load_id!r} has non-positive area")

        vector = _vector3(direction)
        if vector == {"x": 0.0, "y": 0.0, "z": 0.0}:
            raise StructuralAuthoringError(f"load {load_id!r} has a zero direction")
        self._loads.append(
            {
                "id": load_id,
                "label": _required_text("load label", label),
                "case": case,
                "component_id": registered.component_id,
                "pressure_kPa": pressure,
                "area_m2": area,
                "direction": vector,
                "provenance": _required_text("load provenance", provenance),
            }
        )

    def assembly(
        self,
        parts: Sequence[StructuralPart],
        *,
        label: str,
    ) -> bd.Compound:
        if self._assembly is not None:
            raise StructuralAuthoringError("the structural assembly is already defined")

        registered_parts = [self._require_registered(part) for part in parts]
        assembled_ids = [part.component_id for part in registered_parts]
        if len(assembled_ids) != len(set(assembled_ids)):
            raise StructuralAuthoringError(
                "the structural assembly contains a registered component more than once"
            )
        missing_ids = sorted(set(self._parts_by_id) - set(assembled_ids))
        if missing_ids:
            raise StructuralAuthoringError(
                f"registered structural components are missing from the assembly: {missing_ids}"
            )
        if set(assembled_ids) != set(self._parts_by_id):
            raise StructuralAuthoringError(
                "the structural assembly contains unregistered components"
            )

        assembly_shapes = [part.shape for part in registered_parts]
        assembly = bd.Compound(
            assembly_shapes,
            label=_required_text("assembly label", label),
            children=assembly_shapes,
        )
        self._assembled_ids = assembled_ids
        self._assembly = assembly
        return assembly

    def manifest(self) -> dict[str, Any]:
        if self._assembly is None or self._assembled_ids is None:
            raise StructuralAuthoringError(
                "call StructuralModel.assembly(...) before generating the manifest"
            )
        self._validate_topology()
        manifest = {
            "title": self.title,
            "authoring": {
                "mode": "generated",
                "assembly_component_ids": list(self._assembled_ids),
            },
            "components": [dict(component) for component in self._components],
            "connections": [
                {
                    **connection,
                    "connector_component_ids": list(
                        connection["connector_component_ids"]
                    ),
                    "transfers": list(connection["transfers"]),
                }
                for connection in self._connections
            ],
            "loads": [
                {**load, "direction": dict(load["direction"])} for load in self._loads
            ],
        }
        self._assembly.tertius_structural_manifest = manifest
        self._assembly.tertius_structural_component_ids = tuple(self._assembled_ids)
        return manifest

    def _register(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        kind: ComponentKind,
        visual_node_id: str | None,
        part_number: str | None,
        grounded: bool = False,
    ) -> StructuralPart:
        if self._assembly is not None:
            raise StructuralAuthoringError(
                "components cannot be registered after the assembly is defined"
            )
        if not isinstance(shape, bd.Shape):
            raise StructuralAuthoringError(
                f"component {id!r} must wrap a Build123D Shape"
            )
        component_id = _required_text("component ID", id)
        if component_id in self._parts_by_id:
            raise StructuralAuthoringError(
                f"component ID {component_id!r} is already registered"
            )
        node_id = _required_text(
            "visual node ID",
            visual_node_id if visual_node_id is not None else component_id,
        )
        if any(component["visual_node_id"] == node_id for component in self._components):
            raise StructuralAuthoringError(
                f"visual node ID {node_id!r} is already registered"
            )

        shape.label = node_id
        part = StructuralPart(shape=shape, component_id=component_id, kind=kind)
        component: dict[str, Any] = {
            "id": component_id,
            "label": _required_text("component label", label),
            "kind": kind,
            "visual_node_id": node_id,
            "grounded": grounded,
        }
        if part_number is not None:
            component["part_number"] = _required_text("part number", part_number)
        self._parts_by_id[component_id] = part
        self._components.append(component)
        return part

    def _require_registered(self, part: StructuralPart) -> StructuralPart:
        if not isinstance(part, StructuralPart):
            raise StructuralAuthoringError(
                "structural assemblies and connections accept registered "
                "StructuralPart handles only"
            )
        registered = self._parts_by_id.get(part.component_id)
        if registered is not part:
            raise StructuralAuthoringError(
                f"component handle {part.component_id!r} is not registered with this model"
            )
        return registered

    def _validate_topology(self) -> None:
        connected_ids = {
            component_id
            for connection in self._connections
            for component_id in (
                connection["from_component_id"],
                connection["to_component_id"],
            )
        }
        unconnected = sorted(
            component["id"]
            for component in self._components
            if component["kind"] not in {"ground", "connector"}
            and component["id"] not in connected_ids
        )
        if unconnected:
            raise StructuralAuthoringError(
                f"structural components have no declared connection: {unconnected}"
            )

        used_connectors = {
            connector_id
            for connection in self._connections
            for connector_id in connection["connector_component_ids"]
        }
        unused_connectors = sorted(
            component["id"]
            for component in self._components
            if component["kind"] == "connector"
            and component["id"] not in used_connectors
        )
        if unused_connectors:
            raise StructuralAuthoringError(
                f"connector components are not used by a connection: {unused_connectors}"
            )

        grounded_ids = {
            component["id"] for component in self._components if component["grounded"]
        }
        outgoing: dict[str, list[str]] = {}
        for connection in self._connections:
            outgoing.setdefault(connection["from_component_id"], []).append(
                connection["to_component_id"]
            )
        for load in self._loads:
            if not _reaches_ground(load["component_id"], grounded_ids, outgoing):
                raise StructuralAuthoringError(
                    f"load {load['id']!r} does not reach a grounded component"
                )


def helper_source() -> str:
    """Return this standalone module for injection into a compile workspace."""

    return Path(__file__).read_text(encoding="utf-8")


def _required_text(label: str, value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise StructuralAuthoringError(f"{label} must not be empty")
    return text


def _vector3(value: Sequence[float] | dict[str, float]) -> dict[str, float]:
    if isinstance(value, dict):
        if set(value) != {"x", "y", "z"}:
            raise StructuralAuthoringError(
                "load direction mapping must contain exactly x, y, and z"
            )
        return {axis: float(value[axis]) for axis in ("x", "y", "z")}
    if isinstance(value, (str, bytes)) or len(value) != 3:
        raise StructuralAuthoringError("load direction must contain three values")
    return {
        "x": float(value[0]),
        "y": float(value[1]),
        "z": float(value[2]),
    }


def _reaches_ground(
    start_id: str,
    grounded_ids: set[str],
    outgoing: dict[str, list[str]],
) -> bool:
    queue = deque([start_id])
    visited = {start_id}
    while queue:
        component_id = queue.popleft()
        if component_id in grounded_ids:
            return True
        for next_id in outgoing.get(component_id, []):
            if next_id not in visited:
                visited.add(next_id)
                queue.append(next_id)
    return False
