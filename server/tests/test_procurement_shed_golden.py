from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.procurement_analysis import (
    analyze_design_sources,
    build_procurement_analysis,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "procurement" / "3x5shed_expected_bom.json"
NON_DISCRETE_UNITS = {"m", "m2", "m3", "kg", "l", "litre", "liter", "litres", "liters"}


def _read_python_files(project_dir: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8-sig")
        for path in sorted(project_dir.glob("*.py"))
        if path.is_file()
    }


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
        key = (
            requirement.get("part_number"),
            requirement.get("unit") or "each",
            _canonical_dimensions(requirement.get("dimensions")),
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
        normalised.append({
            "part_number": row.get("part_number"),
            "unit": row.get("unit") or "each",
            "quantity": row.get("quantity"),
            "dimensions": dict(_canonical_dimensions(row.get("dimensions"))),
            "material": row.get("material"),
            "finish": row.get("finish"),
            "standard": row.get("standard"),
        })
    return sorted(normalised, key=lambda row: json.dumps(row, sort_keys=True))


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


def _load_actual_analysis() -> dict[str, Any] | None:
    analysis_json = os.environ.get("TERTIUS_PROCUREMENT_SHED_ANALYSIS_JSON")
    if analysis_json:
        return json.loads(Path(analysis_json).read_text(encoding="utf-8-sig"))

    project_dir_raw = os.environ.get("TERTIUS_PROCUREMENT_SHED_DIR")
    tree_json = os.environ.get("TERTIUS_PROCUREMENT_SHED_TREE_JSON")
    if not project_dir_raw or not tree_json:
        return None

    project_dir = Path(project_dir_raw)
    source = analyze_design_sources(_read_python_files(project_dir), entrypoint="design.py")
    tree = json.loads(Path(tree_json).read_text(encoding="utf-8-sig"))
    return build_procurement_analysis(source, tree)


def test_3x5shed_visual_bom_matches_manual_expected_fixture():
    actual = _load_actual_analysis()
    if actual is None:
        return

    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if expected.get("status") != "verified":
        raise AssertionError(
            "3x5shed expected BoM fixture is not manually verified yet. "
            "Fill server/tests/fixtures/procurement/3x5shed_expected_bom.json "
            "with manually calculated line_items before enabling this golden comparison."
        )

    assert actual.get("analysis_mode") == "visual_verified"
    assert actual.get("quantity_authority") == "visual_tree"
    summary = expected.get("expected_summary", {})
    assert len(actual.get("assemblies", [])) == summary.get("assemblies")
    assert len(actual.get("components", [])) == summary.get("components")
    assert len(actual.get("requirements", [])) == summary.get("requirements")
    assert sum(1 for item in actual.get("requirements", []) if item.get("orderable") is not False) == summary.get("orderable_requirements")
    assert sum(1 for item in actual.get("requirements", []) if item.get("orderable") is False) == summary.get("non_orderable_requirements")

    for requirement in actual.get("requirements", []):
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

    assert _canonical_bom_rows(actual) == _canonical_fixture_rows(expected.get("line_items", []))
    assert _canonical_non_orderable_rows(actual) == _canonical_fixture_non_orderable_rows(
        expected.get("non_orderable_line_items", [])
    )
