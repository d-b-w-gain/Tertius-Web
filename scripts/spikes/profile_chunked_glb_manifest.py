#!/usr/bin/env python3
"""Prototype chunked Build123D GLB export with a manifest.

This diagnostic script is intentionally local-only. It demonstrates the shape
of a future chunked compile pipeline: discover logical chunks, export each chunk
to its own GLB, write a manifest, and compare against a previous manifest to
simulate cache hits and changed chunks.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.provenance_runtime import TERTIUS_PROVENANCE_HELPER_SOURCE  # noqa: E402


QUALITY_DEFLECTIONS = {
    "sketch": 200.0,
    "rough": 100.0,
    "low": 50.0,
    "medium": 30.0,
    "normal": 10.0,
    "high": 1.0,
}

CHUNK_SANDBOX_SCRIPT = r"""
import json
import sys
import traceback
from pathlib import Path
from time import perf_counter

import build123d as bd
from OCP.TopoDS import TopoDS_Shape

if not hasattr(TopoDS_Shape, "HashCode"):
    def _topods_shape_hash_code(self, upper_bound):
        return hash(self) % upper_bound
    TopoDS_Shape.HashCode = _topods_shape_hash_code

project_dir = Path.cwd()
entrypoint = sys.argv[1]
quality = sys.argv[2].lower()
output_dir = Path(sys.argv[3])
selection = sys.argv[4]

project_dir_str = str(project_dir.resolve())
if project_dir_str not in sys.path:
    sys.path.insert(0, project_dir_str)

from tertius_provenance import install as install_tertius_provenance
from tertius_provenance import source_call_ids as tertius_source_call_ids
from tertius_provenance import source_map as tertius_source_map
from tertius_provenance import uninstall as uninstall_tertius_provenance

def slugify(value):
    value = str(value or "chunk").strip().lower()
    value = "".join(ch if ch.isalnum() else "-" for ch in value)
    value = "-".join(part for part in value.split("-") if part)
    return value[:96] or "chunk"

def label_for(value, fallback):
    label = str(getattr(value, "label", "") or "").strip()
    return label or fallback

def exportable_shapes(shape):
    nested = []
    for child in getattr(shape, "children", ()) or ():
        if isinstance(child, bd.Shape):
            nested.extend(exportable_shapes(child))
    if nested:
        return nested
    if getattr(shape, "wrapped", None) is not None:
        return [shape]
    return nested

def discover_roots(env):
    roots = []
    for name, value in env.items():
        if name.startswith("_"):
            continue
        if isinstance(value, bd.Shape):
            roots.append((name, value))
        elif hasattr(value, "part") and isinstance(value.part, bd.Shape):
            roots.append((name, value.part))
    if "building" in env and isinstance(env["building"], bd.Shape):
        return [("building", env["building"])]
    return roots

def select_chunks(root_name, root_shape):
    children = [child for child in (getattr(root_shape, "children", ()) or ()) if isinstance(child, bd.Shape)]
    if selection == "root-children" and children:
        return [(f"{root_name}.{index:03d}", child) for index, child in enumerate(children, start=1)]
    if selection == "root":
        return [(root_name, root_shape)]
    if children:
        return [(f"{root_name}.{index:03d}", child) for index, child in enumerate(children, start=1)]
    return [(root_name, root_shape)]

try:
    timings = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    env = {"bd": bd, "build123d": bd}
    design_file = project_dir / entrypoint
    if not design_file.exists():
        raise RuntimeError(f"{entrypoint} not found in staged project.")

    start = perf_counter()
    install_tertius_provenance(project_dir)
    try:
        code = compile(design_file.read_text(encoding="utf-8"), str(design_file), "exec")
        exec(code, env)
    finally:
        uninstall_tertius_provenance()
    timings["design_execution_shape_construction_seconds"] = perf_counter() - start

    roots = discover_roots(env)
    if not roots:
        raise RuntimeError("No Build123D root shapes were generated.")
    root_name, root_shape = roots[0]
    raw_chunks = select_chunks(root_name, root_shape)
    if not raw_chunks:
        raise RuntimeError("No logical chunks were discovered.")

    chunks = []
    used_ids = set()
    for fallback_id, shape in raw_chunks:
        label = label_for(shape, fallback_id)
        base_id = slugify(label)
        chunk_id = base_id
        suffix = 2
        while chunk_id in used_ids:
            chunk_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(chunk_id)
        chunks.append((chunk_id, label, fallback_id, shape))

    deflection = float({
        "sketch": 200.0,
        "rough": 100.0,
        "low": 50.0,
        "medium": 30.0,
        "normal": 10.0,
        "high": 1.0,
    }.get(quality, 100.0))

    exported = []
    for index, (chunk_id, label, fallback_id, shape) in enumerate(chunks, start=1):
        chunk_path = output_dir / f"{index:03d}-{chunk_id}.glb"
        export_shapes = exportable_shapes(shape)
        export_shape = bd.Compound(export_shapes, children=export_shapes) if len(export_shapes) > 1 else export_shapes[0]
        start = perf_counter()
        bd.export_gltf(
            export_shape,
            str(chunk_path),
            binary=True,
            linear_deflection=deflection,
            angular_deflection=0.1,
        )
        export_seconds = perf_counter() - start
        exported.append({
            "chunk_id": chunk_id,
            "label": label,
            "fallback_id": fallback_id,
            "path": str(chunk_path),
            "export_seconds": export_seconds,
            "source_call_ids": tertius_source_call_ids(shape),
            "child_count": len([child for child in (getattr(shape, "children", ()) or ()) if isinstance(child, bd.Shape)]),
        })

    (output_dir / "sandbox-result.json").write_text(json.dumps({
        "ok": True,
        "root_name": root_name,
        "root_label": label_for(root_shape, root_name),
        "selection": selection,
        "quality": quality,
        "linear_deflection": deflection,
        "timings": timings,
        "source_map": tertius_source_map(),
        "chunks": exported,
    }, indent=2), encoding="utf-8")
except Exception:
    (output_dir / "sandbox-result.json").write_text(json.dumps({
        "ok": False,
        "error": traceback.format_exc(),
    }, indent=2), encoding="utf-8")
    traceback.print_exc()
    sys.exit(1)
"""


@dataclass(frozen=True)
class GlbChunks:
    json_chunk: dict[str, Any]
    binary_blob: bytes


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:96] or "chunk"


def collect_local_import_closure(project_dir: Path, entrypoint: str) -> list[Path]:
    visited: set[str] = set()
    ordered: list[Path] = []

    def visit(filename: str) -> None:
        path = project_dir / filename
        if filename in visited or not path.is_file():
            return
        visited.add(filename)
        ordered.append(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            return
        for node in ast.walk(tree):
            module_names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                module_names.append(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Import):
                module_names.extend(alias.name.split(".", 1)[0] for alias in node.names)
            for module_name in module_names:
                visit(f"{module_name}.py")

    visit(entrypoint)
    return ordered


def root_python_files(project_dir: Path) -> list[Path]:
    return sorted(path for path in project_dir.glob("*.py") if path.is_file())


def stage_project_files(project_dir: Path, entrypoint: str, stage_dir: Path, mode: str) -> list[str]:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    source_files = root_python_files(project_dir) if mode == "root-py" else collect_local_import_closure(project_dir, entrypoint)
    for source_file in source_files:
        shutil.copy2(source_file, stage_dir / source_file.name)
    (stage_dir / "tertius_provenance.py").write_text(TERTIUS_PROVENANCE_HELPER_SOURCE, encoding="utf-8")
    return [path.name for path in source_files]


def sandbox_env(project_dir: Path) -> dict[str, str]:
    sandbox_home = str(project_dir)
    env = {
        "HOME": sandbox_home,
        "PATH": "",
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


def parse_glb(data: bytes) -> GlbChunks:
    if len(data) < 20:
        raise ValueError("GLB payload is too short.")
    magic, version, length = struct.unpack("<4sII", data[:12])
    if magic != b"glTF" or version != 2:
        raise ValueError("GLB header is invalid.")
    if length > len(data):
        raise ValueError("GLB payload is truncated.")
    offset = 12
    json_chunk: dict[str, Any] | None = None
    binary_blob = b""
    while offset + 8 <= length:
        chunk_length, chunk_type = struct.unpack("<I4s", data[offset : offset + 8])
        offset += 8
        chunk = data[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == b"JSON":
            parsed = json.loads(chunk.rstrip(b" \t\r\n\x00").decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("GLB JSON chunk must contain an object.")
            json_chunk = parsed
        elif chunk_type == b"BIN\x00":
            binary_blob = chunk
    if json_chunk is None:
        raise ValueError("GLB payload does not contain a JSON chunk.")
    return GlbChunks(json_chunk=json_chunk, binary_blob=binary_blob)


def accessor_count(gltf: dict[str, Any], accessor_index: int) -> int:
    accessors = gltf.get("accessors") or []
    if not isinstance(accessor_index, int) or not (0 <= accessor_index < len(accessors)):
        return 0
    return int(accessors[accessor_index].get("count") or 0)


def analyze_glb(path: Path) -> dict[str, int]:
    chunks = parse_glb(path.read_bytes())
    gltf = chunks.json_chunk
    primitive_count = 0
    vertex_count = 0
    index_count = 0
    triangle_count = 0
    for mesh in gltf.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            primitive_count += 1
            attributes = primitive.get("attributes") or {}
            position = attributes.get("POSITION")
            vertices = accessor_count(gltf, position) if isinstance(position, int) else 0
            indices = accessor_count(gltf, primitive.get("indices")) if isinstance(primitive.get("indices"), int) else 0
            vertex_count += vertices
            index_count += indices
            if int(primitive.get("mode", 4)) == 4:
                triangle_count += indices // 3 if indices else vertices // 3
    mesh_refs: dict[int, int] = {}
    for node in gltf.get("nodes") or []:
        mesh_index = node.get("mesh") if isinstance(node, dict) else None
        if isinstance(mesh_index, int):
            mesh_refs[mesh_index] = mesh_refs.get(mesh_index, 0) + 1
    return {
        "scene_count": len(gltf.get("scenes") or []),
        "node_count": len(gltf.get("nodes") or []),
        "mesh_count": len(gltf.get("meshes") or []),
        "primitive_count": primitive_count,
        "material_count": len(gltf.get("materials") or []),
        "vertex_count": vertex_count,
        "index_count": index_count,
        "estimated_triangle_count": triangle_count,
        "nodes_referencing_already_shared_mesh": sum(count - 1 for count in mesh_refs.values() if count > 1),
    }


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def source_tree_digest(project_dir: Path, staged_files: list[str]) -> str:
    hasher = hashlib.sha256()
    for filename in sorted(staged_files):
        path = project_dir / filename
        hasher.update(filename.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def load_previous_chunks(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(chunk.get("chunk_id")): chunk
        for chunk in data.get("chunks", [])
        if isinstance(chunk, dict) and chunk.get("chunk_id")
    }


def run_chunk_sandbox(stage_dir: Path, entrypoint: str, quality: str, chunks_dir: Path, selection: str, timeout_seconds: int) -> dict[str, Any]:
    args = [sys.executable, "-c", CHUNK_SANDBOX_SCRIPT, entrypoint, quality, str(chunks_dir), selection]
    process = subprocess.Popen(
        args,
        cwd=stage_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=sandbox_env(stage_dir),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        start_new_session=False if sys.platform == "win32" else True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            process.kill()
        stdout, stderr = process.communicate()
        return {"ok": False, "stdout": stdout, "stderr": stderr, "error": f"Timed out after {timeout_seconds} seconds"}
    result_path = chunks_dir / "sandbox-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result.setdefault("ok", process.returncode == 0)
    result["stdout"] = stdout
    result["stderr"] = stderr
    if process.returncode != 0 and "error" not in result:
        result["error"] = stderr.strip() or f"Chunk export exited with {process.returncode}"
    return result


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    stage_dir = output_dir / "stage"
    chunks_dir = output_dir / "chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    chunks_dir.mkdir(parents=True)
    stage_start = perf_counter()
    staged_files = stage_project_files(args.project_dir, args.entrypoint, stage_dir, args.stage_mode)
    staging_seconds = perf_counter() - stage_start
    previous_chunks = load_previous_chunks(args.previous_manifest)
    sandbox_result = run_chunk_sandbox(stage_dir, args.entrypoint, args.quality, chunks_dir, args.chunk_selection, args.timeout_seconds)
    if not sandbox_result.get("ok"):
        failure = {
            "schema_version": 1,
            "ok": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error": sandbox_result.get("error"),
            "stdout": sandbox_result.get("stdout", ""),
            "stderr": sandbox_result.get("stderr", ""),
        }
        (output_dir / "chunk-manifest.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        return failure

    chunks: list[dict[str, Any]] = []
    for chunk in sandbox_result.get("chunks", []):
        path = Path(chunk["path"])
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        compressed = gzip.compress(data)
        previous = previous_chunks.get(chunk["chunk_id"])
        cache_status = "miss"
        if previous and previous.get("digest_sha256") == digest:
            cache_status = "hit"
        elif previous:
            cache_status = "changed"
        chunks.append(
            {
                "chunk_id": chunk["chunk_id"],
                "label": chunk["label"],
                "source_path": chunk.get("fallback_id"),
                "relative_glb_path": str(path.relative_to(output_dir)).replace("\\", "/"),
                "digest_sha256": digest,
                "cache_key": f"glb:{args.quality}:{chunk['chunk_id']}:{digest[:24]}",
                "cache_status": cache_status,
                "raw_glb_bytes": len(data),
                "gzip_bytes": len(compressed),
                "export_seconds": chunk["export_seconds"],
                "child_count": chunk.get("child_count", 0),
                "source_call_ids": chunk.get("source_call_ids", []),
                "gltf": analyze_glb(path),
            }
        )

    largest = max(chunks, key=lambda item: item["raw_glb_bytes"]) if chunks else None
    total_raw = sum(int(chunk["raw_glb_bytes"]) for chunk in chunks)
    total_gzip = sum(int(chunk["gzip_bytes"]) for chunk in chunks)
    total_export = sum(float(chunk["export_seconds"]) for chunk in chunks)
    ideal_parallel = max((float(chunk["export_seconds"]) for chunk in chunks), default=0.0)
    manifest = {
        "schema_version": 1,
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_dir": str(args.project_dir),
        "entrypoint": args.entrypoint,
        "quality": args.quality,
        "linear_deflection": QUALITY_DEFLECTIONS[args.quality],
        "chunk_selection": args.chunk_selection,
        "stage_mode": args.stage_mode,
        "source_tree_digest": source_tree_digest(args.project_dir, staged_files),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "tertius_commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        },
        "root": {
            "name": sandbox_result.get("root_name"),
            "label": sandbox_result.get("root_label"),
            "natural_child_chunk_count": len(chunks),
        },
        "timings": {
            "staging_seconds": staging_seconds,
            **(sandbox_result.get("timings") or {}),
        },
        "source_map": sandbox_result.get("source_map", {}),
        "summary": {
            "chunk_count": len(chunks),
            "largest_chunk_id": largest["chunk_id"] if largest else None,
            "largest_chunk_label": largest["label"] if largest else None,
            "largest_chunk_raw_glb_bytes": largest["raw_glb_bytes"] if largest else 0,
            "largest_chunk_gzip_bytes": largest["gzip_bytes"] if largest else 0,
            "total_raw_glb_bytes": total_raw,
            "total_gzip_bytes": total_gzip,
            "sequential_export_seconds": total_export,
            "ideal_parallel_export_seconds": ideal_parallel,
            "cache_hits": sum(1 for chunk in chunks if chunk["cache_status"] == "hit"),
            "cache_misses": sum(1 for chunk in chunks if chunk["cache_status"] == "miss"),
            "changed_chunks": [chunk["chunk_id"] for chunk in chunks if chunk["cache_status"] == "changed"],
            "oversized_chunks": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "label": chunk["label"],
                    "raw_glb_bytes": chunk["raw_glb_bytes"],
                    "gzip_bytes": chunk["gzip_bytes"],
                }
                for chunk in chunks
                if chunk["raw_glb_bytes"] > args.chunk_raw_limit_bytes or chunk["gzip_bytes"] > args.chunk_gzip_limit_bytes
            ],
        },
        "chunks": chunks,
    }
    (output_dir / "chunk-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(output_dir / "chunk-manifest.json"), "summary": manifest["summary"]}, indent=2))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype chunked GLB export and manifest generation.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--entrypoint", default="design.py")
    parser.add_argument("--quality", choices=sorted(QUALITY_DEFLECTIONS), default="rough")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--stage-mode", choices=["root-py", "closure"], default="root-py")
    parser.add_argument("--chunk-selection", choices=["root-children", "root"], default="root-children")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--chunk-raw-limit-bytes", type=int, default=32 * 1024 * 1024)
    parser.add_argument("--chunk-gzip-limit-bytes", type=int, default=8 * 1024 * 1024)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.project_dir = args.project_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    build_manifest(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
