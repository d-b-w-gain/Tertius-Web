"""Compare an assembled scene manifest with the monolithic GLB (#314).

Assembles the scene the way the viewer does (an asset's root takes the
manifest node's transform; the units nested in it come from their own
assets, at the manifest's positions) and compares it with the monolithic GLB
node by node: names, Tertius extras (source call ids from the manifest),
world-space geometry and triangle counts.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

EXTRAS = ("tertiusComponent", "tertiusConnection", "tertiusPart", "tertiusSourceCallIds")


def parse_glb(path: Path) -> dict:
    data = path.read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20 : 20 + length])


def matmul(a: list[float], b: list[float]) -> list[float]:
    """Column-major 4x4 product a*b."""
    return [sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4)) for col in range(4) for row in range(4)]


def node_matrix(node: dict) -> list[float]:
    if "matrix" in node:
        return [float(v) for v in node["matrix"]]
    tx, ty, tz = node.get("translation", [0, 0, 0])
    qx, qy, qz, qw = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = node.get("scale", [1, 1, 1])
    r = [
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy + qz * qw), 2 * (qx * qz - qy * qw), 0,
        2 * (qx * qy - qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz + qx * qw), 0,
        2 * (qx * qz + qy * qw), 2 * (qy * qz - qx * qw), 1 - 2 * (qx * qx + qy * qy), 0,
        0, 0, 0, 1,
    ]
    scaled = [r[i] * (sx, sy, sz, 1)[i // 4] for i in range(16)]
    scaled[12:15] = [tx, ty, tz]
    return scaled


def triangles(gltf: dict, node: dict) -> int:
    mesh_index = node.get("mesh")
    if not isinstance(mesh_index, int):
        return 0
    total = 0
    for primitive in gltf["meshes"][mesh_index].get("primitives", []):
        accessor = primitive.get("indices", primitive.get("attributes", {}).get("POSITION"))
        total += gltf["accessors"][accessor]["count"] // 3
    return total


def world_bounds(gltf: dict, node: dict, world: list[float]) -> list[float] | None:
    """World-space box of a node's mesh: its POSITION bounds through its
    world matrix. Exporters split placements between node matrices and
    vertex data differently, so this, not the matrix, is what must match."""
    mesh_index = node.get("mesh")
    if not isinstance(mesh_index, int):
        return None
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for primitive in gltf["meshes"][mesh_index].get("primitives", []):
        accessor = gltf["accessors"][primitive["attributes"]["POSITION"]]
        mn, mx = accessor["min"], accessor["max"]
        for corner in range(8):
            p = [(mx if corner >> axis & 1 else mn)[axis] for axis in range(3)]
            w = [sum(world[k * 4 + row] * (p + [1.0])[k] for k in range(4)) for row in range(3)]
            lo = [min(a, b) for a, b in zip(lo, w)]
            hi = [max(a, b) for a, b in zip(hi, w)]
    return lo + hi


def roots(gltf: dict) -> list[int]:
    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)]
    return scene.get("nodes", [])


def tree_from_glb(gltf: dict, index: int, parent: list[float]) -> dict:
    node = gltf["nodes"][index]
    world = matmul(parent, node_matrix(node))
    return {
        "name": node.get("name", ""),
        "extras": {k: v for k, v in (node.get("extras") or {}).items() if k in EXTRAS},
        "world": world,
        "bounds": world_bounds(gltf, node, world),
        "triangles": triangles(gltf, node),
        "children": [tree_from_glb(gltf, child, world) for child in node.get("children", [])],
    }


IDENTITY = [1.0 if i % 5 == 0 else 0.0 for i in range(16)]


def assemble(scene_dir: Path, entry: dict, asset: tuple[dict, int] | None, parent: list[float], loaded: dict) -> dict:
    if "asset" in entry:
        gltf = loaded.setdefault(entry["asset"], parse_glb(scene_dir / "assets" / f"{entry['asset']}.glb"))
        asset = (gltf, roots(gltf)[0])
        local = [float(v) for v in entry["matrix"]]      # the manifest places the asset
    else:
        assert asset is not None, "a manifest node outside any asset"
        local = node_matrix(asset[0]["nodes"][asset[1]])
    gltf, index = asset
    node = gltf["nodes"][index]
    world = matmul(parent, local)
    extras = {k: v for k, v in (node.get("extras") or {}).items() if k in EXTRAS}
    source_ids = [str(i) for i in (entry.get("visual") or {}).get("source_call_ids") or [] if i]
    if source_ids:
        extras["tertiusSourceCallIds"] = source_ids
    asset_children = iter(node.get("children", []))
    children = []
    for child in entry["children"]:
        if "asset" in child:
            children.append(assemble(scene_dir, child, None, world, loaded))
        else:
            children.append(assemble(scene_dir, child, (gltf, next(asset_children)), world, loaded))
    leftover = list(asset_children)
    assert not leftover, f"asset nodes not in the manifest under {node.get('name')!r}"
    return {"name": node.get("name", ""), "extras": extras, "world": world,
            "bounds": world_bounds(gltf, node, world),
            "triangles": triangles(gltf, node), "children": children}


def compare(mono: dict, scene: dict, path: str, problems: list[str]) -> int:
    count = 1
    where = f"{path}/{mono['name']}"
    if mono["name"] != scene["name"]:
        problems.append(f"{where}: name {scene['name']!r}")
    if mono["extras"] != scene["extras"]:
        problems.append(f"{where}: extras {scene['extras']} vs {mono['extras']}")
    if mono["triangles"] != scene["triangles"]:
        problems.append(f"{where}: {scene['triangles']} triangles vs {mono['triangles']}")
    if (mono["bounds"] is None) != (scene["bounds"] is None):
        problems.append(f"{where}: mesh present in only one")
    elif mono["bounds"] is not None:
        error = max(abs(a - b) for a, b in zip(mono["bounds"], scene["bounds"]))
        if error > 1e-6:                       # metres: a micron
            problems.append(f"{where}: geometry off by {error * 1000:.3g} mm")
    if len(mono["children"]) != len(scene["children"]):
        problems.append(f"{where}: {len(scene['children'])} children vs {len(mono['children'])}")
    for a, b in zip(mono["children"], scene["children"]):
        count += compare(a, b, where, problems)
    return count


def check_project(project: Path) -> tuple[int, list[str], dict]:
    """(nodes compared, problems, sizes) for a compiled project folder with
    output.glb and tertius-scene/."""
    mono_gltf = parse_glb(project / "output.glb")
    mono_roots = roots(mono_gltf)
    if len(mono_roots) != 1:
        return 0, [f"monolithic GLB has {len(mono_roots)} roots"], {}
    mono = tree_from_glb(mono_gltf, mono_roots[0], IDENTITY)
    scene_dir = project / "tertius-scene"
    manifest = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    scene = assemble(scene_dir, manifest["root"], None, IDENTITY, {})
    problems: list[str] = []
    nodes = compare(mono, scene, "", problems)
    sizes = {
        "assets": len(manifest["assets"]),
        "asset_bytes": sum((scene_dir / "assets" / f"{a}.glb").stat().st_size for a in manifest["assets"]),
        "monolithic_bytes": (project / "output.glb").stat().st_size,
        "manifest_bytes": (scene_dir / "scene.json").stat().st_size,
    }
    return nodes, problems, sizes
