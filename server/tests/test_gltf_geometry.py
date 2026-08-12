from __future__ import annotations

import json
import struct

from core.gltf_geometry import model_site_dimensions


def _scene() -> dict:
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "name": "candidate",
                "rotation": [-0.7071067811865475, 0, 0, 0.7071067811865475],
                "children": [1, 2],
            },
            {"name": "building-envelope", "mesh": 0},
            {"name": "surface-bay1-roof-left", "children": [3]},
            {"name": "roof-cladding", "mesh": 1},
        ],
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0}}]},
            {"primitives": [{"attributes": {"POSITION": 1}}]},
        ],
        "accessors": [
            {"type": "VEC3", "min": [-1.5, 0, 0], "max": [1.5, 5, 3]},
            {"type": "VEC3", "min": [-1.5, 0, 2.4], "max": [1.5, 5, 3]},
        ],
    }


def _glb(document: dict) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    total = 12 + 8 + len(encoded)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
    )


def test_model_site_dimensions_uses_scene_tree_scale_and_roof_components() -> None:
    result = model_site_dimensions("glb", _glb(_scene()))

    assert result is not None
    assert result["footprint_length_m"] == 5.0
    assert result["footprint_width_m"] == 3.0
    assert result["overall_height_m"] == 3.0
    assert result["roof_eave_height_m"] == 2.4
    assert result["roof_ridge_height_m"] == 3.0
    assert result["reference_height_m"] == 2.7
    assert "authored glTF tree" in result["reference_height_basis"]


def test_model_site_dimensions_prefers_compile_time_analytic_metadata() -> None:
    document = _scene()
    document["extras"] = {
        "tertiusModelGeometry": {
            "schema_version": "tertius.model-site-dimensions.v1",
            "footprint_length_m": 5.2,
            "footprint_width_m": 3.1,
            "overall_height_m": 3.0,
            "reference_height_m": 2.7,
            "reference_height_basis": "Build123D analytic roof bounds",
            "source": "compiled Build123D assembly",
        }
    }

    assert model_site_dimensions("gltf", json.dumps(document).encode("utf-8")) == document["extras"][
        "tertiusModelGeometry"
    ]


def test_model_site_dimensions_ignores_non_gltf_artifacts() -> None:
    assert model_site_dimensions("stl", b"solid fixture") is None
