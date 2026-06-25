from __future__ import annotations

import ast
import math
import re
from typing import Any, Iterable, Iterator

from .ast_helpers import _call_name, _static_value, _unparse
from .model import (
    ConstantValue,
    FunctionSignature,
    PROCUREMENT_HELPER_FUNCTIONS,
    STANDARD_BOM_FIELDS,
    StaticObject,
)
from .static_resolution import StaticValueResolver, _returns_build_geometry

def _infer_kind(function_name: str) -> str:
    name = function_name.lower()
    name_tokens = {token for token in re.split(r"[^a-z0-9]+", name) if token}
    if "fastener" in name:
        return "fastener_assembly"
    if any(token in name for token in ("purlin", "fascia", "column", "rafter", "member")):
        return "structural_member"
    if "bracket" in name or any(token in name_tokens for token in ("gpb", "cp", "apex", "plate")):
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


GEOMETRY_PRIMITIVE_CALLS = {
    "Box",
    "Cone",
    "Cylinder",
    "Sphere",
    "Torus",
    "Wedge",
}


def _function_declares_procurement_inputs(function: ast.FunctionDef, signature: FunctionSignature | None) -> bool:
    params = signature.params if signature else [arg.arg for arg in function.args.args]
    return any(_standard_key(param) in STANDARD_BOM_FIELDS for param in params)


def _walk_function_body(function: ast.FunctionDef) -> Iterator[ast.AST]:
    for statement in function.body:
        for node in ast.walk(statement):
            if node is function:
                continue
            if isinstance(node, ast.FunctionDef):
                continue
            yield node


def _call_label(
    node: ast.Call,
    *,
    filename: str,
    known: dict[str, Any],
    static_resolver: StaticValueResolver,
) -> str | None:
    for keyword in node.keywords:
        if keyword.arg != "label":
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value.strip() or None
        try:
            value = static_resolver.resolve(keyword.value, known, filename=filename).value
        except ValueError:
            return None
        if isinstance(value, str):
            return value.strip() or None
    return None


def _collect_unrepresented_geometry_diagnostics(
    files: dict[str, str],
    source_files: Iterable[str],
    signatures: dict[str, FunctionSignature],
    calls: list[dict[str, Any]],
    static_resolver: StaticValueResolver,
) -> list[dict[str, Any]]:
    useful_call_scopes = {
        str(call.get("source_scope") or "")
        for call in calls
        if call.get("standard_inputs")
    }
    covered_dependency_functions = {
        str(dependency)
        for call in calls
        for value in (call.get("standard_inputs") or {}).values()
        if isinstance(value, dict)
        for dependency in value.get("dependencies", [])
    }
    diagnostics: list[dict[str, Any]] = []

    for filename in source_files:
        try:
            tree = ast.parse(files[filename], filename=filename)
        except SyntaxError:
            continue
        known = static_resolver.known_for(filename)
        for function in [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]:
            if function.name.startswith("_"):
                continue
            signature = signatures.get(function.name)
            if _function_declares_procurement_inputs(function, signature):
                continue
            if function.name in covered_dependency_functions:
                continue
            if any(scope == function.name or scope.startswith(f"{function.name}::") for scope in useful_call_scopes):
                continue

            labels: list[str] = []
            primitive_counts: dict[str, int] = {}
            build_part_count = 0
            for node in _walk_function_body(function):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node.func)
                short_name = name.rsplit(".", 1)[-1] if name else ""
                if short_name == "BuildPart":
                    build_part_count += 1
                if short_name == "Compound":
                    label = _call_label(node, filename=filename, known=known, static_resolver=static_resolver)
                    if label:
                        labels.append(label)
                if short_name in GEOMETRY_PRIMITIVE_CALLS:
                    primitive_counts[short_name] = primitive_counts.get(short_name, 0) + 1

            if not labels and not primitive_counts:
                continue
            diagnostic_labels = sorted(set(labels))
            primary = ", ".join(diagnostic_labels[:4]) or ", ".join(sorted(primitive_counts))
            diagnostics.append({
                "code": "unrepresented_geometry_source",
                "severity": "warning",
                "message": (
                    f"{function.name} builds labelled/direct geometry ({primary}) but no procurement-readable "
                    "metadata was found. Add part_number/product_key, quantity, unit, dimensions, or tertius_bom metadata."
                ),
                "function": function.name,
                "source_file": filename,
                "source_line": function.lineno,
                "labels": diagnostic_labels,
                "primitive_counts": primitive_counts,
                "build_part_count": build_part_count,
                "suggested_action": "Add BoM arguments or explicit tertius_bom requirements for this geometry.",
            })

    return diagnostics


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
                source_value = parameters.get("p2")
                source = source_value if isinstance(source_value, dict) else {}
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
                        count: int | None
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

                        count: int | None
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
