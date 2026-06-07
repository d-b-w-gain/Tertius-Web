from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from core.repositories import require_valid_python_filename


@contextmanager
def hydrate_project_files(files: dict[str, str]) -> Iterator[Path]:
    with TemporaryDirectory(prefix="tertius-project-") as tmp:
        project_dir = Path(tmp)
        for filename, content in files.items():
            safe_name = require_valid_python_filename(filename)
            (project_dir / safe_name).write_text(content, encoding="utf-8")
        yield project_dir
