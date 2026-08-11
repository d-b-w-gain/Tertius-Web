from __future__ import annotations

import argparse
import hashlib
import io
import math
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, ClassVar, cast
from xml.etree.ElementTree import ParseError

import build123d as bd
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Literal, Self

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
MAX_SUBPROCESS_STREAM_BYTES = 64 * 1024
MAX_CONVERTER_STATUS_BYTES = 1024
BREP_BOUNDS_ABS_TOLERANCE_MM = 1e-6
# No py-lib3mf 2.3.1 shutdown warning is reproducible in the pinned runtime.
# Keep the allowlist empty and fail closed until an exact byte-for-byte message
# is captured by a regression test.
ALLOWED_PYLIB3MF_SHUTDOWN_STDERR: frozenset[str] = frozenset()
_MODEL_SUFFIX = ".model"
ChildErrorCode = Literal[
    "invalid_3mf_archive",
    "invalid_3mf_geometry",
    "3mf_resource_limit",
    "conversion_failed",
]
_SAFE_CHILD_ERROR_MESSAGES: dict[ChildErrorCode, str] = {
    "invalid_3mf_archive": "The file is not a safe 3MF archive.",
    "invalid_3mf_geometry": "The 3MF geometry is invalid or unsupported.",
    "3mf_resource_limit": "The 3MF exceeds an import resource limit.",
    "conversion_failed": "The 3MF conversion failed safely.",
}


class Import3mfError(RuntimeError):
    def __init__(self, code: str, user_message: str):
        self.code = code
        self.user_message = user_message
        super().__init__(f"{code}: {user_message}")


@dataclass(frozen=True)
class ConversionOutput:
    brep_bytes: bytes
    manifest: Import3mfManifest


class ConverterStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    status: Literal["succeeded", "failed"]
    error_code: ChildErrorCode | None = None
    user_message: str | None = Field(default=None, max_length=160)

    @field_validator("schema_version", mode="before")
    @classmethod
    def schema_version_is_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def valid_status(self) -> Self:
        if self.status == "succeeded":
            if self.error_code is not None or self.user_message is not None:
                raise ValueError("successful converter status cannot contain an error")
        elif (
            self.error_code is None
            or self.user_message != _SAFE_CHILD_ERROR_MESSAGES[self.error_code]
        ):
            raise ValueError("failed converter status must use a safe known error")
        return self


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
            raise Import3mfError(
                "3mf_resource_limit", "The 3MF exceeds an import resource limit."
            )


@dataclass(frozen=True)
class _ArchiveMetadata:
    source_unit: Import3mfUnit
    source_names: tuple[str, ...]
    vertex_counts: tuple[int, ...]
    triangle_counts: tuple[int, ...]
    has_unpreserved_components: bool


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
            canonical_entries: dict[str, zipfile.ZipInfo] = {}
            for info in actual_infos:
                _validate_entry(info)
                canonical = _canonical_entry_name(info.filename)
                if canonical in canonical_entries:
                    raise _invalid_archive()
                total_size += info.file_size
                limits.enforce("uncompressed_bytes", total_size)
                if (
                    PurePosixPath(info.filename).suffix.lower()
                    in {".xml", _MODEL_SUFFIX}
                    or canonical == "_rels/.rels"
                ):
                    limits.enforce("xml_bytes", info.file_size)
                canonical_entries[canonical] = info
            relationship_info = canonical_entries.get("_rels/.rels")
            if relationship_info is None:
                raise _invalid_archive()
            model_path = _with_bounded_xml(
                archive,
                relationship_info,
                limits.values["xml_bytes"],
                _model_target_from_relationships,
            )
            model_info = canonical_entries.get(model_path)
            if model_info is None or not model_path.endswith(_MODEL_SUFFIX):
                raise _invalid_archive()
            for canonical, info in canonical_entries.items():
                is_xml = (
                    PurePosixPath(info.filename).suffix.lower()
                    in {".xml", _MODEL_SUFFIX}
                    or canonical == "_rels/.rels"
                )
                if is_xml and canonical not in {"_rels/.rels", model_path}:
                    _with_bounded_xml(
                        archive,
                        info,
                        limits.values["xml_bytes"],
                        _validate_xml_document,
                    )
            return _with_bounded_xml(
                archive,
                model_info,
                limits.values["xml_bytes"],
                lambda stream: _parse_model_metadata(stream, limits),
            )
    except Import3mfError:
        raise
    except (
        OSError,
        KeyError,
        RuntimeError,
        DefusedXmlException,
        ParseError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise _invalid_archive() from exc
    raise _invalid_archive()


def _validate_entry(info: zipfile.ZipInfo) -> None:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    if (
        info.flag_bits & 0x1
        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or not path.parts
        or info.filename.startswith(("/", "\\"))
        or bool(PureWindowsPath(info.filename).drive)
        or (info.external_attr >> 16) & 0o170000 == 0o120000
    ):
        raise _invalid_archive()


def _canonical_entry_name(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name.replace("\\", "/"))
    return "/".join(PurePosixPath(normalized).parts).casefold()


def _model_target_from_relationships(stream: BinaryIO) -> str:
    target: str | None = None
    for _, element in _secure_iterparse(stream):
        if (
            _local_name(element.tag) == "Relationship"
            and element.attrib.get("Type", "").rstrip("/").endswith("/3dmodel")
            and element.attrib.get("TargetMode", "Internal") == "Internal"
        ):
            if target is not None:
                raise _invalid_archive()
            target = element.attrib.get("Target", "")
        element.clear()
    if target is None:
        raise _invalid_archive()
    if not target or "?" in target or "#" in target or "\\" in target:
        raise _invalid_archive()
    relative = target.lstrip("/")
    target_path = PurePosixPath(relative)
    if target_path.is_absolute() or any(
        part in {"", ".", ".."} for part in target_path.parts
    ):
        raise _invalid_archive()
    return _canonical_entry_name(relative)


@dataclass
class _ObjectCounts:
    source_name: str
    has_mesh: bool = False
    vertices: int = 0
    triangles: int = 0


def _parse_model_metadata(stream: BinaryIO, limits: ArchiveLimits) -> _ArchiveMetadata:
    unit_names: dict[str, Import3mfUnit] = {
        "micron": "MC",
        "millimeter": "MM",
        "centimeter": "CM",
        "meter": "M",
        "inch": "IN",
        "foot": "FT",
    }
    names: list[str] = []
    vertex_counts: list[int] = []
    triangle_counts: list[int] = []
    total_vertices = total_triangles = 0
    source_unit: Import3mfUnit | None = None
    factor: float | None = None
    current_object: _ObjectCounts | None = None
    mesh_object_count = 0
    component_elements = False
    build_item_count = 0
    build_ids: set[str | None] = set()
    duplicate_build_id = False
    transformed_build_item = False
    for event, element in _secure_iterparse(stream, events=("start", "end")):
        tag = _local_name(element.tag)
        if event == "start" and tag == "model":
            source_unit = unit_names.get(element.attrib.get("unit", "millimeter"))
            if source_unit is None:
                raise _invalid_archive()
            factor = UNIT_TO_MM[getattr(bd.Unit, source_unit)]
        elif event == "start" and tag == "object":
            source_name = element.attrib.get("name", "")
            if len(source_name) > MAX_3MF_SOURCE_NAME_CHARS:
                raise Import3mfError(
                    "invalid_3mf_geometry", "The 3MF contains invalid object metadata."
                )
            current_object = _ObjectCounts(source_name)
        elif event == "start" and tag == "mesh" and current_object is not None:
            current_object.has_mesh = True
        elif event == "start" and tag == "vertex" and current_object is not None:
            if factor is None:
                raise _invalid_archive()
            total_vertices += 1
            current_object.vertices += 1
            limits.enforce("vertices", total_vertices)
            try:
                coordinates = tuple(
                    float(element.attrib[axis]) for axis in ("x", "y", "z")
                )
            except (KeyError, ValueError) as exc:
                raise Import3mfError(
                    "invalid_3mf_geometry", "The 3MF contains invalid coordinates."
                ) from exc
            if not all(math.isfinite(value) for value in coordinates):
                raise Import3mfError(
                    "invalid_3mf_geometry", "The 3MF contains invalid coordinates."
                )
            if any(
                abs(value * factor) > MAX_3MF_COORDINATE_MM for value in coordinates
            ):
                raise Import3mfError(
                    "3mf_resource_limit",
                    "The 3MF coordinates exceed the supported range.",
                )
        elif event == "start" and tag == "triangle" and current_object is not None:
            total_triangles += 1
            current_object.triangles += 1
            limits.enforce("triangles", total_triangles)
        elif event == "start" and tag == "component":
            component_elements = True
        elif event == "start" and tag == "item":
            build_item_count += 1
            object_id = element.attrib.get("objectid")
            duplicate_build_id = duplicate_build_id or object_id in build_ids
            build_ids.add(object_id)
            transformed_build_item = (
                transformed_build_item or "transform" in element.attrib
            )
        elif event == "end" and tag == "object" and current_object is not None:
            if current_object.has_mesh:
                mesh_object_count += 1
                limits.enforce("objects", mesh_object_count)
                names.append(current_object.source_name)
                vertex_counts.append(current_object.vertices)
                triangle_counts.append(current_object.triangles)
            current_object = None
        if event == "end":
            element.clear()
    if source_unit is None:
        raise _invalid_archive()
    if not names:
        raise Import3mfError(
            "invalid_3mf_geometry", "The 3MF contains no mesh objects."
        )
    has_unpreserved_components = (
        component_elements
        or transformed_build_item
        or duplicate_build_id
        or build_item_count != len(names)
    )
    return _ArchiveMetadata(
        source_unit,
        tuple(names),
        tuple(vertex_counts),
        tuple(triangle_counts),
        has_unpreserved_components,
    )


class _BoundedXmlReader:
    def __init__(self, stream: BinaryIO, max_bytes: int):
        self.stream = stream
        self.max_bytes = max_bytes
        self.byte_size = 0

    def read(self, size: int = -1) -> bytes:
        remaining_with_probe = self.max_bytes - self.byte_size + 1
        requested = (
            remaining_with_probe if size < 0 else min(size, remaining_with_probe)
        )
        chunk = self.stream.read(requested)
        self.byte_size += len(chunk)
        if self.byte_size > self.max_bytes:
            raise Import3mfError(
                "3mf_resource_limit", "The 3MF exceeds an import resource limit."
            )
        return chunk


def _with_bounded_xml(archive, info, max_bytes, parser):
    with archive.open(info) as stream:
        return parser(_BoundedXmlReader(stream, max_bytes))


def _secure_iterparse(stream, events=("end",)):
    requested_events = frozenset(events)
    ancestors = []
    for event, element in DefusedElementTree.iterparse(
        stream,
        events=("start", "end"),
        forbid_dtd=True,
        forbid_entities=True,
        forbid_external=True,
    ):
        if event == "start":
            ancestors.append(element)
            if event in requested_events:
                yield event, element
            continue

        if event in requested_events:
            yield event, element
        ancestors.pop()
        if ancestors:
            ancestors[-1].remove(element)
        element.clear()


def _validate_xml_document(stream: BinaryIO) -> None:
    for _, element in _secure_iterparse(stream):
        element.clear()


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
        raise Import3mfError(
            "invalid_3mf_geometry", "The 3MF geometry could not be read."
        ) from exc
    if not shapes or len(shapes) != len(metadata.source_names):
        raise Import3mfError(
            "invalid_3mf_geometry", "The 3MF contains unsupported geometry."
        )
    factor = UNIT_TO_MM.get(mesher.model_unit)
    if factor is None:
        raise Import3mfError(
            "invalid_3mf_geometry", "The 3MF uses an unsupported unit."
        )
    normalized = [shape.scale(factor) if factor != 1.0 else shape for shape in shapes]
    names = safe_part_names(list(metadata.source_names))
    parts = tuple(
        _part(
            index,
            names[index],
            metadata.source_names[index],
            shape,
            metadata.vertex_counts[index],
            metadata.triangle_counts[index],
        )
        for index, shape in enumerate(normalized)
    )
    compound = bd.Compound(normalized, children=normalized)
    brep_path = workdir / "source.brep"
    try:
        if not bd.export_brep(compound, brep_path):
            raise RuntimeError("BREP export failed")
        brep = brep_path.read_bytes()
    except (OSError, RuntimeError, ValueError) as exc:
        raise Import3mfError(
            "conversion_failed", "The converted model could not be saved."
        ) from exc
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
        warnings=("component_graph_not_preserved",)
        if metadata.has_unpreserved_components
        else (),
        parts=parts,
    )
    if len(manifest.model_dump_json().encode()) > MAX_3MF_MANIFEST_BYTES:
        raise Import3mfError(
            "3mf_resource_limit", "The converted manifest is too large."
        )
    loaded = load_brep_bytes(brep, workdir / "roundtrip.brep")
    _validate_brep_round_trip(loaded, parts)
    return ConversionOutput(brep, manifest)


def _part(
    index: int,
    name: str,
    source_name: str,
    shape: bd.Shape,
    vertices: int,
    triangles: int,
) -> Import3mfPart:
    box = shape.bounding_box()
    minimum = (float(box.min.X), float(box.min.Y), float(box.min.Z))
    maximum = (float(box.max.X), float(box.max.Y), float(box.max.Z))
    if not all(
        math.isfinite(value) and abs(value) <= MAX_3MF_COORDINATE_MM
        for value in (*minimum, *maximum)
    ):
        raise Import3mfError(
            "3mf_resource_limit", "The 3MF coordinates exceed the supported range."
        )
    is_solid = isinstance(shape, bd.Solid)
    is_valid = bool(shape.is_valid())
    return Import3mfPart(
        index=index,
        name=name,
        source_name=source_name,
        shape_type="solid" if is_solid else "shell",
        boolean_capable=is_solid and is_valid,
        is_valid=is_valid,
        vertex_count=vertices,
        triangle_count=triangles,
        bounds_mm=Import3mfBounds(min=minimum, max=maximum),
    )


def load_brep_bytes(content: bytes, path: Path) -> bd.Compound:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    shape = bd.import_brep(path)
    first_level = (
        shape.first_level_shapes() if isinstance(shape, bd.Compound) else [shape]
    )
    return bd.Compound(first_level, children=first_level)


def _validate_brep_round_trip(
    loaded: bd.Compound, parts: tuple[Import3mfPart, ...]
) -> None:
    shapes = loaded.first_level_shapes()
    if len(shapes) != len(parts):
        raise Import3mfError(
            "conversion_failed", "The converted model did not pass validation."
        )
    for index, (shape, part) in enumerate(zip(shapes, parts, strict=True)):
        box = shape.bounding_box()
        minimum = (float(box.min.X), float(box.min.Y), float(box.min.Z))
        maximum = (float(box.max.X), float(box.max.Y), float(box.max.Z))
        shape_type = "solid" if isinstance(shape, bd.Solid) else "shell"
        if (
            part.index != index
            or part.shape_type != shape_type
            or any(
                not math.isclose(actual, expected, abs_tol=BREP_BOUNDS_ABS_TOLERANCE_MM)
                for actual, expected in zip(
                    (*minimum, *maximum),
                    (*part.bounds_mm.min, *part.bounds_mm.max),
                    strict=True,
                )
            )
        ):
            raise Import3mfError(
                "conversion_failed", "The converted model did not pass validation."
            )


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
        status_path = workdir / "status.json"
        source_path.write_bytes(source)
        command = worker_command or [sys.executable, "-m", __name__]
        command = [
            *command,
            "--child",
            os.fspath(source_path),
            os.fspath(output_path),
            os.fspath(manifest_path),
            os.fspath(status_path),
        ]
        child_env = os.environ.copy()
        server_root = os.fspath(Path(__file__).resolve().parents[2])
        child_env["PYTHONPATH"] = os.pathsep.join(
            part for part in (server_root, child_env.get("PYTHONPATH", "")) if part
        )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=child_env,
            )
        except OSError as exc:
            raise Import3mfError(
                "conversion_failed", "The 3MF converter could not be started."
            ) from exc
        try:
            stdout_bytes, stderr_bytes = _communicate_bounded(process, timeout_seconds)
        except TimeoutError as exc:
            _terminate_process_group(process)
            raise Import3mfError(
                "3mf_conversion_timeout", "The 3MF conversion timed out."
            ) from exc
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        try:
            stdout = stdout_bytes.decode("utf-8", errors="strict")
            stderr = stderr_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise Import3mfError(
                "conversion_failed", "The 3MF converter returned invalid output."
            ) from exc
        status = _read_converter_status(status_path)
        if status is None:
            raise Import3mfError(
                "conversion_failed", "The 3MF conversion failed safely."
            )
        if stdout or (stderr and stderr not in ALLOWED_PYLIB3MF_SHUTDOWN_STDERR):
            raise Import3mfError(
                "conversion_failed", "The 3MF converter returned unexpected output."
            )
        if status.status == "failed":
            if process.returncode == 0:
                raise Import3mfError(
                    "conversion_failed", "The 3MF conversion failed safely."
                )
            assert status.error_code is not None and status.user_message is not None
            raise Import3mfError(status.error_code, status.user_message)
        valid_files = (
            output_path.is_file()
            and not output_path.is_symlink()
            and manifest_path.is_file()
            and not manifest_path.is_symlink()
        )
        if process.returncode != 0 or not valid_files:
            raise Import3mfError(
                "conversion_failed", "The 3MF conversion failed safely."
            )
        if not 0 < output_path.stat().st_size <= MAX_3MF_DERIVED_BREP_BYTES:
            raise Import3mfError(
                "3mf_resource_limit", "The converted model is too large."
            )
        if not 0 < manifest_path.stat().st_size <= MAX_3MF_MANIFEST_BYTES:
            raise Import3mfError(
                "3mf_resource_limit", "The converted manifest is too large."
            )
        brep = output_path.read_bytes()
        try:
            manifest = Import3mfManifest.model_validate_json(manifest_path.read_bytes())
        except Exception as exc:
            raise Import3mfError(
                "conversion_failed", "The 3MF converter returned an invalid result."
            ) from exc
        if (
            manifest.source_sha256 != hashlib.sha256(source).hexdigest()
            or manifest.brep_sha256 != hashlib.sha256(brep).hexdigest()
            or manifest.brep_byte_size != len(brep)
        ):
            raise Import3mfError(
                "conversion_failed", "The 3MF converter returned an invalid result."
            )
        try:
            loaded = load_brep_bytes(brep, workdir / "parent-roundtrip.brep")
            _validate_brep_round_trip(loaded, manifest.parts)
        except Import3mfError:
            raise
        except Exception as exc:
            raise Import3mfError(
                "conversion_failed", "The 3MF converter returned an invalid result."
            ) from exc
        return ConversionOutput(brep, manifest)


def _communicate_bounded(
    process: subprocess.Popen[bytes], timeout_seconds: float
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("converter subprocess pipes are unavailable")
    streams = {
        process.stdout.fileno(): bytearray(),
        process.stderr.fileno(): bytearray(),
    }
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                captured = streams[key.fd]
                if len(captured) + len(chunk) > MAX_SUBPROCESS_STREAM_BYTES:
                    _terminate_process_group(process)
                    raise Import3mfError(
                        "conversion_failed",
                        "The 3MF converter output exceeded its limit.",
                    )
                captured.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError from exc
    finally:
        selector.close()
    return bytes(streams[process.stdout.fileno()]), bytes(
        streams[process.stderr.fileno()]
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _invalid_archive() -> Import3mfError:
    return Import3mfError("invalid_3mf_archive", "The file is not a safe 3MF archive.")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_converter_status(path: Path) -> ConverterStatus | None:
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or not 0 < path.stat().st_size <= MAX_CONVERTER_STATUS_BYTES
        ):
            return None
        return ConverterStatus.model_validate_json(path.read_bytes())
    except OSError, ValueError:
        return None


def _write_converter_status(path: Path, status: ConverterStatus) -> None:
    content = status.model_dump_json().encode("utf-8")
    if len(content) > MAX_CONVERTER_STATUS_BYTES:
        raise RuntimeError("converter status exceeds its internal limit")
    path.write_bytes(content)


def _child(
    source_path: Path,
    output_path: Path,
    manifest_path: Path,
    status_path: Path,
) -> int:
    try:
        output = convert_3mf_bytes(
            source_path.read_bytes(), output_path.parent / "conversion"
        )
        output_path.write_bytes(output.brep_bytes)
        manifest_path.write_text(output.manifest.model_dump_json(), encoding="utf-8")
        _write_converter_status(
            status_path, ConverterStatus(schema_version=1, status="succeeded")
        )
        return 0
    except Import3mfError as exc:
        code: ChildErrorCode = (
            cast(ChildErrorCode, exc.code)
            if exc.code in _SAFE_CHILD_ERROR_MESSAGES
            else "conversion_failed"
        )
        _write_converter_status(
            status_path,
            ConverterStatus(
                schema_version=1,
                status="failed",
                error_code=code,
                user_message=_SAFE_CHILD_ERROR_MESSAGES[code],
            ),
        )
        return 1
    except Exception:
        _write_converter_status(
            status_path,
            ConverterStatus(
                schema_version=1,
                status="failed",
                error_code="conversion_failed",
                user_message=_SAFE_CHILD_ERROR_MESSAGES["conversion_failed"],
            ),
        )
        return 1


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("status", type=Path)
    args = parser.parse_args()
    if not args.child:
        return 2
    return _child(args.source, args.output, args.manifest, args.status)


if __name__ == "__main__":
    raise SystemExit(_main())
