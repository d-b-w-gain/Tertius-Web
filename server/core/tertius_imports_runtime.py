TERTIUS_IMPORTS_HELPER_SOURCE = r'''
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import build123d as bd

MAX_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2_048
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
UNIT_TO_MM = {
    bd.Unit.MC: 0.001,
    bd.Unit.MM: 1.0,
    bd.Unit.CM: 10.0,
    bd.Unit.M: 1000.0,
    bd.Unit.IN: 25.4,
    bd.Unit.FT: 304.8,
}


@dataclass(frozen=True)
class Imported3mfModel:
    parts: list[bd.Shape]
    parts_by_name: dict[str, bd.Shape]
    compound: bd.Compound


def _invalid_archive() -> RuntimeError:
    return RuntimeError("The file is not a safe 3MF archive.")


def _validate_archive(path: Path) -> None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_UPLOAD_BYTES:
            raise _invalid_archive()
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
                raise _invalid_archive()
            total_size = 0
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                posix_path = PurePosixPath(normalized)
                if (
                    info.flag_bits & 0x1
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or posix_path.is_absolute()
                    or any(part in {"", ".", ".."} for part in posix_path.parts)
                    or info.filename.startswith(("/", "\\"))
                    or bool(PureWindowsPath(info.filename).drive)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise _invalid_archive()
                total_size += info.file_size
                if total_size > MAX_UNCOMPRESSED_BYTES:
                    raise _invalid_archive()
    except RuntimeError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _invalid_archive() from exc


def load_3mf_model(name: str) -> Imported3mfModel:
    if name != "source":
        raise RuntimeError("Only the source 3MF import is supported.")
    path = Path.cwd() / "source.3mf"
    if not path.is_file():
        raise RuntimeError("source.3mf was not found in the project.")
    _validate_archive(path)
    try:
        mesher = bd.Mesher()
        shapes = list(mesher.read(path))
    except Exception as exc:
        raise RuntimeError("The 3MF geometry could not be read.") from exc
    if not shapes:
        raise RuntimeError("The 3MF contains no supported geometry.")
    factor = UNIT_TO_MM.get(mesher.model_unit)
    if factor is None:
        raise RuntimeError("The 3MF uses an unsupported unit.")
    parts = [shape.scale(factor) if factor != 1.0 else shape for shape in shapes]
    if any(not isinstance(shape, (bd.Solid, bd.Shell)) or not shape.is_valid() for shape in parts):
        raise RuntimeError("The 3MF contains invalid or unsupported geometry.")
    parts_by_name = {
        f"part_{index:03d}": shape for index, shape in enumerate(parts, start=1)
    }
    compound = bd.Compound(parts, children=parts)
    return Imported3mfModel(parts, parts_by_name, compound)
'''
