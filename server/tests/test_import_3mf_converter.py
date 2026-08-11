from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import zipfile

import build123d as bd
import pytest
import workflows.intus.import_3mf_converter as converter_module

from core.project_assets import (
    MAX_3MF_ARCHIVE_ENTRIES,
    MAX_3MF_COORDINATE_MM,
    MAX_3MF_OBJECTS,
    MAX_3MF_TRIANGLES,
    MAX_3MF_UNCOMPRESSED_BYTES,
    MAX_3MF_UPLOAD_BYTES,
    MAX_3MF_VERTICES,
)
from tests.fixtures.three_mf import box_mesh, make_3mf, make_box_3mf, make_open_shell_3mf
from workflows.intus.import_3mf_converter import (
    ALLOWED_PYLIB3MF_SHUTDOWN_STDERR,
    ArchiveLimits,
    Import3mfError,
    convert_3mf_bytes,
    load_brep_bytes,
    run_converter_subprocess,
    validate_3mf_archive,
)


@pytest.mark.parametrize(
    ("unit", "factor"),
    [("MC", .001), ("MM", 1), ("CM", 10), ("M", 1000), ("IN", 25.4), ("FT", 304.8)],
)
def test_converter_normalizes_all_units(tmp_path, unit, factor):
    result = convert_3mf_bytes(make_box_3mf(unit=unit), tmp_path)
    assert result.manifest.scale_to_mm == factor
    assert result.manifest.parts[0].bounds_mm.max == pytest.approx((factor, factor, factor))


def test_converter_uses_deterministic_unique_names(tmp_path):
    mesh = box_mesh()
    result = convert_3mf_bytes(make_3mf(objects=[("", *mesh), ("Fin Left", *mesh), ("Fin Left", *mesh)]), tmp_path)
    assert [part.name for part in result.manifest.parts] == ["part_001", "fin_left", "fin_left_002"]


def test_converter_preserves_two_first_level_objects(tmp_path):
    mesh = box_mesh()
    result = convert_3mf_bytes(
        make_3mf(objects=[("First", *mesh), ("Second", *mesh)]), tmp_path
    )
    loaded = load_brep_bytes(result.brep_bytes, tmp_path / "two.brep")
    assert result.manifest.object_count == 2
    assert len(loaded.first_level_shapes()) == 2


def test_converter_maps_invalid_source_metadata_to_bounded_error(tmp_path):
    with pytest.raises(Import3mfError, match="invalid_3mf_geometry"):
        convert_3mf_bytes(make_box_3mf(name="x" * 161), tmp_path)


def test_solid_shell_roundtrip_and_boolean(tmp_path):
    solid = convert_3mf_bytes(make_box_3mf(size=20), tmp_path / "solid")
    loaded = load_brep_bytes(solid.brep_bytes, tmp_path / "loaded.brep")
    parts = loaded.first_level_shapes()
    assert len(parts) == 1
    assert solid.manifest.parts[0].shape_type == "solid"
    cut = parts[0] - bd.Cylinder(2, 20)
    assert cut.is_valid
    assert 0 < cut.volume < parts[0].volume
    shell = convert_3mf_bytes(make_open_shell_3mf(), tmp_path / "shell")
    assert shell.manifest.parts[0].shape_type == "shell"
    assert not shell.manifest.parts[0].boolean_capable


def test_output_digest_size_and_source_manifest_contract(tmp_path):
    source = make_box_3mf()
    result = convert_3mf_bytes(source, tmp_path)
    assert result.manifest.source_sha256 == hashlib.sha256(source).hexdigest()
    assert result.manifest.brep_sha256 == hashlib.sha256(result.brep_bytes).hexdigest()
    assert result.manifest.brep_byte_size == len(result.brep_bytes)


def test_coordinate_limit_accepts_exact_boundary(tmp_path):
    result = convert_3mf_bytes(make_box_3mf(size=MAX_3MF_COORDINATE_MM), tmp_path)
    assert result.manifest.parts[0].bounds_mm.max == pytest.approx(
        (MAX_3MF_COORDINATE_MM,) * 3
    )


def test_output_brep_and_manifest_limits_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(converter_module, "MAX_3MF_DERIVED_BREP_BYTES", 1)
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        convert_3mf_bytes(make_box_3mf(), tmp_path / "brep")
    monkeypatch.setattr(
        converter_module, "MAX_3MF_DERIVED_BREP_BYTES", 512 * 1024 * 1024
    )
    monkeypatch.setattr(converter_module, "MAX_3MF_MANIFEST_BYTES", 1)
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        convert_3mf_bytes(make_box_3mf(), tmp_path / "manifest")


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (b"not zip", "invalid_3mf_archive"),
        (make_box_3mf(size=float("nan")), "invalid_3mf_geometry"),
        (make_box_3mf(size=float("inf")), "invalid_3mf_geometry"),
        (make_box_3mf(size=MAX_3MF_COORDINATE_MM + 1), "3mf_resource_limit"),
    ],
)
def test_rejects_malformed_nonfinite_and_extreme_geometry(tmp_path, source, code):
    with pytest.raises(Import3mfError, match=code):
        convert_3mf_bytes(source, tmp_path)


def test_rejects_traversal_and_encrypted_entries():
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(make_3mf(objects=[("Box", *box_mesh())], extra_entries={"../escape": b"x"}))
    source = make_box_3mf()
    buffer = io.BytesIO(source)
    with zipfile.ZipFile(buffer) as archive:
        infos = archive.infolist()
    infos[0].flag_bits |= 1
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(source, infos=infos)


@pytest.mark.parametrize(
    ("field", "default"),
    [
        ("upload_bytes", MAX_3MF_UPLOAD_BYTES),
        ("archive_entries", MAX_3MF_ARCHIVE_ENTRIES),
        ("uncompressed_bytes", MAX_3MF_UNCOMPRESSED_BYTES),
        ("xml_bytes", 64 * 1024 * 1024),
        ("objects", MAX_3MF_OBJECTS),
        ("vertices", MAX_3MF_VERTICES),
        ("triangles", MAX_3MF_TRIANGLES),
    ],
)
def test_every_resource_limit_accepts_exact_and_rejects_over(field, default):
    values = {name: 10**12 for name in ArchiveLimits.model_fields}
    values[field] = default
    limits = ArchiveLimits(**values)
    limits.enforce(field, default)
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        limits.enforce(field, default + 1)


def test_xml_entity_bomb_is_rejected():
    source = make_3mf(objects=[("Box", *box_mesh())], extra_entries={"Metadata/bomb.xml": b'<!DOCTYPE x [<!ENTITY a "boom">]><x>&a;</x>'})
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(source)


def test_subprocess_kills_process_tree_on_timeout(tmp_path):
    marker = tmp_path / "child"
    script = tmp_path / "hang.py"
    child_code = (
        "import pathlib,time;time.sleep(1);"
        f"pathlib.Path({os.fspath(marker)!r}).write_text('alive')"
    )
    script.write_text(
        "import os, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(60)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(Import3mfError, match="conversion_timeout"):
        run_converter_subprocess(
            make_box_3mf(), timeout_seconds=0.1, worker_command=[sys.executable, os.fspath(script)]
        )
    import time
    time.sleep(1.2)
    assert not marker.exists()


def test_subprocess_has_empty_shutdown_stderr_allowlist_and_clean_success(tmp_path):
    assert ALLOWED_PYLIB3MF_SHUTDOWN_STDERR == frozenset()
    source = make_box_3mf()
    direct = run_converter_subprocess(source, timeout_seconds=30)
    assert direct.manifest.object_count == 1


def test_subprocess_fails_closed_on_unexpected_stderr_after_valid_output(tmp_path):
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from workflows.intus.import_3mf_converter import _child\n"
        "status = _child(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))\n"
        "sys.stderr.write('unexpected native warning\\n')\n"
        "raise SystemExit(status)\n"
    )
    with pytest.raises(Import3mfError, match="conversion_failed"):
        run_converter_subprocess(
            make_box_3mf(),
            timeout_seconds=30,
            worker_command=[sys.executable, os.fspath(script)],
        )


def test_subprocess_fails_closed_on_nonzero_exit(tmp_path):
    script = tmp_path / "failed.py"
    script.write_text("raise SystemExit(3)\n")
    with pytest.raises(Import3mfError, match="conversion_failed"):
        run_converter_subprocess(
            make_box_3mf(),
            timeout_seconds=30,
            worker_command=[sys.executable, os.fspath(script)],
        )


@pytest.mark.parametrize("limit_name", ["MAX_3MF_DERIVED_BREP_BYTES", "MAX_3MF_MANIFEST_BYTES"])
def test_parent_bounds_subprocess_output_before_reading(tmp_path, monkeypatch, limit_name):
    monkeypatch.setattr(converter_module, limit_name, 1)
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        run_converter_subprocess(make_box_3mf(), timeout_seconds=30)
