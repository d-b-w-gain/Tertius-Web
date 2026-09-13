from __future__ import annotations

import json
from pathlib import Path

from core.component_replay import (
    component_geometry_cache_key,
    definition_only_source,
    load_replay_calls,
    prepare_definition_only_project,
    replay_selected_components,
    validate_selected_replay_dependencies,
)


def test_definition_only_source_skips_visual_constructor_without_source_snippet() -> None:
    source = """
import build123d as bd
WIDTH = 100

def make_roof(width):
    return bd.Box(width, 10, 5)

building = make_roof(WIDTH)
"""

    definition_source, skipped = definition_only_source(
        source,
        "design.py",
        relative_file="design.py",
        visual_function_names={"make_roof"},
    )

    assert "def make_roof" in definition_source
    assert "WIDTH = 100" in definition_source
    assert "building = make_roof" not in definition_source
    assert skipped[0].kind == "Assign"
    assert skipped[0].diagnostic_code == "component_replay_skip_visual_constructor"
    assert skipped[0].assigned_names == ("building",)
    assert not hasattr(skipped[0], "source")


def test_definition_only_source_keeps_non_visual_top_level_config_with_provenance() -> None:
    source = """
import math
bay_spacings = [2600.0, 2600.0]
portal_depth = sum(bay_spacings)
roof_pitch = 20.0
roof_cos = math.cos(math.radians(roof_pitch))

def make_roof(width):
    return width

building = shed_building(portal_depth)
"""

    definition_source, skipped = definition_only_source(
        source,
        "design.py",
        relative_file="design.py",
        visual_function_names={"shed_building"},
    )

    assert "portal_depth = sum(bay_spacings)" in definition_source
    assert "roof_cos = math.cos" in definition_source
    assert "building = shed_building" not in definition_source
    assert [item.line for item in skipped] == [11]


def test_definition_only_source_skips_unknown_top_level_calls_even_with_provenance() -> None:
    source = """
import math
import os

bay_spacings = [2600.0, 2600.0]
portal_depth = sum(bay_spacings)
roof_cos = math.cos(math.radians(20.0))
config = load_file()
os.remove("important.txt")

def make_roof(width):
    return width

building = make_roof(portal_depth)
"""

    definition_source, skipped = definition_only_source(
        source,
        "design.py",
        relative_file="design.py",
        visual_function_names={"make_roof"},
    )

    assert "portal_depth = sum(bay_spacings)" in definition_source
    assert "roof_cos = math.cos" in definition_source
    assert "config = load_file" not in definition_source
    assert "os.remove" not in definition_source
    assert [item.diagnostic_code for item in skipped] == [
        "component_replay_skip_unsafe_top_level_call",
        "component_replay_skip_unsupported_top_level",
        "component_replay_skip_visual_constructor",
    ]


def test_definition_only_source_keeps_dataclass_config_constructor_but_skips_side_effects() -> None:
    source = """
from dataclasses import dataclass
from pathlib import Path
import build123d as bd

@dataclass(frozen=True)
class TopHatSection:
    height: float

TopHat50 = TopHatSection(height=50.0)
_LIBRARY_FILE = Path("catalog.json")
Path("side-effect.txt").write_text("bad", encoding="utf-8")
panel = bd.Box(10, 10, 10)

def make_top_hat(section=TopHat50):
    return section.height
"""

    definition_source, skipped = definition_only_source(
        source,
        "tophat.py",
        relative_file="tophat.py",
        visual_function_names={"make_top_hat"},
    )

    assert "TopHat50 = TopHatSection" in definition_source
    assert '_LIBRARY_FILE = Path("catalog.json")' in definition_source
    assert "write_text" not in definition_source
    assert "panel = bd.Box" not in definition_source
    assert [item.diagnostic_code for item in skipped] == [
        "component_replay_skip_unsupported_top_level",
        "component_replay_skip_unsafe_top_level_call",
    ]


def test_replay_calls_reject_unresolved_parameters() -> None:
    source_map = {
        "source_calls": {
            "call_1": {
                "function": "make_roof",
                "qualified_function": "design.make_roof",
                "returned_visual": True,
                "parameters": {"width": {"raw": "WIDTH"}},
            }
        }
    }

    try:
        load_replay_calls(source_map, function="make_roof")
    except ValueError as exc:
        assert "parameters are unresolved: width" in str(exc)
    else:
        raise AssertionError("unresolved parameters should make replay unsafe")


def test_cache_key_ignores_instance_identity_and_placement() -> None:
    baseline = component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"width": 3000, "colour": "red"},
        explicit_dependencies={"profile": "corrugated-v1"},
        function_source_sha256="roof-source",
        runtime_versions={"python": "3.14", "build123d": "0.9"},
        export_settings={"format": "glb", "quality": "rough"},
        placement={"x": 0, "y": 0, "z": 0},
        instance_id="roof.cladding.main",
    )

    moved = component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"colour": "red", "width": 3000},
        explicit_dependencies={"profile": "corrugated-v1"},
        function_source_sha256="roof-source",
        runtime_versions={"build123d": "0.9", "python": "3.14"},
        export_settings={"quality": "rough", "format": "glb"},
        placement={"x": 800, "y": 0, "z": 0},
        instance_id="roof.cladding.shifted",
    )

    assert moved == baseline


def test_skipped_top_level_dependency_makes_replay_ineligible() -> None:
    source = """
import build123d as bd

def build_panel():
    return bd.Box(10, 10, 10)

def make_panel():
    return panel

panel = build_panel()
building = make_panel()
"""
    definition_source, skipped = definition_only_source(
        source,
        "design.py",
        relative_file="design.py",
        visual_function_names={"build_panel", "make_panel"},
    )
    calls = load_replay_calls(
        {
            "source_calls": {
                "call_1": {
                    "function": "make_panel",
                    "qualified_function": "design.make_panel",
                    "definition_file": "design.py",
                    "definition_line": 4,
                    "returned_visual": True,
                    "parameters": {},
                }
            }
        }
    )

    assert (
        validate_selected_replay_dependencies(
            definition_source=definition_source,
            definition_filename="design.py",
            calls=calls,
            skipped=skipped,
        )
        == "component_replay_ineligible_skipped_dependency"
    )


def test_replay_selected_component_skips_top_level_full_build(tmp_path: Path) -> None:
    (tmp_path / "design.py").write_text(
        """
from pathlib import Path
import build123d as bd

WIDTH = 20

def make_roof(width):
    part = bd.Box(width, 10, 5)
    part.label = "roof"
    return part

def shed_building():
    Path("full-build-ran.txt").write_text("yes", encoding="utf-8")
    return make_roof(WIDTH)

building = shed_building()
""",
        encoding="utf-8",
    )
    source_map = {
        "source_calls": {
            "call_1": {
                "function": "make_roof",
                "qualified_function": "design.make_roof",
                "definition_file": "design.py",
                "definition_line": 7,
                "returned_visual": True,
                "parameters": {"width": {"resolved": 20}},
            },
            "call_2": {
                "function": "shed_building",
                "qualified_function": "design.shed_building",
                "definition_file": "design.py",
                "definition_line": 12,
                "returned_visual": True,
                "parameters": {},
            },
        }
    }

    result = replay_selected_components(
        tmp_path,
        source_map,
        output_dir=tmp_path / "replay",
        export_format="glb",
        quality="rough",
        function="make_roof",
        timeout_seconds=30,
    )

    assert result.success is True, result.error
    assert result.diagnostic_code == "component_replay_succeeded"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.component_id == "inferred:design.make_roof:design.py:7"
    assert artifact.byte_size > 0
    assert (tmp_path / "replay" / "component-artifacts" / artifact.relative_artifact_path).exists()
    assert not (tmp_path / "full-build-ran.txt").exists()
    assert not (tmp_path / "replay" / "definition-project" / "full-build-ran.txt").exists()
    assert json.loads(result.stdout)["ok"] is True


def test_replay_selected_imported_component_uses_definition_only_helper_module(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text(
        """
from pathlib import Path
import build123d as bd

Path("helper-import-ran.txt").write_text("yes", encoding="utf-8")

def make_roof(width):
    part = bd.Box(width, 10, 5)
    part.label = "imported roof"
    return part

helper_building = make_roof(5)
""",
        encoding="utf-8",
    )
    (tmp_path / "design.py").write_text(
        """
from helper import make_roof

building = make_roof(20)
""",
        encoding="utf-8",
    )
    source_map = {
        "source_calls": {
            "call_1": {
                "function": "make_roof",
                "qualified_function": "helper.make_roof",
                "definition_file": "helper.py",
                "definition_line": 7,
                "returned_visual": True,
                "parameters": {"width": {"resolved": 20}},
            },
        }
    }

    result = replay_selected_components(
        tmp_path,
        source_map,
        output_dir=tmp_path / "replay",
        export_format="glb",
        quality="rough",
        function="helper.make_roof",
        timeout_seconds=30,
    )

    assert result.success is True, result.error
    assert len(result.artifacts) == 1
    assert result.artifacts[0].component_id == "inferred:helper.make_roof:helper.py:7"
    skipped_files = {item.file for item in result.skipped_top_level}
    assert skipped_files == {"design.py", "helper.py"}
    assert not (tmp_path / "helper-import-ran.txt").exists()
    assert not (tmp_path / "replay" / "definition-project" / "helper-import-ran.txt").exists()


def test_prepare_definition_only_project_transforms_imported_modules(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text(
        """
from pathlib import Path
import build123d as bd

Path("helper-import-ran.txt").write_text("yes", encoding="utf-8")

def make_roof(width):
    return bd.Box(width, 10, 5)

helper_building = make_roof(5)
""",
        encoding="utf-8",
    )
    (tmp_path / "design.py").write_text(
        """
from helper import make_roof

building = make_roof(20)
""",
        encoding="utf-8",
    )

    definition_sources, skipped = prepare_definition_only_project(
        tmp_path,
        tmp_path / "stage",
        visual_function_names={"make_roof", "helper.make_roof"},
    )

    assert "Path(\"helper-import-ran.txt\")" not in definition_sources["helper.py"]
    assert "helper_building = make_roof" not in definition_sources["helper.py"]
    assert "building = make_roof" not in definition_sources["design.py"]
    assert {(item.file, item.diagnostic_code) for item in skipped} == {
        ("helper.py", "component_replay_skip_unsupported_top_level"),
        ("helper.py", "component_replay_skip_visual_constructor"),
        ("design.py", "component_replay_skip_visual_constructor"),
    }
