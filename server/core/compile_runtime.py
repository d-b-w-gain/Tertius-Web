from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from core.repositories import require_valid_project_filename

ALLOWED_RUNTIME_SIDECAR_FILES = {"settings.json"}


def require_valid_runtime_filename(filename: str) -> str:
    if filename in ALLOWED_RUNTIME_SIDECAR_FILES:
        return filename
    return require_valid_project_filename(filename)


def runtime_files_hash(files: dict[str, str]) -> str:
    """Hash an exact, order-independent project source bundle."""

    payload = json.dumps(
        files,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def structural_runtime_files_hash(files: dict[str, str]) -> str:
    """Hash files that can change compiled geometry/structural topology.

    ``tertius_site.py`` is deliberately excluded. Its validated inputs are
    overlaid when structural analysis is requested, so changing a site basis
    must not invalidate or rebuild an otherwise current Build123D artifact.
    """

    return runtime_files_hash(
        {
            filename: content
            for filename, content in files.items()
            if filename != "tertius_site.py"
        }
    )


@contextmanager
def hydrate_project_files(
    files: dict[str, str], binary_files: dict[str, bytes] | None = None
) -> Iterator[Path]:
    with TemporaryDirectory(prefix="tertius-project-") as tmp:
        project_dir = Path(tmp)
        for filename, content in files.items():
            safe_name = require_valid_runtime_filename(filename)
            (project_dir / safe_name).write_text(content, encoding="utf-8")
        for binary_filename, binary_content in (binary_files or {}).items():
            if binary_filename != "source.3mf":
                raise ValueError("Invalid binary runtime filename")
            (project_dir / binary_filename).write_bytes(binary_content)
        yield project_dir
