from __future__ import annotations

import io
from pathlib import PurePosixPath, PureWindowsPath
import unicodedata
import zipfile
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from core.project_assets import (
    MAX_3MF_ARCHIVE_ENTRIES,
    MAX_3MF_UNCOMPRESSED_BYTES,
    MAX_3MF_UPLOAD_BYTES,
    MAX_3MF_XML_BYTES,
)

_MODEL_RELATIONSHIP_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_MODEL_NAMESPACE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MAX_3MF_XML_DEPTH = 64


class Invalid3mfArchiveError(ValueError):
    pass


class Unsupported3mfBuildGraphError(Invalid3mfArchiveError):
    pass


def _invalid() -> Invalid3mfArchiveError:
    return Invalid3mfArchiveError("The file is not a valid 3MF archive.")


def _unsupported() -> Unsupported3mfBuildGraphError:
    return Unsupported3mfBuildGraphError(
        "The file uses an unsupported 3MF build graph."
    )


def _canonical_entry_name(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name.replace("\\", "/"))
    return "/".join(PurePosixPath(normalized).parts).casefold()


class _CappedReader:
    def __init__(self, stream, max_bytes: int):
        self.stream = stream
        self.remaining = max_bytes

    def read(self, size: int = -1) -> bytes:
        requested = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        content = self.stream.read(requested)
        if len(content) > self.remaining:
            raise Invalid3mfArchiveError("The 3MF exceeds the archive size limit.")
        self.remaining -= len(content)
        return content


def _xml_events(archive: zipfile.ZipFile, info: zipfile.ZipInfo):
    if info.file_size > MAX_3MF_XML_BYTES:
        raise Invalid3mfArchiveError("The 3MF exceeds the archive size limit.")
    try:
        with archive.open(info) as stream:
            depth = 0
            for event, element in DefusedElementTree.iterparse(
                _CappedReader(stream, MAX_3MF_XML_BYTES),
                events=("start", "end"),
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            ):
                if event == "start":
                    depth += 1
                    if depth > MAX_3MF_XML_DEPTH:
                        raise _invalid()
                yield event, element
                if event == "end":
                    depth -= 1
    except Invalid3mfArchiveError:
        raise
    except (
        DefusedXmlException,
        OSError,
        ParseError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        raise _invalid() from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _root_model_target(
    archive: zipfile.ZipFile, relationships_info: zipfile.ZipInfo
) -> str:
    target: str | None = None
    elements = []
    for event, element in _xml_events(archive, relationships_info):
        if event == "start":
            elements.append(element)
            continue
        if event == "end":
            if _local_name(element.tag) == "Relationship":
                relationship_type = element.attrib.get("Type", "").rstrip("/")
                if relationship_type == _MODEL_RELATIONSHIP_TYPE:
                    if (
                        element.attrib.get("TargetMode", "Internal") != "Internal"
                        or target is not None
                    ):
                        raise _invalid()
                    target = element.attrib.get("Target", "")
            if len(elements) > 1:
                elements[-2].remove(element)
            element.clear()
            elements.pop()
    if not target or "?" in target or "#" in target or "\\" in target:
        raise _invalid()
    relative = target.lstrip("/")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _invalid()
    return _canonical_entry_name(relative)


def _validate_supported_model(
    archive: zipfile.ZipFile, model_info: zipfile.ZipInfo
) -> None:
    stack: list[str] = []
    all_object_ids: set[str] = set()
    mesh_object_ids: set[str] = set()
    built_ids: list[str | None] = []
    current_object_id: str | None = None
    build_count = 0
    elements = []

    for event, element in _xml_events(archive, model_info):
        local_name = _local_name(element.tag)
        if event == "start":
            elements.append(element)
            stack.append(local_name)
            if len(stack) == 1 and element.tag != f"{{{_MODEL_NAMESPACE}}}model":
                raise _invalid()
            if local_name == "components":
                raise _unsupported()
            if stack == ["model", "resources", "object"]:
                object_id = element.attrib.get("id")
                if not object_id or object_id in all_object_ids:
                    raise _unsupported()
                all_object_ids.add(object_id)
                current_object_id = object_id
            elif stack == ["model", "resources", "object", "mesh"]:
                if current_object_id is None:
                    raise _unsupported()
                mesh_object_ids.add(current_object_id)
            elif stack == ["model", "build"]:
                build_count += 1
            elif stack == ["model", "build", "item"]:
                if "transform" in element.attrib:
                    raise _unsupported()
                built_ids.append(element.attrib.get("objectid"))
            continue

        if stack == ["model", "resources", "object"]:
            current_object_id = None
        if len(elements) > 1:
            elements[-2].remove(element)
        element.clear()
        elements.pop()
        stack.pop()

    if (
        not mesh_object_ids
        or build_count != 1
        or any(object_id is None for object_id in built_ids)
        or len(built_ids) != len(set(built_ids))
        or set(built_ids) != mesh_object_ids
    ):
        raise _unsupported()


def validate_3mf_archive_bytes(content: bytes) -> None:
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    if not content or len(content) > MAX_3MF_UPLOAD_BYTES:
        raise Invalid3mfArchiveError("The 3MF exceeds the upload size limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_3MF_ARCHIVE_ENTRIES:
                raise Invalid3mfArchiveError("The file is not a safe 3MF archive.")
            total_size = 0
            entries: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                canonical = _canonical_entry_name(info.filename)
                if (
                    info.flag_bits & 0x1
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or info.filename.startswith(("/", "\\"))
                    or bool(PureWindowsPath(info.filename).drive)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                    or canonical in entries
                ):
                    raise Invalid3mfArchiveError("The file is not a safe 3MF archive.")
                total_size += info.file_size
                if total_size > MAX_3MF_UNCOMPRESSED_BYTES:
                    raise Invalid3mfArchiveError("The 3MF exceeds the archive size limit.")
                entries[canonical] = info

            if archive.testzip() is not None:
                raise _invalid()

            relationships_info = entries.get("_rels/.rels")
            if relationships_info is None:
                raise _invalid()
            model_target = _root_model_target(archive, relationships_info)
            model_info = entries.get(model_target)
            if model_info is None or PurePosixPath(model_target).suffix != ".model":
                raise _invalid()
            _validate_supported_model(archive, model_info)
    except Invalid3mfArchiveError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _invalid() from exc
