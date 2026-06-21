from __future__ import annotations

import ast
import hashlib
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


SAFE_BUILTIN_NUMERIC_FUNCTIONS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": sum,
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
    if isinstance(node, ast.Constant) and isinstance(node.value, str | int | float | bool):
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
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value, _resolution = _static_value_with_resolution(node.operand, known)
        if isinstance(value, int | float):
            return -value, "static_numeric"
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
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
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
    return {"kind": "expression", "source": _unparse(node)}


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


def _collect_constants(files: dict[str, str], source_files: list[str]) -> dict[tuple[str, str], ConstantValue]:
    constants: dict[tuple[str, str], ConstantValue] = {}
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
                    value, resolution = _static_value_with_resolution(node.value, known)
                except ValueError:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        remember(target.id, value, node.lineno, resolution)
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
) -> dict[str, Any]:
    raw = _compact_expr(node)
    static_known = {
        name: value
        for (constant_file, name), value in constants.items()
        if constant_file == filename
    }
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
    try:
        value, resolution = _static_value_with_resolution(node, static_known)
        return {
            "raw": raw,
            "resolved": value,
            "resolution": method_hint if method_hint == "function_default" else resolution,
            "source_file": filename,
            "source_line": getattr(node, "lineno", None),
        }
    except ValueError:
        return {
            "raw": raw,
            "resolved": None,
            "resolution": "unresolved_expression",
            "source_file": filename,
            "source_line": getattr(node, "lineno", None),
        }


def _infer_kind(function_name: str) -> str:
    name = function_name.lower()
    if "fastener" in name:
        return "fastener_assembly"
    if any(token in name for token in ("purlin", "fascia", "column", "rafter", "member")):
        return "structural_member"
    if any(token in name for token in ("bracket", "gpb", "cp", "apex")):
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
    if key in {"pitch", "roof_pitch", "roof_pitch_degrees"}:
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


def _should_record_call(function_name: str, parameters: dict[str, dict[str, Any]], signature: FunctionSignature | None) -> bool:
    if function_name in PROCUREMENT_HELPER_FUNCTIONS:
        return False
    if signature is not None and signature.return_annotation is not None and not _returns_build_geometry(signature):
        return False
    if function_name[:1].isupper() and signature is None:
        return False
    if function_name.startswith("make_"):
        return True
    if _infer_kind(function_name) != "component":
        return True
    if signature and set(parameters) & STANDARD_BOM_FIELDS:
        return True
    return any(_standard_key(key) in STANDARD_BOM_FIELDS for key in parameters)


def _iterable_count(node: ast.AST, known: dict[str, Any]) -> int | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enumerate" and node.args:
        return _iterable_count(node.args[0], known)
    try:
        value = _static_value(node, known)
    except ValueError:
        return None
    if isinstance(value, list | tuple):
        return len(value)
    return None


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


def analyze_design_sources(files: dict[str, str], entrypoint: str = "design.py") -> dict[str, Any]:
    """Analyze Python source files without executing design.py."""

    source_files = _collect_import_closure(files, entrypoint)
    import_aliases = _collect_import_aliases(files, source_files)
    constants = _collect_constants(files, source_files)
    signatures = _collect_signatures(files, source_files)
    function_instance_counts = _collect_function_instance_counts(files, source_files, constants)
    calls: list[dict[str, Any]] = []

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
                        )
                parameter_scope.append(defaults)
                for child in ast.iter_child_nodes(node):
                    visit(child)
                parameter_scope.pop()
                scope.pop()
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
                            )

                    if _should_record_call(short_name, parameters, signature):
                        kind = _infer_kind(short_name)
                        standard_inputs = {
                            _standard_key(param): value
                            for param, value in parameters.items()
                            if _standard_key(param) in STANDARD_BOM_FIELDS
                        }
                        readiness = _readiness(kind, standard_inputs)
                        calls.append({
                            "function": short_name,
                            "qualified_function": name or "",
                            "source_file": filename,
                            "source_scope": "::".join(scope) or "<module>",
                            "source_line": node.lineno,
                            "parameters": parameters,
                            "standard_inputs": standard_inputs,
                            "bom_kind": kind,
                            "bom_readiness": readiness["level"],
                            "bom_missing_fields": readiness["missing"],
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
    call_text = f"{call.get('function', '')} {call.get('source_scope', '')} {call.get('bom_kind', '')}"
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

    for call in source_analysis.get("calls", []):
        if not isinstance(call, dict):
            continue
        if call.get("diagnostic"):
            continue
        standard_inputs = call.get("standard_inputs") if isinstance(call.get("standard_inputs"), dict) else {}
        if not standard_inputs and call.get("bom_kind") == "component":
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
    for key in ["length_mm", "width_mm", "height_mm"]:
        compact = _compact_dimension(resolved_inputs.get(key))
        if compact is not None:
            parts.append(compact)
    angle = _compact_angle(resolved_inputs.get("angle_deg") or resolved_inputs.get("roof_pitch_deg"))
    if angle is not None:
        parts.append(angle)
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

    if explicit_quantity is not None:
        quantity = explicit_quantity
        source = "explicit"
        confidence = "verified"
    elif visual_count is not None:
        quantity = visual_count
        source = "visual_instances"
        confidence = "verified"
    else:
        quantity = 1
        source = "source_calls"
        confidence = "probable"

    rolled_up_quantity = quantity * assembly_multiplier if source == "source_calls" else quantity

    trace = {
        "explicit_quantity": explicit_quantity,
        "visual_instance_count": visual_count,
        "assembly_instance_multiplier": assembly_multiplier,
        "source_call_count": 1,
    }
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

    diagnostics = list(tree_analysis.get("diagnostics", []))
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
    if not tree_components and explicit_manifest is not None:
        source_assemblies, tree_components = _source_scope_assemblies_and_components(source_analysis)
        if source_assemblies:
            assemblies = source_assemblies
        source_only = bool(tree_components)
        if source_only:
            diagnostics.append({
                "code": "source_only_components_no_visual_tree",
                "message": "GLTF hierarchy did not expose named component groups; requirements are source-derived and not visually linked.",
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
        quantity_evidence = _quantity_evidence(call, component, source_analysis)
        requirement = {
            "id": f"{component['id']}.requirement",
            "component_id": component["id"],
            "assembly_id": component.get("assembly_id"),
            "part_number": part_number,
            "stock_number": _stock_number(part_number, dimensions),
            **quantity_evidence,
            "unit": _resolved_input(call, "unit") if call and _resolved_input(call, "unit") else "each",
            "dimensions": dimensions,
            "material": _resolved_input(call, "material") if call else None,
            "finish": _resolved_input(call, "finish") if call else None,
            "source_trace": component_record["source_trace"],
            "resolution_trace": {
                "part_number": generated_trace or (call.get("standard_inputs", {}).get("part_number") if call else None),
            },
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
