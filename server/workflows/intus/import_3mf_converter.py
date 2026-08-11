from __future__ import annotations

import argparse
import hashlib
import io
import math
import os
import signal
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar
from xml.etree import ElementTree

import build123d as bd

from core.project_assets import (
    IMPORT_3MF_CONVERSION_VERSION,
    MAX_3MF_ARCHIVE_ENTRIES,
    MAX_3MF_COORDINATE_MM,
    MAX_3MF_DERIVED_BREP_BYTES,
    MAX_3MF_MANIFEST_BYTES,
    MAX_3MF_OBJECTS,
    MAX_3MF_SOURCE_NAME_CHARS,
    MAX_3MF_TRIANGLES,
    MAX_3MF_UNCOMPRESSED_BYTES,
    MAX_3MF_UPLOAD_BYTES,
    MAX_3MF_VERTICES,
    MAX_3MF_XML_BYTES,
    Import3mfBounds,
    Import3mfManifest,
    Import3mfPart,
    Import3mfUnit,
    safe_part_names,
)


UNIT_TO_MM = {
    bd.Unit.MC: 0.001,
    bd.Unit.MM: 1.0,
    bd.Unit.CM: 10.0,
    bd.Unit.M: 1000.0,
    bd.Unit.IN: 25.4,
    bd.Unit.FT: 304.8,
}
DEFAULT_CONVERSION_TIMEOUT_SECONDS = 300.0
# No py-lib3mf 2.3.1 shutdown warning is reproducible in the pinned runtime.
# Keep the allowlist empty and fail closed until an exact byte-for-byte message
# is captured by a regression test.
ALLOWED_PYLIB3MF_SHUTDOWN_STDERR: frozenset[str] = frozenset()
_MODEL_SUFFIX = ".model"


class Import3mfError(RuntimeError):
    def __init__(self, code: str, user_message: str):
        self.code = code
        self.user_message = user_message
        super().__init__(f"{code}: {user_message}")


@dataclass(frozen=True)
class ConversionOutput:
    brep_bytes: bytes
    manifest: Import3mfManifest


class ArchiveLimits:
    model_fields: ClassVar[dict[str, int]] = {
        "upload_bytes": MAX_3MF_UPLOAD_BYTES,
        "archive_entries": MAX_3MF_ARCHIVE_ENTRIES,
        "uncompressed_bytes": MAX_3MF_UNCOMPRESSED_BYTES,
        "xml_bytes": MAX_3MF_XML_BYTES,
        "objects": MAX_3MF_OBJECTS,
        "vertices": MAX_3MF_VERTICES,
        "triangles": MAX_3MF_TRIANGLES,
    }

    def __init__(self, **values: int):
        unknown = set(values) - self.model_fields.keys()
        if unknown:
            raise TypeError(f"unknown resource limits: {sorted(unknown)}")
        self.values = {**self.model_fields, **values}
        if any(type(value) is not int or value < 0 for value in self.values.values()):
            raise ValueError("resource limits must be non-negative integers")

    def enforce(self, field: str, value: int) -> None:
        if field not in self.values:
            raise KeyError(field)
        if value > self.values[field]:
            raise Import3mfError("3mf_resource_limit", "The 3MF exceeds an import resource limit.")


@dataclass(frozen=True)
class _ArchiveMetadata:
    source_unit: Import3mfUnit
    source_names: tuple[str, ...]
    vertex_counts: tuple[int, ...]
    triangle_counts: tuple[int, ...]


def validate_3mf_archive(
    source: bytes,
    *,
    limits: ArchiveLimits | None = None,
    infos: list[zipfile.ZipInfo] | None = None,
) -> _ArchiveMetadata:
    limits = limits or ArchiveLimits()
    limits.enforce("upload_bytes", len(source))
    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            actual_infos = infos if infos is not None else archive.infolist()
            limits.enforce("archive_entries", len(actual_infos))
            total_size = 0
            total_xml_size = 0
            model_documents: list[bytes] = []
            for info in actual_infos:
                _validate_entry(info)
                total_size += info.file_size
                limits.enforce("uncompressed_bytes", total_size)
                if PurePosixPath(info.filename).suffix.lower() in {".xml", _MODEL_SUFFIX}:
                    total_xml_size += info.file_size
                    limits.enforce("xml_bytes", total_xml_size)
                    content = archive.read(info.filename)
                    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                        raise _invalid_archive()
                    if info.filename.lower().endswith(_MODEL_SUFFIX):
                        model_documents.append(content)
    except Import3mfError:
        raise
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _invalid_archive() from exc
    if len(model_documents) != 1:
        raise _invalid_archive()
    return _parse_model_metadata(model_documents[0], limits)


def _validate_entry(info: zipfile.ZipInfo) -> None:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    if (
        info.flag_bits & 0x1
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or info.filename.startswith(("/", "\\"))
        or (info.external_attr >> 16) & 0o170000 == 0o120000
    ):
        raise _invalid_archive()


def _parse_model_metadata(document: bytes, limits: ArchiveLimits) -> _ArchiveMetadata:
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise _invalid_archive() from exc
    unit_names: dict[str, Import3mfUnit] = {
        "micron": "MC", "millimeter": "MM", "centimeter": "CM",
        "meter": "M", "inch": "IN", "foot": "FT",
    }
    source_unit = unit_names.get(root.attrib.get("unit", "millimeter"))
    if source_unit is None:
        raise _invalid_archive()
    objects = [element for element in root.iter() if _local_name(element.tag) == "object"]
    mesh_objects = [obj for obj in objects if any(_local_name(child.tag) == "mesh" for child in obj)]
    limits.enforce("objects", len(mesh_objects))
    if not mesh_objects:
        raise Import3mfError("invalid_3mf_geometry", "The 3MF contains no mesh objects.")
    names: list[str] = []
    vertex_counts: list[int] = []
    triangle_counts: list[int] = []
    total_vertices = total_triangles = 0
    for obj in mesh_objects:
        vertices = [element for element in obj.iter() if _local_name(element.tag) == "vertex"]
        triangles = [element for element in obj.iter() if _local_name(element.tag) == "triangle"]
        total_vertices += len(vertices)
        total_triangles += len(triangles)
        limits.enforce("vertices", total_vertices)
        limits.enforce("triangles", total_triangles)
        for vertex in vertices:
            try:
                coordinates = tuple(float(vertex.attrib[axis]) for axis in ("x", "y", "z"))
            except (KeyError, ValueError) as exc:
                raise Import3mfError("invalid_3mf_geometry", "The 3MF contains invalid coordinates.") from exc
            if not all(math.isfinite(value) for value in coordinates):
                raise Import3mfError("invalid_3mf_geometry", "The 3MF contains invalid coordinates.")
        source_name = obj.attrib.get("name", "")
        if len(source_name) > MAX_3MF_SOURCE_NAME_CHARS:
            raise Import3mfError(
                "invalid_3mf_geometry", "The 3MF contains invalid object metadata."
            )
        names.append(source_name)
        vertex_counts.append(len(vertices))
        triangle_counts.append(len(triangles))
    return _ArchiveMetadata(source_unit, tuple(names), tuple(vertex_counts), tuple(triangle_counts))


def convert_3mf_bytes(source: bytes, workdir: Path) -> ConversionOutput:
    if not isinstance(source, bytes):
        raise TypeError("source must be bytes")
    metadata = validate_3mf_archive(source)
    workdir.mkdir(parents=True, exist_ok=True)
    source_path = workdir / "source.3mf"
    source_path.write_bytes(source)
    try:
        mesher = bd.Mesher()
        shapes = mesher.read(source_path)
    except Exception as exc:
        raise Import3mfError("invalid_3mf_geometry", "The 3MF geometry could not be read.") from exc
    if not shapes or len(shapes) != len(metadata.source_names):
        raise Import3mfError("invalid_3mf_geometry", "The 3MF contains unsupported geometry.")
    factor = UNIT_TO_MM.get(mesher.model_unit)
    if factor is None:
        raise Import3mfError("invalid_3mf_geometry", "The 3MF uses an unsupported unit.")
    normalized = [shape.scale(factor) if factor != 1.0 else shape for shape in shapes]
    names = safe_part_names(list(metadata.source_names))
    parts = tuple(
        _part(index, names[index], metadata.source_names[index], shape,
              metadata.vertex_counts[index], metadata.triangle_counts[index])
        for index, shape in enumerate(normalized)
    )
    compound = bd.Compound(normalized, children=normalized)
    brep_path = workdir / "source.brep"
    try:
        if not bd.export_brep(compound, brep_path):
            raise RuntimeError("BREP export failed")
        brep = brep_path.read_bytes()
    except (OSError, RuntimeError, ValueError) as exc:
        raise Import3mfError("conversion_failed", "The converted model could not be saved.") from exc
    if not brep or len(brep) > MAX_3MF_DERIVED_BREP_BYTES:
        raise Import3mfError("3mf_resource_limit", "The converted model is too large.")
    manifest = Import3mfManifest(
        schema_version=1,
        conversion_version=IMPORT_3MF_CONVERSION_VERSION,
        source_sha256=hashlib.sha256(source).hexdigest(),
        brep_sha256=hashlib.sha256(brep).hexdigest(),
        brep_byte_size=len(brep),
        source_unit=metadata.source_unit,
        scale_to_mm=factor,
        object_count=len(parts),
        total_vertices=sum(metadata.vertex_counts),
        total_triangles=sum(metadata.triangle_counts),
        warnings=("component_graph_not_preserved",),
        parts=parts,
    )
    if len(manifest.model_dump_json().encode()) > MAX_3MF_MANIFEST_BYTES:
        raise Import3mfError("3mf_resource_limit", "The converted manifest is too large.")
    loaded = load_brep_bytes(brep, workdir / "roundtrip.brep")
    if len(loaded.first_level_shapes()) != len(parts):
        raise Import3mfError("conversion_failed", "The converted model did not pass validation.")
    return ConversionOutput(brep, manifest)


def _part(index: int, name: str, source_name: str, shape: bd.Shape, vertices: int, triangles: int) -> Import3mfPart:
    box = shape.bounding_box()
    minimum = (float(box.min.X), float(box.min.Y), float(box.min.Z))
    maximum = (float(box.max.X), float(box.max.Y), float(box.max.Z))
    if not all(math.isfinite(value) and abs(value) <= MAX_3MF_COORDINATE_MM for value in (*minimum, *maximum)):
        raise Import3mfError("3mf_resource_limit", "The 3MF coordinates exceed the supported range.")
    is_solid = isinstance(shape, bd.Solid)
    is_valid = bool(shape.is_valid)
    return Import3mfPart(
        index=index, name=name, source_name=source_name,
        shape_type="solid" if is_solid else "shell",
        boolean_capable=is_solid and is_valid, is_valid=is_valid,
        vertex_count=vertices, triangle_count=triangles,
        bounds_mm=Import3mfBounds(min=minimum, max=maximum),
    )


def load_brep_bytes(content: bytes, path: Path) -> bd.Compound:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    shape = bd.import_brep(path)
    first_level = shape.first_level_shapes() if isinstance(shape, bd.Compound) else [shape]
    return bd.Compound(first_level, children=first_level)


def run_converter_subprocess(
    source: bytes,
    timeout_seconds: float = DEFAULT_CONVERSION_TIMEOUT_SECONDS,
    *,
    worker_command: list[str] | None = None,
) -> ConversionOutput:
    if timeout_seconds <= 0 or timeout_seconds > DEFAULT_CONVERSION_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds must be between zero and 300")
    with tempfile.TemporaryDirectory(prefix="tertius-3mf-") as directory:
        workdir = Path(directory)
        source_path = workdir / "input.3mf"
        output_path = workdir / "output.brep"
        manifest_path = workdir / "manifest.json"
        source_path.write_bytes(source)
        command = worker_command or [sys.executable, "-m", __name__]
        command = [*command, "--child", os.fspath(source_path), os.fspath(output_path), os.fspath(manifest_path)]
        child_env = os.environ.copy()
        server_root = os.fspath(Path(__file__).resolve().parents[2])
        child_env["PYTHONPATH"] = os.pathsep.join(
            part for part in (server_root, child_env.get("PYTHONPATH", "")) if part
        )
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True, env=child_env,
            )
        except OSError as exc:
            raise Import3mfError(
                "conversion_failed", "The 3MF converter could not be started."
            ) from exc
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise Import3mfError("conversion_timeout", "The 3MF conversion timed out.") from exc
        valid_files = output_path.is_file() and manifest_path.is_file()
        if process.returncode != 0 or not valid_files:
            raise Import3mfError("conversion_failed", "The 3MF conversion failed safely.")
        if stdout or (stderr and stderr not in ALLOWED_PYLIB3MF_SHUTDOWN_STDERR):
            raise Import3mfError("conversion_failed", "The 3MF converter returned unexpected output.")
        if output_path.stat().st_size > MAX_3MF_DERIVED_BREP_BYTES:
            raise Import3mfError("3mf_resource_limit", "The converted model is too large.")
        if manifest_path.stat().st_size > MAX_3MF_MANIFEST_BYTES:
            raise Import3mfError("3mf_resource_limit", "The converted manifest is too large.")
        brep = output_path.read_bytes()
        try:
            manifest = Import3mfManifest.model_validate_json(manifest_path.read_bytes())
        except Exception as exc:
            raise Import3mfError("conversion_failed", "The 3MF converter returned an invalid result.") from exc
        if (
            manifest.source_sha256 != hashlib.sha256(source).hexdigest()
            or manifest.brep_sha256 != hashlib.sha256(brep).hexdigest()
            or manifest.brep_byte_size != len(brep)
        ):
            raise Import3mfError("conversion_failed", "The 3MF converter returned an invalid result.")
        load_brep_bytes(brep, workdir / "parent-roundtrip.brep")
        return ConversionOutput(brep, manifest)


def _invalid_archive() -> Import3mfError:
    return Import3mfError("invalid_3mf_archive", "The file is not a safe 3MF archive.")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(source_path: Path, output_path: Path, manifest_path: Path) -> int:
    try:
        output = convert_3mf_bytes(source_path.read_bytes(), output_path.parent / "conversion")
        output_path.write_bytes(output.brep_bytes)
        manifest_path.write_text(output.manifest.model_dump_json(), encoding="utf-8")
        return 0
    except Exception:
        return 1


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if not args.child:
        return 2
    return _child(args.source, args.output, args.manifest)


if __name__ == "__main__":
    raise SystemExit(_main())
