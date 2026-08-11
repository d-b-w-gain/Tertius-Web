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
from tests.fixtures.three_mf import (
    box_mesh,
    make_3mf,
    make_box_3mf,
    make_open_shell_3mf,
)
from workflows.intus.import_3mf_converter import (
    ALLOWED_PYLIB3MF_SHUTDOWN_STDERR,
    ArchiveLimits,
    BREP_BOUNDS_ABS_TOLERANCE_MM,
    Import3mfError,
    convert_3mf_bytes,
    load_brep_bytes,
    run_converter_subprocess,
    validate_3mf_archive,
    _validate_brep_round_trip,
)


@pytest.mark.parametrize(
    ("unit", "factor"),
    [("MC", 0.001), ("MM", 1), ("CM", 10), ("M", 1000), ("IN", 25.4), ("FT", 304.8)],
)
def test_converter_normalizes_all_units(tmp_path, unit, factor):
    result = convert_3mf_bytes(make_box_3mf(unit=unit), tmp_path)
    assert result.manifest.scale_to_mm == factor
    assert result.manifest.parts[0].bounds_mm.max == pytest.approx(
        (factor, factor, factor)
    )


def test_converter_uses_deterministic_unique_names(tmp_path):
    mesh = box_mesh()
    result = convert_3mf_bytes(
        make_3mf(objects=[("", *mesh), ("Fin Left", *mesh), ("Fin Left", *mesh)]),
        tmp_path,
    )
    assert [part.name for part in result.manifest.parts] == [
        "part_001",
        "fin_left",
        "fin_left_002",
    ]


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
    baseline = convert_3mf_bytes(make_box_3mf(), tmp_path / "baseline")
    monkeypatch.setattr(
        converter_module, "MAX_3MF_DERIVED_BREP_BYTES", len(baseline.brep_bytes)
    )
    convert_3mf_bytes(make_box_3mf(), tmp_path / "brep-exact")
    monkeypatch.setattr(
        converter_module, "MAX_3MF_DERIVED_BREP_BYTES", len(baseline.brep_bytes) - 1
    )
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        convert_3mf_bytes(make_box_3mf(), tmp_path / "brep")
    monkeypatch.setattr(
        converter_module, "MAX_3MF_DERIVED_BREP_BYTES", 512 * 1024 * 1024
    )
    manifest_size = len(baseline.manifest.model_dump_json().encode())
    monkeypatch.setattr(converter_module, "MAX_3MF_MANIFEST_BYTES", manifest_size)
    convert_3mf_bytes(make_box_3mf(), tmp_path / "manifest-exact")
    monkeypatch.setattr(converter_module, "MAX_3MF_MANIFEST_BYTES", manifest_size - 1)
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
        validate_3mf_archive(
            make_3mf(objects=[("Box", *box_mesh())], extra_entries={"../escape": b"x"})
        )
    source = make_box_3mf()
    buffer = io.BytesIO(source)
    with zipfile.ZipFile(buffer) as archive:
        infos = archive.infolist()
    infos[0].flag_bits |= 1
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(source, infos=infos)


@pytest.mark.parametrize(
    "path",
    ["C:/escape", "C:\\escape", "//server/share/file", "\\\\server\\share\\file"],
)
def test_rejects_windows_drive_and_unc_paths(path):
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(
            make_3mf(objects=[("Box", *box_mesh())], extra_entries={path: b"x"})
        )


def test_rejects_duplicate_canonical_paths_and_unsupported_compression():
    duplicate = make_3mf(
        objects=[("Box", *box_mesh())],
        extra_entries={"3d/3DMODEL.MODEL": b"duplicate"},
    )
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(duplicate)
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(
            make_3mf(objects=[("Box", *box_mesh())], compression=zipfile.ZIP_BZIP2)
        )


@pytest.mark.parametrize(
    "source",
    [
        make_3mf(objects=[("Box", *box_mesh())], include_relationship=False),
        make_3mf(objects=[("Box", *box_mesh())], relationship_target="../evil.model"),
        make_3mf(objects=[("Box", *box_mesh())], relationship_target="/missing.model"),
    ],
)
def test_requires_valid_root_3d_model_relationship(source):
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(source)


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
def test_every_archive_resource_limit_accepts_actual_exact_and_rejects_over(
    field, default
):
    assert ArchiveLimits.model_fields[field] == default
    source = make_3mf(
        objects=[("Box", *box_mesh())],
        extra_entries={"Metadata/extra.xml": b"<metadata/>"},
    )
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        infos = archive.infolist()
    actual = {
        "upload_bytes": len(source),
        "archive_entries": len(infos),
        "uncompressed_bytes": sum(info.file_size for info in infos),
        "xml_bytes": max(
            info.file_size
            for info in infos
            if info.filename.lower().endswith((".xml", ".model"))
        ),
        "objects": 1,
        "vertices": 8,
        "triangles": 12,
    }[field]
    exact = ArchiveLimits(
        **{
            name: actual if name == field else 10**12
            for name in ArchiveLimits.model_fields
        }
    )
    validate_3mf_archive(source, limits=exact)
    over = ArchiveLimits(
        **{
            name: actual - 1 if name == field else 10**12
            for name in ArchiveLimits.model_fields
        }
    )
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        validate_3mf_archive(source, limits=over)


def test_xml_limit_applies_per_part_not_aggregate():
    base = make_3mf(objects=[("Box", *box_mesh())])
    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        cap = archive.getinfo("3D/3dmodel.model").file_size
    second_xml = b"<a>" + b" " * (cap - len(b"<a></a>")) + b"</a>"
    assert len(second_xml) == cap
    source = make_3mf(
        objects=[("Box", *box_mesh())], extra_entries={"Metadata/a.xml": second_xml}
    )
    validate_3mf_archive(source, limits=ArchiveLimits(xml_bytes=cap))
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        validate_3mf_archive(source, limits=ArchiveLimits(xml_bytes=cap - 1))


def test_raw_unused_degenerate_vertex_is_checked_after_unit_normalization(tmp_path):
    vertices, triangles = box_mesh()
    vertices.append((MAX_3MF_COORDINATE_MM / 1000 + 1, 0, 0))
    source = make_3mf(unit="M", objects=[("Box", vertices, triangles)])
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        convert_3mf_bytes(source, tmp_path)


def test_component_warning_only_when_build_transform_is_present(tmp_path):
    plain = convert_3mf_bytes(make_box_3mf(), tmp_path / "plain")
    transformed = convert_3mf_bytes(
        make_3mf(
            objects=[("Box", *box_mesh())],
            build_transform="1 0 0 0 1 0 0 0 1 5 0 0",
        ),
        tmp_path / "transformed",
    )
    assert plain.manifest.warnings == ()
    assert transformed.manifest.warnings == ("component_graph_not_preserved",)


def test_roundtrip_rejects_reordered_or_mismatched_geometry(tmp_path):
    first = convert_3mf_bytes(make_box_3mf(size=1, name="First"), tmp_path / "first")
    second = convert_3mf_bytes(make_box_3mf(size=2, name="Second"), tmp_path / "second")
    shapes = [
        load_brep_bytes(
            second.brep_bytes, tmp_path / "second.brep"
        ).first_level_shapes()[0],
        load_brep_bytes(first.brep_bytes, tmp_path / "first.brep").first_level_shapes()[
            0
        ],
    ]
    with pytest.raises(Import3mfError, match="conversion_failed"):
        _validate_brep_round_trip(
            bd.Compound(shapes, children=shapes),
            (first.manifest.parts[0], second.manifest.parts[0]),
        )
    assert BREP_BOUNDS_ABS_TOLERANCE_MM > 0


def test_xml_entity_bomb_is_rejected():
    source = make_3mf(
        objects=[("Box", *box_mesh())],
        extra_entries={
            "Metadata/bomb.xml": b'<!DOCTYPE x [<!ENTITY a "boom">]><x>&a;</x>'
        },
    )
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
    with pytest.raises(Import3mfError, match="3mf_conversion_timeout"):
        run_converter_subprocess(
            make_box_3mf(),
            timeout_seconds=0.1,
            worker_command=[sys.executable, os.fspath(script)],
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


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_subprocess_kills_on_high_volume_output(tmp_path, stream):
    marker = tmp_path / f"{stream}-child"
    script = tmp_path / "flood.py"
    child_code = (
        "import pathlib,time;time.sleep(1);"
        f"pathlib.Path({os.fspath(marker)!r}).write_text('alive')"
    )
    script.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"stream = sys.{stream}.buffer\n"
        "stream.write(b'x' * (1024 * 1024)); stream.flush(); time.sleep(60)\n"
    )
    with pytest.raises(Import3mfError, match="conversion_failed"):
        run_converter_subprocess(
            make_box_3mf(),
            timeout_seconds=30,
            worker_command=[sys.executable, os.fspath(script)],
        )
    import time

    time.sleep(1.2)
    assert not marker.exists()


@pytest.mark.parametrize(
    "limit_name", ["MAX_3MF_DERIVED_BREP_BYTES", "MAX_3MF_MANIFEST_BYTES"]
)
def test_parent_bounds_subprocess_output_before_reading(
    tmp_path, monkeypatch, limit_name
):
    monkeypatch.setattr(converter_module, limit_name, 1)
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        run_converter_subprocess(make_box_3mf(), timeout_seconds=30)
