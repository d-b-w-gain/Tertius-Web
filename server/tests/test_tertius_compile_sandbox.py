from __future__ import annotations

import json

import pytest

from core.compile_sandbox import run_compile_sandbox


def test_sandbox_exports_only_model_and_emits_compiled_design(tmp_path) -> None:
    (tmp_path / "design.py").write_text(
        "helper = bd.Box(500, 500, 500)\n"
        "model = bd.Box(10, 20, 30)\n",
        encoding="utf-8",
    )

    result = run_compile_sandbox(
        tmp_path,
        "timus_bounds",
        timeout_seconds=120,
    )

    assert result.success is True, result.error
    assert result.output_path is not None
    assert json.loads(result.output_path.read_text(encoding="utf-8")) == {
        "max_dim": pytest.approx(30.0)
    }
    compiled_design = json.loads(
        result.artifact_paths["compiled_design"].read_text(encoding="utf-8")
    )
    assert compiled_design["schema_version"] == "1.0"
    assert compiled_design["components"] == []
    assert compiled_design["readiness"]["mechanical_graph_valid"] is True
    assert compiled_design["readiness"]["procurement_complete"] is False
    assert set(result.artifact_paths) == {
        "compiled_design",
        "procurement",
        "structural",
        "drawing",
        "bounds",
    }
    for kind, artifact_path in result.artifact_paths.items():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        if kind != "compiled_design":
            assert artifact["compiled_design_digest"] == compiled_design[
                "compiled_design_digest"
            ]


def test_sandbox_rejects_removed_structural_manifest_export(tmp_path) -> None:
    (tmp_path / "design.py").write_text(
        "model = bd.Box(10, 20, 30)\n"
        "TERTIUS_STRUCTURAL = {}\n",
        encoding="utf-8",
    )

    result = run_compile_sandbox(
        tmp_path,
        "timus_bounds",
        timeout_seconds=120,
    )

    assert result.success is False
    assert result.output_path is None
    assert "removed Tertius manifest exports" in (result.error or "")
