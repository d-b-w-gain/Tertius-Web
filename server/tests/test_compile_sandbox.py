import os
import json
import struct
import time
from pathlib import Path

from core.compile_sandbox import run_compile_sandbox


def test_compile_sandbox_rejects_unsupported_export_format_before_spawn(tmp_path):
    (tmp_path / "design.py").write_text(
        """
from pathlib import Path

Path("spawned.txt").write_text("spawned", encoding="utf-8")
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "../stl", timeout_seconds=5)

    assert result.success is False
    assert result.error == "Unsupported export format: ../stl"
    assert not (tmp_path / "spawned.txt").exists()


def test_compile_sandbox_allows_timus_views_export(tmp_path, monkeypatch):
    spawned = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout):
            spawned["command"] = True
            (tmp_path / "output.timus_views").write_text("{}", encoding="utf-8")
            return "", ""

    monkeypatch.setattr("core.compile_sandbox.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    result = run_compile_sandbox(tmp_path, "timus_views", timeout_seconds=5)

    assert result.success is True
    assert spawned["command"] is True
    assert result.output_path == tmp_path / "output.timus_views"


def test_compile_sandbox_allows_timus_bounds_export(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB_PASSWORD", "super-secret")
    (tmp_path / "helper.py").write_text("WIDTH = 30\n", encoding="utf-8")
    (tmp_path / "design.py").write_text(
        """
from pathlib import Path
import os
import build123d as bd
from helper import WIDTH

Path("leaked-secret.txt").write_text(os.environ.get("APP_DB_PASSWORD", ""), encoding="utf-8")
part = bd.Box(WIDTH, 20, 10)
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "timus_bounds", timeout_seconds=30)

    assert result.success is True, result.error
    assert result.output_path == tmp_path / "output.timus_bounds"
    assert json.loads(result.output_path.read_text(encoding="utf-8")) == {"max_dim": 30.0}
    assert (tmp_path / "leaked-secret.txt").read_text(encoding="utf-8") == ""


def test_compile_sandbox_preserves_build123d_part_color_in_glb(tmp_path):
    (tmp_path / "design.py").write_text(
        """
import build123d as bd

part = bd.Solid.make_box(20, 20, 20)
part.label = "Red test cube"
part.color = bd.Color(1.0, 0.0, 0.0, 1.0)

building = bd.Compound(children=[part], label="Colour test assembly")
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "glb", timeout_seconds=30)

    assert result.success is True, result.error
    assert result.output_path is not None
    data = result.output_path.read_bytes()
    magic, _version, _length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    gltf_json = json.loads(data[20 : 20 + chunk_len].decode("utf-8"))

    base_colors = [
        material.get("pbrMetallicRoughness", {}).get("baseColorFactor")
        for material in gltf_json.get("materials", [])
    ]
    assert [1.0, 0.0, 0.0, 1.0] in base_colors
    assert any(material.get("extras", {}).get("tertiusAuthoredColor") is True for material in gltf_json["materials"])


def test_compile_sandbox_marks_build123d_alpha_color_as_blended_in_glb(tmp_path):
    (tmp_path / "design.py").write_text(
        """
import build123d as bd

part = bd.Solid.make_box(20, 20, 20)
part.label = "Glass test cube"
part.color = bd.Color(0.25, 0.72, 1.0, 0.35)

building = bd.Compound(children=[part], label="Alpha colour test assembly")
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "glb", timeout_seconds=30)

    assert result.success is True, result.error
    assert result.output_path is not None
    data = result.output_path.read_bytes()
    magic, _version, _length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    gltf_json = json.loads(data[20 : 20 + chunk_len].decode("utf-8"))

    blended_materials = []
    for material in gltf_json.get("materials", []):
        base_color = material.get("pbrMetallicRoughness", {}).get("baseColorFactor")
        if isinstance(base_color, list) and len(base_color) >= 4 and abs(base_color[3] - 0.35) < 1e-6:
            blended_materials.append(material)
    assert blended_materials
    assert all(material.get("alphaMode") == "BLEND" for material in blended_materials)
    assert all(material.get("extras", {}).get("tertiusAuthoredColor") is True for material in blended_materials)


def test_compile_sandbox_exports_bom_item_metadata_in_glb_node_extras(tmp_path):
    (tmp_path / "design.py").write_text(
        """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_plate(part_number="PLATE-001", quantity=1, unit="each", length_mm=120, width_mm=80, material="steel"):
    part = bd.Solid.make_box(width_mm, length_mm, 6)
    part.label = part_number
    return part

plate = make_plate()
building = bd.Compound(children=[plate], label="BoM test assembly")
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "glb", timeout_seconds=30)

    assert result.success is True, result.error
    assert result.output_path is not None
    data = result.output_path.read_bytes()
    magic, _version, _length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    gltf_json = json.loads(data[20 : 20 + chunk_len].decode("utf-8"))

    bom_nodes = [
        node
        for node in gltf_json.get("nodes", [])
        if node.get("extras", {}).get("tertiusBom", {}).get("part_number") == "PLATE-001"
    ]
    assert bom_nodes
    metadata = bom_nodes[0]["extras"]["tertiusBom"]
    assert metadata["quantity"] == 1
    assert metadata["unit"] == "each"
    assert metadata["material"] == "steel"
    assert metadata["dimensions"] == {"length_mm": 120, "width_mm": 80}


def test_compile_sandbox_preserves_labels_inside_moved_compound_in_glb(tmp_path):
    (tmp_path / "design.py").write_text(
        """
import build123d as bd

child = bd.Solid.make_box(20, 10, 5)
child.label = "CHILD-BRACKET"

fastener = bd.Solid.make_box(4, 4, 12)
fastener.label = "CHILD-FASTENER"

subassembly = bd.Compound(children=[child, fastener])
building = bd.Compound(children=[
    subassembly.moved(bd.Location((50, 0, 0))),
], label="Moved compound assembly")
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "glb", timeout_seconds=30)

    assert result.success is True, result.error
    assert result.output_path is not None
    data = result.output_path.read_bytes()
    magic, _version, _length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    gltf_json = json.loads(data[20 : 20 + chunk_len].decode("utf-8"))

    node_names = {node.get("name") for node in gltf_json.get("nodes", [])}
    assert "CHILD-BRACKET" in node_names
    assert "CHILD-FASTENER" in node_names


def test_compile_sandbox_marks_compound_material_colors_as_authored_in_glb(tmp_path):
    (tmp_path / "design.py").write_text(
        """
import build123d as bd

manor_red = bd.Color(0.3686, 0.1608, 0.1569, 1.0)

left = bd.Box(40, 30, 20).moved(bd.Location((-30, 0, 0)))
left.color = manor_red

right = bd.Cylinder(radius=14, height=24).moved(bd.Location((30, 0, 0)))
right.color = manor_red

building = bd.Compound([left, right], label="two child-coloured solids real compound")
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "glb", timeout_seconds=30)

    assert result.success is True, result.error
    assert result.output_path is not None
    data = result.output_path.read_bytes()
    magic, _version, _length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    gltf_json = json.loads(data[20 : 20 + chunk_len].decode("utf-8"))

    coloured_materials = [
        material
        for material in gltf_json.get("materials", [])
        if material.get("pbrMetallicRoughness", {}).get("baseColorFactor") == [
            0.3686000108718872,
            0.1607999950647354,
            0.15690000355243683,
            1.0,
        ]
    ]
    assert coloured_materials
    assert all(material.get("extras", {}).get("tertiusAuthoredColor") is True for material in coloured_materials)


def test_compile_sandbox_compiles_default_purlin_to_glb(tmp_path):
    default_purlin = Path("server/workflows/intus/templates/default_purlin.py").read_text(encoding="utf-8")
    (tmp_path / "design.py").write_text(default_purlin, encoding="utf-8")

    result = run_compile_sandbox(tmp_path, "glb", timeout_seconds=60)

    assert result.success is True, result.error
    assert result.output_path is not None
    data = result.output_path.read_bytes()
    magic, _version, _length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"


def test_compile_sandbox_does_not_expose_worker_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB_PASSWORD", "super-secret")
    (tmp_path / "design.py").write_text(
        """
from pathlib import Path
import os

Path("leaked-secret.txt").write_text(os.environ.get("APP_DB_PASSWORD", ""), encoding="utf-8")
raise RuntimeError("stop after leak attempt")
""",
        encoding="utf-8",
    )

    run_compile_sandbox(tmp_path, "stl", timeout_seconds=5)

    assert (tmp_path / "leaked-secret.txt").read_text(encoding="utf-8") == ""


def test_compile_sandbox_timeout_kills_spawned_children(tmp_path):
    marker = tmp_path / "child-survived.txt"
    child_code = (
        "import pathlib, time; "
        "time.sleep(15); "
        f"pathlib.Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    (tmp_path / "design.py").write_text(
        f"""
import subprocess
import sys
import time

print("DESIGN_STARTED", flush=True)
subprocess.Popen([sys.executable, "-c", {child_code!r}])
time.sleep(30)
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "stl", timeout_seconds=10)
    time.sleep(6)

    assert result.success is False
    assert "DESIGN_STARTED" in result.stdout
    assert not marker.exists()
