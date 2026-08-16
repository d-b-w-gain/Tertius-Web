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


class Invalid3mfArchiveError(ValueError):
    pass


def _invalid() -> Invalid3mfArchiveError:
    return Invalid3mfArchiveError("The file is not a valid 3MF archive.")


def _canonical_entry_name(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name.replace("\\", "/"))
    return "/".join(PurePosixPath(normalized).parts).casefold()


def _read_xml(archive: zipfile.ZipFile, info: zipfile.ZipInfo):
    if info.file_size > MAX_3MF_XML_BYTES:
        raise Invalid3mfArchiveError("The 3MF exceeds the archive size limit.")
    try:
        with archive.open(info) as stream:
            content = stream.read(MAX_3MF_XML_BYTES + 1)
        if len(content) > MAX_3MF_XML_BYTES:
            raise Invalid3mfArchiveError("The 3MF exceeds the archive size limit.")
        return DefusedElementTree.fromstring(content)
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


def _root_model_target(relationships) -> str:
    target: str | None = None
    for element in relationships.iter():
        if element.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        relationship_type = element.attrib.get("Type", "").rstrip("/")
        if relationship_type != _MODEL_RELATIONSHIP_TYPE:
            continue
        if element.attrib.get("TargetMode", "Internal") != "Internal" or target is not None:
            raise _invalid()
        target = element.attrib.get("Target", "")
    if not target or "?" in target or "#" in target or "\\" in target:
        raise _invalid()
    relative = target.lstrip("/")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _invalid()
    return _canonical_entry_name(relative)


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
            model_target = _root_model_target(_read_xml(archive, relationships_info))
            model_info = entries.get(model_target)
            if model_info is None or PurePosixPath(model_target).suffix != ".model":
                raise _invalid()
            model = _read_xml(archive, model_info)
            if model.tag != f"{{{_MODEL_NAMESPACE}}}model":
                raise _invalid()
            if not any(element.tag == f"{{{_MODEL_NAMESPACE}}}mesh" for element in model.iter()):
                raise _invalid()
    except Invalid3mfArchiveError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _invalid() from exc
