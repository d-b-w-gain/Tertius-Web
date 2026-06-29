from __future__ import annotations

import ast
from typing import Any

from .ast_helpers import _assignment_target_names, _call_name, _collect_import_aliases, _collect_import_closure
from .model import STANDARD_BOM_FIELDS, positive_number
from .source_inventory import (
    _collect_fastener_call_placement_counts,
    _collect_function_instance_counts,
    _collect_return_product_counts,
    _collect_source_call_instance_counts,
    _collect_unrepresented_geometry_diagnostics,
    _infer_kind,
    _infer_standard_inputs_from_parameters,
    _readiness,
    _should_record_call,
    _standard_key,
)
from .static_resolution import StaticValueResolver, _collect_constants, _collect_signatures, _resolve_expr

_positive_number = positive_number


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
                    assignment_scoped_parameters = parameter_scope[-1]
                    resolved_assignment = _resolve_expr(
                        node.value,
                        filename=filename,
                        constants=constants,
                        import_aliases=import_aliases,
                        method_hint="local_assignment",
                        scoped_parameters=assignment_scoped_parameters,
                        static_resolver=static_resolver,
                    )
                    if resolved_assignment.get("resolved") is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                assignment_scoped_parameters[target.id] = resolved_assignment
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
                    call_scoped_parameters = parameter_scope[-1] if parameter_scope else None
                    parameters: dict[str, dict[str, Any]] = {}

                    if signature:
                        for param, default in signature.defaults.items():
                            parameters[param] = _resolve_expr(
                                default,
                                filename=signature.filename,
                                constants=constants,
                                import_aliases=import_aliases,
                                method_hint="function_default",
                                scoped_parameters=call_scoped_parameters,
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
                                scoped_parameters=call_scoped_parameters,
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
                                scoped_parameters=call_scoped_parameters,
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
                                scoped_parameters=call_scoped_parameters,
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
                        instance_count_candidates: list[int] = []
                        for count_candidate in [
                            fastener_call_placement_counts.get((filename, node.lineno)),
                            source_call_instance_counts.get((filename, node.lineno)),
                            source_scope_hole_counts.get((filename, scope_key)) if kind == "fastener_assembly" else None,
                            return_product_counts.get(short_name),
                        ]:
                            positive_value = _positive_number(count_candidate)
                            if positive_value is not None:
                                instance_count_candidates.append(int(positive_value))
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

    diagnostics.extend(_collect_unrepresented_geometry_diagnostics(
        files,
        source_files,
        signatures,
        calls,
        static_resolver,
    ))

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
