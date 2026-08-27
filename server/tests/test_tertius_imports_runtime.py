import importlib.util
import sys
import zipfile

import build123d as bd
import pytest

from core.compile_sandbox import run_compile_sandbox
from core.tertius_imports_runtime import TERTIUS_IMPORTS_HELPER_SOURCE
from tests.fixtures.three_mf import (
    UNSUPPORTED_BUILD_GRAPH_CASES,
    box_mesh,
    make_3mf,
)


def load_helper(tmp_path):
    path = tmp_path / "tertius_imports.py"
    path.write_text(TERTIUS_IMPORTS_HELPER_SOURCE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("tertius_imports_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_source(tmp_path, *, unit="MM", objects, **options):
    content = make_3mf(unit=unit, objects=objects, **options)
    (tmp_path / "source.3mf").write_bytes(content)
    return content


def test_loader_normalizes_inches_and_supports_boolean(tmp_path, monkeypatch):
    helper = load_helper(tmp_path)
    vertices, triangles = box_mesh(1)
    write_source(tmp_path, unit="IN", objects=[("Cube", vertices, triangles)])
    monkeypatch.chdir(tmp_path)

    imported = helper.load_3mf_model("source")

    assert len(imported.parts) == 1
    assert list(imported.parts_by_name) == ["part_001"]
    assert imported.compound.bounding_box().size.X == pytest.approx(25.4)
    assert isinstance(imported.parts[0], bd.Solid)
    cut = imported.parts[0] - bd.Box(5, 5, 30)
    assert cut.volume < imported.parts[0].volume


def test_loader_returns_two_stable_parts(tmp_path, monkeypatch):
    helper = load_helper(tmp_path)
    vertices, triangles = box_mesh(2)
    write_source(
        tmp_path,
        objects=[("Left Part", vertices, triangles), ("Left Part", vertices, triangles)],
    )
    monkeypatch.chdir(tmp_path)

    imported = helper.load_3mf_model("source")

    assert len(imported.parts) == 2
    assert list(imported.parts_by_name) == ["part_001", "part_002"]
    assert len(imported.compound.first_level_shapes()) == 2


@pytest.mark.parametrize(
    ("case", "options"),
    UNSUPPORTED_BUILD_GRAPH_CASES,
    ids=[case for case, _options in UNSUPPORTED_BUILD_GRAPH_CASES],
)
def test_loader_rejects_unsupported_build_graph(tmp_path, monkeypatch, case, options):
    helper = load_helper(tmp_path)
    vertices, triangles = box_mesh(2)
    write_source(
        tmp_path,
        objects=[("First", vertices, triangles), ("Second", vertices, triangles)],
        **options,
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="unsupported 3MF build graph"):
        helper.load_3mf_model("source")


def test_loader_rejects_unsafe_archive_before_native_parser(tmp_path, monkeypatch):
    helper = load_helper(tmp_path)
    with zipfile.ZipFile(tmp_path / "source.3mf", "w") as archive:
        archive.writestr("../escape.model", b"invalid")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="safe 3MF archive"):
        helper.load_3mf_model("source")


def test_compile_sandbox_allows_design_to_move_identity_build(tmp_path):
    vertices, triangles = box_mesh(10)
    write_source(tmp_path, objects=[("Cube", vertices, triangles)])
    (tmp_path / "design.py").write_text(
        'from tertius_imports import load_3mf_model\n'
        'imported = load_3mf_model("source")\n'
        'parts = imported.parts\n'
        'parts_by_name = imported.parts_by_name\n'
        'model = imported.compound.moved(bd.Location((5, 0, 0)))\n',
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "glb", quality="sketch", timeout_seconds=60)

    assert result.success is True, result.error
    assert result.output_path is not None
    assert result.output_path.read_bytes()[:4] == b"glTF"


def test_compile_sandbox_rejects_input_build_transform(tmp_path):
    vertices, triangles = box_mesh(10)
    write_source(
        tmp_path,
        objects=[("Cube", vertices, triangles)],
        build_transform="1 0 0 0 1 0 0 0 1 5 0 0",
    )
    (tmp_path / "design.py").write_text(
        'from tertius_imports import load_3mf_model\n'
        'model = load_3mf_model("source").compound\n',
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "glb", quality="sketch", timeout_seconds=60)

    assert result.success is False
    assert "unsupported 3MF build graph" in (result.error or "")


def test_loader_rejects_missing_or_unknown_source(tmp_path, monkeypatch):
    helper = load_helper(tmp_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="Only the source 3MF import is supported"):
        helper.load_3mf_model("other")
    with pytest.raises(RuntimeError, match="source.3mf was not found"):
        helper.load_3mf_model("source")
