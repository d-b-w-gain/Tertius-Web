import json
import struct

from core.glb_dedup import annotate_mesh_digests, deduplicate_glb_bytes


def _glb(gltf: dict, binary: bytes) -> bytes:
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    binary += b"\x00" * ((4 - len(binary) % 4) % 4)
    length = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, length),
            struct.pack("<I4s", len(json_bytes), b"JSON"),
            json_bytes,
            struct.pack("<I4s", len(binary), b"BIN\x00"),
            binary,
        )
    )


def _parse(data: bytes) -> tuple[dict, bytes]:
    json_length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + json_length])
    binary_offset = 20 + json_length
    binary_length = struct.unpack_from("<I", data, binary_offset)[0]
    binary = data[binary_offset + 8 : binary_offset + 8 + binary_length]
    return gltf, binary


def test_deduplicate_glb_bytes_shares_geometry_and_preserves_instances():
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack("<3H", 0, 1, 2)
    indices_block = indices + b"\x00\x00"
    first_indices_offset = len(positions)
    second_positions_offset = first_indices_offset + len(indices_block)
    second_indices_offset = second_positions_offset + len(positions)
    binary = positions + indices_block + positions + indices_block
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": first_indices_offset, "byteLength": len(indices), "target": 34963},
            {"buffer": 0, "byteOffset": second_positions_offset, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": second_indices_offset, "byteLength": len(indices), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 3, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "materials": [{"name": "steel"}],
        "meshes": [
            {"name": "bolt-a", "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]},
            {"name": "bolt-b", "primitives": [{"attributes": {"POSITION": 2}, "indices": 3, "material": 0}]},
        ],
        "nodes": [
            {"name": "PB1230HS-1", "mesh": 0, "extras": {"mark": "B1"}},
            {"name": "PB1230HS-2", "mesh": 1, "translation": [100, 0, 0], "extras": {"mark": "B2"}},
        ],
        "scenes": [{"nodes": [0, 1]}],
        "scene": 0,
    }
    original = _glb(gltf, binary)

    optimized, stats = deduplicate_glb_bytes(original)
    result, result_binary = _parse(optimized)

    assert len(optimized) < len(original)
    assert stats == {"accessors": 2, "meshes": 1, "bytes": len(original) - len(optimized)}
    assert len(result["accessors"]) == 2
    assert len(result["bufferViews"]) == 2
    assert len(result["meshes"]) == 1
    assert [node["mesh"] for node in result["nodes"]] == [0, 0]
    assert result["nodes"][1]["translation"] == [100, 0, 0]
    assert result["nodes"][0]["extras"] == {"mark": "B1"}
    assert result["nodes"][1]["extras"] == {"mark": "B2"}
    assert result["buffers"][0]["byteLength"] <= len(result_binary)


def _triangle_glb(offset: float, extra_meshes: int = 0) -> bytes:
    """A GLB holding one triangle mesh (after `extra_meshes` other ones)."""
    meshes, accessors, views, binary = [], [], [], b""
    for index in range(extra_meshes + 1):
        x = offset + index if index < extra_meshes else 1
        positions = struct.pack("<9f", 0, 0, 0, x, 0, 0, 0, 1, 0)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(positions)})
        binary += positions
        accessors.append({"bufferView": index, "componentType": 5126, "count": 3, "type": "VEC3"})
        meshes.append({"primitives": [{"attributes": {"POSITION": index}}]})
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
        "meshes": meshes,
        "nodes": [{"mesh": index} for index in range(len(meshes))],
        "scenes": [{"nodes": list(range(len(meshes)))}],
    }
    return _glb(gltf, binary)


def test_mesh_digests_match_equal_geometry_across_glbs():
    alone, _ = _parse(annotate_mesh_digests(_triangle_glb(0)))
    later, _ = _parse(annotate_mesh_digests(_triangle_glb(5, extra_meshes=2)))

    digests = [mesh["extras"]["tertiusMeshDigest"] for mesh in later["meshes"]]
    assert alone["meshes"][0]["extras"]["tertiusMeshDigest"] == digests[2]
    assert len(set(digests)) == 3


def test_mesh_digests_leave_non_glb_content_alone():
    assert annotate_mesh_digests(b"not a glb") == b"not a glb"
