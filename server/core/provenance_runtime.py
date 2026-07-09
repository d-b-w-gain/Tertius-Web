from __future__ import annotations

TERTIUS_PROVENANCE_HELPER_SOURCE = r'''
from __future__ import annotations

import inspect
from pathlib import Path
import sys
from typing import Any

import build123d as bd

STANDARD_FIELDS = {
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
    "grade",
    "source_library",
    "bracket_type",
    "roof_pitch",
    "roof_pitch_deg",
    "angle",
    "angle_deg",
    "span",
    "span_mm",
    "model_length",
    "stock_length_mm",
    "stock_width_mm",
    "cut_length_mm",
    "cut_width_mm",
    "drawing_number",
}

ALIASES = {
    "length": "length_mm",
    "model_length": "length_mm",
    "grip_length": "grip_length_mm",
    "roof_pitch": "roof_pitch_deg",
    "angle": "angle_deg",
    "span": "span_mm",
    "supplier_part_number": "part_number",
}

_project_dir: Path | None = None
_project_dir_prefix = ""
_previous_profile = None
_active_calls: dict[int, str] = {}
_call_stack: list[str] = []
_source_calls: dict[str, dict[str, Any]] = {}
_next_call_index = 1
_patched_methods: list[tuple[type, str, Any]] = []
_patched_functions: list[tuple[Any, str, Any]] = []
_inside_tracer = False


def _shape_classes() -> list[type]:
    classes: list[type] = []
    for name in ("Shape", "Part", "Solid", "Compound"):
        cls = getattr(bd, name, None)
        if isinstance(cls, type) and cls not in classes:
            classes.append(cls)
    return classes


def _jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonish(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonish(item) for item in value]
    return str(value)


def _path_for(filename: str) -> Path | None:
    try:
        return Path(filename).resolve()
    except Exception:
        return None


def _relative_file(filename: str | None) -> str | None:
    if not filename:
        return None
    path = _path_for(filename)
    if path is None:
        return filename
    if _project_dir is not None:
        try:
            return str(path.relative_to(_project_dir))
        except Exception:
            pass
    return path.name


def _is_project_frame(frame) -> bool:
    if _project_dir is None:
        return False
    filename = frame.f_code.co_filename
    if not isinstance(filename, str) or not filename.endswith(".py"):
        return False
    normalized = filename.replace("\\", "/")
    if _project_dir_prefix and not normalized.startswith(_project_dir_prefix):
        return False
    name = normalized.rsplit("/", 1)[-1]
    if name in {"tertius_bom.py", "tertius_provenance.py"}:
        return False
    return True


def _shape_targets(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        targets: list[Any] = []
        for item in value:
            targets.extend(_shape_targets(item))
        return targets
    part = getattr(value, "part", None)
    if isinstance(part, bd.Shape):
        return [part]
    if isinstance(value, bd.Shape):
        return [value]
    return []


def _source_ids(value: Any) -> list[str]:
    ids = getattr(value, "tertius_source_call_ids", None)
    if not isinstance(ids, list):
        return []
    return [str(item) for item in ids if item]


def _set_source_ids(value: Any, ids: list[str]) -> None:
    if not isinstance(value, bd.Shape):
        return
    unique: list[str] = []
    for item in ids:
        if item and item not in unique:
            unique.append(item)
    try:
        setattr(value, "tertius_source_call_ids", unique)
    except Exception:
        pass


def _append_source_id(value: Any, call_id: str) -> None:
    for target in _shape_targets(value):
        ids = _source_ids(target)
        if call_id not in ids:
            ids.append(call_id)
        _set_source_ids(target, ids)


def _append_source_ids_from(source: Any, target: Any) -> None:
    source_ids: list[str] = []
    for source_target in _shape_targets(source):
        for call_id in _source_ids(source_target):
            if call_id not in source_ids:
                source_ids.append(call_id)
    if not source_ids:
        return
    for target_shape in _shape_targets(target):
        ids = _source_ids(target_shape)
        for call_id in source_ids:
            if call_id not in ids:
                ids.append(call_id)
        _set_source_ids(target_shape, ids)


def _copy_provenance(source: Any, result: Any) -> Any:
    if not isinstance(source, bd.Shape) or not isinstance(result, bd.Shape):
        return result
    _set_source_ids(result, _source_ids(source))
    source_children = [child for child in (getattr(source, "children", ()) or ()) if isinstance(child, bd.Shape)]
    result_children = [child for child in (getattr(result, "children", ()) or ()) if isinstance(child, bd.Shape)]
    for source_child, result_child in zip(source_children, result_children):
        _copy_provenance(source_child, result_child)
    return result


def _patch_shape_method(name: str) -> None:
    for cls in _shape_classes():
        original = getattr(cls, name, None)
        if original is None or getattr(original, "_tertius_provenance_patch", False):
            continue

        def patched(self, *args, __original=original, **kwargs):
            result = __original(self, *args, **kwargs)
            try:
                _copy_provenance(self, result)
            except Exception:
                pass
            return result

        setattr(patched, "_tertius_provenance_patch", True)
        setattr(cls, name, patched)
        _patched_methods.append((cls, name, original))


def _patch_shape_binary_operator(name: str) -> None:
    for cls in _shape_classes():
        original = getattr(cls, name, None)
        if original is None or getattr(original, "_tertius_provenance_patch", False):
            continue

        def patched(self, other, __original=original):
            result = __original(self, other)
            try:
                _copy_provenance(self, result)
                _append_source_ids_from(other, result)
            except Exception:
                pass
            return result

        setattr(patched, "_tertius_provenance_patch", True)
        setattr(cls, name, patched)
        _patched_methods.append((cls, name, original))


def _patch_add() -> None:
    original = getattr(bd, "add", None)
    if original is None or getattr(original, "_tertius_provenance_patch", False):
        return

    def patched(*objects, **kwargs):
        result = original(*objects, **kwargs)
        try:
            context = bd.BuildPart._get_context() if hasattr(bd.BuildPart, "_get_context") else None
            context_part = getattr(context, "part", None) if context is not None else None
            if context_part is not None:
                for item in objects:
                    _append_source_ids_from(item, context_part)
        except Exception:
            pass
        return result

    setattr(patched, "_tertius_provenance_patch", True)
    setattr(bd, "add", patched)
    _patched_functions.append((bd, "add", original))


def _argument_values(frame) -> dict[str, Any]:
    code = frame.f_code
    locals_ = frame.f_locals
    arg_count = code.co_argcount + getattr(code, "co_posonlyargcount", 0)
    kwonly_count = getattr(code, "co_kwonlyargcount", 0)
    names = list(code.co_varnames[:arg_count])
    names.extend(code.co_varnames[arg_count:arg_count + kwonly_count])
    values = {name: locals_.get(name) for name in names if name not in {"self", "cls"}}
    try:
        arg_info = inspect.getargvalues(frame)
        if arg_info.varargs and arg_info.varargs in locals_:
            values[arg_info.varargs] = locals_[arg_info.varargs]
        if arg_info.keywords and arg_info.keywords in locals_:
            for key, value in (locals_[arg_info.keywords] or {}).items():
                values[str(key)] = value
    except Exception:
        pass
    return values


def _standard_key(key: str) -> str:
    normalized = key.strip().lower()
    return ALIASES.get(normalized, normalized)


def _trace(value: Any, source_file: str | None, source_line: int | None) -> dict[str, Any]:
    return {
        "raw": _jsonish(value),
        "resolved": _jsonish(value),
        "resolution": "runtime_argument",
        "source_file": source_file,
        "source_line": source_line,
    }


def _standard_inputs(arguments: dict[str, Any], source_file: str | None, source_line: int | None) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for key, value in arguments.items():
        standard_key = _standard_key(key)
        if standard_key not in STANDARD_FIELDS:
            continue
        inputs[standard_key] = _trace(value, source_file, source_line)
    return inputs


def _merge_standard_input(
    inputs: dict[str, Any],
    key: str,
    value: Any,
    source_file: str | None,
    source_line: int | None,
    *,
    resolution: str,
) -> None:
    standard_key = _standard_key(key)
    if standard_key not in STANDARD_FIELDS:
        return
    existing = inputs.get(standard_key)
    if isinstance(existing, dict) and existing.get("resolved") is not None:
        return
    trace = _trace(value, source_file, source_line)
    trace["resolution"] = resolution
    inputs[standard_key] = trace


def _merge_return_locals(call_id: str, frame) -> None:
    record = _source_calls.get(call_id)
    if not isinstance(record, dict):
        return
    inputs = record.setdefault("standard_inputs", {})
    if not isinstance(inputs, dict):
        return
    source_file = record.get("source_file")
    source_line = record.get("source_line")
    locals_ = dict(frame.f_locals)

    for key, value in locals_.items():
        if key.startswith("_"):
            continue
        _merge_standard_input(
            inputs,
            key,
            value,
            source_file,
            source_line,
            resolution="runtime_return_local",
        )

    def is_metadata_carrier(value: Any) -> bool:
        module = str(getattr(type(value), "__module__", ""))
        if module.startswith(("build123d", "OCP", "ocp_vscode")):
            return False
        return any(hasattr(value, key) for key in ("part_number", "product_key", "manufacturer", "standard"))

    attribute_values: dict[str, list[Any]] = {}
    for value in locals_.values():
        if value is None or isinstance(value, (str, int, float, bool, dict, list, tuple, set)):
            continue
        if not is_metadata_carrier(value):
            continue
        for key in STANDARD_FIELDS:
            if not hasattr(value, key):
                continue
            try:
                resolved = getattr(value, key)
            except Exception:
                continue
            attribute_values.setdefault(key, []).append(resolved)

    for key, values in attribute_values.items():
        comparable_values = {_jsonish(value) for value in values}
        if len(comparable_values) != 1:
            continue
        _merge_standard_input(
            inputs,
            key,
            values[0],
            source_file,
            source_line,
            resolution="runtime_return_local_attribute",
        )


def _profile(frame, event: str, arg):
    global _inside_tracer, _next_call_index
    if _inside_tracer:
        return _profile
    if event not in {"call", "return", "exception"}:
        return _profile
    if not _is_project_frame(frame):
        return _profile

    _inside_tracer = True
    try:
        frame_id = id(frame)
        if event == "call":
            if frame.f_code.co_name.startswith("<"):
                return _profile
            call_id = f"call_{_next_call_index}"
            _next_call_index += 1
            caller = frame.f_back
            caller_file = _relative_file(caller.f_code.co_filename) if caller is not None else _relative_file(frame.f_code.co_filename)
            caller_line = caller.f_lineno if caller is not None else frame.f_lineno
            definition_file = _relative_file(frame.f_code.co_filename)
            arguments = _argument_values(frame)
            record = {
                "id": call_id,
                "function": frame.f_code.co_name,
                "qualified_function": f"{Path(frame.f_code.co_filename).stem}.{frame.f_code.co_name}",
                "source_file": caller_file,
                "source_line": caller_line,
                "definition_file": definition_file,
                "definition_line": frame.f_code.co_firstlineno,
                "source_scope": "::".join(
                    _source_calls[stack_id]["function"]
                    for stack_id in _call_stack
                    if stack_id in _source_calls
                ) or "<module>",
                "parameters": {
                    key: _trace(value, caller_file, caller_line)
                    for key, value in arguments.items()
                },
                "standard_inputs": _standard_inputs(arguments, caller_file, caller_line),
                "bom_kind": "runtime_visual_source",
                "bom_readiness": "runtime",
                "bom_missing_fields": [],
            }
            _source_calls[call_id] = record
            _active_calls[frame_id] = call_id
            _call_stack.append(call_id)
        elif event in {"return", "exception"}:
            call_id = _active_calls.pop(frame_id, None)
            if call_id is not None:
                if event == "return":
                    _merge_return_locals(call_id, frame)
                    targets = _shape_targets(arg)
                    if targets:
                        for target in targets:
                            _append_source_id(target, call_id)
                        _source_calls[call_id]["returned_visual"] = True
                for index in range(len(_call_stack) - 1, -1, -1):
                    if _call_stack[index] == call_id:
                        del _call_stack[index]
                        break
    finally:
        _inside_tracer = False
    return _profile


def install(project_dir: str | Path) -> None:
    global _project_dir, _project_dir_prefix, _previous_profile
    _project_dir = Path(project_dir).resolve()
    _project_dir_prefix = str(_project_dir).replace("\\", "/").rstrip("/") + "/"
    for name in ("moved", "located"):
        _patch_shape_method(name)
    for name in ("__add__", "__sub__", "__and__"):
        _patch_shape_binary_operator(name)
    _patch_add()
    _previous_profile = sys.getprofile()
    sys.setprofile(_profile)


def uninstall() -> None:
    sys.setprofile(_previous_profile)


def source_call_ids(value: Any) -> list[str]:
    return _source_ids(value)


def source_map() -> dict[str, Any]:
    return {
        "version": 1,
        "source_calls": {
            call_id: dict(record)
            for call_id, record in _source_calls.items()
            if record.get("returned_visual")
        },
    }
'''
