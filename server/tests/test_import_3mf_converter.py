from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import zipfile

import build123d as bd
import core.project_assets as project_assets
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
    make_invalid_multishell_solid_3mf,
    make_open_shell_3mf,
)
from workflows.intus.import_3mf_converter import (
    ALLOWED_PYLIB3MF_SHUTDOWN_STDERR,
    ArchiveLimits,
    BREP_BOUNDS_ABS_TOLERANCE_MM,
    ConverterStatus,
    Import3mfError,
    convert_3mf_bytes,
    load_brep_bytes,
    run_converter_subprocess,
    validate_3mf_archive,
    _parse_model_metadata,
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
    first = box_mesh(1)
    second_vertices, second_triangles = box_mesh(2)
    second = ([(x + 5, y, z) for x, y, z in second_vertices], second_triangles)
    result = convert_3mf_bytes(
        make_3mf(objects=[("First", *first), ("Second", *second)]), tmp_path
    )
    loaded = load_brep_bytes(result.brep_bytes, tmp_path / "two.brep")
    assert result.manifest.object_count == 2
    assert len(loaded.first_level_shapes()) == 2
    assert [part.name for part in result.manifest.parts] == ["first", "second"]
    assert result.manifest.parts[0].bounds_mm.max == (1.0, 1.0, 1.0)
    assert result.manifest.parts[1].bounds_mm.min == (5.0, 0.0, 0.0)


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
    assert cut.is_valid()
    assert 0 < cut.volume < parts[0].volume
    shell = convert_3mf_bytes(make_open_shell_3mf(), tmp_path / "shell")
    assert shell.manifest.parts[0].shape_type == "shell"
    assert not shell.manifest.parts[0].boolean_capable


def test_invalid_closed_multishell_solid_is_not_boolean_capable(tmp_path):
    result = convert_3mf_bytes(make_invalid_multishell_solid_3mf(), tmp_path)
    part_manifest = result.manifest.parts[0]
    assert part_manifest.shape_type == "solid"
    assert not part_manifest.is_valid
    assert not part_manifest.boolean_capable


def test_conversion_is_deterministic(tmp_path):
    source = make_box_3mf(size=7)
    first = convert_3mf_bytes(source, tmp_path / "first")
    second = convert_3mf_bytes(source, tmp_path / "second")
    assert first.brep_bytes == second.brep_bytes
    assert first.manifest == second.manifest


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
        ("xml_depth", project_assets.MAX_3MF_XML_DEPTH),
        ("objects", MAX_3MF_OBJECTS),
        ("build_items", project_assets.MAX_3MF_BUILD_ITEMS),
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
        "xml_depth": 6,
        "objects": 1,
        "build_items": 1,
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


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE model [<!ENTITY payload "boom">]>',
        '<!DOCTYPE model [<!ENTITY payload SYSTEM "file:///etc/passwd">]>',
    ],
)
def test_utf16_dtd_and_entities_are_rejected_for_all_encodings(declaration):
    model = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        f"{declaration}"
        '<model unit="millimeter" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        "<resources>&payload;</resources><build/></model>"
    ).encode("utf-16")
    source = make_3mf(objects=[("Box", *box_mesh())], model_document=model)
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(source)


def test_utf16_external_entity_in_relationships_is_rejected():
    relationships = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE Relationships [<!ENTITY target SYSTEM "file:///etc/passwd">]>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="&target;" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        "</Relationships>"
    ).encode("utf-16")
    source = make_3mf(
        objects=[("Box", *box_mesh())], relationships_document=relationships
    )
    with pytest.raises(Import3mfError, match="invalid_3mf_archive"):
        validate_3mf_archive(source)


def test_streaming_parser_accepts_chunks_and_fails_early_on_vertex_limit():
    vertices = [(float(index), 0.0, 0.0) for index in range(2_000)]
    source = make_3mf(objects=[("Points", vertices, [])])
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        validate_3mf_archive(source, limits=ArchiveLimits(vertices=1_999))
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        document = archive.read("3D/3dmodel.model")

    class Chunked(io.BytesIO):
        max_requested = 0

        def read(self, size=-1):
            self.max_requested = max(self.max_requested, size)
            return super().read(min(size, 127) if size >= 0 else 127)

    stream = Chunked(document)
    metadata = _parse_model_metadata(stream, ArchiveLimits(vertices=2_000))
    assert metadata.vertex_counts == (2_000,)
    assert stream.max_requested <= 16 * 1024
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        _parse_model_metadata(Chunked(document), ArchiveLimits(vertices=1_999))


def test_streaming_parser_detaches_completed_wide_siblings(monkeypatch):
    child_count = 1_000_000
    document = b"<root>" + b"<child/>" * child_count + b"</root>"
    original_iterparse = converter_module.DefusedElementTree.iterparse
    maximum_root_children = 0

    def tracking_iterparse(*args, **kwargs):
        nonlocal maximum_root_children
        requested_events = kwargs.pop("events", ("end",))
        root = None
        for event, element in original_iterparse(
            *args, events=("start", "end"), **kwargs
        ):
            if event == "start" and root is None:
                root = element
            if event in requested_events:
                yield event, element
            if root is not None:
                maximum_root_children = max(maximum_root_children, len(root))

    monkeypatch.setattr(
        converter_module.DefusedElementTree, "iterparse", tracking_iterparse
    )
    converter_module._validate_xml_document(io.BytesIO(document))

    assert maximum_root_children < child_count // 20


def test_xml_depth_limit_accepts_exact_and_rejects_over_before_tail_is_read():
    max_depth = project_assets.MAX_3MF_XML_DEPTH
    exact_document = b"<n>" * max_depth + b"</n>" * max_depth
    converter_module._validate_xml_document(io.BytesIO(exact_document))

    over_document = (
        b"<n>" * (max_depth + 1) + b"tail" * 250_000 + b"</n>" * (max_depth + 1)
    )

    class Chunked(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 64) if size >= 0 else 64)

    stream = Chunked(over_document)
    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        converter_module._validate_xml_document(stream)
    assert stream.tell() < len(over_document) // 100


@pytest.mark.parametrize("repeated_ids", [False, True])
def test_build_item_limit_accepts_exact_and_rejects_over(repeated_ids):
    vertices, triangles = box_mesh()

    def model_document(item_count: int) -> bytes:
        vertex_xml = "".join(
            f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices
        )
        triangle_xml = "".join(
            f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles
        )
        items = "".join(
            f'<item objectid="{1 if repeated_ids else index + 1}"/>'
            for index in range(item_count)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            '<resources><object id="1" type="model" name="Mesh">'
            f"<mesh><vertices>{vertex_xml}</vertices>"
            f"<triangles>{triangle_xml}</triangles></mesh></object></resources>"
            f"<build>{items}</build></model>"
        ).encode()

    exact = validate_3mf_archive(
        make_3mf(
            objects=[],
            model_document=model_document(project_assets.MAX_3MF_BUILD_ITEMS),
        )
    )
    assert exact.has_unpreserved_components

    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        validate_3mf_archive(
            make_3mf(
                objects=[],
                model_document=model_document(project_assets.MAX_3MF_BUILD_ITEMS + 1),
            )
        )


def test_component_only_objects_do_not_consume_mesh_object_limit():
    vertices, triangles = box_mesh()

    def model_document(component_count: int, mesh_count: int) -> bytes:
        components = "".join(
            f'<object id="{index}" type="model"><components>'
            f'<component objectid="{component_count + 1}"/>'
            "</components></object>"
            for index in range(1, component_count + 1)
        )
        meshes = []
        build = []
        for mesh_index in range(mesh_count):
            object_id = component_count + mesh_index + 1
            vertex_xml = "".join(
                f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices
            )
            triangle_xml = "".join(
                f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles
            )
            meshes.append(
                f'<object id="{object_id}" type="model" name="Mesh {mesh_index}">'
                f"<mesh><vertices>{vertex_xml}</vertices>"
                f"<triangles>{triangle_xml}</triangles></mesh></object>"
            )
            build.append(f'<item objectid="{object_id}"/>')
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            f"<resources>{components}{''.join(meshes)}</resources>"
            f"<build>{''.join(build)}</build></model>"
        ).encode()

    exact = validate_3mf_archive(
        make_3mf(objects=[], model_document=model_document(100, 1)),
        limits=ArchiveLimits(objects=1),
    )
    assert exact.source_names == ("Mesh 0",)
    assert exact.has_unpreserved_components

    with pytest.raises(Import3mfError, match="3mf_resource_limit"):
        validate_3mf_archive(
            make_3mf(objects=[], model_document=model_document(100, 2)),
            limits=ArchiveLimits(objects=1),
        )


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


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (b"not zip", "invalid_3mf_archive"),
        (make_box_3mf(size=float("nan")), "invalid_3mf_geometry"),
        (make_box_3mf(size=MAX_3MF_COORDINATE_MM + 1), "3mf_resource_limit"),
    ],
)
def test_subprocess_preserves_bounded_domain_error(source, code):
    with pytest.raises(Import3mfError, match=code) as raised:
        run_converter_subprocess(source, timeout_seconds=30)
    assert raised.value.code == code


def test_converter_status_json_is_strict_and_bounded():
    success = ConverterStatus(schema_version=1, status="succeeded")
    assert ConverterStatus.model_validate_json(success.model_dump_json()) == success
    with pytest.raises(ValueError):
        ConverterStatus.model_validate_json(
            '{"schema_version":true,"status":"succeeded"}'
        )
    with pytest.raises(ValueError):
        ConverterStatus.model_validate(
            {
                "schema_version": 1,
                "status": "failed",
                "error_code": "invalid_3mf_archive",
                "user_message": "raw source metadata",
            }
        )


def test_subprocess_malformed_status_fails_closed(tmp_path):
    script = tmp_path / "bad-status.py"
    script.write_text(
        "import pathlib,sys\n"
        "pathlib.Path(sys.argv[5]).write_text('{bad json')\n"
        "raise SystemExit(1)\n"
    )
    with pytest.raises(Import3mfError, match="conversion_failed"):
        run_converter_subprocess(
            make_box_3mf(),
            timeout_seconds=30,
            worker_command=[sys.executable, os.fspath(script)],
        )


def test_subprocess_fails_closed_on_unexpected_stderr_after_valid_output(tmp_path):
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from workflows.intus.import_3mf_converter import _child\n"
        "status = _child(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]))\n"
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
