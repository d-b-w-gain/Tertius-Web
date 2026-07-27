from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256
import operator
from typing import Any, Callable

from pydantic import ValidationError

from .contracts import (
    CapabilityState,
    DesignComponent,
    DesignConnection,
    DesignLoadPath,
    DesignSurfaceLoad,
    ProjectStructuralCapture,
)

DECLARATION_NAME = "TERTIUS_STRUCTURAL"
_COMPONENT_METHOD_KINDS = {
    "ground": "ground",
    "member": "member",
    "surface": "surface",
    "connector": "connector",
    "support": "support",
}
_MISSING = object()


class StructuralDeclarationError(ValueError):
    """Raised when a design's static structural declaration cannot be captured."""


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}


@dataclass(frozen=True)
class _ComponentHandle:
    model_name: str
    component_id: str


@dataclass
class _GeneratedModel:
    name: str
    title: str
    components: list[dict[str, Any]] = field(default_factory=list)
    connections: list[dict[str, Any]] = field(default_factory=list)
    loads: list[dict[str, Any]] = field(default_factory=list)
    assembled_component_ids: list[str] | None = None

    def declaration(self) -> dict[str, Any]:
        if self.assembled_component_ids is None:
            raise StructuralDeclarationError(
                "call StructuralModel.assembly(...) before assigning "
                f"{DECLARATION_NAME}"
            )
        component_ids = [component["id"] for component in self.components]
        if len(self.assembled_component_ids) != len(set(self.assembled_component_ids)):
            raise StructuralDeclarationError(
                "StructuralModel.assembly(...) contains a component more than once"
            )
        missing = sorted(set(component_ids) - set(self.assembled_component_ids))
        extra = sorted(set(self.assembled_component_ids) - set(component_ids))
        if missing or extra:
            raise StructuralDeclarationError(
                "StructuralModel.assembly(...) must contain every registered component "
                f"exactly once; missing={missing}, unregistered={extra}"
            )
        return {
            "title": self.title,
            "authoring": {
                "mode": "generated",
                "assembly_component_ids": list(self.assembled_component_ids),
            },
            "components": self.components,
            "connections": self.connections,
            "loads": self.loads,
        }


def _static_value(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise StructuralDeclarationError(f"unknown static name {node.id!r}")
        return names[node.id]
    if isinstance(node, ast.List):
        return [_static_value(item, names) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_static_value(item, names) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_static_value(item, names) for item in node.elts}
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise StructuralDeclarationError("dictionary unpacking is not supported")
            result[_static_value(key, names)] = _static_value(value, names)
        return result
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _static_value(node.left, names)
        right = _static_value(node.right, names)
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_static_value(node.operand, names))
    raise StructuralDeclarationError(
        f"unsupported expression {type(node).__name__}; "
        "structural declarations may use literals, static names, and arithmetic only"
    )


def _call_keywords(call: ast.Call) -> dict[str, ast.AST]:
    keywords: dict[str, ast.AST] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            raise StructuralDeclarationError(
                "structural authoring calls do not support keyword unpacking"
            )
        if keyword.arg in keywords:
            raise StructuralDeclarationError(
                f"structural authoring call repeats keyword {keyword.arg!r}"
            )
        keywords[keyword.arg] = keyword.value
    return keywords


def _keyword_value(
    keywords: dict[str, ast.AST],
    name: str,
    names: dict[str, Any],
    *,
    default: Any = _MISSING,
) -> Any:
    node = keywords.get(name)
    if node is None:
        if default is _MISSING:
            raise StructuralDeclarationError(
                f"structural authoring call requires keyword {name!r}"
            )
        return default
    return _static_value(node, names)


def _component_handle(
    node: ast.AST,
    handles: dict[str, _ComponentHandle],
    *,
    model_name: str,
    context: str,
) -> _ComponentHandle:
    if not isinstance(node, ast.Name) or node.id not in handles:
        name = node.id if isinstance(node, ast.Name) else type(node).__name__
        raise StructuralDeclarationError(
            f"{context} references unregistered structural handle {name!r}"
        )
    handle = handles[node.id]
    if handle.model_name != model_name:
        raise StructuralDeclarationError(
            f"{context} references component {handle.component_id!r} from another model"
        )
    return handle


def _component_handle_list(
    node: ast.AST,
    handles: dict[str, _ComponentHandle],
    *,
    model_name: str,
    context: str,
) -> list[_ComponentHandle]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise StructuralDeclarationError(f"{context} must be a literal list of handles")
    return [
        _component_handle(
            item,
            handles,
            model_name=model_name,
            context=context,
        )
        for item in node.elts
    ]


def _direction_value(value: Any) -> dict[str, float]:
    if isinstance(value, dict) and set(value) == {"x", "y", "z"}:
        return {axis: float(value[axis]) for axis in ("x", "y", "z")}
    if (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and not any(isinstance(item, (list, tuple, dict, set)) for item in value)
    ):
        return {"x": float(value[0]), "y": float(value[1]), "z": float(value[2])}
    raise StructuralDeclarationError(
        "StructuralModel.surface_load(...) direction must contain x, y, and z"
    )


def _generated_structural_declaration(tree: ast.Module) -> dict[str, Any] | None:
    names: dict[str, Any] = {}
    models: dict[str, _GeneratedModel] = {}
    handles: dict[str, _ComponentHandle] = {}
    legacy_declaration: dict[str, Any] | None = None

    for statement in tree.body:
        target_name: str | None = None
        value_node: ast.AST | None = None
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            target_name = statement.targets[0].id
            value_node = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target_name = statement.target.id
            value_node = statement.value

        if target_name is not None and isinstance(value_node, ast.Call):
            call = value_node
            if isinstance(call.func, ast.Name) and call.func.id == "StructuralModel":
                if call.args:
                    raise StructuralDeclarationError(
                        "StructuralModel(...) accepts the title as a keyword only"
                    )
                keywords = _call_keywords(call)
                unexpected = sorted(set(keywords) - {"title"})
                if unexpected:
                    raise StructuralDeclarationError(
                        f"StructuralModel(...) has unsupported keywords {unexpected}"
                    )
                title = str(_keyword_value(keywords, "title", names))
                models[target_name] = _GeneratedModel(name=target_name, title=title)
                continue

            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in models
            ):
                model_name = call.func.value.id
                model = models[model_name]
                method = call.func.attr
                keywords = _call_keywords(call)
                if method in _COMPONENT_METHOD_KINDS:
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            f"StructuralModel.{method}(...) requires one Build123D shape"
                        )
                    allowed = {"id", "label", "visual_node_id", "part_number"}
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            f"StructuralModel.{method}(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    component_id = str(_keyword_value(keywords, "id", names))
                    visual_node_id = str(
                        _keyword_value(
                            keywords,
                            "visual_node_id",
                            names,
                            default=component_id,
                        )
                    )
                    component: dict[str, Any] = {
                        "id": component_id,
                        "label": str(_keyword_value(keywords, "label", names)),
                        "kind": _COMPONENT_METHOD_KINDS[method],
                        "visual_node_id": visual_node_id,
                        "grounded": method == "ground",
                    }
                    part_number = _keyword_value(
                        keywords,
                        "part_number",
                        names,
                        default=None,
                    )
                    if part_number is not None:
                        component["part_number"] = str(part_number)
                    model.components.append(component)
                    handles[target_name] = _ComponentHandle(
                        model_name=model_name,
                        component_id=component_id,
                    )
                    continue
                if method == "assembly":
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            "StructuralModel.assembly(...) requires one list of handles"
                        )
                    unexpected = sorted(set(keywords) - {"label"})
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.assembly(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    _keyword_value(keywords, "label", names)
                    model.assembled_component_ids = [
                        handle.component_id
                        for handle in _component_handle_list(
                            call.args[0],
                            handles,
                            model_name=model_name,
                            context="StructuralModel.assembly(...)",
                        )
                    ]
                    continue
                if method == "manifest" and target_name == DECLARATION_NAME:
                    if call.args or call.keywords:
                        raise StructuralDeclarationError(
                            "StructuralModel.manifest(...) does not accept arguments"
                        )
                    return model.declaration()

        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in models
            ):
                model_name = call.func.value.id
                model = models[model_name]
                method = call.func.attr
                keywords = _call_keywords(call)
                if method == "connect":
                    if len(call.args) != 2:
                        raise StructuralDeclarationError(
                            "StructuralModel.connect(...) requires source and target handles"
                        )
                    allowed = {"via", "id", "label", "transfers"}
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            f"StructuralModel.connect(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    source = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.connect(...) source",
                    )
                    target = _component_handle(
                        call.args[1],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.connect(...) target",
                    )
                    connector_node = keywords.get("via")
                    connectors = (
                        []
                        if connector_node is None
                        else _component_handle_list(
                            connector_node,
                            handles,
                            model_name=model_name,
                            context="StructuralModel.connect(...) via",
                        )
                    )
                    model.connections.append(
                        {
                            "id": str(_keyword_value(keywords, "id", names)),
                            "label": str(_keyword_value(keywords, "label", names)),
                            "from_component_id": source.component_id,
                            "to_component_id": target.component_id,
                            "connector_component_ids": [
                                connector.component_id for connector in connectors
                            ],
                            "transfers": list(
                                _keyword_value(keywords, "transfers", names)
                            ),
                        }
                    )
                    continue
                if method == "surface_load":
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            "StructuralModel.surface_load(...) requires one surface handle"
                        )
                    allowed = {
                        "id",
                        "label",
                        "case",
                        "pressure_kPa",
                        "area_m2",
                        "direction",
                        "provenance",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.surface_load(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    loaded_component = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.surface_load(...)",
                    )
                    model.loads.append(
                        {
                            "id": str(_keyword_value(keywords, "id", names)),
                            "label": str(_keyword_value(keywords, "label", names)),
                            "case": str(_keyword_value(keywords, "case", names)),
                            "component_id": loaded_component.component_id,
                            "pressure_kPa": float(
                                _keyword_value(keywords, "pressure_kPa", names)
                            ),
                            "area_m2": float(
                                _keyword_value(keywords, "area_m2", names)
                            ),
                            "direction": _direction_value(
                                _keyword_value(keywords, "direction", names)
                            ),
                            "provenance": str(
                                _keyword_value(keywords, "provenance", names)
                            ),
                        }
                    )
                    continue

        if target_name is None or value_node is None:
            continue
        try:
            value = _static_value(value_node, names)
        except (StructuralDeclarationError, ArithmeticError, TypeError, ValueError):
            if target_name == DECLARATION_NAME:
                raise
            continue
        names[target_name] = value
        if target_name == DECLARATION_NAME:
            if not isinstance(value, dict):
                raise StructuralDeclarationError(
                    f"{DECLARATION_NAME} must be a dictionary"
                )
            legacy_declaration = value

    return legacy_declaration


def _structural_declaration(source: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise StructuralDeclarationError(f"design.py is not valid Python: {exc.msg}") from exc

    declaration = _generated_structural_declaration(tree)
    if declaration is None:
        raise StructuralDeclarationError(
            f"design.py does not declare {DECLARATION_NAME}"
        )
    if not isinstance(declaration, dict):
        raise StructuralDeclarationError(f"{DECLARATION_NAME} must be a dictionary")
    return declaration


def _trace_load_paths(
    components: list[DesignComponent],
    connections: list[DesignConnection],
    loads: list[DesignSurfaceLoad],
) -> list[DesignLoadPath]:
    components_by_id = {component.id: component for component in components}
    outgoing: dict[str, list[DesignConnection]] = {}
    for connection in connections:
        outgoing.setdefault(connection.from_component_id, []).append(connection)
    for candidates in outgoing.values():
        candidates.sort(key=lambda connection: connection.id)

    paths: list[DesignLoadPath] = []
    for load in loads:
        queue: deque[tuple[str, list[str], list[str]]] = deque(
            [(load.component_id, [load.component_id], [])]
        )
        visited = {load.component_id}
        complete: DesignLoadPath | None = None
        while queue:
            component_id, component_path, connection_path = queue.popleft()
            component = components_by_id[component_id]
            if component.grounded:
                complete = DesignLoadPath(
                    load_id=load.id,
                    status="complete",
                    component_ids=component_path,
                    connection_ids=connection_path,
                    grounded_component_id=component_id,
                    detail=f"Load reaches grounded component {component.label}.",
                )
                break
            for connection in outgoing.get(component_id, []):
                next_id = connection.to_component_id
                if next_id in visited:
                    continue
                visited.add(next_id)
                queue.append(
                    (
                        next_id,
                        [*component_path, next_id],
                        [*connection_path, connection.id],
                    )
                )
        paths.append(
            complete
            or DesignLoadPath(
                load_id=load.id,
                status="blocked",
                component_ids=[load.component_id],
                connection_ids=[],
                detail="No declared connection path reaches a grounded component.",
            )
        )
    return paths


def _validate_graph_inputs(
    components: list[DesignComponent],
    connections: list[DesignConnection],
    loads: list[DesignSurfaceLoad],
) -> None:
    if not components:
        raise StructuralDeclarationError("structural capture contains no components")
    component_ids = [component.id for component in components]
    if len(component_ids) != len(set(component_ids)):
        raise StructuralDeclarationError("components contain duplicate IDs")
    visual_node_ids = [component.visual_node_id for component in components]
    if len(visual_node_ids) != len(set(visual_node_ids)):
        raise StructuralDeclarationError("components contain duplicate visual node IDs")
    connection_ids = [connection.id for connection in connections]
    if len(connection_ids) != len(set(connection_ids)):
        raise StructuralDeclarationError("connections contain duplicate IDs")
    load_ids = [load.id for load in loads]
    if len(load_ids) != len(set(load_ids)):
        raise StructuralDeclarationError("loads contain duplicate IDs")

    valid_ids = set(component_ids)
    components_by_id = {component.id: component for component in components}
    connected_ids: set[str] = set()
    used_connector_ids: set[str] = set()
    for connection in connections:
        if connection.from_component_id not in valid_ids:
            raise StructuralDeclarationError(
                "connection source component references missing ID "
                f"{connection.from_component_id!r}"
            )
        if connection.to_component_id not in valid_ids:
            raise StructuralDeclarationError(
                "connection target component references missing ID "
                f"{connection.to_component_id!r}"
            )
        if connection.from_component_id == connection.to_component_id:
            raise StructuralDeclarationError(
                f"connection {connection.id!r} connects a component to itself"
            )
        connected_ids.update(
            {connection.from_component_id, connection.to_component_id}
        )
        for connector_id in connection.connector_component_ids:
            if connector_id not in valid_ids:
                raise StructuralDeclarationError(
                    "connection connector component references missing ID "
                    f"{connector_id!r}"
                )
            if components_by_id[connector_id].kind != "connector":
                raise StructuralDeclarationError(
                    f"connection {connection.id!r} via component "
                    f"{connector_id!r} is not a connector"
                )
            used_connector_ids.add(connector_id)

    unconnected = sorted(
        component.id
        for component in components
        if component.kind not in {"ground", "connector"}
        and component.id not in connected_ids
    )
    if unconnected:
        raise StructuralDeclarationError(
            f"structural components have no declared connection: {unconnected}"
        )
    unused_connectors = sorted(
        component.id
        for component in components
        if component.kind == "connector"
        and component.id not in used_connector_ids
    )
    if unused_connectors:
        raise StructuralDeclarationError(
            f"connector components are not used by a connection: {unused_connectors}"
        )

    for load in loads:
        if load.component_id not in valid_ids:
            raise StructuralDeclarationError(
                f"load component references missing ID {load.component_id!r}"
            )
        if components_by_id[load.component_id].kind != "surface":
            raise StructuralDeclarationError(
                f"load component {load.component_id!r} is not a surface"
            )


def parse_project_structural_capture(
    source: str,
    *,
    project_name: str,
) -> ProjectStructuralCapture:
    declaration = _structural_declaration(source)
    try:
        components = [
            DesignComponent.model_validate(value)
            for value in declaration.get("components", [])
        ]
        connections = [
            DesignConnection.model_validate(value)
            for value in declaration.get("connections", [])
        ]
        loads = [
            DesignSurfaceLoad.model_validate(value)
            for value in declaration.get("loads", [])
        ]
    except (TypeError, ValidationError) as exc:
        raise StructuralDeclarationError(f"invalid {DECLARATION_NAME}: {exc}") from exc

    _validate_graph_inputs(components, connections, loads)
    paths = _trace_load_paths(components, connections, loads)
    blocked_count = sum(path.status == "blocked" for path in paths)
    generated_authoring = (
        isinstance(declaration.get("authoring"), dict)
        and declaration["authoring"].get("mode") == "generated"
    )
    capabilities = [
        CapabilityState(
            id="design-capture",
            label="Design capture",
            status="online",
            detail=(
                "Generated structural authoring calls parsed without executing design.py."
                if generated_authoring
                else "Static structural declarations parsed without executing design.py."
            ),
        ),
        CapabilityState(
            id="load-path",
            label="Load path",
            status="online" if paths and blocked_count == 0 else "blocked",
            detail=(
                f"{len(paths)} declared load path(s) reach ground."
                if paths and blocked_count == 0
                else f"{blocked_count or len(loads)} load path(s) do not reach ground."
            ),
        ),
        CapabilityState(
            id="solver",
            label="Member checks",
            status="pending",
            detail="Connectivity captured; force distribution and capacities are not solved yet.",
        ),
        CapabilityState(
            id="reports",
            label="Calculation reports",
            status="pending",
            detail="No calculation report artifact has been generated.",
        ),
    ]
    warnings = [
        "LOAD PATH CAPTURE ONLY — MEMBER, CONNECTION, ANCHOR, AND CONCRETE CAPACITIES ARE NOT CHECKED."
    ]
    if generated_authoring:
        warnings.append(
            "STRUCTURAL MANIFEST GENERATED FROM REGISTERED BUILD123D HANDLES; "
            "UNREGISTERED ASSEMBLY MEMBERS FAIL CAPTURE AND COMPILE."
        )
    if blocked_count:
        warnings.append(f"{blocked_count} declared load path(s) are disconnected from ground.")

    try:
        return ProjectStructuralCapture(
            project_name=project_name,
            design_hash=sha256(source.encode("utf-8")).hexdigest(),
            title=str(declaration.get("title") or f"Structural Workbench — {project_name}"),
            authoring_mode="generated" if generated_authoring else "legacy",
            components=components,
            connections=connections,
            loads=loads,
            load_paths=paths,
            capabilities=capabilities,
            warnings=warnings,
        )
    except ValidationError as exc:
        raise StructuralDeclarationError(f"invalid {DECLARATION_NAME}: {exc}") from exc
