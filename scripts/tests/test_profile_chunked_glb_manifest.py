from __future__ import annotations

import json
import struct
from pathlib import Path

from scripts.spikes.profile_chunked_glb_manifest import (
    analyze_glb,
    collect_local_import_closure,
    load_previous_chunks,
    parse_glb,
    slugify,
    stage_project_files,
)


def _pad4(data: bytes, pad: bytes = b" ") -> bytes:
    return data + pad * ((4 - len(data) % 4) % 4)


def _write_glb(path: Path, gltf: dict, binary: bytes) -> None:
    json_chunk = _pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = _pad4(binary, b"\x00")
    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    path.write_bytes(
        b"".join(
            [
                struct.pack("<4sII", b"glTF", 2, total_length),
                struct.pack("<I4s", len(json_chunk), b"JSON"),
                json_chunk,
                struct.pack("<I4s", len(bin_chunk), b"BIN\x00"),
                bin_chunk,
            ]
        )
    )


def test_slugify_makes_stable_chunk_id() -> None:
    assert slugify("Roof Cladding RC01-52-32-20") == "roof-cladding-rc01-52-32-20"
    assert slugify("  !!!  ") == "chunk"


def test_analyze_glb_counts_complexity(tmp_path: Path) -> None:
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack("<3H", 0, 1, 2)
    binary = positions + indices + b"\x00\x00"
    glb_path = tmp_path / "triangle.glb"
    _write_glb(
        glb_path,
        {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0, 1]}],
            "nodes": [{"mesh": 0}, {"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
            "materials": [{"name": "steel"}],
            "buffers": [{"byteLength": len(binary)}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
            ],
        },
        binary,
    )

    chunks = parse_glb(glb_path.read_bytes())
    analysis = analyze_glb(glb_path)

    assert chunks.json_chunk["asset"]["version"] == "2.0"
    assert analysis["node_count"] == 2
    assert analysis["mesh_count"] == 1
    assert analysis["primitive_count"] == 1
    assert analysis["vertex_count"] == 3
    assert analysis["estimated_triangle_count"] == 1
    assert analysis["nodes_referencing_already_shared_mesh"] == 1


def test_stage_project_files_can_use_root_py_or_import_closure(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "design.py").write_text("from helper import make\npart = make()\n", encoding="utf-8")
    (project / "helper.py").write_text("def make():\n    return None\n", encoding="utf-8")
    (project / "unused.py").write_text("value = 1\n", encoding="utf-8")

    closure = collect_local_import_closure(project, "design.py")
    assert [path.name for path in closure] == ["design.py", "helper.py"]

    closure_stage = tmp_path / "closure"
    root_stage = tmp_path / "root"
    assert stage_project_files(project, "design.py", closure_stage, "closure") == ["design.py", "helper.py"]
    assert stage_project_files(project, "design.py", root_stage, "root-py") == ["design.py", "helper.py", "unused.py"]
    assert (closure_stage / "tertius_provenance.py").exists()


def test_load_previous_chunks_indexes_by_chunk_id(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "chunks": [
                    {"chunk_id": "roof", "digest_sha256": "aaa"},
                    {"chunk_id": "floor", "digest_sha256": "bbb"},
                    {"label": "missing id"},
                ]
            }
        ),
        encoding="utf-8",
    )

    previous = load_previous_chunks(manifest)

    assert sorted(previous) == ["floor", "roof"]
    assert previous["roof"]["digest_sha256"] == "aaa"
