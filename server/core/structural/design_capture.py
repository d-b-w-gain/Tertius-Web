from __future__ import annotations

import ast
from collections import deque
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


def _structural_declaration(source: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise StructuralDeclarationError(f"design.py is not valid Python: {exc.msg}") from exc

    names: dict[str, Any] = {}
    declaration: Any | None = None
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
            declaration = value

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
    component_ids = [component.id for component in components]
    if len(component_ids) != len(set(component_ids)):
        raise StructuralDeclarationError("components contain duplicate IDs")
    valid_ids = set(component_ids)
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
        for connector_id in connection.connector_component_ids:
            if connector_id not in valid_ids:
                raise StructuralDeclarationError(
                    "connection connector component references missing ID "
                    f"{connector_id!r}"
                )
    for load in loads:
        if load.component_id not in valid_ids:
            raise StructuralDeclarationError(
                f"load component references missing ID {load.component_id!r}"
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
    capabilities = [
        CapabilityState(
            id="design-capture",
            label="Design capture",
            status="online",
            detail="Static structural declarations parsed without executing design.py.",
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
    if blocked_count:
        warnings.append(f"{blocked_count} declared load path(s) are disconnected from ground.")

    try:
        return ProjectStructuralCapture(
            project_name=project_name,
            design_hash=sha256(source.encode("utf-8")).hexdigest(),
            title=str(declaration.get("title") or f"Structural Workbench — {project_name}"),
            components=components,
            connections=connections,
            loads=loads,
            load_paths=paths,
            capabilities=capabilities,
            warnings=warnings,
        )
    except ValidationError as exc:
        raise StructuralDeclarationError(f"invalid {DECLARATION_NAME}: {exc}") from exc
