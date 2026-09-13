from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any


_COMPONENT_BYTES = {
    5120: 1,
    5121: 1,
    5122: 2,
    5123: 2,
    5125: 4,
    5126: 4,
}
_TYPE_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


def _parse_glb(data: bytes) -> tuple[dict[str, Any], bytes] | None:
    if len(data) < 28:
        return None
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        return None

    chunks: list[tuple[bytes, bytes]] = []
    offset = 12
    while offset < len(data):
        if offset + 8 > len(data):
            return None
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        chunk_end = offset + chunk_length
        if chunk_end > len(data):
            return None
        chunks.append((chunk_type, data[offset:chunk_end]))
        offset = chunk_end

    if len(chunks) != 2 or chunks[0][0] != b"JSON" or chunks[1][0] != b"BIN\x00":
        return None
    try:
        gltf = json.loads(chunks[0][1].decode("utf-8").rstrip(" \t\r\n\x00"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(gltf, dict):
        return None
    return gltf, chunks[1][1]


def _accessor_payload(
    gltf: dict[str, Any],
    binary: bytes,
    accessor: dict[str, Any],
) -> tuple[bytes, dict[str, Any]] | None:
    if "sparse" in accessor or not isinstance(accessor.get("bufferView"), int):
        return None
    views = gltf.get("bufferViews")
    if not isinstance(views, list):
        return None
    view_index = accessor["bufferView"]
    if not (0 <= view_index < len(views)) or not isinstance(views[view_index], dict):
        return None
    view = views[view_index]
    if int(view.get("buffer", 0)) != 0:
        return None

    component_type = accessor.get("componentType")
    accessor_type = accessor.get("type")
    if not isinstance(component_type, int) or not isinstance(accessor_type, str):
        return None
    component_bytes = _COMPONENT_BYTES.get(component_type)
    component_count = _TYPE_COMPONENTS.get(accessor_type)
    count = accessor.get("count")
    if component_bytes is None or component_count is None or not isinstance(count, int):
        return None
    element_bytes = component_bytes * component_count
    stride = int(view.get("byteStride", element_bytes))
    if stride < element_bytes:
        return None
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    end = start + (count - 1) * stride + element_bytes if count else start
    if start < 0 or end > len(binary):
        return None
    if stride == element_bytes:
        payload = binary[start:end]
    else:
        payload = b"".join(
            binary[start + index * stride : start + index * stride + element_bytes]
            for index in range(count)
        )

    identity = {
        key: value
        for key, value in accessor.items()
        if key not in {"bufferView", "byteOffset", "name"}
    }
    return payload, identity


def _remap_accessor_references(gltf: dict[str, Any], remap: dict[int, int]) -> None:
    for mesh in gltf.get("meshes", []):
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get("primitives", []):
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes")
            if isinstance(attributes, dict):
                for semantic, accessor_index in tuple(attributes.items()):
                    if isinstance(accessor_index, int):
                        attributes[semantic] = remap[accessor_index]
            if isinstance(primitive.get("indices"), int):
                primitive["indices"] = remap[primitive["indices"]]
            for target in primitive.get("targets", []):
                if isinstance(target, dict):
                    for semantic, accessor_index in tuple(target.items()):
                        if isinstance(accessor_index, int):
                            target[semantic] = remap[accessor_index]

    for skin in gltf.get("skins", []):
        if isinstance(skin, dict) and isinstance(skin.get("inverseBindMatrices"), int):
            skin["inverseBindMatrices"] = remap[skin["inverseBindMatrices"]]
    for animation in gltf.get("animations", []):
        if not isinstance(animation, dict):
            continue
        for sampler in animation.get("samplers", []):
            if not isinstance(sampler, dict):
                continue
            for key in ("input", "output"):
                if isinstance(sampler.get(key), int):
                    sampler[key] = remap[sampler[key]]


def _deduplicate_meshes(gltf: dict[str, Any]) -> int:
    meshes = gltf.get("meshes")
    if not isinstance(meshes, list):
        return 0
    unique: list[dict[str, Any]] = []
    remap: dict[int, int] = {}
    by_identity: dict[str, int] = {}
    for old_index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            remap[old_index] = len(unique)
            unique.append(mesh)
            continue
        identity = {key: value for key, value in mesh.items() if key != "name"}
        key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        new_index = by_identity.get(key)
        if new_index is None:
            new_index = len(unique)
            by_identity[key] = new_index
            unique.append(mesh)
        remap[old_index] = new_index
    if len(unique) == len(meshes):
        return 0
    for node in gltf.get("nodes", []):
        if isinstance(node, dict) and isinstance(node.get("mesh"), int):
            node["mesh"] = remap[node["mesh"]]
    gltf["meshes"] = unique
    return len(meshes) - len(unique)


def deduplicate_glb_bytes(data: bytes) -> tuple[bytes, dict[str, int]]:
    """Share repeated GLB accessors and meshes while retaining every scene node."""

    parsed = _parse_glb(data)
    if parsed is None:
        return data, {"accessors": 0, "meshes": 0, "bytes": 0}
    gltf, binary = parsed
    if gltf.get("extensionsRequired") or any(
        isinstance(image, dict) and "bufferView" in image
        for image in gltf.get("images", [])
    ):
        return data, {"accessors": 0, "meshes": 0, "bytes": 0}
    buffers = gltf.get("buffers")
    accessors = gltf.get("accessors")
    if (
        not isinstance(buffers, list)
        or len(buffers) != 1
        or not isinstance(accessors, list)
    ):
        return data, {"accessors": 0, "meshes": 0, "bytes": 0}

    new_binary = bytearray()
    new_views: list[dict[str, Any]] = []
    new_accessors: list[dict[str, Any]] = []
    accessor_remap: dict[int, int] = {}
    by_identity: dict[tuple[str, bytes], int] = {}
    for old_index, accessor in enumerate(accessors):
        if not isinstance(accessor, dict):
            return data, {"accessors": 0, "meshes": 0, "bytes": 0}
        extracted = _accessor_payload(gltf, binary, accessor)
        if extracted is None:
            return data, {"accessors": 0, "meshes": 0, "bytes": 0}
        payload, identity = extracted
        identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        key = (identity_json, hashlib.sha256(payload).digest())
        new_index = by_identity.get(key)
        if new_index is None:
            while len(new_binary) % 4:
                new_binary.append(0)
            byte_offset = len(new_binary)
            new_binary.extend(payload)
            old_view = gltf["bufferViews"][accessor["bufferView"]]
            new_view: dict[str, Any] = {
                "buffer": 0,
                "byteOffset": byte_offset,
                "byteLength": len(payload),
            }
            if "target" in old_view:
                new_view["target"] = old_view["target"]
            new_index = len(new_accessors)
            by_identity[key] = new_index
            new_views.append(new_view)
            new_accessor = dict(accessor)
            new_accessor["bufferView"] = len(new_views) - 1
            new_accessor.pop("byteOffset", None)
            new_accessors.append(new_accessor)
        accessor_remap[old_index] = new_index

    _remap_accessor_references(gltf, accessor_remap)
    gltf["accessors"] = new_accessors
    gltf["bufferViews"] = new_views
    deduplicated_meshes = _deduplicate_meshes(gltf)
    buffers[0]["byteLength"] = len(new_binary)

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    binary_bytes = bytes(new_binary)
    binary_bytes += b"\x00" * ((4 - len(binary_bytes) % 4) % 4)
    new_length = 12 + 8 + len(json_bytes) + 8 + len(binary_bytes)
    result = b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, new_length),
            struct.pack("<I4s", len(json_bytes), b"JSON"),
            json_bytes,
            struct.pack("<I4s", len(binary_bytes), b"BIN\x00"),
            binary_bytes,
        )
    )
    if len(result) >= len(data):
        return data, {"accessors": 0, "meshes": 0, "bytes": 0}
    return result, {
        "accessors": len(accessors) - len(new_accessors),
        "meshes": deduplicated_meshes,
        "bytes": len(data) - len(result),
    }


def deduplicate_glb_file(path: str | Path) -> dict[str, int]:
    glb_path = Path(path)
    original = glb_path.read_bytes()
    optimized, stats = deduplicate_glb_bytes(original)
    if optimized is not original:
        glb_path.write_bytes(optimized)
    return stats
