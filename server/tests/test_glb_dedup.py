import json
import struct

from core.glb_dedup import deduplicate_glb_bytes


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
