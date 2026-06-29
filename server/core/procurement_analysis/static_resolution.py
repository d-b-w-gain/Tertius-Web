from __future__ import annotations

import ast
import json
from typing import Any

from .ast_helpers import (
    _call_name,
    _collect_import_aliases,
    _compact_expr,
    _static_value,
    _static_value_with_resolution,
    _unparse,
)
from .model import ConstantValue, FunctionSignature, StaticObject, StaticResolution

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
            dict_dependencies: list[str] = []
            for key_node, value_node in zip(node.keys, node.values, strict=True):
                if key_node is None:
                    return None
                try:
                    key = self.resolve(key_node, known, filename=filename, call_stack=call_stack)
                    value = self.resolve(value_node, known, filename=filename, call_stack=call_stack)
                except ValueError:
                    return None
                result[key.value] = value.value
                dict_dependencies.extend(key.dependencies)
                dict_dependencies.extend(value.dependencies)
            return StaticResolution(value=result, resolution="static_container", dependencies=tuple(dict_dependencies))
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
                target_list = local_known.get(call.func.value.id)
                if not isinstance(target_list, list):
                    setattr(statement, "_static_not_supported", True)
                    return None
                try:
                    target_list.append(self.resolve(call.args[0], local_known, filename=filename, call_stack=call_stack).value)
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

        def remember_file_constant(name: str, value: Any, line: int, resolution: str) -> None:
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
                    remember_file_constant(target.id, value, node.lineno, resolution)
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
            keyword_only_params = [arg.arg for arg in node.args.kwonlyargs]
            defaults: dict[str, ast.AST] = {}
            if node.args.defaults:
                default_params = params[-len(node.args.defaults):]
                defaults.update(zip(default_params, node.args.defaults, strict=True))
            for param, default in zip(keyword_only_params, node.args.kw_defaults, strict=True):
                if default is not None:
                    defaults[param] = default
            signatures[node.name] = FunctionSignature(
                name=node.name,
                params=[*params, *keyword_only_params],
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
