from __future__ import annotations

import json
import os
import pytest
import shutil
import struct
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.compile_sandbox import run_compile_sandbox
from core.procurement_analysis import (
    analyze_design_sources,
    analyze_gltf_tree,
    build_procurement_analysis,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "procurement" / "3x5shed_expected_bom.json"
DEFAULT_SHED_DIR = Path(r"C:\Users\dbwga\Documents\Projects\CAD\3x5shed")
NON_DISCRETE_UNITS = {"m", "m2", "m3", "kg", "l", "litre", "liter", "litres", "liters"}
COMPARABLE_REFERENCE_STATUSES = {"manually_verified"}
SHED_ACCEPTANCE_QUANTITIES = {
    "100AC": 26,
    "100CP": 32,
    "YELLOWTONGUE-19-1800X800": 4,
    "YELLOWTONGUE-19-3600X800": 4,
}
SHED_FORBIDDEN_PART_NUMBERS = {
    "C10019",
    "DIN-6921-M12X25",
    "DIN-6923-M12",
}


def _load_expected_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _reference_line_id(row: dict[str, Any]) -> str:
    raw_dimensions = row.get("dimensions")
    dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
    part_number = row.get("part_number") or dimensions.get("component_label") or "(missing)"
    dimension_text = ",".join(f"{key}={value}" for key, value in sorted(dimensions.items()))
    return f"{part_number}:{row.get('quantity')} {row.get('unit') or 'each'}:{dimension_text}"


REFERENCE_LINE_ITEMS = _load_expected_fixture().get("line_items", [])


def _read_python_files(project_dir: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8-sig")
        for path in sorted(project_dir.glob("*.py"))
        if path.is_file()
    }


def _copy_project_for_compile(project_dir: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="tertius-shed-golden-"))
    for path in sorted(project_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in {".py", ".json", ".csv"}:
            shutil.copy2(path, temp_dir / path.name)
    return temp_dir


def _read_gltf_artifact(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if data[:4] != b"glTF":
        return json.loads(data.decode("utf-8-sig"))

    if len(data) < 20:
        raise ValueError(f"{path} is not a valid GLB file.")
    magic, version, length = struct.unpack("<4sII", data[:12])
    if magic != b"glTF" or version != 2:
        raise ValueError(f"{path} is not a GLB v2 file.")

    offset = 12
    while offset + 8 <= length:
        chunk_length, chunk_type = struct.unpack("<I4s", data[offset:offset + 8])
        offset += 8
        chunk_end = offset + chunk_length
        if chunk_end > length:
            raise ValueError("GLB chunk is truncated.")
        chunk = data[offset:chunk_end]
        offset = chunk_end
        if chunk_type == b"JSON":
            return json.loads(chunk.rstrip(b" \t\r\n\x00").decode("utf-8"))

    raise ValueError(f"{path} does not contain a GLB JSON chunk.")


def _gltf_to_scene_tree(gltf: dict[str, Any]) -> dict[str, Any]:
    nodes = gltf.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("GLTF JSON must contain a nodes list.")

    def convert_node(index: int) -> dict[str, Any]:
        node = nodes[index]
        if not isinstance(node, dict):
            raise ValueError(f"GLTF node {index} is not an object.")
        raw_child_indexes = node.get("children")
        child_indexes: list[Any] = raw_child_indexes if isinstance(raw_child_indexes, list) else []
        has_mesh = isinstance(node.get("mesh"), int)
        converted = {
            "id": str(index),
            "name": str(node.get("name") or ("Mesh" if has_mesh else f"node_{index}")),
            "type": "Mesh" if has_mesh else "Object3D",
            "isMesh": has_mesh,
            "children": [convert_node(child_index) for child_index in child_indexes if isinstance(child_index, int)],
        }
        if isinstance(node.get("extras"), dict):
            converted["extras"] = node["extras"]
        for key in ("translation", "rotation", "scale", "matrix"):
            if isinstance(node.get(key), list):
                converted[key] = node[key]
        return converted

    scene_indexes: list[int] = []
    scene_id = gltf.get("scene")
    scenes = gltf.get("scenes")
    if isinstance(scene_id, int) and isinstance(scenes, list) and 0 <= scene_id < len(scenes):
        scene = scenes[scene_id]
        if isinstance(scene, dict) and isinstance(scene.get("nodes"), list):
            scene_indexes = [index for index in scene["nodes"] if isinstance(index, int)]

    if not scene_indexes:
        referenced = {
            child_index
            for node in nodes
            if isinstance(node, dict)
            for child_index in (node.get("children") or [])
            if isinstance(child_index, int)
        }
        scene_indexes = [index for index in range(len(nodes)) if index not in referenced]

    return {
        "name": "Scene",
        "type": "Scene",
        "children": [convert_node(index) for index in scene_indexes],
    }


def _compile_shed_analysis(project_dir: Path) -> dict[str, Any]:
    compile_dir = _copy_project_for_compile(project_dir)
    result = run_compile_sandbox(compile_dir, "glb", quality="sketch", timeout_seconds=300)
    if not result.success or result.output_path is None:
        raise AssertionError(
            "3x5shed GLB compile failed.\n"
            f"compile_dir={compile_dir}\n"
            f"stdout={result.stdout}\n"
            f"stderr={result.stderr}\n"
            f"error={result.error}"
        )

    source = analyze_design_sources(_read_python_files(project_dir), entrypoint="design.py")
    tree = analyze_gltf_tree(_gltf_to_scene_tree(_read_gltf_artifact(result.output_path)))
    return build_procurement_analysis(source, tree)


def _canonical_dimensions(dimensions: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(dimensions, dict):
        return ()
    return tuple(
        sorted(
            (str(key), str(value))
            for key, value in dimensions.items()
            if key not in {"component_label"}
        )
    )


def _canonical_all_dimensions(dimensions: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(dimensions, dict):
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in dimensions.items()))


def _canonical_bom_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], float] = defaultdict(float)
    for requirement in analysis.get("requirements", []):
        if requirement.get("orderable") is False:
            continue
        part_number = requirement.get("part_number")
        key = (
            part_number,
            requirement.get("unit") or "each",
            _canonical_dimensions(requirement.get("dimensions")) if part_number else _canonical_all_dimensions(requirement.get("dimensions")),
            requirement.get("material"),
            requirement.get("finish"),
            requirement.get("standard"),
        )
        grouped[key] += float(requirement.get("quantity") or 0)

    rows = []
    for (part_number, unit, dimensions, material, finish, standard), quantity in grouped.items():
        rows.append({
            "part_number": part_number,
            "unit": unit,
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "dimensions": dict(dimensions),
            "material": material,
            "finish": finish,
            "standard": standard,
        })
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def _canonical_non_orderable_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], float] = defaultdict(float)
    for requirement in analysis.get("requirements", []):
        if requirement.get("orderable") is not False:
            continue
        key = (
            requirement.get("part_number"),
            requirement.get("unit") or "each",
            _canonical_all_dimensions(requirement.get("dimensions")),
        )
        grouped[key] += float(requirement.get("quantity") or 0)

    rows = []
    for (part_number, unit, dimensions), quantity in grouped.items():
        rows.append({
            "part_number": part_number,
            "unit": unit,
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "dimensions": dict(dimensions),
        })
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def _canonical_fixture_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalised = []
    for row in rows:
        part_number = row.get("part_number")
        normalised.append({
            "part_number": part_number,
            "unit": row.get("unit") or "each",
            "quantity": row.get("quantity"),
            "dimensions": dict(_canonical_dimensions(row.get("dimensions")) if part_number else _canonical_all_dimensions(row.get("dimensions"))),
            "material": row.get("material"),
            "finish": row.get("finish"),
            "standard": row.get("standard"),
        })
    return sorted(normalised, key=lambda row: json.dumps(row, sort_keys=True))


def _canonical_fixture_row(row: dict[str, Any]) -> dict[str, Any]:
    rows = _canonical_fixture_rows([row])
    if not rows:
        raise AssertionError("Expected fixture row did not canonicalise to a BoM row.")
    return rows[0]


def _row_identity(row: dict[str, Any]) -> str:
    return json.dumps({
        "part_number": row.get("part_number"),
        "unit": row.get("unit") or "each",
        "dimensions": row.get("dimensions") or {},
        "material": row.get("material"),
        "finish": row.get("finish"),
        "standard": row.get("standard"),
    }, sort_keys=True)


def _canonical_fixture_non_orderable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalised = []
    for row in rows:
        normalised.append({
            "part_number": row.get("part_number"),
            "unit": row.get("unit") or "each",
            "quantity": row.get("quantity"),
            "dimensions": dict(_canonical_all_dimensions(row.get("dimensions"))),
        })
    return sorted(normalised, key=lambda row: json.dumps(row, sort_keys=True))


def _rolled_up_part_quantities(analysis: dict[str, Any]) -> dict[str, float]:
    quantities: dict[str, float] = defaultdict(float)
    for requirement in analysis.get("requirements", []):
        part_number = requirement.get("part_number")
        if not part_number:
            continue
        quantities[str(part_number)] += float(
            requirement.get("rolled_up_quantity", requirement.get("quantity") or 0) or 0
        )
    return dict(quantities)


def _load_actual_analysis() -> dict[str, Any] | None:
    analysis_json = os.environ.get("TERTIUS_PROCUREMENT_SHED_ANALYSIS_JSON")
    if analysis_json:
        return json.loads(Path(analysis_json).read_text(encoding="utf-8-sig"))

    project_dir_raw = os.environ.get("TERTIUS_PROCUREMENT_SHED_DIR")
    project_dir = Path(project_dir_raw) if project_dir_raw else DEFAULT_SHED_DIR
    if project_dir.exists():
        return _compile_shed_analysis(project_dir)

    tree_json = os.environ.get("TERTIUS_PROCUREMENT_SHED_TREE_JSON")
    if not project_dir_raw or not tree_json:
        return None

    source = analyze_design_sources(_read_python_files(project_dir), entrypoint="design.py")
    tree = json.loads(Path(tree_json).read_text(encoding="utf-8-sig"))
    return build_procurement_analysis(source, tree)


@pytest.fixture(scope="session")
def expected_fixture() -> dict[str, Any]:
    expected = _load_expected_fixture()
    if expected.get("status") not in COMPARABLE_REFERENCE_STATUSES:
        pytest.skip(
            "3x5shed expected BoM fixture is not ready for comparison. "
            "Fill server/tests/fixtures/procurement/3x5shed_expected_bom.json "
            "with manually calculated line_items before running this comparison."
        )
    return expected


@pytest.fixture(scope="session")
def actual_analysis() -> dict[str, Any]:
    actual = _load_actual_analysis()
    if actual is None:
        pytest.skip(
            "3x5shed CAD project or analysis artifact is not available for the golden comparison."
        )
    return actual


@pytest.fixture(scope="session")
def actual_bom_by_identity(actual_analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {_row_identity(row): row for row in _canonical_bom_rows(actual_analysis)}


def test_3x5shed_visual_analysis_contract(actual_analysis: dict[str, Any]):
    assert actual_analysis.get("analysis_mode") == "visual_verified"
    assert actual_analysis.get("quantity_authority") == "visual_tree"
    part_quantities = _rolled_up_part_quantities(actual_analysis)
    for part_number, expected_quantity in SHED_ACCEPTANCE_QUANTITIES.items():
        assert part_quantities.get(part_number) == expected_quantity
    for part_number in SHED_FORBIDDEN_PART_NUMBERS:
        assert part_number not in part_quantities

    for requirement in actual_analysis.get("requirements", []):
        if requirement.get("orderable") is False:
            continue
        quantity_source = requirement.get("quantity_source")
        unit = str(requirement.get("unit") or "each").lower()
        if unit in NON_DISCRETE_UNITS:
            assert quantity_source == "metadata_quantity_non_discrete"
        else:
            assert quantity_source == "visual_instances"
            assert requirement.get("quantity") == 1
            assert requirement.get("visual_instance_count") == 1


@pytest.mark.parametrize("expected_line", REFERENCE_LINE_ITEMS, ids=_reference_line_id)
def test_3x5shed_reference_bom_line_matches_actual(
    expected_line: dict[str, Any],
    actual_bom_by_identity: dict[str, dict[str, Any]],
    expected_fixture: dict[str, Any],
):
    expected_row = _canonical_fixture_row(expected_line)
    actual_row = actual_bom_by_identity.get(_row_identity(expected_row))
    assert actual_row == expected_row


def test_3x5shed_has_no_unexpected_orderable_bom_lines(
    actual_bom_by_identity: dict[str, dict[str, Any]],
    expected_fixture: dict[str, Any],
):
    expected_rows = _canonical_fixture_rows(expected_fixture.get("line_items", []))
    expected_identities = {_row_identity(row) for row in expected_rows}
    unexpected_rows = [
        row
        for identity, row in sorted(actual_bom_by_identity.items())
        if identity not in expected_identities
    ]
    assert unexpected_rows == []


def test_3x5shed_non_orderable_rows_match_expected(actual_analysis: dict[str, Any], expected_fixture: dict[str, Any]):
    assert _canonical_non_orderable_rows(actual_analysis) == _canonical_fixture_non_orderable_rows(
        expected_fixture.get("non_orderable_line_items", [])
    )
