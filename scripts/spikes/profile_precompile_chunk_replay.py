#!/usr/bin/env python3
"""Prototype pre-compile chunk replay for issue #309.

This script tests the harder question than post-build GLB splitting:
can a worker avoid running the final top-level design build and instead replay
only selected chunk-producing functions from recorded provenance?

It is intentionally conservative. It loads definitions plus provenance-safe
non-visual top-level configuration, skips top-level visual construction, and
falls back when the selected call cannot be replayed from resolved arguments.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any


QUALITY_DEFLECTIONS = {
    "sketch": 200.0,
    "rough": 100.0,
    "low": 50.0,
    "medium": 30.0,
    "normal": 10.0,
    "high": 1.0,
}

COMPONENT_CONTRACT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReplayCall:
    call_id: str
    function: str
    qualified_function: str
    definition_file: str
    definition_line: int
    parameters: dict[str, Any]


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
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


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
    artifact_schema_version: int = 1,
    instance_id: str | None = None,
    placement: dict[str, Any] | None = None,
) -> str:
    """Return the pre-export geometry/material artifact key.

    ``instance_id`` and ``placement`` are accepted to make the exclusion
    explicit: instance identity belongs in the scene manifest, not the geometry
    artifact key.
    """
    del instance_id, placement
    payload = {
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
    return _contract_digest(payload)


def component_replay_eligibility(registration: dict[str, Any], provenance_record: dict[str, Any]) -> dict[str, Any]:
    registered_replay = registration.get("replay")
    qualified_function = provenance_record.get("qualified_function") or provenance_record.get("function")
    if registered_replay and registered_replay not in {provenance_record.get("function"), qualified_function}:
        return {
            "eligible": False,
            "diagnostic_code": "component_replay_registration_provenance_conflict",
            "field": "replay",
        }

    registered_inputs = registration.get("inputs") or {}
    provenance_inputs = extract_resolved_parameters(provenance_record)
    for key, value in registered_inputs.items():
        if key not in provenance_inputs or provenance_inputs[key] != value:
            return {
                "eligible": False,
                "diagnostic_code": "component_replay_registration_provenance_conflict",
                "field": f"inputs.{key}",
            }

    unresolved = unresolved_parameter_names(provenance_record)
    if unresolved:
        return {
            "eligible": False,
            "diagnostic_code": "component_replay_ineligible_unresolved_input",
            "fields": unresolved,
        }

    return {"eligible": True, "diagnostic_code": "component_replay_eligible"}


def expr_contains_call(node: ast.AST) -> bool:
    return any(isinstance(child, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)) for child in ast.walk(node))


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return None


def statement_calls_named_function(node: ast.AST, names: set[str]) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = call_name(child.func)
        if name and (name in names or name.rsplit(".", 1)[-1] in names):
            return True
    return False


def is_definition_safe_statement(node: ast.stmt, visual_function_names: set[str] | None = None) -> bool:
    visual_function_names = visual_function_names or set()
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return True
    if visual_function_names:
        if statement_calls_named_function(node, {"show_object"}):
            return False
        return not statement_calls_named_function(node, visual_function_names)
    if isinstance(node, ast.Assign):
        return not expr_contains_call(node.value)
    if isinstance(node, ast.AnnAssign):
        return node.value is None or not expr_contains_call(node.value)
    if isinstance(node, ast.AugAssign):
        return False
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    return False


def definition_only_source(source: str, filename: str, visual_function_names: set[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    tree = ast.parse(source, filename=filename)
    kept: list[ast.stmt] = []
    skipped: list[dict[str, Any]] = []
    for node in tree.body:
        if is_definition_safe_statement(node, visual_function_names):
            kept.append(node)
        else:
            skipped.append(
                {
                    "line": getattr(node, "lineno", None),
                    "kind": type(node).__name__,
                    "source": ast.get_source_segment(source, node) or "",
                }
            )
    tree.body = kept
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), skipped


def stage_project_files(project_dir: Path, entrypoint: str, stage_dir: Path) -> list[str]:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    copied: list[str] = []
    for source in sorted(project_dir.glob("*.py")):
        shutil.copy2(source, stage_dir / source.name)
        copied.append(source.name)
    if entrypoint not in copied:
        raise FileNotFoundError(f"{entrypoint} was not found in {project_dir}")
    return copied


def extract_resolved_parameters(record: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    parameters = record.get("parameters") or {}
    for name, trace in parameters.items():
        if isinstance(trace, dict) and "resolved" in trace:
            resolved[str(name)] = trace["resolved"]
    return resolved


def unresolved_parameter_names(record: dict[str, Any]) -> list[str]:
    parameters = record.get("parameters") or {}
    return [
        str(name)
        for name, trace in parameters.items()
        if isinstance(trace, dict) and "resolved" not in trace
    ]


def source_calls_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    calls = payload.get("source_calls")
    if isinstance(calls, dict):
        return calls
    nested = payload.get("source_map")
    if isinstance(nested, dict) and isinstance(nested.get("source_calls"), dict):
        return nested["source_calls"]
    raise ValueError("No source_calls map found. Use a provenance source map or manifest with source_map.source_calls.")


def visual_function_names_from_payload(payload: dict[str, Any]) -> set[str]:
    calls = source_calls_from_payload(payload)
    names: set[str] = set()
    for record in calls.values():
        if not isinstance(record, dict) or not record.get("returned_visual"):
            continue
        if function := record.get("function"):
            names.add(str(function))
        qualified = str(record.get("qualified_function") or "")
        if qualified:
            names.add(qualified)
    return names


def load_replay_calls(source_map_path: Path, function: str | None, call_id: str | None) -> list[ReplayCall]:
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    calls = source_calls_from_payload(source_map)

    selected: list[ReplayCall] = []
    for key, record in calls.items():
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
        raise ValueError("No replayable visual-producing calls matched the selection.")
    return selected


def replay_cache_key(call: ReplayCall, source_text: str, quality: str) -> str:
    payload = {
        "function": call.function,
        "qualified_function": call.qualified_function,
        "definition_file": call.definition_file,
        "definition_line": call.definition_line,
        "parameters": call.parameters,
        "quality": quality,
        "definition_source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


REPLAY_SANDBOX = r"""
import hashlib
import inspect
import json
import types
import sys
import traceback
from pathlib import Path
from time import perf_counter

import build123d as bd

stage_dir = Path.cwd()
entrypoint = sys.argv[1]
calls = json.loads(sys.argv[2])
quality = sys.argv[3]
deflection = float(sys.argv[4])
output_dir = Path(sys.argv[5])

try:
    sys.path.insert(0, str(stage_dir))
    source = (stage_dir.parent / "definition-only.py").read_text(encoding="utf-8")
    env = {"bd": bd, "build123d": bd, "__name__": "__tertius_definition_only__", "__file__": str(stage_dir / entrypoint)}
    load_started = perf_counter()
    exec(compile(source, str(stage_dir / entrypoint), "exec"), env)
    definition_load_seconds = perf_counter() - load_started

    def jsonish(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): jsonish(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, (list, tuple, set)):
            return [jsonish(item) for item in value]
        return None

    def source_hash(value):
        try:
            source_text = inspect.getsource(value)
        except Exception:
            return None
        return hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    def cache_key_for(call, func):
        dependencies = {}
        globals_ = getattr(func, "__globals__", {})
        for name in sorted(getattr(func, "__code__", None).co_names if getattr(func, "__code__", None) is not None else []):
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
        payload = {
            "function": call["function"],
            "qualified_function": call["qualified_function"],
            "parameters": call["parameters"],
            "quality": quality,
            "dependencies": dependencies,
            "function_source_sha256": source_hash(func),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    results = []
    for call in calls:
        func = env.get(call["function"])
        if not callable(func) and "." in call.get("qualified_function", ""):
            module_name, attr_name = call["qualified_function"].rsplit(".", 1)
            module = env.get(module_name)
            func = getattr(module, attr_name, None)
        if not callable(func):
            raise RuntimeError(f"Function {call['qualified_function']} is not available after definition-only load.")
        precompile_cache_key = cache_key_for(call, func)
        started = perf_counter()
        result = func(**call["parameters"])
        replay_seconds = perf_counter() - started
        shape = result.part if hasattr(result, "part") and isinstance(result.part, bd.Shape) else result
        if not isinstance(shape, bd.Shape):
            raise RuntimeError(f"Function {call['function']} did not return a Build123D shape.")
        glb_path = output_dir / f"{call['call_id']}-{call['function']}.glb"
        export_started = perf_counter()
        bd.export_gltf(shape, str(glb_path), binary=True, linear_deflection=deflection, angular_deflection=0.1)
        export_seconds = perf_counter() - export_started
        data = glb_path.read_bytes()
        results.append({
            "call_id": call["call_id"],
            "function": call["function"],
            "parameters": call["parameters"],
            "relative_glb_path": glb_path.name,
            "replay_seconds": replay_seconds,
            "export_seconds": export_seconds,
            "raw_glb_bytes": len(data),
            "gzip_bytes": len(__import__("gzip").compress(data)),
            "digest_sha256": __import__("hashlib").sha256(data).hexdigest(),
            "precompile_cache_key": precompile_cache_key,
        })

    print(json.dumps({"ok": True, "definition_load_seconds": definition_load_seconds, "results": results}))
except Exception:
    print(json.dumps({"ok": False, "error": traceback.format_exc()}))
    sys.exit(2)
"""


def sandbox_env(stage_dir: Path) -> dict[str, str]:
    env = {
        "HOME": str(stage_dir),
        "PYTHONPATH": "",
        "PYTHONIOENCODING": "utf-8",
        "TMP": str(stage_dir),
        "TEMP": str(stage_dir),
    }
    env["PATH"] = os.environ.get("PATH", "")
    if sys.platform == "win32":
        env["USERPROFILE"] = str(stage_dir)
        for name in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT"):
            if value := os.environ.get(name):
                env[name] = value
    return env


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    stage_dir = output_dir / "stage"
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    staged = stage_project_files(args.project_dir.resolve(), args.entrypoint, stage_dir)
    source_map = json.loads(args.source_map.read_text(encoding="utf-8"))
    visual_function_names = visual_function_names_from_payload(source_map)
    calls = load_replay_calls(args.source_map, args.function, args.call_id)

    entry_source = (stage_dir / args.entrypoint).read_text(encoding="utf-8")
    definition_source, skipped = definition_only_source(entry_source, str(stage_dir / args.entrypoint), visual_function_names)
    (output_dir / "definition-only.py").write_text(definition_source, encoding="utf-8")

    prepared_calls = [
        {
            "call_id": call.call_id,
            "function": call.function,
            "qualified_function": call.qualified_function,
            "parameters": call.parameters,
            "precompile_cache_key": replay_cache_key(call, definition_source, args.quality),
        }
        for call in calls
    ]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            REPLAY_SANDBOX,
            args.entrypoint,
            json.dumps(prepared_calls),
            args.quality,
            str(QUALITY_DEFLECTIONS[args.quality]),
            str(chunks_dir),
        ],
        cwd=stage_dir,
        env=sandbox_env(stage_dir),
        text=True,
        capture_output=True,
        timeout=args.timeout_seconds,
        check=False,
    )
    payload = json.loads(process.stdout) if process.stdout.strip() else {"ok": False, "error": process.stderr}
    manifest = {
        "schema_version": 1,
        "mode": "precompile-config-only-chunk-replay",
        "ok": bool(payload.get("ok")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_dir": str(args.project_dir.resolve()),
        "entrypoint": args.entrypoint,
        "quality": args.quality,
        "staged_files": staged,
        "selected_calls": prepared_calls,
        "skipped_top_level_from_static_pass": skipped,
        "definition_load_seconds": payload.get("definition_load_seconds"),
        "chunks": payload.get("results", []),
        "error": payload.get("error"),
        "stderr": process.stderr,
    }
    for chunk in manifest["chunks"]:
        prepared = next((call for call in prepared_calls if call["call_id"] == chunk["call_id"]), None)
        if prepared and chunk.get("precompile_cache_key"):
            prepared["precompile_cache_key"] = chunk["precompile_cache_key"]
        elif prepared:
            chunk["precompile_cache_key"] = prepared["precompile_cache_key"]
    manifest["summary"] = {
        "selected_call_count": len(prepared_calls),
        "replayed_chunk_count": len(manifest["chunks"]),
        "total_replay_seconds": sum(float(chunk.get("replay_seconds", 0.0)) for chunk in manifest["chunks"]),
        "total_export_seconds": sum(float(chunk.get("export_seconds", 0.0)) for chunk in manifest["chunks"]),
        "total_raw_glb_bytes": sum(int(chunk.get("raw_glb_bytes", 0)) for chunk in manifest["chunks"]),
        "top_level_statements_skipped": len(payload.get("skipped_top_level", skipped)),
    }
    (output_dir / "precompile-replay-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], indent=2))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay selected visual-producing calls without running top-level design construction.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--entrypoint", default="design.py")
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--quality", choices=sorted(QUALITY_DEFLECTIONS), default="rough")
    parser.add_argument("--function")
    parser.add_argument("--call-id")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def main() -> int:
    run_replay(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
