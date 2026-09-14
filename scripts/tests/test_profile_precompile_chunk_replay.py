from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "spikes" / "profile_precompile_chunk_replay.py"
SPEC = importlib.util.spec_from_file_location("profile_precompile_chunk_replay", MODULE_PATH)
replay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = replay
assert SPEC.loader is not None
SPEC.loader.exec_module(replay)


def test_definition_only_source_skips_top_level_constructor_call() -> None:
    source = """
from build123d import Box
WIDTH = 100

def make_roof(width):
    return Box(width, 10, 5)

building = make_roof(WIDTH)
"""

    definition_source, skipped = replay.definition_only_source(source, "design.py")

    assert "def make_roof" in definition_source
    assert "WIDTH = 100" in definition_source
    assert "building = make_roof" not in definition_source
    assert skipped[0]["kind"] == "Assign"


def test_definition_only_source_keeps_non_visual_config_when_provenance_is_available() -> None:
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

    definition_source, skipped = replay.definition_only_source(source, "design.py", {"shed_building"})

    assert "portal_depth = sum(bay_spacings)" in definition_source
    assert "roof_cos = math.cos" in definition_source
    assert "building = shed_building" not in definition_source
    assert [item["line"] for item in skipped] == [11]


def test_extract_resolved_parameters_uses_provenance_trace_values() -> None:
    record = {
        "parameters": {
            "width": {"resolved": 100, "source": "literal"},
            "label": {"resolved": "roof"},
            "ignored": "not a trace",
        }
    }

    assert replay.extract_resolved_parameters(record) == {"width": 100, "label": "roof"}


def test_load_replay_calls_filters_visual_function(tmp_path: Path) -> None:
    source_map = tmp_path / "source-map.json"
    source_map.write_text(
        json.dumps(
            {
                "source_calls": {
                    "call_1": {
                        "function": "make_roof",
                        "definition_file": "design.py",
                        "definition_line": 4,
                        "returned_visual": True,
                        "parameters": {"width": {"resolved": 100}},
                    },
                    "call_2": {
                        "function": "helper",
                        "returned_visual": False,
                        "parameters": {},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    calls = replay.load_replay_calls(source_map, function="make_roof", call_id=None)

    assert len(calls) == 1
    assert calls[0].call_id == "call_1"
    assert calls[0].parameters == {"width": 100}


def test_load_replay_calls_accepts_manifest_source_map(tmp_path: Path) -> None:
    manifest = tmp_path / "chunk-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "chunks": [],
                "source_map": {
                    "source_calls": {
                        "call_1": {
                            "function": "make_roof",
                            "qualified_function": "helper.make_roof",
                            "definition_file": "helper.py",
                            "definition_line": 3,
                            "returned_visual": True,
                            "parameters": {"width": {"resolved": 100}},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    calls = replay.load_replay_calls(manifest, function="make_roof", call_id=None)

    assert calls[0].qualified_function == "helper.make_roof"


def test_load_replay_calls_rejects_unresolved_parameters(tmp_path: Path) -> None:
    source_map = tmp_path / "source-map.json"
    source_map.write_text(
        json.dumps(
            {
                "source_calls": {
                    "call_1": {
                        "function": "make_roof",
                        "returned_visual": True,
                        "parameters": {"width": {"raw": "WIDTH"}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    try:
        replay.load_replay_calls(source_map, function="make_roof", call_id=None)
    except ValueError as exc:
        assert "parameters are unresolved: width" in str(exc)
    else:
        raise AssertionError("unresolved parameters should make replay unsafe")


def test_replay_cache_key_changes_when_parameters_change() -> None:
    first = replay.ReplayCall("call_1", "make_roof", "design.make_roof", "design.py", 4, {"width": 100})
    second = replay.ReplayCall("call_1", "make_roof", "design.make_roof", "design.py", 4, {"width": 200})

    assert replay.replay_cache_key(first, "def make_roof(): pass", "rough") != replay.replay_cache_key(
        second,
        "def make_roof(): pass",
        "rough",
    )


def test_component_geometry_cache_key_ignores_unrelated_source_and_placement_edits() -> None:
    baseline = replay.component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"width": 3000, "colour": "red"},
        explicit_dependencies={"profile": "corrugated-v1"},
        function_source_sha256="roof-source",
        helper_source_sha256=["roof-helper"],
        runtime_versions={"python": "3.12", "build123d": "0.9"},
        export_settings={"format": "glb", "quality": "rough"},
        placement={"x": 0, "y": 0, "z": 0},
        instance_id="roof.cladding.main",
    )

    unrelated_edit = replay.component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"colour": "red", "width": 3000},
        explicit_dependencies={"profile": "corrugated-v1"},
        function_source_sha256="roof-source",
        helper_source_sha256=["roof-helper"],
        runtime_versions={"build123d": "0.9", "python": "3.12"},
        export_settings={"quality": "rough", "format": "glb"},
        placement={"x": 800, "y": 0, "z": 0},
        instance_id="roof.cladding.shifted",
    )

    assert unrelated_edit == baseline


def test_component_geometry_cache_key_invalidates_geometry_material_and_declared_dependency_changes() -> None:
    baseline = replay.component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"width": 3000, "colour": "red"},
        explicit_dependencies={"profile": "corrugated-v1"},
        material={"baseColorFactor": [1, 0, 0, 1]},
        function_source_sha256="roof-source",
        export_settings={"format": "glb", "quality": "rough"},
    )

    geometry_changed = replay.component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"width": 3500, "colour": "red"},
        explicit_dependencies={"profile": "corrugated-v1"},
        material={"baseColorFactor": [1, 0, 0, 1]},
        function_source_sha256="roof-source",
        export_settings={"format": "glb", "quality": "rough"},
    )
    material_changed = replay.component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"width": 3000, "colour": "red"},
        explicit_dependencies={"profile": "corrugated-v1"},
        material={"baseColorFactor": [0, 0, 1, 1]},
        function_source_sha256="roof-source",
        export_settings={"format": "glb", "quality": "rough"},
    )
    dependency_changed = replay.component_geometry_cache_key(
        component_id="roof.cladding",
        replay_identity="design.roof_cladding",
        inputs={"width": 3000, "colour": "red"},
        explicit_dependencies={"profile": "corrugated-v2"},
        material={"baseColorFactor": [1, 0, 0, 1]},
        function_source_sha256="roof-source",
        export_settings={"format": "glb", "quality": "rough"},
    )

    assert geometry_changed != baseline
    assert material_changed != baseline
    assert dependency_changed != baseline


def test_component_geometry_cache_key_rejects_non_deterministic_inputs() -> None:
    try:
        replay.component_geometry_cache_key(
            component_id="roof.cladding",
            replay_identity="design.roof_cladding",
            inputs={"widths": {3000, 3500}},
        )
    except ValueError as exc:
        assert "inputs.widths is not deterministic JSON" in str(exc)
    else:
        raise AssertionError("non-deterministic input should fail closed")


def test_component_replay_eligibility_rejects_unresolved_inputs() -> None:
    result = replay.component_replay_eligibility(
        {"component_id": "roof.cladding", "replay": "roof_cladding", "inputs": {}},
        {
            "function": "roof_cladding",
            "qualified_function": "design.roof_cladding",
            "parameters": {"width": {"raw": "shed_width"}},
        },
    )

    assert result == {
        "eligible": False,
        "diagnostic_code": "component_replay_ineligible_unresolved_input",
        "fields": ["width"],
    }


def test_component_replay_eligibility_reports_registration_provenance_conflict() -> None:
    result = replay.component_replay_eligibility(
        {
            "component_id": "roof.cladding",
            "replay": "wall_cladding",
            "inputs": {"width": 3000},
        },
        {
            "function": "roof_cladding",
            "qualified_function": "design.roof_cladding",
            "parameters": {"width": {"resolved": 3000}},
        },
    )

    assert result == {
        "eligible": False,
        "diagnostic_code": "component_replay_registration_provenance_conflict",
        "field": "replay",
    }


def test_component_replay_eligibility_reports_registered_input_conflict() -> None:
    result = replay.component_replay_eligibility(
        {
            "component_id": "roof.cladding",
            "replay": "roof_cladding",
            "inputs": {"width": 3500},
        },
        {
            "function": "roof_cladding",
            "qualified_function": "design.roof_cladding",
            "parameters": {"width": {"resolved": 3000}},
        },
    )

    assert result == {
        "eligible": False,
        "diagnostic_code": "component_replay_registration_provenance_conflict",
        "field": "inputs.width",
    }
