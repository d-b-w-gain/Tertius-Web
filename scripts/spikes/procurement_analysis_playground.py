#!/usr/bin/env python3
r"""Run deterministic procurement analysis outside the web app.

Examples:

  python scripts/spikes/procurement_analysis_playground.py \
      --project-dir C:\path\to\project \
      --tree-json C:\path\to\scene-tree.json \
      --out C:\tmp\procurement_analysis.json

  python scripts/spikes/procurement_analysis_playground.py \
      --project-dir C:\path\to\project \
      --gltf C:\path\to\model.gltf \
      --out C:\tmp\procurement_analysis.json

  python scripts/spikes/procurement_analysis_playground.py \
      --project-dir C:\path\to\project \
      --source-only \
      --out C:\tmp\procurement_analysis.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.procurement_analysis import (  # noqa: E402
    analyze_design_sources,
    analyze_gltf_tree,
    build_procurement_analysis,
)
from core.compile_sandbox import run_compile_sandbox  # noqa: E402


BUILD123D_COMPOUND_COMPAT = '''

def __tertius_compat_flatten_shapes(items):
    flattened = []
    if items is None:
        return flattened
    for item in items:
        if item is None:
            continue
        if hasattr(item, "wrapped"):
            flattened.append(item)
            continue
        if isinstance(item, (str, bytes)):
            flattened.append(item)
            continue
        try:
            flattened.extend(__tertius_compat_flatten_shapes(list(item)))
        except TypeError:
            flattened.append(item)
    return flattened


def __tertius_compat_compound(children=None, label="", color=None, material="", joints=None, parent=None):
    from build123d import Compound as __TertiusBuild123dCompound

    return __TertiusBuild123dCompound(
        __tertius_compat_flatten_shapes(children),
        label=label,
        color=color,
        material=material,
        joints=joints,
        parent=parent,
    )
'''


def insert_after_future_imports(text: str, insertion: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_at = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("from __future__ import "):
            insert_at = index + 1
            continue
        if insert_at and (not stripped or stripped.startswith("#")):
            insert_at = index + 1
            continue
        if insert_at:
            break
    return "".join(lines[:insert_at]) + insertion + "\n" + "".join(lines[insert_at:])


def read_python_files(project_dir: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8-sig")
        for path in sorted(project_dir.glob("*.py"))
        if path.is_file()
    }


def copy_project_for_compile(project_dir: Path, entrypoint: str, *, compat_build123d_compound: bool) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="tertius-procurement-analysis-"))
    for path in sorted(project_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in {".py", ".json", ".csv"}:
            shutil.copy2(path, temp_dir / path.name)
    if entrypoint != "design.py":
        shutil.copy2(project_dir / entrypoint, temp_dir / "design.py")
    if compat_build123d_compound:
        for path in sorted(temp_dir.glob("*.py")):
            if path.name.endswith(".json.py"):
                continue
            text = path.read_text(encoding="utf-8-sig")
            patched = insert_after_future_imports(text, BUILD123D_COMPOUND_COMPAT)
            patched = re.sub(r"\bbd\.Compound\(\s*children\s*=", "__tertius_compat_compound(", patched)
            patched = re.sub(r"\bCompound\(\s*children\s*=", "__tertius_compat_compound(", patched)
            patched = re.sub(r"\bbd\.Compound\(\s*parts\s*,", "__tertius_compat_compound(parts,", patched)
            patched = re.sub(r"\bCompound\(\s*parts\s*,", "__tertius_compat_compound(parts,", patched)
            if patched != text:
                path.write_text(patched, encoding="utf-8")
    return temp_dir


def gltf_to_scene_tree(gltf: dict[str, Any]) -> dict[str, Any]:
    nodes = gltf.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("GLTF JSON must contain a nodes list.")

    def convert_node(index: int) -> dict[str, Any]:
        node = nodes[index]
        if not isinstance(node, dict):
            raise ValueError(f"GLTF node {index} is not an object.")
        child_indexes = node.get("children") if isinstance(node.get("children"), list) else []
        has_mesh = isinstance(node.get("mesh"), int)
        converted = {
            "id": str(index),
            "name": str(node.get("name") or ("Mesh" if has_mesh else f"node_{index}")),
            "type": "Mesh" if has_mesh else "Object3D",
            "isMesh": has_mesh,
            "children": [convert_node(child_index) for child_index in child_indexes],
        }
        if isinstance(node.get("extras"), dict):
            converted["extras"] = node["extras"]
        for key in ("translation", "rotation", "scale", "matrix"):
            if isinstance(node.get(key), list):
                converted[key] = node[key]
        return converted

    scene_indexes: list[int] = []
    scene_id = gltf.get("scene")
    scenes = gltf.get("scenes")
    if isinstance(scene_id, int) and isinstance(scenes, list) and 0 <= scene_id < len(scenes):
        scene = scenes[scene_id]
        if isinstance(scene, dict) and isinstance(scene.get("nodes"), list):
            scene_indexes = [index for index in scene["nodes"] if isinstance(index, int)]

    if not scene_indexes:
        referenced = {
            child_index
            for node in nodes
            if isinstance(node, dict)
            for child_index in (node.get("children") or [])
            if isinstance(child_index, int)
        }
        scene_indexes = [index for index in range(len(nodes)) if index not in referenced]

    scene_tree = {
        "name": "Scene",
        "type": "Scene",
        "children": [convert_node(index) for index in scene_indexes],
    }
    if isinstance(gltf.get("extras"), dict):
        scene_tree["extras"] = gltf["extras"]
    return scene_tree


def read_gltf_artifact(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if data[:4] != b"glTF":
        return json.loads(data.decode("utf-8-sig"))

    if len(data) < 20:
        raise ValueError(f"{path} is not a valid GLB file.")
    magic, version, _length = struct.unpack("<4sII", data[:12])
    if magic != b"glTF" or version != 2:
        raise ValueError(f"{path} is not a GLB v2 file.")

    offset = 12
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack("<I4s", data[offset:offset + 8])
        offset += 8
        chunk_data = data[offset:offset + chunk_length]
        offset += chunk_length
        if chunk_type == b"JSON":
            return json.loads(chunk_data.decode("utf-8").rstrip(" \t\r\n\0"))

    raise ValueError(f"{path} does not contain a GLB JSON chunk.")


def load_tree(args: argparse.Namespace) -> dict[str, Any]:
    if args.tree_json:
        return json.loads(Path(args.tree_json).read_text(encoding="utf-8-sig"))
    if args.gltf:
        gltf = json.loads(Path(args.gltf).read_text(encoding="utf-8-sig"))
        return gltf_to_scene_tree(gltf)

    project_dir = resolve_project_dir(args)
    entrypoint = resolve_entrypoint(args)
    compile_dir = copy_project_for_compile(
        project_dir,
        entrypoint,
        compat_build123d_compound=args.compat_build123d_compound,
    )
    print(f"Compiling temporary {args.export_format.upper()} in {compile_dir}")
    result = run_compile_sandbox(
        compile_dir,
        args.export_format,
        quality=args.quality,
        timeout_seconds=args.compile_timeout,
    )
    if not result.success or result.output_path is None:
        raise RuntimeError(
            "GLTF compile failed:\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}\n"
            f"ERROR:\n{result.error}"
        )
    manifest_path = compile_dir / "bom_manifest.json"
    if manifest_path.exists():
        args.explicit_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    gltf = read_gltf_artifact(result.output_path)
    return gltf_to_scene_tree(gltf)


def resolve_project_dir(args: argparse.Namespace) -> Path:
    if args.design_py:
        return Path(args.design_py).resolve().parent
    if args.project_dir:
        return Path(args.project_dir).resolve()
    raise ValueError("Pass either --project-dir or --design-py.")


def resolve_entrypoint(args: argparse.Namespace) -> str:
    if args.design_py:
        return Path(args.design_py).resolve().name
    return args.entrypoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, help="Directory containing design.py and local imports.")
    parser.add_argument("--design-py", type=Path, help="Direct path to the design.py entrypoint.")
    parser.add_argument("--entrypoint", default="design.py", help="Entrypoint filename inside --project-dir.")
    parser.add_argument("--tree-json", type=Path, help="Simplified scene-tree JSON fixture.")
    parser.add_argument("--gltf", type=Path, help="Text .gltf JSON file to convert into a scene tree.")
    parser.add_argument("--source-only", action="store_true", help="Skip GLTF compile and build from deterministic source evidence.")
    parser.add_argument(
        "--export-format",
        choices=["glb", "gltf"],
        default="glb",
        help="Visual artifact format to compile when no tree/GLTF is supplied. GLB preserves patched Build123D labels.",
    )
    parser.add_argument("--quality", default="sketch", help="Visual compile quality when no tree/GLTF is supplied.")
    parser.add_argument("--compile-timeout", type=int, default=240, help="Seconds to wait for temporary GLTF compile.")
    parser.add_argument(
        "--compat-build123d-compound",
        action="store_true",
        dest="compat_build123d_compound",
        help="Enable temp-only rewrite from Compound(children=...) to Compound(...). This can help old runtimes but flattens visual hierarchy.",
    )
    parser.add_argument("--out", required=True, type=Path, help="Output procurement_analysis.json path.")
    args = parser.parse_args()
    args.explicit_manifest = None

    project_dir = resolve_project_dir(args)
    entrypoint = resolve_entrypoint(args)
    files = read_python_files(project_dir)
    if entrypoint not in files:
        raise FileNotFoundError(f"{entrypoint} was not found in {project_dir}")

    source_analysis = analyze_design_sources(files, entrypoint=entrypoint)
    if args.source_only:
        tree_analysis = {"assemblies": [], "components": [], "diagnostics": []}
        args.explicit_manifest = {}
    else:
        tree_analysis = analyze_gltf_tree(load_tree(args))
    procurement_analysis = build_procurement_analysis(
        source_analysis,
        tree_analysis,
        explicit_manifest=args.explicit_manifest,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(procurement_analysis, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")
    print(
        "Assemblies={assemblies} Components={components} Requirements={requirements} Diagnostics={diagnostics}".format(
            assemblies=len(procurement_analysis.get("assemblies", [])),
            components=len(procurement_analysis.get("components", [])),
            requirements=len(procurement_analysis.get("requirements", [])),
            diagnostics=len(procurement_analysis.get("diagnostics", [])),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
