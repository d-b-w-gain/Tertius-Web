from pathlib import Path
from uuid import uuid4

import pytest

from core.artifacts import ArtifactStore
from core.compile_runtime import hydrate_project_files


def test_artifact_store_writes_tenant_scoped_file(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    tenant_id = uuid4()
    project_id = uuid4()

    result = store.write_bytes(tenant_id, project_id, "stl", b"solid test")

    assert result.storage_key.endswith(".stl")
    assert str(tenant_id) in result.storage_key
    assert str(project_id) in result.storage_key
    assert result.content_type == "application/octet-stream"
    assert result.byte_size == len(b"solid test")
    assert store.path_for(result.storage_key).read_bytes() == b"solid test"


def test_artifact_store_unknown_kind_defaults_to_octet_stream(tmp_path: Path):
    store = ArtifactStore(tmp_path)

    result = store.write_bytes(uuid4(), uuid4(), "unknown", b"data")

    assert result.storage_key.endswith(".unknown")
    assert result.content_type == "application/octet-stream"


def test_artifact_store_rejects_path_traversal(tmp_path: Path):
    store = ArtifactStore(tmp_path)

    with pytest.raises(ValueError):
        store.path_for("../outside.stl")


def test_artifact_store_rejects_absolute_storage_key(tmp_path: Path):
    store = ArtifactStore(tmp_path)

    with pytest.raises(ValueError):
        store.path_for("/tmp/outside.stl")


def test_artifact_store_rejects_paths_that_escape_root(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        store.path_for("tenant/../../outside.stl")


def test_hydrate_project_files_creates_python_files_and_cleans_up():
    with hydrate_project_files({"design.py": "x = 1", "helpers.py": "y = 2"}) as project_dir:
        assert (project_dir / "design.py").read_text(encoding="utf-8") == "x = 1"
        assert (project_dir / "helpers.py").read_text(encoding="utf-8") == "y = 2"
        captured_dir = project_dir

    assert not captured_dir.exists()


@pytest.mark.parametrize("filename", ["../design.py", "/tmp/design.py", "notes.txt", "nested/helpers.py"])
def test_hydrate_project_files_rejects_invalid_filenames(filename: str):
    with pytest.raises(ValueError):
        with hydrate_project_files({filename: "x = 1"}):
            pass
