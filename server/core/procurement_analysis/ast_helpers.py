from __future__ import annotations

import ast
import math
from typing import Any, Sequence

from .model import ConstantValue, SAFE_BUILTIN_NUMERIC_FUNCTIONS, SAFE_MATH_NUMERIC_FUNCTIONS, StaticObject

def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node)


def _literal_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, str | int | float | bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_value(node.operand)
        if isinstance(value, int | float):
            return -value
    raise ValueError("not a simple literal")


def _static_value_with_resolution(node: ast.AST, known: dict[str, Any]) -> tuple[Any, str]:
    if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, str | int | float | bool)):
        return node.value, "literal_assignment"
    if isinstance(node, ast.List | ast.Tuple):
        values: list[Any] = []
        resolutions: list[str] = []
        for item in node.elts:
            try:
                value, resolution = _static_value_with_resolution(item, known)
                values.append(value)
                resolutions.append(resolution)
            except ValueError:
                values.append(None)
                resolutions.append("static_numeric")
        return values, "static_numeric" if any(resolution == "static_numeric" for resolution in resolutions) else "literal_assignment"
    if isinstance(node, ast.Name) and node.id in known:
        value = known[node.id]
        if isinstance(value, ConstantValue):
            return value.value, value.resolution
        return value, "literal_assignment"
    if isinstance(node, ast.Attribute):
        name = _unparse(node)
        if name in known:
            value = known[name]
            if isinstance(value, ConstantValue):
                return value.value, value.resolution
            return value, "literal_assignment"
        owner, _owner_resolution = _static_value_with_resolution(node.value, known)
        if isinstance(owner, StaticObject) and node.attr in owner.attrs:
            return owner.attrs[node.attr], "static_object_attribute"
    if isinstance(node, ast.Subscript):
        value, _value_resolution = _static_value_with_resolution(node.value, known)
        index, _index_resolution = _static_value_with_resolution(node.slice, known)
        if isinstance(value, list | tuple) and isinstance(index, int):
            try:
                return value[index], "static_numeric"
            except IndexError as exc:
                raise ValueError("not a static value") from exc
        if isinstance(value, dict) and index in value:
            return value[index], "static_lookup"
    if isinstance(node, ast.Dict):
        result: dict[Any, Any] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            if key_node is None:
                raise ValueError("not a static value")
            key, _key_resolution = _static_value_with_resolution(key_node, known)
            value, _value_resolution = _static_value_with_resolution(value_node, known)
            result[key] = value
        return result, "literal_assignment"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value, _resolution = _static_value_with_resolution(node.operand, known)
        return not bool(value), "static_bool"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value, _resolution = _static_value_with_resolution(node.operand, known)
        if isinstance(value, int | float):
            return -value, "static_numeric"
    if isinstance(node, ast.BoolOp):
        values = [_static_value_with_resolution(value, known)[0] for value in node.values]
        if isinstance(node.op, ast.And):
            return all(bool(value) for value in values), "static_bool"
        if isinstance(node.op, ast.Or):
            return any(bool(value) for value in values), "static_bool"
    if isinstance(node, ast.Compare):
        left = _static_value_with_resolution(node.left, known)[0]
        comparisons: list[bool] = []
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            right = _static_value_with_resolution(comparator, known)[0]
            if isinstance(operator, ast.Eq):
                comparisons.append(left == right)
            elif isinstance(operator, ast.NotEq):
                comparisons.append(left != right)
            elif isinstance(operator, ast.Is):
                comparisons.append(left is right)
            elif isinstance(operator, ast.IsNot):
                comparisons.append(left is not right)
            elif isinstance(operator, ast.Lt):
                comparisons.append(left < right)
            elif isinstance(operator, ast.LtE):
                comparisons.append(left <= right)
            elif isinstance(operator, ast.Gt):
                comparisons.append(left > right)
            elif isinstance(operator, ast.GtE):
                comparisons.append(left >= right)
            else:
                raise ValueError("not a static value")
            left = right
        return all(comparisons), "static_bool"
    if isinstance(node, ast.BinOp):
        left, _left_resolution = _static_value_with_resolution(node.left, known)
        right, _right_resolution = _static_value_with_resolution(node.right, known)
        if isinstance(left, int | float) and isinstance(right, int | float):
            if isinstance(node.op, ast.Add):
                return left + right, "static_numeric"
            if isinstance(node.op, ast.Sub):
                return left - right, "static_numeric"
            if isinstance(node.op, ast.Mult):
                return left * right, "static_numeric"
            if isinstance(node.op, ast.Div):
                return left / right, "static_numeric"
            if isinstance(node.op, ast.Pow):
                return left ** right, "static_numeric"
    if isinstance(node, ast.IfExp):
        condition, _condition_resolution = _static_value_with_resolution(node.test, known)
        if isinstance(condition, bool):
            return _static_value_with_resolution(node.body if condition else node.orelse, known)
    if isinstance(node, ast.Call):
        call_name = _call_name(node.func)
        if call_name in {"float", "int", "str"} and len(node.args) == 1 and not node.keywords:
            value, _value_resolution = _static_value_with_resolution(node.args[0], known)
            try:
                if call_name == "float":
                    return float(value), "static_cast"
                if call_name == "int":
                    return int(value), "static_cast"
                return str(value), "static_cast"
            except (TypeError, ValueError) as exc:
                raise ValueError("not a static value") from exc
        if call_name == "round_up_to_increment" and len(node.args) == 2 and not node.keywords:
            value, _value_resolution = _static_value_with_resolution(node.args[0], known)
            increment, _increment_resolution = _static_value_with_resolution(node.args[1], known)
            if isinstance(value, int | float) and isinstance(increment, int | float):
                return math.ceil(value / increment) * increment, "static_numeric"
        if isinstance(node.func, ast.Name) and node.func.id in SAFE_BUILTIN_NUMERIC_FUNCTIONS and not node.keywords:
            args = [_static_value_with_resolution(arg, known)[0] for arg in node.args]
            if node.func.id == "sum":
                if len(args) == 1 and isinstance(args[0], list) and all(isinstance(item, int | float) for item in args[0]):
                    return sum(args[0]), "static_numeric"
            elif all(isinstance(arg, int | float) for arg in args):
                return SAFE_BUILTIN_NUMERIC_FUNCTIONS[node.func.id](*args), "static_numeric"
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "math":
            function = SAFE_MATH_NUMERIC_FUNCTIONS.get(node.func.attr)
            if function is not None and not node.keywords:
                args = [_static_value_with_resolution(arg, known)[0] for arg in node.args]
                if all(isinstance(arg, int | float) for arg in args):
                    return function(*args), "static_numeric"
    raise ValueError("not a static value")


def _static_value(node: ast.AST, known: dict[str, Any]) -> Any:
    return _static_value_with_resolution(node, known)[0]


def _compact_expr(node: ast.AST) -> dict[str, Any]:
    if isinstance(node, ast.Constant):
        return {"kind": "literal", "value": node.value}
    if isinstance(node, ast.Name):
        return {"kind": "reference", "name": node.id}
    if isinstance(node, ast.Attribute):
        return {"kind": "reference", "name": _unparse(node)}
    return {"kind": "expression", "source": _unparse(node)}


def _assignment_target_names(targets: Sequence[ast.AST]) -> list[str]:
    names: list[str] = []

    def collect(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Tuple | ast.List):
            for item in target.elts:
                collect(item)
        elif isinstance(target, ast.Attribute):
            names.append(_unparse(target))

    for target in targets:
        collect(target)
    return names


def _normalize_file_name(module_name: str) -> str:
    return f"{module_name.split('.', 1)[0]}.py"


def _collect_import_closure(files: dict[str, str], entrypoint: str) -> list[str]:
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(filename: str) -> None:
        if filename in visited or filename not in files:
            return
        visited.add(filename)
        ordered.append(filename)
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                candidates.append(_normalize_file_name(node.module))
            elif isinstance(node, ast.Import):
                candidates.extend(_normalize_file_name(alias.name) for alias in node.names)
            for candidate in candidates:
                if candidate in files:
                    visit(candidate)

    visit(entrypoint)
    return ordered


def _collect_import_aliases(files: dict[str, str], source_files: list[str]) -> dict[tuple[str, str], tuple[str, str]]:
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_file = _normalize_file_name(node.module)
                if imported_file not in files:
                    continue
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    aliases[(filename, local_name)] = (imported_file, alias.name)
    return aliases
