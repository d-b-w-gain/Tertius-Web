from __future__ import annotations

from pathlib import Path

import pytest
from core.structural import build_source_inventory


def write_source(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_inventory_follows_local_imports_without_executing_source(tmp_path: Path):
    write_source(
        tmp_path,
        "design.py",
        """
import json
import build123d as bd
from frame import build_frame
from sections.catalog import C100

raise RuntimeError("inventory must not execute this")
model = build_frame(section=C100)
""",
    )
    write_source(
        tmp_path,
        "frame.py",
        """
from pathlib import Path
from sections import properties

CONFIG = Path("loads.json")

def build_frame(section):
    return properties.make_member(section)
""",
    )
    write_source(tmp_path, "sections/__init__.py", "")
    write_source(tmp_path, "sections/catalog.py", 'C100 = "C10015"\n')
    write_source(
        tmp_path,
        "sections/properties.py",
        "def make_member(section):\n    return section\n",
    )
    write_source(tmp_path, "legacy_report.py", "raise AssertionError('not imported')\n")
    (tmp_path / "loads.json").write_text("{}", encoding="utf-8")

    inventory = build_source_inventory(tmp_path)

    assert [item["path"] for item in inventory["source_files"]] == [
        "design.py",
        "frame.py",
        "sections/__init__.py",
        "sections/catalog.py",
        "sections/properties.py",
    ]
    assert inventory["external_imports"] == ["build123d"]
    assert inventory["standard_library_imports"] == ["json", "pathlib"]
    assert inventory["out_of_closure_python_files"] == ["legacy_report.py"]
    assert inventory["diagnostics"] == []
    assert inventory["literal_file_references"] == [
        {
            "source_file": "frame.py",
            "line": 5,
            "call": "Path",
            "literal": "loads.json",
            "within_project": True,
            "exists": True,
        }
    ]
    assert any(
        call["source_file"] == "design.py" and call["call"] == "RuntimeError"
        for call in inventory["module_level_calls"]
    )


def test_inventory_resolves_relative_and_literal_dynamic_imports(tmp_path: Path):
    write_source(tmp_path, "design.py", "from model.frame import build\n")
    write_source(tmp_path, "model/__init__.py", "")
    write_source(
        tmp_path,
        "model/frame.py",
        """
from .nodes import Node
from importlib import import_module

loads = import_module("model.loads")
""",
    )
    write_source(tmp_path, "model/nodes.py", "class Node:\n    pass\n")
    write_source(tmp_path, "model/loads.py", "DEAD = 'dead'\n")

    inventory = build_source_inventory(tmp_path)

    assert [item["path"] for item in inventory["source_files"]] == [
        "design.py",
        "model/__init__.py",
        "model/frame.py",
        "model/loads.py",
        "model/nodes.py",
    ]
    assert any(
        item["kind"] == "dynamic"
        and item["module"] == "model.loads"
        and item["classification"] == "local"
        for item in inventory["imports"]
    )


def test_inventory_reports_syntax_and_invalid_relative_imports(tmp_path: Path):
    write_source(tmp_path, "design.py", "from .outside import item\nimport broken\n")
    write_source(tmp_path, "broken.py", "def nope(:\n")

    inventory = build_source_inventory(tmp_path)

    assert [item["path"] for item in inventory["source_files"]] == [
        "broken.py",
        "design.py",
    ]
    assert {item["code"] for item in inventory["diagnostics"]} == {
        "syntax_error",
        "unresolved_relative_import",
    }
    assert (
        next(item for item in inventory["source_files"] if item["path"] == "broken.py")[
            "syntax_valid"
        ]
        is False
    )


def test_closure_digest_is_stable_and_changes_with_imported_source(tmp_path: Path):
    write_source(tmp_path, "design.py", "from helper import value\n")
    write_source(tmp_path, "helper.py", "value = 1\n")

    first = build_source_inventory(tmp_path)
    repeat = build_source_inventory(tmp_path)
    write_source(tmp_path, "helper.py", "value = 2\n")
    changed = build_source_inventory(tmp_path)

    assert first["closure_digest"] == repeat["closure_digest"]
    assert changed["closure_digest"] != first["closure_digest"]


def test_entrypoint_must_exist_inside_project(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        build_source_inventory(tmp_path)

    write_source(tmp_path, "design.py", "")
    with pytest.raises(ValueError):
        build_source_inventory(tmp_path, "../design.py")
