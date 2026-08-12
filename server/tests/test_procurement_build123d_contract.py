"""Real Build123D -> GLB -> Procurement conformance tests.

These tests deliberately exercise the same compile and analysis boundary used by
the application.  Synthetic source dictionaries and hand-authored visual trees
cannot prove that Build123D operations preserve the metadata Procurement needs.

Every case is a hard contract. A regression in any supported Build123D operation
must fail this suite rather than being repaired in an individual design module.

Renderer colour propagation is outside this suite.  It has a separate WebGL/GLB
contract and must not be confused with Procurement identity or quantity.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.compile_sandbox import CompileSandboxResult, run_compile_sandbox
from core.procurement_analysis import (
    analyze_design_sources,
    analyze_gltf_tree,
    build_procurement_analysis,
)
from workflows.extus.extus_server import gltf_to_scene_tree


@dataclass(frozen=True)
class CompiledProcurement:
    result: CompileSandboxResult
    gltf: dict
    analysis: dict


def _write_project(project_dir: Path, files: dict[str, str]) -> None:
    for relative_path, source in files.items():
        path = project_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.strip() + "\n", encoding="utf-8")


def _read_glb_json(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, total_length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"
    assert version == 2
    assert total_length == len(data)
    chunk_length, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    return json.loads(data[20 : 20 + chunk_length].decode("utf-8"))


def _compile_procurement(project_dir: Path, files: dict[str, str]) -> CompiledProcurement:
    _write_project(project_dir, files)
    result = run_compile_sandbox(
        project_dir,
        "glb",
        quality="sketch",
        timeout_seconds=60,
    )
    assert result.success is True, result.error
    assert result.output_path is not None

    gltf = _read_glb_json(result.output_path)
    source = analyze_design_sources(files)
    visual = analyze_gltf_tree(gltf_to_scene_tree(gltf))
    analysis = build_procurement_analysis(source, visual)
    return CompiledProcurement(result=result, gltf=gltf, analysis=analysis)


def _requirements(compiled: CompiledProcurement, part_number: str) -> list[dict]:
    return [
        requirement
        for requirement in compiled.analysis["requirements"]
        if requirement.get("part_number") == part_number
    ]


def _bom_nodes(compiled: CompiledProcurement, part_number: str) -> list[dict]:
    return [
        node
        for node in compiled.gltf.get("nodes", [])
        if node.get("extras", {}).get("tertiusBom", {}).get("part_number")
        == part_number
    ]


def _rolled_up_quantity(requirements: list[dict]) -> float:
    return sum(
        float(requirement.get("rolled_up_quantity", requirement.get("quantity", 0)))
        for requirement in requirements
    )


def test_imported_shape_wrapper_survives_placement_and_drives_procurement(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "parts.py": """
from dataclasses import dataclass
import build123d as bd
from tertius_bom import bom_item

@dataclass(frozen=True)
class CataloguePart:
    shape: bd.Shape

@bom_item
def catalogue_plate(
    part_number="PLATE-WRAPPED",
    quantity=1,
    unit="each",
    length_mm=120,
    width_mm=80,
    thickness_mm=6,
    material="steel",
):
    part = bd.Solid.make_box(width_mm, length_mm, thickness_mm)
    part.label = part_number
    return CataloguePart(shape=part)
""",
            "model.py": """
import build123d as bd
from parts import catalogue_plate

placed = catalogue_plate().shape.moved(bd.Location((50, 0, 0)))
building = bd.Compound(children=[placed], label="Wrapped catalogue assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    requirements = _requirements(compiled, "PLATE-WRAPPED")
    assert len(requirements) == 1
    assert requirements[0]["quantity_source"] == "visual_instances"
    assert requirements[0]["quantity"] == 1
    assert requirements[0]["material"] == "steel"
    assert requirements[0]["dimensions"] == {
        "length_mm": 120,
        "thickness_mm": 6,
        "width_mm": 80,
    }


def test_bom_false_assembly_excludes_parent_but_keeps_real_child_items(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_bolt(part_number="BOLT-M8", quantity=1, unit="each", diameter_mm=8):
    bolt = bd.Cylinder(radius=diameter_mm / 2, height=30)
    bolt.label = part_number
    return bolt

@bom_item
def make_nut(part_number="NUT-M8", quantity=1, unit="each", diameter_mm=8):
    nut = bd.Cylinder(radius=8, height=6)
    nut.label = part_number
    return nut

bolt = make_bolt()
nut = make_nut().moved(bd.Location((0, 0, 30)))
connection = bd.Compound(children=[bolt, nut], label="Knee connection")
connection.tertius_bom = {"bom": False}
building = bd.Compound(
    children=[connection.moved(bd.Location((100, 0, 0)))],
    label="Connection assembly",
)
""",
            "design.py": """
from model import building
""",
        },
    )

    assert _rolled_up_quantity(_requirements(compiled, "BOLT-M8")) == 1
    assert _rolled_up_quantity(_requirements(compiled, "NUT-M8")) == 1
    assert not [
        requirement
        for requirement in compiled.analysis["requirements"]
        if requirement.get("component_label") == "Knee connection"
    ]


def test_repeated_placements_are_counted_from_real_visual_instances(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_stud(part_number="STUD-C100", quantity=1, unit="each", length_mm=2400):
    stud = bd.Solid.make_box(100, 40, length_mm)
    stud.label = part_number
    return stud

prototype = make_stud()
placements = [
    prototype.moved(bd.Location((offset, 0, 0)))
    for offset in (0, 600, 1200)
]
building = bd.Compound(children=placements, label="Repeated stud wall")
""",
            "design.py": """
from model import building
""",
        },
    )

    requirements = _requirements(compiled, "STUD-C100")
    assert _rolled_up_quantity(requirements) == 3
    assert {requirement["quantity_source"] for requirement in requirements} == {
        "visual_instances"
    }
    assert {requirement["dimensions"]["length_mm"] for requirement in requirements} == {
        2400
    }


def test_decorated_multi_solid_kit_is_one_procurement_set(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_bracket_kit(part_number="KNEE-KIT-01", quantity=1, unit="set"):
    left = bd.Solid.make_box(60, 6, 80)
    right = bd.Solid.make_box(6, 60, 80).moved(bd.Location((54, 0, 0)))
    kit = bd.Compound(children=[left, right], label=part_number)
    return kit

building = bd.Compound(children=[make_bracket_kit()], label="Kit assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    requirements = _requirements(compiled, "KNEE-KIT-01")
    assert len(requirements) == 1
    assert requirements[0]["unit"] == "set"
    assert requirements[0]["quantity"] == 1
    assert not [
        diagnostic
        for diagnostic in compiled.analysis["diagnostics"]
        if diagnostic.get("code") == "auto_placeholder_part_number"
        and "KNEE-KIT-01" in str(diagnostic)
    ]


def test_same_part_number_with_distinct_lengths_retains_dimensional_identity(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_member(part_number="C10012", quantity=1, unit="each", length_mm=100):
    member = bd.Solid.make_box(40, 20, length_mm)
    member.label = part_number
    return member

short = make_member(length_mm=100)
long = make_member(length_mm=200).moved(bd.Location((100, 0, 0)))
building = bd.Compound(children=[short, long], label="Variable length members")
""",
            "design.py": """
from model import building
""",
        },
    )

    requirements = _requirements(compiled, "C10012")
    assert len(requirements) == 2
    assert {
        requirement["dimensions"]["length_mm"] for requirement in requirements
    } == {100, 200}
    assert _rolled_up_quantity(requirements) == 2


@pytest.mark.parametrize("container_expression", ["[left, right]", "(left, right,)"])
def test_decorator_attaches_metadata_to_list_and_tuple_results(
    tmp_path,
    container_expression,
):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": f"""
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_base_pair(part_number="BASE-PAIR", quantity=1, unit="each"):
    left = bd.Solid.make_box(40, 40, 6)
    left.label = "BASE-PAIR-L"
    right = bd.Solid.make_box(40, 40, 6).moved(bd.Location((100, 0, 0)))
    right.label = "BASE-PAIR-R"
    return {container_expression}

bases = make_base_pair()
building = bd.Compound(children=list(bases), label="Base pair assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    requirements = _requirements(compiled, "BASE-PAIR")
    assert _rolled_up_quantity(requirements) == 2
    assert len(_bom_nodes(compiled, "BASE-PAIR")) >= 2


def test_multiple_compile_roots_keep_their_own_procurement_metadata(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "design.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_plate(part_number, length_mm):
    plate = bd.Solid.make_box(20, 10, length_mm)
    plate.label = part_number
    return plate

left = make_plate("MULTI-LEFT", 100)
right = make_plate("MULTI-RIGHT", 200).moved(bd.Location((100, 0, 0)))
""",
        },
    )

    left = _requirements(compiled, "MULTI-LEFT")
    right = _requirements(compiled, "MULTI-RIGHT")
    assert len(left) == 1
    assert len(right) == 1
    assert left[0]["dimensions"] == {"length_mm": 100}
    assert right[0]["dimensions"] == {"length_mm": 200}


@pytest.mark.parametrize(
    ("operation", "placement_expression"),
    [
        ("moved", "source.moved(bd.Location((50, 0, 0)))"),
        ("located", "source.located(bd.Location((50, 0, 0)))"),
        ("copied", "copy.copy(source)"),
    ],
)
def test_common_shape_placement_and_copy_operations_preserve_identity(
    tmp_path,
    operation,
    placement_expression,
):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": f"""
import copy
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_plate(part_number="PLACED-PLATE", quantity=1, unit="each"):
    plate = bd.Solid.make_box(40, 20, 6)
    plate.label = part_number
    return plate

source = make_plate()
placed = {placement_expression}
placed.label = "PLACED-PLATE-{operation}"
building = bd.Compound(children=[placed], label="Placement operation assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    requirements = _requirements(compiled, "PLACED-PLATE")
    assert len(requirements) == 1
    assert requirements[0]["quantity_source"] == "visual_instances"
    assert _bom_nodes(compiled, "PLACED-PLATE")


def test_explicit_item_quantity_rolls_up_against_visual_instances(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_bolt_group(part_number="BOLT-GROUP", quantity=4, unit="each"):
    group_marker = bd.Cylinder(radius=4, height=30)
    group_marker.label = part_number
    return group_marker

left = make_bolt_group()
right = make_bolt_group().moved(bd.Location((100, 0, 0)))
building = bd.Compound(children=[left, right], label="Two bolt groups")
""",
            "design.py": """
from model import building
""",
        },
    )

    requirements = _requirements(compiled, "BOLT-GROUP")
    assert _rolled_up_quantity(requirements) == 8
    assert {requirement["quantity"] for requirement in requirements} == {4}


def test_decorator_attaches_metadata_to_dictionary_results(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_bases(part_number="DICT-BASE", quantity=1, unit="each"):
    return {
        "left": bd.Solid.make_box(40, 40, 6),
        "right": bd.Solid.make_box(40, 40, 6).moved(bd.Location((100, 0, 0))),
    }

bases = make_bases()
for key, base in bases.items():
    base.label = f"DICT-BASE-{{key}}"
building = bd.Compound(children=list(bases.values()), label="Dictionary base assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    assert _rolled_up_quantity(_requirements(compiled, "DICT-BASE")) == 2


def test_decorator_preserves_extended_procurement_fields(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_member(
    part_number="EXTENDED-C100",
    quantity=1,
    unit="each",
    cut_length_mm=2380,
    ordered_length_mm=2400,
    manufacturer="Example Steel",
):
    member = bd.Solid.make_box(40, 20, cut_length_mm)
    member.label = part_number
    return member

building = bd.Compound(children=[make_member()], label="Extended metadata assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    nodes = _bom_nodes(compiled, "EXTENDED-C100")
    assert nodes
    metadata = nodes[0]["extras"]["tertiusBom"]
    assert metadata["manufacturer"] == "Example Steel"
    assert metadata["dimensions"] == {
        "cut_length_mm": 2380,
        "ordered_length_mm": 2400,
    }


def test_bom_component_links_shape_wrapper_to_visual_node(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
from dataclasses import dataclass
import build123d as bd
from tertius_bom import bom_component, requirement

@dataclass(frozen=True)
class CataloguePart:
    shape: bd.Shape

wrapped = CataloguePart(shape=bd.Solid.make_box(20, 10, 5))
bom_component(
    wrapped,
    id="wrapped-plate",
    role="plate",
    requirements=[requirement(part_number="COMPONENT-WRAPPER", quantity=1)],
)
building = bd.Compound(children=[wrapped.shape], label="Component wrapper assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    assert any(
        node.get("name") == "bom:wrapped-plate:plate"
        for node in compiled.gltf.get("nodes", [])
    )


def test_mirror_preserves_decorated_item_metadata(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_bracket(part_number="MIRROR-BRACKET", quantity=1, unit="each"):
    bracket = bd.Solid.make_box(40, 20, 6)
    bracket.label = part_number
    return bracket

mirrored = bd.mirror(make_bracket(), about=bd.Plane.YZ)
building = bd.Compound(children=[mirrored], label="Mirrored bracket assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    assert _bom_nodes(compiled, "MIRROR-BRACKET")


@pytest.mark.parametrize(
    "operation",
    ["union", "subtract", "intersect", "method_cut"],
)
def test_boolean_result_preserves_decorated_item_metadata(tmp_path, operation):
    expressions = {
        "union": "source + bd.Box(10, 10, 6).moved(bd.Location((35, 5, 0)))",
        "subtract": "source - bd.Box(10, 10, 40).moved(bd.Location((15, 5, 0)))",
        "intersect": "source & bd.Box(30, 20, 20)",
        "method_cut": "source.cut(bd.Box(10, 10, 40).moved(bd.Location((15, 5, 0))))",
    }
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": f"""
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_plate(part_number="BOOL-PLATE", quantity=1, unit="each"):
    plate = bd.Solid.make_box(40, 20, 6)
    plate.label = part_number
    return plate

source = make_plate()
result = {expressions[operation]}
result.label = "BOOL-PLATE-{operation}"
building = bd.Compound(children=[result], label="Boolean plate assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    assert _bom_nodes(compiled, "BOOL-PLATE")


def test_rotate_preserves_decorated_item_metadata(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_member(part_number="ROTATED-MEMBER", quantity=1, unit="each"):
    member = bd.Solid.make_box(80, 20, 6)
    member.label = part_number
    return member

rotated = make_member().rotate(bd.Axis.Z, 90)
building = bd.Compound(children=[rotated], label="Rotated member assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    assert _bom_nodes(compiled, "ROTATED-MEMBER")


def test_buildpart_add_preserves_decorated_item_metadata(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "model.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def make_insert(part_number="BUILDER-INSERT", quantity=1, unit="each"):
    insert = bd.Solid.make_box(30, 20, 6)
    insert.label = part_number
    return insert

source = make_insert()
with bd.BuildPart() as builder:
    bd.add(source)
result = builder.part
result.label = "Built insert"
building = bd.Compound(children=[result], label="Builder assembly")
""",
            "design.py": """
from model import building
""",
        },
    )

    assert _bom_nodes(compiled, "BUILDER-INSERT")


def test_top_level_shape_wrapper_is_a_compile_root(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "design.py": """
from dataclasses import dataclass
import build123d as bd
from tertius_bom import bom_item

@dataclass(frozen=True)
class CataloguePart:
    shape: bd.Shape

@bom_item
def make_part(part_number="SHAPE-WRAPPER", quantity=1, unit="each"):
    part = bd.Solid.make_box(20, 10, 5)
    part.label = part_number
    return CataloguePart(shape=part)

model = make_part()
""",
        },
    )

    assert _bom_nodes(compiled, "SHAPE-WRAPPER")


def test_compile_emits_bom_manifest_with_runtime_diagnostics(tmp_path):
    compiled = _compile_procurement(
        tmp_path,
        {
            "design.py": """
import build123d as bd
from tertius_bom import bom_item

@bom_item
def optional_part(enabled=False, part_number="OPTIONAL-PART"):
    if enabled:
        return bd.Solid.make_box(20, 10, 5)
    return None

ignored = optional_part()
building = bd.Compound(children=[bd.Solid.make_box(5, 5, 5)], label="Manifest assembly")
""",
        },
    )

    manifest_path = tmp_path / "tertius-bom-manifest.json"
    assert manifest_path.is_file()
    assert compiled.result.bom_manifest_path == manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert any(
        diagnostic.get("code") == "bom_item_metadata_not_attached"
        for diagnostic in manifest["diagnostics"]
    )
    assert compiled.result.output_path is not None
