from __future__ import annotations

import io
from pathlib import PurePosixPath, PureWindowsPath
import zipfile

from core.project_assets import (
    MAX_3MF_ARCHIVE_ENTRIES,
    MAX_3MF_UNCOMPRESSED_BYTES,
    MAX_3MF_UPLOAD_BYTES,
)


class Invalid3mfArchiveError(ValueError):
    pass


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
            has_model = False
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if (
                    info.flag_bits & 0x1
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or info.filename.startswith(("/", "\\"))
                    or bool(PureWindowsPath(info.filename).drive)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise Invalid3mfArchiveError("The file is not a safe 3MF archive.")
                total_size += info.file_size
                if total_size > MAX_3MF_UNCOMPRESSED_BYTES:
                    raise Invalid3mfArchiveError("The 3MF exceeds the archive size limit.")
                has_model = has_model or path.suffix.lower() == ".model"
            if not has_model:
                raise Invalid3mfArchiveError("The file is not a valid 3MF archive.")
    except Invalid3mfArchiveError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise Invalid3mfArchiveError("The file is not a valid 3MF archive.") from exc
