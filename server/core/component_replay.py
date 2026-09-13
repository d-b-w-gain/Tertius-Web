from __future__ import annotations

import ast
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COMPONENT_CONTRACT_SCHEMA_VERSION = 1
COMPONENT_ARTIFACT_SCHEMA_VERSION = 1

QUALITY_DEFLECTIONS = {
    "sketch": 200.0,
    "rough": 100.0,
    "low": 50.0,
    "medium": 30.0,
    "normal": 10.0,
    "high": 1.0,
}

SAFE_CONFIG_CALLS = {
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "float",
    "int",
    "len",
    "list",
    "max",
    "min",
    "Path",
    "range",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
}
SAFE_CONFIG_MODULES = {"math"}


@dataclass(frozen=True)
class SkippedTopLevelStatement:
    line: int | None
    kind: str
    diagnostic_code: str
    file: str
    assigned_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayCall:
    call_id: str
    function: str
    qualified_function: str
    definition_file: str
    definition_line: int
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ComponentReplayArtifact:
    call_id: str
    component_id: str
    function: str
    relative_artifact_path: str
    cache_key: str
    digest_sha256: str
    byte_size: int
    replay_seconds: float
    export_seconds: float


@dataclass(frozen=True)
class ComponentReplayResult:
    success: bool
    artifacts: tuple[ComponentReplayArtifact, ...] = ()
    skipped_top_level: tuple[SkippedTopLevelStatement, ...] = ()
    definition_load_seconds: float | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    diagnostic_code: str | None = None


def _canonical_contract_value(value: Any, path: str = "value") -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{path} is not deterministic JSON")
        return value
    if isinstance(value, list | tuple):
        return [_canonical_contract_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        canonical: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            canonical[key] = _canonical_contract_value(item, f"{path}.{key}")
        return canonical
    raise ValueError(f"{path} is not deterministic JSON")


def _contract_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def component_geometry_cache_key(
    *,
    component_id: str,
    replay_identity: str,
    inputs: dict[str, Any],
    explicit_dependencies: dict[str, Any] | None = None,
    inferred_dependencies: dict[str, Any] | None = None,
    function_source_sha256: str = "",
    helper_source_sha256: list[str] | None = None,
    source_map_version: int = 1,
    runtime_versions: dict[str, Any] | None = None,
    export_settings: dict[str, Any] | None = None,
    material: dict[str, Any] | None = None,
    artifact_schema_version: int = COMPONENT_ARTIFACT_SCHEMA_VERSION,
    instance_id: str | None = None,
    placement: dict[str, Any] | None = None,
) -> str:
    del instance_id, placement
    return _contract_digest(
        {
            "contract_schema_version": COMPONENT_CONTRACT_SCHEMA_VERSION,
            "artifact_schema_version": artifact_schema_version,
            "component_id": component_id,
            "replay_identity": replay_identity,
            "inputs": _canonical_contract_value(inputs, "inputs"),
            "explicit_dependencies": _canonical_contract_value(explicit_dependencies or {}, "explicit_dependencies"),
            "inferred_dependencies": _canonical_contract_value(inferred_dependencies or {}, "inferred_dependencies"),
            "function_source_sha256": function_source_sha256,
            "helper_source_sha256": sorted(helper_source_sha256 or []),
            "source_map_version": source_map_version,
            "runtime_versions": _canonical_contract_value(runtime_versions or {}, "runtime_versions"),
            "export_settings": _canonical_contract_value(export_settings or {}, "export_settings"),
            "material": _canonical_contract_value(material or {}, "material"),
        }
    )


def extract_resolved_parameters(record: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    parameters = record.get("parameters") or {}
    for name, trace in parameters.items():
        if isinstance(trace, dict) and "resolved" in trace:
            resolved[str(name)] = trace["resolved"]
    return resolved


def unresolved_parameter_names(record: dict[str, Any]) -> list[str]:
    parameters = record.get("parameters") or {}
    return [str(name) for name, trace in parameters.items() if isinstance(trace, dict) and "resolved" not in trace]


def source_calls_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    calls = payload.get("source_calls")
    if isinstance(calls, dict):
        return calls
    source_map = payload.get("source_map")
    if isinstance(source_map, dict) and isinstance(source_map.get("source_calls"), dict):
        return source_map["source_calls"]
    raise ValueError("No source_calls map found")


def visual_function_names_from_payload(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for record in source_calls_from_payload(payload).values():
        if not isinstance(record, dict) or not record.get("returned_visual"):
            continue
        if function := record.get("function"):
            names.add(str(function))
        if qualified := record.get("qualified_function"):
            names.add(str(qualified))
    return names


def load_replay_calls(source_map: dict[str, Any], *, function: str | None = None, call_id: str | None = None) -> list[ReplayCall]:
    selected: list[ReplayCall] = []
    for key, record in source_calls_from_payload(source_map).items():
        if not isinstance(record, dict) or not record.get("returned_visual"):
            continue
        if call_id and key != call_id:
            continue
        qualified_function = str(record.get("qualified_function") or record.get("function") or "")
        if function and record.get("function") != function and qualified_function != function:
            continue
        unresolved = unresolved_parameter_names(record)
        if unresolved:
            raise ValueError(f"Call {key} cannot be replayed because parameters are unresolved: {', '.join(unresolved)}")
        selected.append(
            ReplayCall(
                call_id=str(key),
                function=str(record["function"]),
                qualified_function=qualified_function,
                definition_file=str(record.get("definition_file") or ""),
                definition_line=int(record.get("definition_line") or 0),
                parameters=extract_resolved_parameters(record),
            )
        )
    if not selected:
        raise ValueError("No replayable visual-producing calls matched the selection")
    return selected


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return None


def _statement_calls_named_function(node: ast.AST, names: set[str]) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child.func)
        if name and (name in names or name.rsplit(".", 1)[-1] in names):
            return True
    return False


def _expr_contains_call(node: ast.AST) -> bool:
    return any(isinstance(child, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)) for child in ast.walk(node))


def _safe_config_call_name(name: str | None) -> bool:
    if name is None:
        return False
    if name in SAFE_CONFIG_CALLS:
        return True
    owner, _, attr = name.partition(".")
    return bool(owner in SAFE_CONFIG_MODULES and attr and not attr.startswith("_"))


def _expr_has_only_safe_config_calls(node: ast.AST, safe_constructor_names: set[str] | None = None) -> bool:
    safe_constructor_names = safe_constructor_names or set()
    for child in ast.walk(node):
        if isinstance(child, (ast.Await, ast.Yield, ast.YieldFrom)):
            return False
        if isinstance(child, ast.Call):
            call_name = _call_name(child.func)
            if call_name in safe_constructor_names:
                continue
            if not _safe_config_call_name(call_name):
                return False
    return True


def _assigned_names(node: ast.stmt) -> tuple[str, ...]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]

    names: list[str] = []
    for target in targets:
        for child in ast.walk(target):
            if isinstance(child, ast.Name) and child.id not in names:
                names.append(child.id)
    return tuple(sorted(names))


def _skip_code_for_statement(node: ast.stmt, visual_function_names: set[str]) -> str:
    if _statement_calls_named_function(node, {"show_object"}):
        return "component_replay_skip_viewer_side_effect"
    if visual_function_names and _statement_calls_named_function(node, visual_function_names):
        return "component_replay_skip_visual_constructor"
    if isinstance(node, ast.AugAssign):
        return "component_replay_skip_top_level_mutation"
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        if value is not None and _expr_contains_call(value):
            return "component_replay_skip_unsafe_top_level_call"
        return "component_replay_skip_top_level_call"
    return "component_replay_skip_unsupported_top_level"


def is_definition_safe_statement(
    node: ast.stmt,
    visual_function_names: set[str] | None = None,
    safe_constructor_names: set[str] | None = None,
) -> bool:
    visual_function_names = visual_function_names or set()
    safe_constructor_names = safe_constructor_names or set()
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return True
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    if _statement_calls_named_function(node, {"show_object"}):
        return False
    if isinstance(node, ast.Assign):
        if not _expr_contains_call(node.value):
            return True
        return not _statement_calls_named_function(node, visual_function_names) and _expr_has_only_safe_config_calls(node.value, safe_constructor_names)
    if isinstance(node, ast.AnnAssign):
        if node.value is None or not _expr_contains_call(node.value):
            return True
        return not _statement_calls_named_function(node, visual_function_names) and _expr_has_only_safe_config_calls(node.value, safe_constructor_names)
    return False


def _local_config_constructor_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for decorator in node.decorator_list:
            name = _call_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
            if name in {"dataclass", "dataclasses.dataclass"}:
                names.add(node.name)
                break
    return names


def definition_only_source(
    source: str,
    filename: str,
    *,
    relative_file: str,
    visual_function_names: set[str] | None = None,
) -> tuple[str, tuple[SkippedTopLevelStatement, ...]]:
    visual_names = visual_function_names or set()
    tree = ast.parse(source, filename=filename)
    safe_constructor_names = _local_config_constructor_names(tree)
    kept: list[ast.stmt] = []
    skipped: list[SkippedTopLevelStatement] = []
    for node in tree.body:
        if is_definition_safe_statement(node, visual_names, safe_constructor_names):
            kept.append(node)
            continue
        skipped.append(
            SkippedTopLevelStatement(
                line=getattr(node, "lineno", None),
                kind=type(node).__name__,
                diagnostic_code=_skip_code_for_statement(node, visual_names),
                file=relative_file,
                assigned_names=_assigned_names(node),
            )
        )
    tree.body = kept
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), tuple(skipped)


def prepare_definition_only_project(
    project_dir: Path,
    stage_dir: Path,
    *,
    visual_function_names: set[str],
) -> tuple[dict[str, str], tuple[SkippedTopLevelStatement, ...]]:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    definition_sources: dict[str, str] = {}
    skipped: list[SkippedTopLevelStatement] = []
    for source_path in sorted(project_dir.glob("*.py")):
        relative_file = source_path.name
        definition_source_text, file_skipped = definition_only_source(
            source_path.read_text(encoding="utf-8"),
            str(source_path),
            relative_file=relative_file,
            visual_function_names=visual_function_names,
        )
        (stage_dir / relative_file).write_text(definition_source_text, encoding="utf-8")
        definition_sources[relative_file] = definition_source_text
        skipped.extend(file_skipped)
    return definition_sources, tuple(skipped)


def _function_global_names(source: str, filename: str, function_name: str) -> set[str]:
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            local_names = {arg.arg for arg in node.args.args}
            local_names.update(arg.arg for arg in node.args.kwonlyargs)
            if node.args.vararg is not None:
                local_names.add(node.args.vararg.arg)
            if node.args.kwarg is not None:
                local_names.add(node.args.kwarg.arg)
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                    local_names.add(child.id)
            return {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) and child.id not in local_names
            }
    return set()


def _source_file_key(filename: str | None, fallback: str) -> str:
    if not filename:
        return Path(fallback).name
    return Path(filename).name


def validate_selected_replay_dependencies(
    *,
    definition_source: str,
    definition_filename: str,
    calls: list[ReplayCall],
    skipped: tuple[SkippedTopLevelStatement, ...],
    definition_sources: dict[str, str] | None = None,
) -> str | None:
    skipped_names_by_file: dict[str, set[str]] = {}
    for item in skipped:
        skipped_names_by_file.setdefault(item.file, set()).update(item.assigned_names)
    if not any(skipped_names_by_file.values()):
        return None
    for call in calls:
        file_key = _source_file_key(call.definition_file, definition_filename)
        source = (definition_sources or {}).get(file_key, definition_source)
        skipped_names = skipped_names_by_file.get(file_key, set())
        if _function_global_names(source, file_key, call.function) & skipped_names:
            return "component_replay_ineligible_skipped_dependency"
    return None


def inferred_component_id(call: ReplayCall) -> str:
    return f"inferred:{call.qualified_function}:{call.definition_file}:{call.definition_line}"


def _sandbox_env(project_dir: Path) -> dict[str, str]:
    sandbox_home = str(project_dir)
    env = {
        "HOME": sandbox_home,
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": "",
        "PYTHONIOENCODING": "utf-8",
        "TMP": sandbox_home,
        "TEMP": sandbox_home,
    }
    if sys.platform == "win32":
        env["USERPROFILE"] = sandbox_home
        for name in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT"):
            if value := os.environ.get(name):
                env[name] = value
    return env


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    os.killpg(process.pid, signal.SIGKILL)


COMPONENT_REPLAY_SANDBOX = r"""
import gzip
import hashlib
import inspect
import json
import sys
import traceback
import types
from pathlib import Path
from time import perf_counter

import build123d as bd

project_dir = Path.cwd()
definition_path = Path(sys.argv[1])
calls = json.loads(sys.argv[2])
export_format = sys.argv[3].lower()
quality = sys.argv[4].lower()
deflection = float(sys.argv[5])
artifact_dir = Path(sys.argv[6])

try:
    sys.path.insert(0, str(project_dir))
    env = {"bd": bd, "build123d": bd, "__name__": "__tertius_definition_only__", "__file__": str(definition_path)}
    source = definition_path.read_text(encoding="utf-8")
    load_started = perf_counter()
    exec(compile(source, str(definition_path), "exec"), env)
    definition_load_seconds = perf_counter() - load_started

    def jsonish(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): jsonish(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, (list, tuple)):
            return [jsonish(item) for item in value]
        return None

    def source_hash(value):
        try:
            source_text = inspect.getsource(value)
        except Exception:
            return None
        return hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    def inferred_dependencies(func):
        dependencies = {}
        code = getattr(func, "__code__", None)
        globals_ = getattr(func, "__globals__", {})
        for name in sorted(code.co_names if code is not None else []):
            if name not in globals_ or name.startswith("__"):
                continue
            value = globals_[name]
            encoded = jsonish(value)
            if encoded is not None:
                dependencies[name] = {"kind": "value", "value": encoded}
                continue
            if isinstance(value, (types.FunctionType, type)):
                digest = source_hash(value)
                if digest:
                    dependencies[name] = {"kind": "source", "sha256": digest}
        return dependencies

    def shape_from_result(value, function_name):
        shape = value.part if hasattr(value, "part") and isinstance(value.part, bd.Shape) else value
        if not isinstance(shape, bd.Shape):
            raise RuntimeError(f"Function {function_name} did not return a Build123D shape")
        return shape

    results = []
    for call in calls:
        func = env.get(call["function"])
        if not callable(func) and "." in call.get("qualified_function", ""):
            module_name, attr_name = call["qualified_function"].rsplit(".", 1)
            module = env.get(module_name)
            func = getattr(module, attr_name, None)
        if not callable(func):
            raise RuntimeError(f"Function {call['qualified_function']} is not available after definition-only load")

        function_source_sha256 = source_hash(func) or ""
        dependencies = inferred_dependencies(func)
        payload = {
            "contract_schema_version": 1,
            "artifact_schema_version": 1,
            "component_id": call["component_id"],
            "replay_identity": call["qualified_function"],
            "inputs": call["parameters"],
            "explicit_dependencies": {},
            "inferred_dependencies": dependencies,
            "function_source_sha256": function_source_sha256,
            "helper_source_sha256": [],
            "source_map_version": 1,
            "runtime_versions": {
                "python": sys.version.split()[0],
                "build123d": getattr(bd, "__version__", ""),
            },
            "export_settings": {"format": export_format, "quality": quality},
            "material": {},
        }
        cache_key = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        replay_started = perf_counter()
        shape = shape_from_result(func(**call["parameters"]), call["function"])
        replay_seconds = perf_counter() - replay_started
        artifact_path = artifact_dir / f"{call['call_id']}-{call['function']}.{export_format}"
        export_started = perf_counter()
        if export_format in {"glb", "gltf"}:
            bd.export_gltf(shape, str(artifact_path), binary=(export_format == "glb"), linear_deflection=deflection, angular_deflection=0.1)
        elif export_format == "stl":
            bd.export_stl(shape, str(artifact_path))
        elif export_format == "step":
            bd.export_step(shape, str(artifact_path))
        else:
            raise RuntimeError(f"Unsupported replay export format: {export_format}")
        export_seconds = perf_counter() - export_started
        data = artifact_path.read_bytes()
        results.append({
            "call_id": call["call_id"],
            "component_id": call["component_id"],
            "function": call["function"],
            "relative_artifact_path": artifact_path.name,
            "cache_key": cache_key,
            "digest_sha256": hashlib.sha256(data).hexdigest(),
            "byte_size": len(data),
            "gzip_bytes": len(gzip.compress(data)),
            "replay_seconds": replay_seconds,
            "export_seconds": export_seconds,
        })

    print(json.dumps({"ok": True, "definition_load_seconds": definition_load_seconds, "artifacts": results}))
except Exception:
    print(json.dumps({"ok": False, "error": traceback.format_exc()}))
    sys.exit(2)
"""


def replay_selected_components(
    project_dir: Path,
    source_map: dict[str, Any],
    *,
    output_dir: Path,
    entrypoint: str = "design.py",
    export_format: str = "glb",
    quality: str = "rough",
    function: str | None = None,
    call_id: str | None = None,
    timeout_seconds: int = 300,
) -> ComponentReplayResult:
    project_dir = project_dir.resolve()
    output_dir = output_dir.resolve()

    if export_format not in {"glb", "gltf", "stl", "step"}:
        return ComponentReplayResult(success=False, diagnostic_code="component_replay_unsupported_export_format", error=f"Unsupported replay export format: {export_format}")
    if quality not in QUALITY_DEFLECTIONS:
        return ComponentReplayResult(success=False, diagnostic_code="component_replay_unsupported_quality", error=f"Unsupported replay quality: {quality}")

    try:
        calls = load_replay_calls(source_map, function=function, call_id=call_id)
    except ValueError as exc:
        return ComponentReplayResult(success=False, diagnostic_code="component_replay_ineligible_source_map", error=str(exc))

    entry_path = project_dir / entrypoint
    if not entry_path.exists():
        return ComponentReplayResult(success=False, diagnostic_code="component_replay_missing_entrypoint", error=f"{entrypoint} was not found")

    visual_names = visual_function_names_from_payload(source_map)
    output_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = output_dir / "definition-project"
    definition_sources, skipped = prepare_definition_only_project(
        project_dir,
        stage_dir,
        visual_function_names=visual_names,
    )
    definition_source_text = definition_sources.get(entrypoint, "")
    dependency_error = validate_selected_replay_dependencies(
        definition_source=definition_source_text,
        definition_filename=str(entry_path),
        calls=calls,
        skipped=skipped,
        definition_sources=definition_sources,
    )
    if dependency_error is not None:
        return ComponentReplayResult(success=False, skipped_top_level=skipped, diagnostic_code=dependency_error, error="Selected replay function depends on skipped top-level setup")

    artifact_dir = output_dir / "component-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    definition_path = stage_dir / entrypoint
    prepared_calls = [
        {
            "call_id": call.call_id,
            "component_id": inferred_component_id(call),
            "function": call.function,
            "qualified_function": call.qualified_function,
            "parameters": call.parameters,
        }
        for call in calls
    ]
    args = [
        sys.executable,
        "-c",
        COMPONENT_REPLAY_SANDBOX,
        str(definition_path),
        json.dumps(prepared_calls, sort_keys=True),
        export_format,
        quality,
        str(QUALITY_DEFLECTIONS[quality]),
        str(artifact_dir),
    ]
    if sys.platform == "win32":
        process = subprocess.Popen(args, cwd=stage_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_sandbox_env(stage_dir), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        process = subprocess.Popen(args, cwd=stage_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_sandbox_env(stage_dir), start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        stdout, stderr = process.communicate()
        return ComponentReplayResult(
            success=False,
            skipped_top_level=skipped,
            stdout=str(exc.stdout or stdout or ""),
            stderr=str(exc.stderr or stderr or ""),
            error=f"Component replay timed out after {timeout_seconds} seconds",
            diagnostic_code="component_replay_timeout",
        )

    payload = json.loads(stdout) if stdout.strip() else {"ok": False, "error": stderr}
    if process.returncode != 0 or not payload.get("ok"):
        return ComponentReplayResult(
            success=False,
            skipped_top_level=skipped,
            stdout=stdout,
            stderr=stderr,
            error=str(payload.get("error") or stderr or f"Component replay exited with status {process.returncode}"),
            diagnostic_code="component_replay_failed",
        )

    artifacts = tuple(
        ComponentReplayArtifact(
            call_id=str(item["call_id"]),
            component_id=str(item["component_id"]),
            function=str(item["function"]),
            relative_artifact_path=str(item["relative_artifact_path"]),
            cache_key=str(item["cache_key"]),
            digest_sha256=str(item["digest_sha256"]),
            byte_size=int(item["byte_size"]),
            replay_seconds=float(item["replay_seconds"]),
            export_seconds=float(item["export_seconds"]),
        )
        for item in payload.get("artifacts", [])
    )
    return ComponentReplayResult(
        success=True,
        artifacts=artifacts,
        skipped_top_level=skipped,
        definition_load_seconds=float(payload["definition_load_seconds"]),
        stdout=stdout,
        stderr=stderr,
        diagnostic_code="component_replay_succeeded",
    )
