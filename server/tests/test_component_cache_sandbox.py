"""Incremental component compile through the real compile sandbox (#312/#313).

Each test compiles a small Build123D design several times against one
component cache folder, the way consecutive compile jobs share the worker's
tenant-scoped store.
"""

from __future__ import annotations

import json
import re
import shutil
import struct

from core.compile_sandbox import run_compile_sandbox

PART_NUMBER = re.compile(r"^[A-HJKMNP-TV-Z]{3}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{5}$")

DESIGN = '''
import build123d as bd
from cadcache import part

ROOF_COLOUR = "red"
WALL_COLOUR = "blue"


def thickness():
    return 5


@part
def roof(width, length):
    r = bd.Box(width, length, thickness())
    r.label = "roof " + ROOF_COLOUR
    r.tertius_bom = {"colour": ROOF_COLOUR}
    return r


@part
def wall(length, height):
    w = bd.Box(5, length, height)
    w.label = "wall " + WALL_COLOUR
    return w


model = bd.Compound(
    children=[
        roof(400, 600).moved(bd.Location((0, 0, 300))),
        wall(600, 300).moved(bd.Location((-200, 0, 150))),
        wall(600, 300).moved(bd.Location((200, 0, 150))),
    ],
    label="mini shed",
)
'''


def compile_design(tmp_path, source, cache_dir, export_format="glb", name="run"):
    project = tmp_path / name
    if project.exists():
        shutil.rmtree(project)
    project.mkdir()
    (project / "design.py").write_text(source, encoding="utf-8")
    result = run_compile_sandbox(
        project,
        export_format,
        timeout_seconds=300,
        component_cache_dir=cache_dir,
    )
    assert result.success is True, result.error
    report = json.loads(result.component_report_path.read_text(encoding="utf-8"))
    return result, report


def statuses(report):
    return {item["component"]: item["cache_status"] for item in report["components"]}


def part_numbers(report):
    return {item["component"]: item["part_number"] for item in report["components"]}


def glb_json(path):
    data = path.read_bytes()
    length, kind = struct.unpack("<I4s", data[12:20])
    assert kind == b"JSON"
    return json.loads(data[20 : 20 + length])


def test_unchanged_and_unrelated_edits_hit_and_related_edit_rebuilds_one_component(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    _, cold = compile_design(tmp_path, DESIGN, cache_dir)
    assert statuses(cold) == {"roof": "built", "wall": "built"}
    assert cold["counts"]["built"] == 2 and cold["counts"]["hit"] == 1
    numbers = part_numbers(cold)
    assert all(PART_NUMBER.match(number) for number in numbers.values())

    result, warm = compile_design(tmp_path, DESIGN, cache_dir)
    assert statuses(warm) == {"roof": "hit", "wall": "hit"}
    assert part_numbers(warm) == numbers

    # Hits keep their viewer metadata: part numbers, BoM attributes and
    # source navigation all survive the cache.
    gltf = glb_json(result.output_path)
    parts = [node["extras"]["tertiusPart"] for node in gltf["nodes"] if "tertiusPart" in node.get("extras", {})]
    assert {part["component"] for part in parts} == {"roof", "wall"}
    assert {part["partNumber"] for part in parts} == set(numbers.values())
    source_calls = gltf["extras"]["tertiusSourceMap"]["source_calls"]
    roof_calls = [call for call in source_calls.values() if call["function"] == "roof"]
    assert roof_calls and roof_calls[0]["returned_visual"] is True
    assert roof_calls[0]["component_part_number"] == numbers["roof"]
    assert roof_calls[0]["parameters"]["width"]["resolved"] == 400
    roof_nodes = [
        node for node in gltf["nodes"]
        if node.get("extras", {}).get("tertiusPart", {}).get("component") == "roof"
    ]
    assert roof_nodes[0]["extras"]["tertiusSourceCallIds"] == [roof_calls[0]["id"]]

    # Comments, formatting and globals no part reads are not inputs.
    unrelated = DESIGN.replace(
        'WALL_COLOUR = "blue"', 'WALL_COLOUR = "blue"  # cladding colour\nUNUSED = 3'
    )
    _, unrelated_report = compile_design(tmp_path, unrelated, cache_dir)
    assert statuses(unrelated_report) == {"roof": "hit", "wall": "hit"}

    # A global only the roof reads rebuilds the roof alone, and gives it a
    # new part number; the wall keeps its own.
    related = unrelated.replace('ROOF_COLOUR = "red"', 'ROOF_COLOUR = "green"')
    _, related_report = compile_design(tmp_path, related, cache_dir)
    assert statuses(related_report) == {"roof": "built", "wall": "hit"}
    assert part_numbers(related_report)["wall"] == numbers["wall"]
    assert part_numbers(related_report)["roof"] != numbers["roof"]

    # A refactor of a helper the roof calls rebuilds it, but its inputs are
    # unchanged, so its part number is too.
    refactor = related.replace("    return 5\n", "    value = 5\n    return value\n")
    _, refactor_report = compile_design(tmp_path, refactor, cache_dir)
    assert statuses(refactor_report) == {"roof": "built", "wall": "hit"}
    assert part_numbers(refactor_report) == part_numbers(related_report)


def test_cache_is_keyed_on_export_tessellation(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    compile_design(tmp_path, DESIGN, cache_dir, export_format="glb")
    _, step = compile_design(tmp_path, DESIGN, cache_dir, export_format="step")
    assert statuses(step) == {"roof": "built", "wall": "built"}
    _, stl = compile_design(tmp_path, DESIGN, cache_dir, export_format="stl")
    assert statuses(stl) == {"roof": "hit", "wall": "hit"}


MANAGED = '''
import build123d as bd
from cadcache import part
from tertius import (
    ConnectionDefinition,
    PortPlacement,
    ProcurementFacet,
    ProductDefinition,
    StructuralFacet,
    managed_component,
    physical_connection,
)

PANEL = ProductDefinition(
    key="test.panel",
    label="Panel",
    geometry={"kind": "panel"},
    procurement=ProcurementFacet(part_number="TEST-PANEL", unit="each"),
)
STUD = ProductDefinition(
    key="test.stud",
    label="Stud",
    geometry={"kind": "stud"},
    procurement=ProcurementFacet(part_number="TEST-STUD", unit="each"),
    port_families={"top": ["test-screwed"]},
)
CLEAT = ProductDefinition(
    key="test.cleat",
    label="Cleat",
    geometry={"kind": "cleat"},
    procurement=ProcurementFacet(part_number="TEST-CLEAT", unit="each"),
    structural=StructuralFacet(kind="connector", evidence_basis="Test cleat."),
)
SCREWED = ConnectionDefinition(
    key="test.screwed",
    label="Screwed cleat",
    family="test-screwed",
    transfers=("force", "shear"),
    analysis_model="pinned",
    maximum_port_offset_mm=5.0,
)


@part
def registered_panel(width):
    return managed_component(bd.Box(width, 10, 1), product=PANEL, mark="P1")


@part
def stud_pair(height):
    # Two components joined by a connection, all registered inside the part.
    studs = [
        managed_component(
            bd.Box(10, 10, height).moved(bd.Location((x, 100, height / 2))),
            product=STUD,
            mark=f"S{index}",
            ports={"top": PortPlacement((x, 100, height), (0, 0, 1))},
        )
        for index, x in enumerate((0, 3))
    ]
    cleat = managed_component(
        bd.Box(4, 4, 1).moved(bd.Location((1.5, 100, height))), product=CLEAT, mark="C1"
    )
    joint = physical_connection(
        bd.Compound(children=[cleat]),
        definition=SCREWED,
        ports=[stud.ports.top for stud in studs],
        connector_components=[cleat],
        mark="J1",
    )
    return bd.Compound(children=[*studs, joint])


@part
def plain_panel(width):
    return bd.Box(width, 10, 1)


@part
def framed(panel):
    # Includes a component registered outside the part: a hit could not
    # replay it, so this part must not be stored.
    return bd.Compound(children=[panel])


model = bd.Compound(children=[
    registered_panel(100),
    stud_pair(200),
    framed(managed_component(plain_panel(50).moved(bd.Location((0, 50, 0))), product=PANEL, mark="P2")),
])
'''


def test_registrations_replay_on_a_hit(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    compiled_runs = []
    for _ in range(2):
        result, report = compile_design(tmp_path, MANAGED, cache_dir, export_format="stl")
        compiled_runs.append(
            json.loads(result.artifact_paths["compiled_design"].read_text(encoding="utf-8"))
        )
    first, second = compiled_runs
    assert sorted(component["id"] for component in first["components"]) == [
        "C1", "P1", "P2", "S0", "S1",
    ]
    assert [connection["id"] for connection in first["connections"]] == ["J1"]
    # Replayed registrations give exactly the graph a full build gives.
    assert second == first
    components = {item["component"]: item for item in report["components"]}
    assert components["registered_panel"]["cache_status"] == "hit"
    assert components["stud_pair"]["cache_status"] == "hit"
    assert components["plain_panel"]["cache_status"] == "hit"
    assert components["framed"]["cache_status"] == "uncacheable"
    assert components["framed"]["diagnostic"] == (
        "component_replay_ineligible_foreign_registration"
    )


def test_without_a_cache_folder_parts_run_uncached(tmp_path):
    project = tmp_path / "plain"
    project.mkdir()
    (project / "design.py").write_text(DESIGN, encoding="utf-8")
    result = run_compile_sandbox(project, "stl", timeout_seconds=300)
    assert result.success is True, result.error
    report = json.loads(result.component_report_path.read_text(encoding="utf-8"))
    assert report["cache_enabled"] is False
    assert statuses(report) == {"roof": "uncached", "wall": "uncached"}
    assert not (project / ".cadcache").exists()
