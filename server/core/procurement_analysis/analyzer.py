from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any


STANDARD_BOM_FIELDS = {
    "mark",
    "part_number",
    "product_key",
    "manufacturer",
    "standard",
    "size",
    "length",
    "length_mm",
    "width",
    "width_mm",
    "height",
    "height_mm",
    "thickness",
    "thickness_mm",
    "diameter",
    "diameter_mm",
    "grip_length",
    "grip_length_mm",
    "quantity",
    "unit",
    "role",
    "material",
    "finish",
    "source_library",
    "bracket_type",
    "roof_pitch",
    "roof_pitch_deg",
    "angle",
    "angle_deg",
    "span",
    "span_mm",
    "drawing_number",
}

PROCUREMENT_HELPER_FUNCTIONS = {
    "assembly_key",
    "assembly_label",
}

GENERATED_NAME_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^$",
        r"^mesh$",
        r"^component$",
        r"^solid$",
        r"^=>\d+(?:_\d+)?$",
        r"^node[_\s-]?\d+$",
        r"^mesh[_\s-]?\d+$",
        r"^object[_\s-]?3?d?[_\s-]?\d+$",
        r"^shape[_\s-]?\d+$",
        r"^face[_\s-]?\d+$",
        r"^edge[_\s-]?\d+$",
        r"^compound[_\s-]?\d+$",
        r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$",
    ]
]


@dataclass(frozen=True)
class FunctionSignature:
    name: str
    params: list[str]
    defaults: dict[str, ast.AST]
    filename: str
    line: int
    return_annotation: str | None


@dataclass(frozen=True)
class ConstantValue:
    name: str
    value: Any
    filename: str
    line: int
    resolution: str = "literal_assignment"


@dataclass(frozen=True)
class StaticObject:
    attrs: dict[str, Any]


@dataclass(frozen=True)
class StaticResolution:
    value: Any
    resolution: str
    dependencies: tuple[str, ...] = ()


SAFE_BUILTIN_NUMERIC_FUNCTIONS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": sum,
    "ceil": math.ceil,
    "floor": math.floor,
}

SAFE_MATH_NUMERIC_FUNCTIONS = {
    "radians": math.radians,
    "degrees": math.degrees,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "atan": math.atan,
    "ceil": math.ceil,
    "floor": math.floor,
    "sqrt": math.sqrt,
}


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
        name = _call_name(node.func)
        if name in {"float", "int", "str"} and len(node.args) == 1 and not node.keywords:
            value, _value_resolution = _static_value_with_resolution(node.args[0], known)
            try:
                if name == "float":
                    return float(value), "static_cast"
                if name == "int":
                    return int(value), "static_cast"
                return str(value), "static_cast"
            except (TypeError, ValueError) as exc:
                raise ValueError("not a static value") from exc
        if name == "round_up_to_increment" and len(node.args) == 2 and not node.keywords:
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


def _assignment_target_names(targets: list[ast.AST]) -> list[str]:
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


class StaticValueResolver:
    def __init__(
        self,
        files: dict[str, str],
        source_files: list[str],
        import_aliases: dict[tuple[str, str], tuple[str, str]],
        constants: dict[tuple[str, str], ConstantValue],
    ) -> None:
        self.files = files
        self.source_files = source_files
        self.import_aliases = import_aliases
        self.constants = constants
        self.catalog_sections = self._collect_catalog_sections()
        self.function_defs = self._collect_function_defs()
        self.constructor_fields = self._collect_constructor_fields()
        self.constructor_defaults = self._collect_constructor_defaults()

    def _collect_catalog_sections(self) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for text in self.files.values():
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                continue
            payload_sections = payload.get("sections") if isinstance(payload, dict) else None
            if isinstance(payload_sections, list):
                sections.extend(section for section in payload_sections if isinstance(section, dict))
        return sections

    def _collect_function_defs(self) -> dict[str, tuple[str, ast.FunctionDef]]:
        function_defs: dict[str, tuple[str, ast.FunctionDef]] = {}
        for filename in self.source_files:
            try:
                tree = ast.parse(self.files[filename], filename=filename)
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    function_defs.setdefault(node.name, (filename, node))
        return function_defs

    def _collect_constructor_fields(self) -> dict[str, list[str]]:
        constructor_fields: dict[str, list[str]] = {}
        for filename in self.source_files:
            try:
                tree = ast.parse(self.files[filename], filename=filename)
            except SyntaxError:
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                fields: list[str] = []
                for child in node.body:
                    if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                        fields.append(child.target.id)
                    elif isinstance(child, ast.Assign):
                        for target in child.targets:
                            if isinstance(target, ast.Name):
                                fields.append(target.id)
                constructor_fields[node.name] = fields
        return constructor_fields

    def _collect_constructor_defaults(self) -> dict[str, dict[str, Any]]:
        constructor_defaults: dict[str, dict[str, Any]] = {}
        for filename in self.source_files:
            try:
                tree = ast.parse(self.files[filename], filename=filename)
            except SyntaxError:
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                defaults: dict[str, Any] = {}
                for child in node.body:
                    if not isinstance(child, ast.AnnAssign) or child.value is None or not isinstance(child.target, ast.Name):
                        continue
                    try:
                        defaults[child.target.id] = _static_value(child.value, {})
                    except ValueError:
                        continue
                constructor_defaults[node.name] = defaults
        return constructor_defaults

    def known_for(self, filename: str) -> dict[str, Any]:
        known: dict[str, Any] = {
            name: value
            for (constant_file, name), value in self.constants.items()
            if constant_file == filename
        }
        for (alias_file, local_name), (imported_file, imported_name) in self.import_aliases.items():
            if alias_file != filename:
                continue
            imported_value = self.constants.get((imported_file, imported_name))
            if imported_value is not None:
                known[local_name] = imported_value
            for (constant_file, constant_name), value in self.constants.items():
                if constant_file != imported_file:
                    continue
                if constant_name == imported_name or constant_name.startswith(f"{imported_name}."):
                    known[f"{local_name}{constant_name[len(imported_name):]}"] = value
        return known

    def value_tuple(self, node: ast.AST, known: dict[str, Any], *, filename: str | None = None) -> tuple[Any, str]:
        resolution = self.resolve(node, known, filename=filename)
        return resolution.value, resolution.resolution

    def resolve(
        self,
        node: ast.AST,
        known: dict[str, Any],
        *,
        filename: str | None = None,
        call_stack: tuple[str, ...] = (),
    ) -> StaticResolution:
        try:
            value, resolution = _static_value_with_resolution(node, known)
            return StaticResolution(value=value, resolution=resolution)
        except ValueError:
            pass

        container_value = self._resolve_container(node, known, filename=filename, call_stack=call_stack)
        if container_value is not None:
            return container_value

        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name:
                catalog_value = self._resolve_catalog_lookup(node, known, name)
                if catalog_value is not None:
                    return catalog_value
                constructor_value = self._resolve_constructor(node, known, name, filename=filename)
                if constructor_value is not None:
                    return constructor_value
                helper_value = self._resolve_helper_call(node, known, name, filename=filename, call_stack=call_stack)
                if helper_value is not None:
                    return helper_value
        raise ValueError("not a static value")

    def _resolve_container(
        self,
        node: ast.AST,
        known: dict[str, Any],
        *,
        filename: str | None,
        call_stack: tuple[str, ...],
    ) -> StaticResolution | None:
        if isinstance(node, ast.List | ast.Tuple):
            values: list[Any] = []
            dependencies: list[str] = []
            for item in node.elts:
                try:
                    resolved = self.resolve(item, known, filename=filename, call_stack=call_stack)
                except ValueError:
                    return None
                values.append(resolved.value)
                dependencies.extend(resolved.dependencies)
            return StaticResolution(value=values, resolution="static_container", dependencies=tuple(dependencies))
        if isinstance(node, ast.Dict):
            result: dict[Any, Any] = {}
            dependencies: list[str] = []
            for key_node, value_node in zip(node.keys, node.values, strict=True):
                if key_node is None:
                    return None
                try:
                    key = self.resolve(key_node, known, filename=filename, call_stack=call_stack)
                    value = self.resolve(value_node, known, filename=filename, call_stack=call_stack)
                except ValueError:
                    return None
                result[key.value] = value.value
                dependencies.extend(key.dependencies)
                dependencies.extend(value.dependencies)
            return StaticResolution(value=result, resolution="static_container", dependencies=tuple(dependencies))
        return None

    def _all_attr_aliases(self) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for (_filename, name), constant in self.constants.items():
            if name == "ATTR_ALIAS" and isinstance(constant.value, dict):
                aliases.update({str(key): str(value) for key, value in constant.value.items()})
        return aliases

    def _resolve_catalog_lookup(self, node: ast.Call, known: dict[str, Any], name: str) -> StaticResolution | None:
        if not name.endswith("_tables") or not node.args:
            return None
        try:
            part_number, _resolution = self.value_tuple(node.args[0], known)
        except ValueError:
            return None
        normalized = str(part_number).strip().upper().replace(" ", "").replace("-", "").replace("/", "")
        for section in self.catalog_sections:
            aliases = [section.get("key"), section.get("part_number"), section.get("part_number_alias")]
            for alias in aliases:
                if not alias:
                    continue
                alias_text = str(alias).split(" ", 1)[0].strip().upper().replace(" ", "").replace("-", "").replace("/", "")
                if alias_text != normalized:
                    continue
                attrs = dict(section)
                for public_name, data_key in self._all_attr_aliases().items():
                    if data_key in section:
                        attrs[str(public_name)] = section[data_key]
                for key, value in list(section.items()):
                    if key.endswith("_mm") and key[:-3] not in attrs:
                        attrs[key[:-3]] = value
                if "B" not in attrs:
                    for key in ("flange_mean_mm", "flange_mm"):
                        if key in section:
                            attrs["B"] = section[key]
                            break
                return StaticResolution(value=StaticObject(attrs), resolution="static_catalog_lookup")
        return None

    def _resolve_constructor(
        self,
        node: ast.Call,
        known: dict[str, Any],
        name: str,
        *,
        filename: str | None,
    ) -> StaticResolution | None:
        short_name = name.rsplit(".", 1)[-1]
        if not short_name[:1].isupper():
            return None
        fields = self.constructor_fields.get(short_name, [])
        if not fields and not node.keywords:
            return None
        attrs: dict[str, Any] = dict(self.constructor_defaults.get(short_name, {}))
        for index, arg in enumerate(node.args):
            if index >= len(fields):
                return None
            try:
                attrs[fields[index]], _resolution = self.value_tuple(arg, known, filename=filename)
            except ValueError:
                return None
        for keyword in node.keywords:
            if keyword.arg is None:
                return None
            try:
                attrs[keyword.arg], _resolution = self.value_tuple(keyword.value, known, filename=filename)
            except ValueError:
                return None
        return StaticResolution(value=StaticObject(attrs), resolution="static_constructor")

    def _resolve_helper_call(
        self,
        node: ast.Call,
        known: dict[str, Any],
        name: str,
        *,
        filename: str | None,
        call_stack: tuple[str, ...],
    ) -> StaticResolution | None:
        short_name = name.rsplit(".", 1)[-1]
        if "." in name or short_name in call_stack or short_name not in self.function_defs:
            return None
        function_file, function = self.function_defs[short_name]
        function_known = self.known_for(function_file)
        if filename == function_file:
            function_known.update(known)
        local_known = dict(function_known)
        params = [arg.arg for arg in function.args.args]
        if len(node.args) > len(params):
            return None
        for index, arg in enumerate(node.args):
            try:
                local_known[params[index]] = self.resolve(
                    arg,
                    known,
                    filename=filename,
                    call_stack=call_stack,
                ).value
            except ValueError:
                return None
        defaults = list(function.args.defaults)
        default_params = params[-len(defaults):] if defaults else []
        for param, default in zip(default_params, defaults, strict=True):
            if param in local_known:
                continue
            try:
                local_known[param] = self.resolve(
                    default,
                    function_known,
                    filename=function_file,
                    call_stack=call_stack,
                ).value
            except ValueError:
                return None
        for keyword in node.keywords:
            if keyword.arg is None or keyword.arg not in params:
                return None
            try:
                local_known[keyword.arg] = self.resolve(
                    keyword.value,
                    known,
                    filename=filename,
                    call_stack=call_stack,
                ).value
            except ValueError:
                return None

        for statement in function.body:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                continue
            returned = self._execute_static_statement(
                statement,
                local_known,
                filename=function_file,
                call_stack=(*call_stack, short_name),
            )
            if returned is not None:
                return StaticResolution(
                    value=returned.value,
                    resolution="static_helper_call",
                    dependencies=(short_name,),
                )
            if getattr(statement, "_static_not_supported", False):
                return None
            continue
        return None

    def _execute_static_statement(
        self,
        statement: ast.stmt,
        local_known: dict[str, Any],
        *,
        filename: str,
        call_stack: tuple[str, ...],
    ) -> StaticResolution | None:
        if isinstance(statement, ast.Assign):
            try:
                assigned = self.resolve(statement.value, local_known, filename=filename, call_stack=call_stack)
            except ValueError:
                setattr(statement, "_static_not_supported", True)
                return None
            for target in statement.targets:
                if not isinstance(target, ast.Name):
                    setattr(statement, "_static_not_supported", True)
                    return None
                local_known[target.id] = assigned.value
            return None
        if isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
            current = local_known.get(statement.target.id)
            try:
                increment = self.resolve(statement.value, local_known, filename=filename, call_stack=call_stack).value
            except ValueError:
                setattr(statement, "_static_not_supported", True)
                return None
            if not isinstance(current, int | float) or not isinstance(increment, int | float):
                setattr(statement, "_static_not_supported", True)
                return None
            if isinstance(statement.op, ast.Add):
                local_known[statement.target.id] = current + increment
                return None
            if isinstance(statement.op, ast.Sub):
                local_known[statement.target.id] = current - increment
                return None
            setattr(statement, "_static_not_supported", True)
            return None
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "append" and isinstance(call.func.value, ast.Name) and len(call.args) == 1:
                target = local_known.get(call.func.value.id)
                if not isinstance(target, list):
                    setattr(statement, "_static_not_supported", True)
                    return None
                try:
                    target.append(self.resolve(call.args[0], local_known, filename=filename, call_stack=call_stack).value)
                except ValueError:
                    setattr(statement, "_static_not_supported", True)
                return None
        if isinstance(statement, ast.If):
            try:
                condition = self.resolve(statement.test, local_known, filename=filename, call_stack=call_stack).value
            except ValueError:
                setattr(statement, "_static_not_supported", True)
                return None
            branch = statement.body if bool(condition) else statement.orelse
            for child in branch:
                returned = self._execute_static_statement(child, local_known, filename=filename, call_stack=call_stack)
                if returned is not None:
                    return returned
                if getattr(child, "_static_not_supported", False):
                    setattr(statement, "_static_not_supported", True)
                    return None
            return None
        if isinstance(statement, ast.While):
            for _iteration in range(1000):
                try:
                    condition = self.resolve(statement.test, local_known, filename=filename, call_stack=call_stack).value
                except ValueError:
                    setattr(statement, "_static_not_supported", True)
                    return None
                if not bool(condition):
                    return None
                for child in statement.body:
                    returned = self._execute_static_statement(child, local_known, filename=filename, call_stack=call_stack)
                    if returned is not None:
                        return returned
                    if getattr(child, "_static_not_supported", False):
                        setattr(statement, "_static_not_supported", True)
                        return None
            setattr(statement, "_static_not_supported", True)
            return None
        if isinstance(statement, ast.Return):
            if statement.value is None:
                setattr(statement, "_static_not_supported", True)
                return None
            try:
                return self.resolve(statement.value, local_known, filename=filename, call_stack=call_stack)
            except ValueError:
                setattr(statement, "_static_not_supported", True)
                return None
        setattr(statement, "_static_not_supported", True)
        return None

    def procurement_inputs_for_call(
        self,
        function_name: str,
        parameters: dict[str, dict[str, Any]],
        *,
        filename: str,
        call_stack: tuple[str, ...] = (),
    ) -> dict[str, dict[str, Any]]:
        short_name = function_name.rsplit(".", 1)[-1]
        direct = self._procurement_inputs_from_product_table(parameters, filename=filename, source_function=short_name)
        if direct:
            return direct
        if short_name in call_stack or short_name not in self.function_defs:
            return {}

        function_file, function = self.function_defs[short_name]
        local_known = self.known_for(function_file)
        for param in function.args.args:
            trace = parameters.get(param.arg)
            if isinstance(trace, dict) and "resolved" in trace:
                local_known[param.arg] = trace.get("resolved")

        for statement in function.body:
            if not isinstance(statement, ast.Return) or statement.value is None:
                continue
            return_call = self._unwrap_factory_return_call(statement.value)
            if return_call is None:
                continue
            target_name = _call_name(return_call.func)
            if not target_name:
                continue
            target_short = target_name.rsplit(".", 1)[-1]
            target_parameters = self._parameters_for_static_call(
                return_call,
                target_short,
                local_known,
                filename=function_file,
                dependencies=(*call_stack, short_name, target_short),
            )
            if target_parameters is None:
                continue
            return self.procurement_inputs_for_call(
                target_short,
                target_parameters,
                filename=function_file,
                call_stack=(*call_stack, short_name),
            )
        return {}

    def _unwrap_factory_return_call(self, node: ast.AST) -> ast.Call | None:
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name and name.rsplit(".", 1)[-1].startswith("make_"):
                return node
            if isinstance(node.func, ast.Attribute) and node.func.attr == "moved":
                return self._unwrap_factory_return_call(node.func.value)
        return None

    def _parameters_for_static_call(
        self,
        call: ast.Call,
        short_name: str,
        known: dict[str, Any],
        *,
        filename: str,
        dependencies: tuple[str, ...],
    ) -> dict[str, dict[str, Any]] | None:
        parameters: dict[str, dict[str, Any]] = {}
        target = self.function_defs.get(short_name)
        params = [arg.arg for arg in target[1].args.args] if target else []
        if target and target[1].args.defaults:
            default_params = params[-len(target[1].args.defaults):]
            for param, default in zip(default_params, target[1].args.defaults, strict=True):
                try:
                    resolved = self.resolve(default, self.known_for(target[0]), filename=target[0])
                except ValueError:
                    continue
                parameters[param] = self._static_trace(
                    value=resolved.value,
                    resolution="function_default",
                    source_file=target[0],
                    source_line=getattr(default, "lineno", target[1].lineno),
                    raw=_compact_expr(default),
                    dependencies=dependencies,
                )
        for index, arg in enumerate(call.args):
            param = params[index] if index < len(params) else f"arg_{index}"
            try:
                resolved = self.resolve(arg, known, filename=filename)
            except ValueError:
                return None
            parameters[param] = self._static_trace(
                value=resolved.value,
                resolution=f"factory_return_{resolved.resolution}",
                source_file=filename,
                source_line=getattr(arg, "lineno", call.lineno),
                raw=_compact_expr(arg),
                dependencies=dependencies,
            )
        for keyword in call.keywords:
            if keyword.arg is None:
                return None
            try:
                resolved = self.resolve(keyword.value, known, filename=filename)
            except ValueError:
                return None
            parameters[keyword.arg] = self._static_trace(
                value=resolved.value,
                resolution=f"factory_return_{resolved.resolution}",
                source_file=filename,
                source_line=getattr(keyword.value, "lineno", call.lineno),
                raw=_compact_expr(keyword.value),
                dependencies=dependencies,
            )
        return parameters

    def _procurement_inputs_from_product_table(
        self,
        parameters: dict[str, dict[str, Any]],
        *,
        filename: str,
        source_function: str,
    ) -> dict[str, dict[str, Any]]:
        product_key_trace = next(
            (
                parameters[key]
                for key in ("product_key", "key", "arg_0")
                if isinstance(parameters.get(key), dict) and parameters[key].get("resolved") is not None
            ),
            None,
        )
        if product_key_trace is None:
            return {}
        product_key = product_key_trace.get("resolved")
        known = self.known_for(filename)
        product: StaticObject | None = None
        for value in known.values():
            constant_value = value.value if isinstance(value, ConstantValue) else value
            if not isinstance(constant_value, dict) or product_key not in constant_value:
                continue
            candidate = constant_value[product_key]
            if isinstance(candidate, StaticObject) and candidate.attrs.get("part_number"):
                product = candidate
                break
        if product is None:
            return {}

        attrs = product.attrs
        dependencies = (source_function,)
        inferred: dict[str, dict[str, Any]] = {}
        for field in ("part_number", "manufacturer", "standard", "material", "finish", "unit"):
            if attrs.get(field) is not None:
                inferred[field] = self._static_trace(
                    value=attrs[field],
                    resolution="static_product_table",
                    source_file=filename,
                    source_line=product_key_trace.get("source_line"),
                    raw={"kind": "catalog_product", "key": product_key, "field": field},
                    dependencies=dependencies,
                )

        length_trace = parameters.get("length_mm") or parameters.get("length")
        if isinstance(length_trace, dict) and length_trace.get("resolved") is not None:
            inferred["length_mm"] = {
                **length_trace,
                "resolution": f"product_parameter_{length_trace.get('resolution', 'resolved')}",
            }
        else:
            for field in ("model_length", "length_mm", "length_max", "length"):
                if attrs.get(field) is not None:
                    inferred["length_mm"] = self._static_trace(
                        value=attrs[field],
                        resolution="static_product_table",
                        source_file=filename,
                        source_line=product_key_trace.get("source_line"),
                        raw={"kind": "catalog_product", "key": product_key, "field": field},
                        dependencies=dependencies,
                    )
                    break
        return inferred

    def _static_trace(
        self,
        *,
        value: Any,
        resolution: str,
        source_file: str,
        source_line: int | None,
        raw: dict[str, Any],
        dependencies: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        trace: dict[str, Any] = {
            "raw": raw,
            "resolved": value,
            "resolution": resolution,
            "source_file": source_file,
            "source_line": source_line,
        }
        if dependencies:
            trace["dependencies"] = list(dict.fromkeys(dependencies))
        return trace
        return None


def _collect_constants(files: dict[str, str], source_files: list[str]) -> dict[tuple[str, str], ConstantValue]:
    constants: dict[tuple[str, str], ConstantValue] = {}
    import_aliases = _collect_import_aliases(files, source_files)
    resolver = StaticValueResolver(files, source_files, import_aliases, constants)

    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        known: dict[str, Any] = {}
        def remember(name: str, value: Any, line: int, resolution: str = "literal_assignment") -> None:
            constant = ConstantValue(
                name=name,
                value=value,
                filename=filename,
                line=line,
                resolution=resolution,
            )
            known[name] = constant
            constants[(filename, name)] = constant

        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    value, resolution = resolver.value_tuple(node.value, known, filename=filename)
                except ValueError:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        remember(target.id, value, node.lineno, resolution)
                continue

            if isinstance(node, ast.ClassDef):
                class_known: dict[str, Any] = {**known}
                for child in node.body:
                    if not isinstance(child, ast.Assign):
                        continue
                    try:
                        value, resolution = resolver.value_tuple(child.value, class_known, filename=filename)
                    except ValueError:
                        continue
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            class_known[target.id] = value
                            remember(f"{node.name}.{target.id}", value, child.lineno, f"class_attribute_{resolution}")
                continue

            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                try:
                    items = _static_value(node.iter, known)
                except ValueError:
                    continue
                if not isinstance(items, list | tuple):
                    continue
                for item in items:
                    loop_known = {**known, node.target.id: item}
                    for child in node.body:
                        if not isinstance(child, ast.Expr) or not isinstance(child.value, ast.Call):
                            continue
                        call = child.value
                        if not isinstance(call.func, ast.Attribute) or call.func.attr != "append":
                            continue
                        if not isinstance(call.func.value, ast.Name) or len(call.args) != 1:
                            continue
                        list_name = call.func.value.id
                        current_value = known.get(list_name)
                        current = current_value.value if isinstance(current_value, ConstantValue) else current_value
                        if not isinstance(current, list):
                            continue
                        try:
                            appended = _static_value(call.args[0], loop_known)
                        except ValueError:
                            appended = None
                        current.append(appended)
                        remember(list_name, current, child.lineno, "static_numeric")

    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        known = resolver.known_for(filename)

        def remember(name: str, value: Any, line: int, resolution: str) -> None:
            existing = constants.get((filename, name))
            if existing is not None and existing.line > line:
                known[name] = existing
                return
            constants[(filename, name)] = ConstantValue(
                name=name,
                value=value,
                filename=filename,
                line=line,
                resolution=resolution,
            )
            known[name] = constants[(filename, name)]

        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            try:
                value, resolution = resolver.value_tuple(node.value, known, filename=filename)
            except ValueError:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    remember(target.id, value, node.lineno, resolution)
    return constants


def _collect_signatures(files: dict[str, str], source_files: list[str]) -> dict[str, FunctionSignature]:
    signatures: dict[str, FunctionSignature] = {}
    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            params = [arg.arg for arg in node.args.args]
            defaults: dict[str, ast.AST] = {}
            if node.args.defaults:
                default_params = params[-len(node.args.defaults):]
                defaults.update(zip(default_params, node.args.defaults, strict=True))
            signatures[node.name] = FunctionSignature(
                name=node.name,
                params=params,
                defaults=defaults,
                filename=filename,
                line=node.lineno,
                return_annotation=_unparse(node.returns) if node.returns is not None else None,
            )
    return signatures


def _returns_build_geometry(signature: FunctionSignature | None) -> bool:
    if signature is None or signature.return_annotation is None:
        return True
    annotation = signature.return_annotation
    return any(token in annotation for token in ["Compound", "Part", "Shape", "Solid", "bd."])


def _resolve_expr(
    node: ast.AST,
    *,
    filename: str,
    constants: dict[tuple[str, str], ConstantValue],
    import_aliases: dict[tuple[str, str], tuple[str, str]],
    method_hint: str,
    scoped_parameters: dict[str, dict[str, Any]] | None = None,
    static_resolver: StaticValueResolver | None = None,
) -> dict[str, Any]:
    raw = _compact_expr(node)
    if static_resolver is not None:
        static_known = static_resolver.known_for(filename)
    else:
        static_known = {
            name: value
            for (constant_file, name), value in constants.items()
            if constant_file == filename
        }
    if scoped_parameters:
        static_known.update({
            name: value.get("resolved")
            for name, value in scoped_parameters.items()
            if isinstance(value, dict) and value.get("resolved") is not None
        })
    if isinstance(node, ast.Constant):
        return {
            "raw": raw,
            "resolved": node.value,
            "resolution": method_hint if method_hint == "function_default" else "literal",
            "source_file": filename,
            "source_line": getattr(node, "lineno", None),
        }
    if isinstance(node, ast.Name):
        if scoped_parameters and node.id in scoped_parameters:
            resolved = dict(scoped_parameters[node.id])
            resolved["raw"] = raw
            resolved["resolution"] = f"parameter_{resolved.get('resolution', 'resolved')}"
            return resolved
        constant = constants.get((filename, node.id))
        resolution = "literal_assignment"
        if constant is None:
            imported = import_aliases.get((filename, node.id))
            if imported:
                constant = constants.get(imported)
                resolution = "imported_constant"
        if constant is not None:
            resolved_method = resolution if resolution == "imported_constant" else constant.resolution
            return {
                "raw": raw,
                "resolved": constant.value,
                "resolution": resolved_method if method_hint != "function_default" else "function_default",
                "source_file": constant.filename,
                "source_line": constant.line,
            }
        return {
            "raw": raw,
            "resolved": None,
            "resolution": "unresolved",
            "source_file": filename,
            "source_line": getattr(node, "lineno", None),
        }
    if isinstance(node, ast.Attribute):
        name = _unparse(node)
        constant = constants.get((filename, name))
        resolution = "class_attribute"
        if constant is None:
            root = name.split(".", 1)[0]
            imported = import_aliases.get((filename, root))
            if imported and "." in name:
                imported_file, imported_name = imported
                constant = constants.get((imported_file, f"{imported_name}.{name.split('.', 1)[1]}"))
                resolution = "imported_class_attribute"
        if constant is not None:
            return {
                "raw": raw,
                "resolved": constant.value,
                "resolution": resolution if method_hint != "function_default" else "function_default",
                "source_file": constant.filename,
                "source_line": constant.line,
            }
        if static_resolver is not None:
            try:
                static_resolution = static_resolver.resolve(node, static_known, filename=filename)
                return {
                    "raw": raw,
                    "resolved": static_resolution.value,
                    "resolution": static_resolution.resolution,
                    "source_file": filename,
                    "source_line": getattr(node, "lineno", None),
                }
            except ValueError:
                pass
        return {
            "raw": raw,
            "resolved": None,
            "resolution": "unresolved_attribute",
            "unresolved_reason": "attribute_not_in_static_context",
            "source_file": filename,
            "source_line": getattr(node, "lineno", None),
        }
    try:
        if static_resolver is not None:
            static_resolution = static_resolver.resolve(node, static_known, filename=filename)
            value = static_resolution.value
            resolution = static_resolution.resolution
            dependencies = static_resolution.dependencies
        else:
            value, resolution = _static_value_with_resolution(node, static_known)
            dependencies = ()
        result = {
            "raw": raw,
            "resolved": value,
            "resolution": method_hint if method_hint == "function_default" else resolution,
            "source_file": filename,
            "source_line": getattr(node, "lineno", None),
        }
        if dependencies:
            result["dependencies"] = list(dependencies)
        return result
    except ValueError:
        return {
            "raw": raw,
            "resolved": None,
            "resolution": "unresolved_expression",
            "unresolved_reason": "unsupported_or_unresolved_static_expression",
            "source_file": filename,
            "source_line": getattr(node, "lineno", None),
        }


def _infer_kind(function_name: str) -> str:
    name = function_name.lower()
    if "fastener" in name:
        return "fastener_assembly"
    if any(token in name for token in ("purlin", "fascia", "column", "rafter", "member")):
        return "structural_member"
    if any(token in name for token in ("bracket", "gpb", "cp", "apex", "plate")):
        return "bracket"
    if "block" in name:
        return "block"
    if "rebar" in name:
        return "rebar"
    if "foundation" in name:
        return "foundation"
    return "component"


def _standard_key(key: str) -> str:
    if key in {"supplier_part_number"}:
        return "part_number"
    if key in {"span", "span_mm"}:
        return "width_mm"
    if key in {"roof_pitch", "roof_pitch_degrees"}:
        return "angle_deg"
    if key in {"column_height", "column_height_mm"}:
        return "height_mm"
    if key == "length":
        return "length_mm"
    if key == "grip_length":
        return "grip_length_mm"
    if key in {"bracket_type", "product_key"}:
        return "part_number"
    if key == "angle":
        return "angle_deg"
    if key == "roof_pitch":
        return "roof_pitch_deg"
    return key


def _readiness(kind: str, standard_inputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    keys = set(standard_inputs)
    missing: list[str] = []
    if kind == "fastener_assembly":
        if "size" not in keys:
            missing.append("size")
        if "length_mm" not in keys:
            missing.append("length_mm")
    elif kind == "structural_member":
        if "part_number" not in keys:
            missing.append("part_number")
        if "length_mm" not in keys:
            missing.append("length_mm")
    elif kind == "bracket":
        if "part_number" not in keys:
            missing.append("part_number")

    if not missing and standard_inputs:
        level = "ok"
    elif standard_inputs:
        level = "warning"
    else:
        level = "info"
    return {"level": level, "missing": missing}


def _should_record_call(
    function_name: str,
    parameters: dict[str, dict[str, Any]],
    signature: FunctionSignature | None,
    qualified_function_name: str | None = None,
) -> bool:
    if function_name in PROCUREMENT_HELPER_FUNCTIONS:
        return False
    if function_name.startswith("_"):
        return False
    if signature is not None and signature.return_annotation is not None and not _returns_build_geometry(signature):
        return False
    if function_name[:1].isupper() and signature is None:
        return False
    if qualified_function_name and "." in qualified_function_name and not function_name.startswith("make_") and _infer_kind(function_name) == "component":
        return False
    if function_name.startswith("make_"):
        return True
    if _infer_kind(function_name) != "component":
        return True
    if signature and set(parameters) & STANDARD_BOM_FIELDS:
        return True
    return any(_standard_key(key) in STANDARD_BOM_FIELDS for key in parameters)


def _iterable_values(
    node: ast.AST,
    known: dict[str, Any],
    *,
    static_resolver: StaticValueResolver | None = None,
    filename: str | None = None,
) -> list[Any] | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enumerate" and node.args:
        values = _iterable_values(node.args[0], known, static_resolver=static_resolver, filename=filename)
        return list(enumerate(values)) if values is not None else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
        try:
            args = [
                (static_resolver.resolve(arg, known, filename=filename).value if static_resolver else _static_value(arg, known))
                for arg in node.args
            ]
        except ValueError:
            return None
        if not all(isinstance(arg, int | float) for arg in args):
            return None
        int_args = [int(arg) for arg in args]
        if len(int_args) == 1:
            return list(range(int_args[0]))
        if len(int_args) == 2:
            return list(range(int_args[0], int_args[1]))
        if len(int_args) == 3 and int_args[2] != 0:
            return list(range(int_args[0], int_args[1], int_args[2]))
        return None
    try:
        value = static_resolver.resolve(node, known, filename=filename).value if static_resolver else _static_value(node, known)
    except ValueError:
        return None
    if isinstance(value, list | tuple):
        return list(value)
    return None


def _iterable_count(
    node: ast.AST,
    known: dict[str, Any],
    *,
    static_resolver: StaticValueResolver | None = None,
    filename: str | None = None,
) -> int | None:
    values = _iterable_values(node, known, static_resolver=static_resolver, filename=filename)
    return len(values) if values is not None else None


def _collect_function_instance_counts(
    files: dict[str, str],
    source_files: list[str],
    constants: dict[tuple[str, str], ConstantValue],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        known = {
            name: value.value
            for (constant_file, name), value in constants.items()
            if constant_file == filename
        }

        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            variable_factories: dict[str, str] = {}
            for node in ast.walk(function):
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                    continue
                call_name = _call_name(node.value.func)
                if not call_name:
                    continue
                short_name = call_name.rsplit(".", 1)[-1]
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        variable_factories[target.id] = short_name

            for loop in (node for node in ast.walk(function) if isinstance(node, ast.For)):
                loop_count = _iterable_count(loop.iter, known)
                if loop_count is None:
                    continue
                for child in ast.walk(loop):
                    if not isinstance(child, ast.Attribute) or child.attr != "children":
                        continue
                    if isinstance(child.value, ast.Name):
                        factory = variable_factories.get(child.value.id)
                        if factory:
                            counts[factory] = max(counts.get(factory, 1), loop_count)
    return counts


def _assign_static_target(target: ast.AST, value: Any, known: dict[str, Any]) -> None:
    if isinstance(target, ast.Name):
        known[target.id] = value
    elif isinstance(target, ast.Tuple | ast.List) and isinstance(value, list | tuple):
        for item, element in zip(target.elts, value, strict=False):
            _assign_static_target(item, element, known)


def _is_procurement_factory_call(short_name: str) -> bool:
    if not short_name:
        return False
    if short_name.startswith("make_"):
        return True
    if short_name[:1].isupper():
        return False
    return _infer_kind(short_name) != "component"


def _collect_source_call_instance_counts(
    files: dict[str, str],
    source_files: list[str],
    static_resolver: StaticValueResolver,
) -> tuple[dict[tuple[str, int], int], dict[tuple[str, str], int]]:
    counts: dict[tuple[str, int], int] = {}
    scope_hole_counts: dict[tuple[str, str], int] = {}

    def resolved_hole_count(call: ast.Call, local_known: dict[str, Any], filename: str) -> int:
        count = 0
        for keyword in call.keywords:
            if keyword.arg is None:
                continue
            key = keyword.arg.lower()
            if not (key.startswith("holes_") or key.endswith("_holes") or key in {"holes", "bolt_holes"}):
                continue
            try:
                value = static_resolver.resolve(keyword.value, local_known, filename=filename).value
            except ValueError:
                continue
            if isinstance(value, int | float) and value > 0:
                count += int(value)
            elif isinstance(value, list | tuple):
                count += sum(int(item) for item in value if isinstance(item, int | float) and item > 0)
        return count

    def count_expr_calls(filename: str, node: ast.AST, multiplier: int, local_known: dict[str, Any], scope_key: str) -> None:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _call_name(child.func)
            short_name = name.rsplit(".", 1)[-1] if name else ""
            if _is_procurement_factory_call(short_name):
                key = (filename, child.lineno)
                counts[key] = counts.get(key, 0) + multiplier
                hole_count = resolved_hole_count(child, local_known, filename)
                if hole_count:
                    scope_key_tuple = (filename, scope_key)
                    scope_hole_counts[scope_key_tuple] = scope_hole_counts.get(scope_key_tuple, 0) + hole_count * multiplier

    def infer_segment_count(target: ast.AST, local_known: dict[str, Any]) -> int | None:
        if not isinstance(target, ast.Name):
            return None
        target_name = target.id.lower()
        if target_name not in {"nseg", "n_seg", "n_segments", "segment_count", "segments"}:
            return None
        total = next(
            (
                local_known.get(key)
                for key in ("height", "total_length", "member_length", "length")
                if isinstance(local_known.get(key), int | float)
            ),
            None,
        )
        segment = next(
            (
                local_known.get(key)
                for key in ("leg_segment_length", "segment_length", "stock_length", "max_segment_length")
                if isinstance(local_known.get(key), int | float)
            ),
            None,
        )
        if not isinstance(total, int | float) or not isinstance(segment, int | float) or segment <= 0:
            return None
        return max(1, int(math.ceil(total / segment)))

    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue

        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            function_known = static_resolver.known_for(filename)
            params = [arg.arg for arg in function.args.args]
            defaults = list(function.args.defaults)
            default_params = params[-len(defaults):] if defaults else []
            for param, default in zip(default_params, defaults, strict=True):
                try:
                    function_known[param] = static_resolver.resolve(default, function_known, filename=filename).value
                except ValueError:
                    pass

            scope_key = function.name

            def visit_statements(statements: list[ast.stmt], multiplier: int, local_known: dict[str, Any]) -> None:
                for statement in statements:
                    if isinstance(statement, ast.Assign):
                        count_expr_calls(filename, statement.value, multiplier, local_known, scope_key)
                        try:
                            value = static_resolver.resolve(statement.value, local_known, filename=filename).value
                        except ValueError:
                            for target in statement.targets:
                                inferred = infer_segment_count(target, local_known)
                                if inferred is not None:
                                    _assign_static_target(target, inferred, local_known)
                            continue
                        for target in statement.targets:
                            _assign_static_target(target, value, local_known)
                        continue

                    if isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
                        count_expr_calls(filename, statement.value, multiplier, local_known, scope_key)
                        try:
                            current = local_known.get(statement.target.id)
                            value = static_resolver.resolve(statement.value, local_known, filename=filename).value
                        except ValueError:
                            continue
                        if isinstance(current, int | float) and isinstance(value, int | float):
                            if isinstance(statement.op, ast.Add):
                                local_known[statement.target.id] = current + value
                            elif isinstance(statement.op, ast.Sub):
                                local_known[statement.target.id] = current - value
                        continue

                    if isinstance(statement, ast.Expr):
                        count_expr_calls(filename, statement.value, multiplier, local_known, scope_key)
                        continue

                    if isinstance(statement, ast.Return):
                        if statement.value is not None:
                            count_expr_calls(filename, statement.value, multiplier, local_known, scope_key)
                        continue

                    if isinstance(statement, ast.For):
                        values = _iterable_values(
                            statement.iter,
                            local_known,
                            static_resolver=static_resolver,
                            filename=filename,
                        )
                        if values is not None and len(values) <= 1000:
                            for value in values:
                                child_known = dict(local_known)
                                _assign_static_target(statement.target, value, child_known)
                                visit_statements(statement.body, multiplier, child_known)
                            continue
                        loop_count = _static_count(
                            statement.iter,
                            local_known,
                            {},
                            static_resolver=static_resolver,
                            filename=filename,
                        )
                        if loop_count is not None:
                            visit_statements(statement.body, multiplier * loop_count, dict(local_known))
                        else:
                            visit_statements(statement.body, multiplier, dict(local_known))
                        continue

                    if isinstance(statement, ast.If):
                        try:
                            condition = static_resolver.resolve(statement.test, local_known, filename=filename).value
                        except ValueError:
                            visit_statements(statement.body, multiplier, dict(local_known))
                            visit_statements(statement.orelse, multiplier, dict(local_known))
                            continue
                        visit_statements(statement.body if bool(condition) else statement.orelse, multiplier, dict(local_known))
                        continue

                    for child in ast.iter_child_nodes(statement):
                        count_expr_calls(filename, child, multiplier, local_known, scope_key)

            visit_statements(function.body, 1, dict(function_known))

    return counts, scope_hole_counts


def _trace_with_value(source: dict[str, Any], value: Any, resolution: str) -> dict[str, Any]:
    return {
        **source,
        "resolved": value,
        "resolution": resolution,
    }


def _function_initial_mark(function_name: str) -> str | None:
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", function_name) if token]
    if len(tokens) > 1 and tokens[0].lower() in {"make", "build", "create"}:
        tokens = tokens[1:]
    if not tokens:
        return None
    mark = "".join(token[0] for token in tokens).upper()
    return mark or None


def _resolved_parameter(parameters: dict[str, dict[str, Any]], key: str) -> Any:
    value = parameters.get(key)
    if isinstance(value, dict):
        return value.get("resolved")
    return None


def _infer_standard_inputs_from_parameters(
    function_name: str,
    kind: str,
    parameters: dict[str, dict[str, Any]],
    standard_inputs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    inferred: dict[str, dict[str, Any]] = {}

    if "mark" not in standard_inputs and kind in {"bracket", "component"}:
        mark = _function_initial_mark(function_name)
        if mark:
            inferred["mark"] = {
                "raw": {"kind": "generated", "source": "function_initials", "function": function_name},
                "resolved": mark,
                "resolution": "inferred_function_mark",
                "source_file": None,
                "source_line": None,
            }

    if "part_number" not in standard_inputs and kind == "structural_member":
        for key in ("section", "profile"):
            trace = parameters.get(key)
            if not isinstance(trace, dict):
                continue
            value = trace.get("resolved")
            if isinstance(value, StaticObject):
                value = value.attrs.get("part_number") or value.attrs.get("product_key") or value.attrs.get("name")
            if value is not None:
                inferred["part_number"] = _trace_with_value(trace, value, "inferred_section_identity")
                break

    if "length_mm" not in standard_inputs:
        p1 = _resolved_parameter(parameters, "p1")
        p2 = _resolved_parameter(parameters, "p2")
        if isinstance(p1, list | tuple) and isinstance(p2, list | tuple) and len(p1) == len(p2):
            try:
                length = math.sqrt(sum((float(b) - float(a)) ** 2 for a, b in zip(p1, p2, strict=True)))
            except (TypeError, ValueError):
                length = None
            if length is not None:
                source = parameters.get("p2") if isinstance(parameters.get("p2"), dict) else {}
                inferred["length_mm"] = {
                    **source,
                    "raw": {"kind": "expression", "source": "distance(p1, p2)"},
                    "resolved": length,
                    "resolution": "inferred_endpoint_distance",
                }

    if "plate" in function_name.lower():
        dimension_keys = {
            "w": "width_mm",
            "width": "width_mm",
            "h": "height_mm",
            "height": "height_mm",
            "t": "thickness_mm",
            "thickness": "thickness_mm",
        }
        for param, standard_key in dimension_keys.items():
            if standard_key in standard_inputs:
                continue
            trace = parameters.get(param)
            if isinstance(trace, dict) and trace.get("resolved") is not None:
                inferred[standard_key] = _trace_with_value(trace, trace["resolved"], "inferred_plate_dimension")

    return inferred


def _static_count(
    node: ast.AST,
    known: dict[str, Any],
    local_counts: dict[str, int],
    *,
    static_resolver: StaticValueResolver | None = None,
    filename: str | None = None,
) -> int | None:
    if isinstance(node, ast.Name):
        count = local_counts.get(node.id)
        if count is not None:
            return count
    if isinstance(node, ast.List | ast.Tuple):
        return len(node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_count(node.left, known, local_counts)
        right = _static_count(node.right, known, local_counts)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.ListComp) and node.generators:
        count = 1
        for generator in node.generators:
            generator_count = _static_count(
                generator.iter,
                known,
                local_counts,
                static_resolver=static_resolver,
                filename=filename,
            )
            if generator_count is None:
                return None
            count *= generator_count
        return count
    return _iterable_count(node, known, static_resolver=static_resolver, filename=filename)


def _collect_return_item_counts(
    files: dict[str, str],
    source_files: list[str],
    constants: dict[tuple[str, str], ConstantValue],
) -> dict[tuple[str, int], int]:
    return_counts: dict[tuple[str, int], int] = {}
    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        known = {
            name: value.value
            for (constant_file, name), value in constants.items()
            if constant_file == filename
        }

        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            local_counts: dict[str, int] = {}

            def visit_statements(statements: list[ast.stmt], multiplier: int = 1) -> None:
                for statement in statements:
                    if isinstance(statement, ast.Assign):
                        if isinstance(statement.value, ast.List | ast.Tuple):
                            count = len(statement.value.elts)
                        elif isinstance(statement.value, ast.ListComp):
                            count = _static_count(statement.value, known, local_counts)
                        else:
                            count = None
                        if count is not None:
                            for target in statement.targets:
                                if isinstance(target, ast.Name):
                                    local_counts[target.id] = count

                        if isinstance(statement.value, ast.Call):
                            call_name = _call_name(statement.value.func)
                            short_name = call_name.rsplit(".", 1)[-1] if call_name else ""
                            for target in statement.targets:
                                if isinstance(target, ast.Tuple):
                                    for index, item in enumerate(target.elts):
                                        if isinstance(item, ast.Name):
                                            returned_count = return_counts.get((short_name, index))
                                            if returned_count is not None:
                                                local_counts[item.id] = returned_count

                    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                        call = statement.value
                        if isinstance(call.func, ast.Attribute) and call.func.attr == "append" and isinstance(call.func.value, ast.Name):
                            list_name = call.func.value.id
                            local_counts[list_name] = local_counts.get(list_name, 0) + multiplier

                    if isinstance(statement, ast.For):
                        loop_count = _static_count(statement.iter, known, local_counts)
                        if loop_count is not None:
                            visit_statements(statement.body, multiplier * loop_count)

            visit_statements(function.body)
            for statement in function.body:
                if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Tuple):
                    for index, item in enumerate(statement.value.elts):
                        if isinstance(item, ast.Name) and item.id in local_counts:
                            return_counts[(function.name, index)] = local_counts[item.id]
    return return_counts


def _return_item_is_metadata(node: ast.AST) -> bool:
    name = _unparse(node).lower()
    return any(token in name for token in ("hole", "holes", "point", "points", "offset", "offsets"))


def _collect_return_product_counts(files: dict[str, str], source_files: list[str]) -> dict[str, int]:
    product_counts: dict[str, int] = {}
    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            for statement in function.body:
                if not isinstance(statement, ast.Return) or not isinstance(statement.value, ast.Tuple):
                    continue
                count = 0
                for item in statement.value.elts:
                    if _return_item_is_metadata(item):
                        break
                    count += 1
                if count > 1:
                    product_counts[function.name] = count
                break
    return product_counts


def _moved_prototype_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "moved":
        return None
    if isinstance(node.func.value, ast.Name):
        return node.func.value.id
    return None


def _collect_fastener_call_placement_counts(
    files: dict[str, str],
    source_files: list[str],
    constants: dict[tuple[str, str], ConstantValue],
    static_resolver: StaticValueResolver,
) -> dict[tuple[str, int], int]:
    return_counts = _collect_return_item_counts(files, source_files, constants)
    placement_counts: dict[tuple[str, int], int] = {}

    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        known = static_resolver.known_for(filename)

        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            local_counts: dict[str, int] = {}
            prototypes: dict[str, dict[int, int]] = {}

            def add_counts(target: dict[int, int], source: dict[int, int], multiplier: int = 1) -> None:
                for source_line, count in source.items():
                    target[source_line] = target.get(source_line, 0) + count * multiplier

            def count_proto_moves(node: ast.AST, multiplier: int) -> None:
                sources = prototype_sources(node)
                if sources:
                    for source_line, count in sources.items():
                        placement_counts[(filename, source_line)] = placement_counts.get((filename, source_line), 0) + count * multiplier
                    return
                for child in ast.iter_child_nodes(node):
                    count_proto_moves(child, multiplier)

            def prototype_source_line(node: ast.AST) -> int | None:
                if not isinstance(node, ast.Call):
                    return None
                name = _call_name(node.func)
                short_name = name.rsplit(".", 1)[-1] if name else ""
                if short_name.startswith("make_") or _infer_kind(short_name) != "component":
                    return node.lineno
                if isinstance(node.func, ast.Attribute) and node.func.attr == "moved":
                    return prototype_source_line(node.func.value)
                return None

            def prototype_sources(node: ast.AST) -> dict[int, int]:
                source_line = prototype_source_line(node)
                if source_line is not None:
                    return {source_line: 1}
                if isinstance(node, ast.Name) and node.id in prototypes:
                    return dict(prototypes[node.id])
                moved_name = _moved_prototype_name(node)
                if moved_name and moved_name in prototypes:
                    return dict(prototypes[moved_name])
                if isinstance(node, ast.List | ast.Tuple):
                    result: dict[int, int] = {}
                    for item in node.elts:
                        add_counts(result, prototype_sources(item))
                    return result
                if isinstance(node, ast.Call):
                    name = _call_name(node.func)
                    short_name = name.rsplit(".", 1)[-1] if name else ""
                    if short_name == "Compound":
                        for keyword in node.keywords:
                            if keyword.arg == "children":
                                return prototype_sources(keyword.value)
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "moved":
                        return prototype_sources(node.func.value)
                return {}

            def assign_loop_target(target: ast.AST, value: Any, local_known: dict[str, Any]) -> None:
                if isinstance(target, ast.Name):
                    local_known[target.id] = value
                elif isinstance(target, ast.Tuple) and isinstance(value, tuple | list):
                    for item, element in zip(target.elts, value, strict=False):
                        if isinstance(item, ast.Name):
                            local_known[item.id] = element

            def eval_bool(node: ast.AST, local_known: dict[str, Any]) -> bool | None:
                try:
                    value = static_resolver.resolve(node, local_known, filename=filename).value
                except ValueError:
                    return None
                return bool(value)

            def visit_statements(statements: list[ast.stmt], multiplier: int = 1, local_known: dict[str, Any] | None = None) -> bool:
                if local_known is None:
                    local_known = dict(known)
                for statement in statements:
                    if isinstance(statement, ast.Assign):
                        assigned_call = statement.value if isinstance(statement.value, ast.Call) else None
                        assigned_name = _call_name(assigned_call.func) if assigned_call else None
                        assigned_short_name = assigned_name.rsplit(".", 1)[-1] if assigned_name else ""
                        source_line = prototype_source_line(statement.value)
                        moved_name = _moved_prototype_name(statement.value)
                        source_map = prototype_sources(statement.value)

                        if source_map and any(isinstance(target, ast.Name) for target in statement.targets):
                            for target in statement.targets:
                                if isinstance(target, ast.Name):
                                    prototypes[target.id] = source_map
                            continue

                        if moved_name and moved_name in prototypes:
                            for target in statement.targets:
                                if isinstance(target, ast.Name):
                                    prototypes[target.id] = dict(prototypes[moved_name])
                            continue

                        try:
                            resolved_value = static_resolver.resolve(statement.value, local_known, filename=filename).value
                        except ValueError:
                            resolved_value = None
                        else:
                            for target in statement.targets:
                                assign_loop_target(target, resolved_value, local_known)

                        if isinstance(statement.value, ast.List | ast.Tuple):
                            count = len(statement.value.elts)
                        elif isinstance(statement.value, ast.ListComp):
                            count = _static_count(
                                statement.value,
                                local_known,
                                local_counts,
                                static_resolver=static_resolver,
                                filename=filename,
                            )
                        else:
                            count = None
                        if count is not None:
                            for target in statement.targets:
                                if isinstance(target, ast.Name):
                                    local_counts[target.id] = count

                        if assigned_call:
                            for target in statement.targets:
                                if isinstance(target, ast.Tuple):
                                    for index, item in enumerate(target.elts):
                                        if isinstance(item, ast.Name):
                                            returned_count = return_counts.get((assigned_short_name, index))
                                            if returned_count is not None:
                                                local_counts[item.id] = returned_count

                        if not source_map:
                            count_proto_moves(statement.value, multiplier)
                        continue

                    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                        call = statement.value
                        if isinstance(call.func, ast.Attribute) and call.func.attr == "append" and isinstance(call.func.value, ast.Name):
                            list_name = call.func.value.id
                            local_counts[list_name] = local_counts.get(list_name, 0) + multiplier
                            target_list = local_known.get(list_name)
                            if isinstance(target_list, list) and call.args:
                                try:
                                    target_list.append(static_resolver.resolve(call.args[0], local_known, filename=filename).value)
                                except ValueError:
                                    pass
                            if call.args:
                                count_proto_moves(call.args[0], multiplier)
                            continue
                        count_proto_moves(statement.value, multiplier)
                        continue

                    if isinstance(statement, ast.For):
                        values = _iterable_values(
                            statement.iter,
                            local_known,
                            static_resolver=static_resolver,
                            filename=filename,
                        )
                        if values is not None and len(values) <= 1000:
                            for value in values:
                                child_known = dict(local_known)
                                assign_loop_target(statement.target, value, child_known)
                                visit_statements(statement.body, multiplier, child_known)
                        else:
                            loop_count = _static_count(
                                statement.iter,
                                local_known,
                                local_counts,
                                static_resolver=static_resolver,
                                filename=filename,
                            )
                            if loop_count is not None:
                                visit_statements(statement.body, multiplier * loop_count, dict(local_known))
                        continue

                    if isinstance(statement, ast.If):
                        condition = eval_bool(statement.test, local_known)
                        if condition is True:
                            if len(statement.body) == 1 and isinstance(statement.body[0], ast.Continue):
                                return True
                            should_continue = visit_statements(statement.body, multiplier, dict(local_known))
                            if should_continue:
                                return True
                        elif condition is False:
                            should_continue = visit_statements(statement.orelse, multiplier, dict(local_known))
                            if should_continue:
                                return True
                        continue

                    count_proto_moves(statement, multiplier)
                return False

            visit_statements(function.body, local_known=dict(known))

    return placement_counts


def analyze_design_sources(files: dict[str, str], entrypoint: str = "design.py") -> dict[str, Any]:
    """Analyze Python source files without executing design.py."""

    source_files = _collect_import_closure(files, entrypoint)
    import_aliases = _collect_import_aliases(files, source_files)
    constants = _collect_constants(files, source_files)
    static_resolver = StaticValueResolver(files, source_files, import_aliases, constants)
    signatures = _collect_signatures(files, source_files)
    function_instance_counts = _collect_function_instance_counts(files, source_files, constants)
    source_call_instance_counts, source_scope_hole_counts = _collect_source_call_instance_counts(files, source_files, static_resolver)
    fastener_call_placement_counts = _collect_fastener_call_placement_counts(files, source_files, constants, static_resolver)
    return_product_counts = _collect_return_product_counts(files, source_files)
    calls: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError as exc:
            calls.append({
                "function": "",
                "source_file": filename,
                "source_line": exc.lineno,
                "diagnostic": "syntax_error",
            })
            continue

        scope: list[str] = []
        parameter_scope: list[dict[str, dict[str, Any]]] = []
        assignment_target_scope: list[list[str]] = []
        seen_calls: set[tuple[int, int, int | None, int | None, str]] = set()

        def visit(node: ast.AST) -> None:
            if isinstance(node, ast.FunctionDef):
                scope.append(node.name)
                signature = signatures.get(node.name)
                defaults: dict[str, dict[str, Any]] = {}
                if signature:
                    for param, default in signature.defaults.items():
                        defaults[param] = _resolve_expr(
                            default,
                            filename=signature.filename,
                            constants=constants,
                            import_aliases=import_aliases,
                            method_hint="function_default",
                            scoped_parameters=parameter_scope[-1] if parameter_scope else None,
                            static_resolver=static_resolver,
                        )
                parameter_scope.append(defaults)
                for child in ast.iter_child_nodes(node):
                    visit(child)
                parameter_scope.pop()
                scope.pop()
                return

            if isinstance(node, ast.Assign):
                if parameter_scope:
                    scoped_parameters = parameter_scope[-1]
                    resolved_assignment = _resolve_expr(
                        node.value,
                        filename=filename,
                        constants=constants,
                        import_aliases=import_aliases,
                        method_hint="local_assignment",
                        scoped_parameters=scoped_parameters,
                        static_resolver=static_resolver,
                    )
                    if resolved_assignment.get("resolved") is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                scoped_parameters[target.id] = resolved_assignment
                assignment_target_scope.append(_assignment_target_names(node.targets))
                visit(node.value)
                assignment_target_scope.pop()
                return

            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                key = (
                    node.lineno,
                    node.col_offset,
                    getattr(node, "end_lineno", None),
                    getattr(node, "end_col_offset", None),
                    name or "",
                )
                if key not in seen_calls:
                    seen_calls.add(key)
                    short_name = name.rsplit(".", 1)[-1] if name else ""
                    signature = signatures.get(short_name)
                    scoped_parameters = parameter_scope[-1] if parameter_scope else None
                    parameters: dict[str, dict[str, Any]] = {}

                    if signature:
                        for param, default in signature.defaults.items():
                            parameters[param] = _resolve_expr(
                                default,
                                filename=signature.filename,
                                constants=constants,
                                import_aliases=import_aliases,
                                method_hint="function_default",
                                scoped_parameters=scoped_parameters,
                                static_resolver=static_resolver,
                            )
                        for index, arg in enumerate(node.args):
                            param = signature.params[index] if index < len(signature.params) else f"arg_{index}"
                            parameters[param] = _resolve_expr(
                                arg,
                                filename=filename,
                                constants=constants,
                                import_aliases=import_aliases,
                                method_hint="argument",
                                scoped_parameters=scoped_parameters,
                                static_resolver=static_resolver,
                            )
                    else:
                        for index, arg in enumerate(node.args):
                            parameters[f"arg_{index}"] = _resolve_expr(
                                arg,
                                filename=filename,
                                constants=constants,
                                import_aliases=import_aliases,
                                method_hint="argument",
                                scoped_parameters=scoped_parameters,
                                static_resolver=static_resolver,
                            )

                    for keyword in node.keywords:
                        if keyword.arg:
                            parameters[keyword.arg] = _resolve_expr(
                                keyword.value,
                                filename=filename,
                                constants=constants,
                                import_aliases=import_aliases,
                                method_hint="argument",
                                scoped_parameters=scoped_parameters,
                                static_resolver=static_resolver,
                            )

                    if _should_record_call(short_name, parameters, signature, name):
                        kind = _infer_kind(short_name)
                        standard_inputs = {
                            _standard_key(param): value
                            for param, value in parameters.items()
                            if _standard_key(param) in STANDARD_BOM_FIELDS
                        }
                        inferred_inputs = static_resolver.procurement_inputs_for_call(
                            short_name,
                            parameters,
                            filename=filename,
                        )
                        for standard_key, value in inferred_inputs.items():
                            existing = standard_inputs.get(standard_key)
                            if not isinstance(existing, dict) or existing.get("resolved") is None:
                                standard_inputs[standard_key] = value
                        parameter_inferred_inputs = _infer_standard_inputs_from_parameters(
                            short_name,
                            kind,
                            parameters,
                            standard_inputs,
                        )
                        for standard_key, value in parameter_inferred_inputs.items():
                            existing = standard_inputs.get(standard_key)
                            if not isinstance(existing, dict) or existing.get("resolved") is None:
                                standard_inputs[standard_key] = value
                        for standard_key, value in standard_inputs.items():
                            if isinstance(value, dict) and value.get("resolved") is None and str(value.get("resolution", "")).startswith("unresolved"):
                                diagnostics.append({
                                    "code": "unresolved_formula_dependency",
                                    "severity": "warning",
                                    "message": f"{short_name}.{standard_key} could not be resolved from deterministic source analysis.",
                                    "function": short_name,
                                    "input": standard_key,
                                    "raw": value.get("raw"),
                                    "unresolved_reason": value.get("unresolved_reason"),
                                    "source_file": value.get("source_file") or filename,
                                    "source_line": value.get("source_line") or node.lineno,
                                })
                        readiness = _readiness(kind, standard_inputs)
                        scope_key = "::".join(scope) or "<module>"
                        instance_count_candidates = [
                            value
                            for value in [
                                fastener_call_placement_counts.get((filename, node.lineno)),
                                source_call_instance_counts.get((filename, node.lineno)),
                                source_scope_hole_counts.get((filename, scope_key)) if kind == "fastener_assembly" else None,
                                return_product_counts.get(short_name),
                            ]
                            if _positive_number(value) is not None
                        ]
                        calls.append({
                            "function": short_name,
                            "qualified_function": name or "",
                            "source_file": filename,
                            "source_scope": "::".join(scope) or "<module>",
                            "source_line": node.lineno,
                            "assignment_targets": list(assignment_target_scope[-1]) if assignment_target_scope else [],
                            "parameters": parameters,
                            "standard_inputs": standard_inputs,
                            "bom_kind": kind,
                            "bom_readiness": readiness["level"],
                            "bom_missing_fields": readiness["missing"],
                            "source_instance_count": max(instance_count_candidates) if instance_count_candidates else None,
                            "signature_source": {
                                "filename": signature.filename,
                                "line": signature.line,
                            } if signature else None,
                        })

            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(tree)

    return {
        "entrypoint": entrypoint,
        "source_files": source_files,
        "calls": calls,
        "constants": [
            {
                "name": value.name,
                "value": value.value,
                "source_file": value.filename,
                "source_line": value.line,
            }
            for value in constants.values()
        ],
        "function_instance_counts": function_instance_counts,
        "diagnostics": diagnostics,
    }


def _node_name(node: dict[str, Any]) -> str:
    return str(node.get("name") or "")


def _is_mesh(node: dict[str, Any]) -> bool:
    return bool(node.get("isMesh")) or node.get("type") == "Mesh" or "mesh" in node


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children")
    return children if isinstance(children, list) else []


def _is_generated_or_default(name: str) -> bool:
    return any(pattern.match(name.strip()) for pattern in GENERATED_NAME_PATTERNS)


def _has_mesh_descendant(node: dict[str, Any]) -> bool:
    return _is_mesh(node) or any(_has_mesh_descendant(child) for child in _children(node))


def _is_named_group(node: dict[str, Any]) -> bool:
    return not _is_mesh(node) and bool(_children(node)) and not _is_generated_or_default(_node_name(node))


def _has_named_group_child_with_meshes(node: dict[str, Any]) -> bool:
    return any(
        (_is_named_group(child) and _has_mesh_descendant(child))
        or _has_named_group_child_with_meshes(child)
        for child in _children(node)
    )


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


def _human_label(value: str) -> str:
    text = re.sub(r"^(make|build)_", "", value.strip())
    text = text.replace("_", " ").replace("-", " ")
    words = [word for word in text.split() if word]
    if not words:
        return value
    acronyms = {"zc": "Z/C", "gpb": "GPB", "cp": "CP"}
    return " ".join(acronyms.get(word.lower(), word.capitalize()) for word in words)


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def analyze_gltf_tree(gltf: dict[str, Any]) -> dict[str, Any]:
    """Classify a simplified GLTF tree or scene tree into assemblies/components."""

    roots = _children(gltf) or [gltf]
    assemblies: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    object_to_assembly: dict[int, str] = {}
    used_ids: set[str] = set()

    def path_for(ancestors: list[dict[str, Any]], node: dict[str, Any]) -> str:
        parts = [_node_name(item) or str(item.get("type") or "node") for item in [*ancestors, node]]
        return "/".join(part.replace("/", "_") for part in parts if part)

    def visit(node: dict[str, Any], ancestors: list[dict[str, Any]]) -> None:
        name = _node_name(node)
        if _is_mesh(node):
            return
        if _is_named_group(node) and _has_mesh_descendant(node):
            if _has_named_group_child_with_meshes(node):
                parent_id = next((object_to_assembly[id(item)] for item in reversed(ancestors) if id(item) in object_to_assembly), None)
                path = path_for(ancestors, node)
                assembly_id = _unique_id(_slug(path, "assembly"), used_ids)
                object_to_assembly[id(node)] = assembly_id
                assemblies.append({
                    "id": assembly_id,
                    "label": name,
                    "path": path,
                    "parent_id": parent_id,
                })
            else:
                parent_id = next((object_to_assembly[id(item)] for item in reversed(ancestors) if id(item) in object_to_assembly), None)
                path = path_for(ancestors, node)
                component_id = _unique_id(_slug(path, "component"), used_ids)
                components.append({
                    "id": component_id,
                    "label": name,
                    "path": path,
                    "assembly_id": parent_id,
                    "visual_node_ids": [str(node.get("id") or path)],
                })
        elif name and not _is_generated_or_default(name):
            diagnostics.append({
                "code": "ignored_named_node_without_meshes",
                "message": f"{name} is named but has no mesh descendants.",
                "path": path_for(ancestors, node),
            })

        for child in _children(node):
            visit(child, [*ancestors, node])

    for root in roots:
        visit(root, [])

    return {
        "assemblies": assemblies,
        "components": components,
        "diagnostics": diagnostics,
    }


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) > 2}


def _source_match_score(component: dict[str, Any], call: dict[str, Any]) -> tuple[int, str]:
    component_text = f"{component.get('label', '')} {component.get('path', '')}"
    assignment_text = " ".join(str(target) for target in call.get("assignment_targets", []) if target)
    call_text = f"{call.get('function', '')} {call.get('source_scope', '')} {call.get('bom_kind', '')} {assignment_text}"
    overlap = _tokens(component_text) & _tokens(call_text)
    score = len(overlap) * 3
    reasons = [f"token overlap: {', '.join(sorted(overlap))}"] if overlap else []
    label = str(component.get("label", "")).lower()
    kind = str(call.get("bom_kind", "")).lower()
    if "bracket" in label and kind == "bracket":
        score += 4
        reasons.append("bracket kind match")
    if "column" in label and "column" in str(call.get("source_scope", "")).lower():
        score += 5
        reasons.append("column scope match")
    if "rafter" in label and "rafter" in str(call.get("source_scope", "")).lower():
        score += 5
        reasons.append("rafter scope match")
    return score, "; ".join(reasons)


def _best_source_call(component: dict[str, Any], source_analysis: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if isinstance(component.get("_source_call"), dict):
        return component["_source_call"], "source-only component candidate"

    calls = source_analysis.get("calls", [])
    best: tuple[dict[str, Any], int, str] | None = None
    for call in calls:
        score, reason = _source_match_score(component, call)
        if best is None or score > best[1]:
            best = (call, score, reason)
    if (best is None or best[1] < 3) and len(calls) == 1:
        return calls[0], "single candidate source call"
    if best is None or best[1] < 3:
        return None, "no source match"
    return best[0], best[2] or "best source match"


def _source_call_key(call: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not isinstance(call, dict):
        return None
    return (
        call.get("source_file"),
        call.get("source_line"),
        call.get("function"),
        call.get("source_scope"),
    )


def _merge_source_scopes(
    assemblies: list[dict[str, Any]],
    components: list[dict[str, Any]],
    source_assemblies: list[dict[str, Any]],
    source_components: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    used_assembly_ids = {
        str(assembly.get("id"))
        for assembly in assemblies
        if assembly.get("id") is not None
    }
    used_component_ids = {
        str(component.get("id"))
        for component in components
        if component.get("id") is not None
    }
    id_map: dict[str, str] = {}
    merged_assemblies = list(assemblies)
    merged_components = list(components)

    for assembly in source_assemblies:
        old_id = str(assembly.get("id") or _slug(str(assembly.get("label") or "source-scope"), "source-scope"))
        new_id = old_id if old_id not in used_assembly_ids else _unique_id(old_id, used_assembly_ids)
        used_assembly_ids.add(new_id)
        id_map[old_id] = new_id
        merged = dict(assembly)
        merged["id"] = new_id
        parent_id = merged.get("parent_id")
        if isinstance(parent_id, str) and parent_id in id_map:
            merged["parent_id"] = id_map[parent_id]
        merged_assemblies.append(merged)

    for component in source_components:
        old_id = str(component.get("id") or _slug(str(component.get("label") or "source-component"), "source-component"))
        new_id = old_id if old_id not in used_component_ids else _unique_id(old_id, used_component_ids)
        used_component_ids.add(new_id)
        merged = dict(component)
        merged["id"] = new_id
        assembly_id = merged.get("assembly_id")
        if isinstance(assembly_id, str) and assembly_id in id_map:
            merged["assembly_id"] = id_map[assembly_id]
        merged_components.append(merged)

    return merged_assemblies, merged_components


def _manifest_scopes_to_assemblies(explicit_manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not explicit_manifest:
        return []
    assemblies: list[dict[str, Any]] = []
    for scope in explicit_manifest.get("scopes", []):
        if not isinstance(scope, dict):
            continue
        assemblies.append({
            "id": str(scope.get("id") or _slug(str(scope.get("label") or "scope"), "scope")),
            "label": str(scope.get("label") or scope.get("id") or "Scope"),
            "path": str(scope.get("label") or scope.get("id") or "Scope"),
            "parent_id": scope.get("parent_id"),
            "source_file": scope.get("source_file"),
            "source_line": scope.get("source_line"),
            "scope_source": scope.get("scope_source"),
        })
    return assemblies


def _source_scope_assemblies_and_components(source_analysis: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assemblies: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    assembly_ids: dict[str, str | None] = {"<module>": None}
    used_ids: set[str] = set()
    entrypoint = str(source_analysis.get("entrypoint") or "design.py")

    for call in source_analysis.get("calls", []):
        if not isinstance(call, dict):
            continue
        if call.get("diagnostic"):
            continue
        if call.get("source_file") != entrypoint:
            continue
        standard_inputs = call.get("standard_inputs") if isinstance(call.get("standard_inputs"), dict) else {}
        if not standard_inputs and call.get("bom_kind") == "component":
            continue
        if (
            _resolved_input(call, "part_number") is None
            and call.get("bom_kind") != "bracket"
            and not _is_decomposable_fastener_assembly(call)
        ):
            continue

        source_scope = str(call.get("source_scope") or "<module>")
        assembly_id = None
        if source_scope != "<module>":
            scope_parts = source_scope.split("::")
            parent_id = None
            scope_path: list[str] = []
            for scope_part in scope_parts:
                scope_path.append(scope_part)
                scope_key = "::".join(scope_path)
                if scope_key not in assembly_ids:
                    new_id = _unique_id(_slug(scope_key, "source-scope"), used_ids)
                    assembly_ids[scope_key] = new_id
                    assemblies.append({
                        "id": new_id,
                        "label": _human_label(scope_part),
                        "path": scope_key,
                        "parent_id": parent_id,
                        "source": "ast_source_scope",
                    })
                parent_id = assembly_ids[scope_key]
            assembly_id = parent_id

        label = str(call.get("function") or "source_call")
        component_id = _unique_id(
            _slug(f"{source_scope}-{label}-{call.get('source_line')}", "source-component"),
            used_ids,
        )
        components.append({
            "id": component_id,
            "label": label,
            "path": f"{source_scope}/{label}",
            "assembly_id": assembly_id,
            "visual_node_ids": [],
            "_source_call": call,
        })

    return assemblies, components


def _resolved_input(call: dict[str, Any], key: str) -> Any:
    value = call.get("standard_inputs", {}).get(key)
    if isinstance(value, dict):
        return value.get("resolved")
    return None


def _input_trace(call: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    if not call:
        return None
    value = call.get("standard_inputs", {}).get(key)
    return value if isinstance(value, dict) else None


def _notable_input_trace(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    if value.get("dependencies") or value.get("unresolved_reason"):
        return value
    return None


def _is_decomposable_fastener_assembly(call: dict[str, Any] | None) -> bool:
    if not call or call.get("bom_kind") != "fastener_assembly":
        return False
    return _resolved_input(call, "size") is not None and _resolved_input(call, "length_mm") is not None


def _make_generated_part_key(component: dict[str, Any], call: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    label = str(component.get("label") or "component")
    kind = str(call.get("bom_kind") if call else "component").upper().replace("_", "-")
    standard_inputs = call.get("standard_inputs", {}) if call else {}
    resolved_inputs = {
        key: value.get("resolved")
        for key, value in standard_inputs.items()
        if isinstance(value, dict) and value.get("resolved") is not None
    }
    angle = resolved_inputs.get("roof_pitch_deg") or resolved_inputs.get("angle_deg")
    compact_key = _compact_identity_key(resolved_inputs)
    if compact_key:
        return compact_key, {
            "raw": {"kind": "generated", "fields": resolved_inputs},
            "resolved": compact_key,
            "resolution": "generated_compact_identity",
            "source_file": call.get("source_file") if call else None,
            "source_line": call.get("source_line") if call else None,
        }
    prefix_parts = [kind, re.sub(r"[^A-Z0-9]+", "-", label.upper()).strip("-") or "COMPONENT"]
    public_component = {key: value for key, value in component.items() if not key.startswith("_")}
    if angle is not None:
        prefix_parts.append(f"{str(angle).replace('.', 'P')}DEG")
    seed = repr({
        "component": public_component,
        "function": call.get("function") if call else "",
        "source_scope": call.get("source_scope") if call else "",
        "inputs": sorted(resolved_inputs.items()),
    })
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()
    return "-".join(prefix_parts + [digest]), {
        "raw": {"kind": "generated", "seed": seed},
        "resolved": "-".join(prefix_parts + [digest]),
        "resolution": "generated_deterministic",
        "source_file": call.get("source_file") if call else None,
        "source_line": call.get("source_line") if call else None,
    }


def _compact_dimension(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        scaled = value / 100.0
        if abs(scaled - round(scaled)) < 0.000001:
            return str(int(round(scaled))).zfill(2)
        return f"{scaled:.1f}".rstrip("0").rstrip(".").replace(".", "P")
    text = str(value).strip()
    return text or None


def _compact_angle(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if abs(value - round(value)) < 0.000001:
            return str(int(round(value)))
        return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", "P")
    text = str(value).strip()
    return text or None


def _compact_identity_key(resolved_inputs: dict[str, Any]) -> str | None:
    mark = resolved_inputs.get("mark")
    if not mark:
        return None
    parts = [str(mark).strip().upper()]
    for key in ["length_mm", "width_mm", "height_mm", "thickness_mm"]:
        compact = _compact_dimension(resolved_inputs.get(key))
        if compact is not None:
            parts.append(compact)
    angle = _compact_angle(resolved_inputs.get("angle_deg") or resolved_inputs.get("roof_pitch_deg"))
    if angle is not None:
        parts.append(angle)
    if len(parts) == 1:
        return None
    return "-".join(part for part in parts if part)


def _stock_number(part_number: Any, dimensions: dict[str, Any]) -> str | None:
    if not part_number:
        return None
    length = dimensions.get("length_mm")
    if length is None:
        return None
    compact = _compact_dimension(length)
    if compact is None:
        return None
    return f"{part_number}-{compact}"


def _requirement_stock_number(part_number: Any, dimensions: dict[str, Any], resolution_trace: dict[str, Any]) -> str | None:
    part_trace = resolution_trace.get("part_number")
    if isinstance(part_trace, dict) and part_trace.get("resolution") == "static_product_table":
        return None
    return _stock_number(part_number, dimensions)


def _positive_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value > 0:
        return value
    return None


def _visual_instance_count(component: dict[str, Any]) -> int | None:
    explicit = _positive_number(component.get("visual_instance_count"))
    if explicit is not None:
        return int(explicit)
    visual_node_ids = component.get("visual_node_ids")
    if isinstance(visual_node_ids, list) and len(visual_node_ids) > 1:
        return len(visual_node_ids)
    return None


def _assembly_multiplier(call: dict[str, Any] | None, source_analysis: dict[str, Any]) -> int:
    if not call:
        return 1
    source_scope = str(call.get("source_scope") or "")
    if not source_scope:
        return 1
    root_scope = source_scope.split("::", 1)[0]
    counts = source_analysis.get("function_instance_counts")
    if isinstance(counts, dict):
        value = _positive_number(counts.get(root_scope))
        if value is not None:
            return int(value)
    return 1


def _quantity_evidence(call: dict[str, Any] | None, component: dict[str, Any], source_analysis: dict[str, Any]) -> dict[str, Any]:
    explicit_quantity = _positive_number(_resolved_input(call, "quantity")) if call else None
    visual_count = _visual_instance_count(component)
    assembly_multiplier = _assembly_multiplier(call, source_analysis)
    source_instance_count = int(_positive_number(call.get("source_instance_count")) or 1) if call else 1

    if explicit_quantity == 1 and source_instance_count != 1:
        quantity = source_instance_count
        source = "explicit_source_calls"
        confidence = "probable"
    elif explicit_quantity is not None:
        quantity = explicit_quantity
        source = "explicit"
        confidence = "verified"
    elif visual_count is not None:
        quantity = visual_count
        source = "visual_instances"
        confidence = "verified"
    else:
        quantity = source_instance_count
        source = "source_calls"
        confidence = "probable"

    rolled_up_quantity = quantity * assembly_multiplier if source in {"source_calls", "explicit_source_calls"} else quantity

    trace = {
        "explicit_quantity": explicit_quantity,
        "visual_instance_count": visual_count,
        "assembly_instance_multiplier": assembly_multiplier,
        "source_call_count": 1,
    }
    if source_instance_count != 1:
        trace["source_instance_count"] = source_instance_count
    if explicit_quantity is not None and visual_count is not None and explicit_quantity != visual_count:
        confidence = "diagnostic"
        trace["mismatch"] = {
            "explicit_quantity": explicit_quantity,
            "visual_instance_count": visual_count,
        }

    return {
        "quantity": quantity,
        "rolled_up_quantity": rolled_up_quantity,
        "quantity_source": source,
        "quantity_confidence": confidence,
        "visual_instance_count": visual_count,
        "assembly_instance_multiplier": assembly_multiplier,
        "source_call_count": 1,
        "count_trace": trace,
    }


def _generated_fastener_part_trace(
    *,
    part_number: str,
    standard: str,
    call: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "raw": {"kind": "generated", "standard": standard},
        "resolved": part_number,
        "resolution": "generated_standard_fastener",
        "source_file": call.get("source_file") if call else None,
        "source_line": call.get("source_line") if call else None,
    }


def _fastener_assembly_requirements(
    *,
    component: dict[str, Any],
    call: dict[str, Any],
    component_record: dict[str, Any],
    quantity_evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    size = _resolved_input(call, "size")
    length = _resolved_input(call, "length_mm")
    grip_length = _resolved_input(call, "grip_length_mm")
    size_text = str(size).strip().upper() if size is not None else None
    length_text = _compact_angle(length)

    bolt_part = f"DIN-6921-{size_text}X{length_text}" if size_text and length_text else None
    nut_part = f"DIN-6923-{size_text}" if size_text else None
    base_dimensions = {"size": size_text} if size_text else {}

    bolt_dimensions = dict(base_dimensions)
    if length is not None:
        bolt_dimensions["length_mm"] = length
    if grip_length is not None:
        bolt_dimensions["grip_length_mm"] = grip_length

    nut_dimensions = dict(base_dimensions)
    bolt_quantity = {
        **quantity_evidence,
        "count_trace": dict(quantity_evidence.get("count_trace", {})),
    }
    nut_quantity = {
        **quantity_evidence,
        "count_trace": dict(quantity_evidence.get("count_trace", {})),
    }

    return [
        {
            "id": f"{component['id']}.bolt.requirement",
            "component_id": component["id"],
            "assembly_id": component.get("assembly_id"),
            "part_number": bolt_part,
            "stock_number": None,
            **bolt_quantity,
            "unit": "each",
            "dimensions": bolt_dimensions,
            "material": _resolved_input(call, "material"),
            "finish": _resolved_input(call, "finish"),
            "source_trace": {
                **component_record["source_trace"],
                "decomposed_from": "fastener_assembly",
                "procurement_item": "bolt",
            },
            "resolution_trace": {
                "part_number": _generated_fastener_part_trace(
                    part_number=bolt_part,
                    standard="DIN 6921 hex flange bolt",
                    call=call,
                ) if bolt_part else None,
            },
        },
        {
            "id": f"{component['id']}.nut.requirement",
            "component_id": component["id"],
            "assembly_id": component.get("assembly_id"),
            "part_number": nut_part,
            "stock_number": None,
            **nut_quantity,
            "unit": "each",
            "dimensions": nut_dimensions,
            "material": _resolved_input(call, "material"),
            "finish": _resolved_input(call, "finish"),
            "source_trace": {
                **component_record["source_trace"],
                "decomposed_from": "fastener_assembly",
                "procurement_item": "nut",
            },
            "resolution_trace": {
                "part_number": _generated_fastener_part_trace(
                    part_number=nut_part,
                    standard="DIN 6923 hex flange nut",
                    call=call,
                ) if nut_part else None,
            },
        },
    ]


def _requirement_group_key(requirement: dict[str, Any]) -> tuple[Any, ...]:
    dimensions = requirement.get("dimensions") if isinstance(requirement.get("dimensions"), dict) else {}
    return (
        requirement.get("assembly_id"),
        requirement.get("part_number"),
        requirement.get("stock_number"),
        requirement.get("unit"),
        tuple(sorted(dimensions.items())),
        requirement.get("material"),
        requirement.get("finish"),
    )


def _annotate_source_call_counts(requirements: list[dict[str, Any]]) -> None:
    counts: dict[tuple[Any, ...], int] = {}
    for requirement in requirements:
        key = _requirement_group_key(requirement)
        counts[key] = counts.get(key, 0) + 1
    for requirement in requirements:
        source_call_count = counts[_requirement_group_key(requirement)]
        requirement["source_call_count"] = source_call_count
        trace = requirement.get("count_trace")
        if isinstance(trace, dict):
            trace["source_call_count"] = source_call_count


def _normalize_explicit_manifest_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        quantity = _positive_number(requirement.get("quantity")) or 1
        visual_count = _positive_number(requirement.get("visual_instance_count"))
        source_call_count = int(_positive_number(requirement.get("source_call_count")) or 1)
        normalized.append({
            **requirement,
            "quantity": quantity,
            "rolled_up_quantity": requirement.get("rolled_up_quantity") or quantity,
            "quantity_source": requirement.get("quantity_source") or "explicit",
            "quantity_confidence": requirement.get("quantity_confidence") or "verified",
            "visual_instance_count": int(visual_count) if visual_count is not None else None,
            "source_call_count": source_call_count,
            "count_trace": requirement.get("count_trace") or {
                "explicit_quantity": quantity,
                "visual_instance_count": int(visual_count) if visual_count is not None else None,
                "source_call_count": source_call_count,
            },
        })
    return normalized


def build_procurement_analysis(
    source_analysis: dict[str, Any],
    tree_analysis: dict[str, Any],
    explicit_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic procurement analysis artifact."""

    diagnostics = [
        *list(tree_analysis.get("diagnostics", [])),
        *list(source_analysis.get("diagnostics", [])),
    ]
    if explicit_manifest and explicit_manifest.get("requirements"):
        return {
            "version": 1,
            "source": "explicit_manifest",
            "assemblies": explicit_manifest.get("scopes", []),
            "components": explicit_manifest.get("components", []),
            "requirements": _normalize_explicit_manifest_requirements(explicit_manifest.get("requirements", [])),
            "diagnostics": explicit_manifest.get("diagnostics", []),
        }

    source_only = False
    tree_components = list(tree_analysis.get("components", []))
    assemblies = list(tree_analysis.get("assemblies", []))
    if not assemblies:
        assemblies = _manifest_scopes_to_assemblies(explicit_manifest)
    source_assemblies, source_components = _source_scope_assemblies_and_components(source_analysis)
    if not tree_components and source_components:
        assemblies, tree_components = _merge_source_scopes(assemblies, [], source_assemblies, source_components)
        source_only = True
        diagnostics.append({
            "code": "source_only_components_no_visual_tree",
            "message": "GLTF hierarchy did not expose named component groups; requirements are source-derived and not visually linked.",
        })
    elif tree_components and source_components:
        matched_call_keys: set[tuple[Any, ...]] = set()
        for component in tree_components:
            call, _match_reason = _best_source_call(component, source_analysis)
            call_key = _source_call_key(call)
            if call_key is not None:
                matched_call_keys.add(call_key)
        source_supplements = [
            component
            for component in source_components
            if _source_call_key(component.get("_source_call")) not in matched_call_keys
        ]
        if source_supplements:
            visual_component_count = len(tree_components)
            assemblies, tree_components = _merge_source_scopes(
                assemblies,
                tree_components,
                source_assemblies,
                source_supplements,
            )
            diagnostics.append({
                "code": "hybrid_source_components_added",
                "message": (
                    f"GLTF exposed {visual_component_count} named component groups; "
                    f"{len(source_supplements)} source-derived procurement components were added for unmatched calls."
                ),
                "visual_component_count": visual_component_count,
                "source_component_count": len(source_components),
                "added_source_component_count": len(source_supplements),
            })

    requirements: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    for component in tree_components:
        call, match_reason = _best_source_call(component, source_analysis)
        public_component = {key: value for key, value in component.items() if not key.startswith("_")}
        component_record = {
            **public_component,
            "source_trace": {
                "function": call.get("function") if call else None,
                "source_file": call.get("source_file") if call else None,
                "source_scope": call.get("source_scope") if call else None,
                "source_line": call.get("source_line") if call else None,
                "match_reason": match_reason,
            },
        }
        components.append(component_record)

        part_number = _resolved_input(call, "part_number") if call else None
        generated_trace = None
        if not part_number and call and call.get("bom_kind") in {"bracket", "component"}:
            part_number, generated_trace = _make_generated_part_key(component, call)

        dimensions = {
            key: _resolved_input(call, key)
            for key in ["length_mm", "width_mm", "height_mm", "thickness_mm", "diameter_mm", "grip_length_mm", "roof_pitch_deg", "angle_deg"]
            if call and _resolved_input(call, key) is not None
        }
        dimension_trace = {
            key: trace
            for key in dimensions
            if (trace := _notable_input_trace(_input_trace(call, key))) is not None
        }
        quantity_trace = _notable_input_trace(_input_trace(call, "quantity"))
        resolution_trace = {
            "part_number": generated_trace or (call.get("standard_inputs", {}).get("part_number") if call else None),
        }
        if dimension_trace:
            resolution_trace["dimensions"] = dimension_trace
        if quantity_trace:
            resolution_trace["quantity"] = quantity_trace
        quantity_evidence = _quantity_evidence(call, component, source_analysis)
        if _is_decomposable_fastener_assembly(call):
            fastener_requirements = _fastener_assembly_requirements(
                component=component,
                call=call,
                component_record=component_record,
                quantity_evidence=quantity_evidence,
            )
            requirements.extend(fastener_requirements)
            for fastener_requirement in fastener_requirements:
                if not fastener_requirement.get("part_number"):
                    diagnostics.append({
                        "code": "requirement_missing_part_number",
                        "message": f"{component.get('label')} {fastener_requirement['source_trace']['procurement_item']} has no resolved part number.",
                        "component_id": component["id"],
                    })
            continue

        requirement = {
            "id": f"{component['id']}.requirement",
            "component_id": component["id"],
            "assembly_id": component.get("assembly_id"),
            "part_number": part_number,
            "stock_number": _requirement_stock_number(part_number, dimensions, resolution_trace),
            **quantity_evidence,
            "unit": _resolved_input(call, "unit") if call and _resolved_input(call, "unit") else "each",
            "dimensions": dimensions,
            "material": _resolved_input(call, "material") if call else None,
            "finish": _resolved_input(call, "finish") if call else None,
            "source_trace": component_record["source_trace"],
            "resolution_trace": resolution_trace,
        }
        requirements.append(requirement)

        if quantity_evidence["quantity_confidence"] == "diagnostic":
            diagnostics.append({
                "code": "quantity_evidence_mismatch",
                "message": f"{component.get('label')} has conflicting explicit and visual quantities.",
                "component_id": component["id"],
                "count_trace": quantity_evidence["count_trace"],
            })

        if not part_number:
            diagnostics.append({
                "code": "requirement_missing_part_number",
                "message": f"{component.get('label')} has no resolved part number.",
                "component_id": component["id"],
            })

    _annotate_source_call_counts(requirements)

    return {
        "version": 1,
        "source": "source_only_analysis" if source_only else "deterministic_analysis",
        "assemblies": assemblies,
        "components": components,
        "requirements": requirements,
        "diagnostics": diagnostics,
    }
