import io
import zipfile

import pytest

from core.three_mf_archive import Invalid3mfArchiveError, validate_3mf_archive_bytes
from tests.fixtures.three_mf import box_mesh, make_3mf, make_box_3mf


def _archive(entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_validate_3mf_archive_accepts_generated_fixture():
    validate_3mf_archive_bytes(make_box_3mf())


@pytest.mark.parametrize("name", ["../3D/model.model", "/3D/model.model", "C:\\3D\\model.model"])
def test_validate_3mf_archive_rejects_unsafe_paths(name):
    with pytest.raises(Invalid3mfArchiveError, match="safe 3MF"):
        validate_3mf_archive_bytes(_archive({name: b"<model/>"}))


def test_validate_3mf_archive_requires_model_entry():
    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(_archive({"[Content_Types].xml": b"<Types/>"}))


def test_validate_3mf_archive_requires_opc_root_relationship():
    vertices, triangles = box_mesh()

    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(
            make_3mf(objects=[("Box", vertices, triangles)], include_relationship=False)
        )


def test_validate_3mf_archive_rejects_missing_relationship_target():
    vertices, triangles = box_mesh()

    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(
            make_3mf(
                objects=[("Box", vertices, triangles)],
                relationship_target="/3D/missing.model",
            )
        )


def test_validate_3mf_archive_rejects_malformed_model_xml():
    vertices, triangles = box_mesh()

    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(
            make_3mf(
                objects=[("Box", vertices, triangles)],
                model_document=b"<model><broken></model>",
            )
        )


def test_validate_3mf_archive_rejects_arbitrary_zip_with_model_suffix():
    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(_archive({"3D/model.model": b"not XML"}))


def test_validate_3mf_archive_rejects_too_many_entries(monkeypatch):
    monkeypatch.setattr("core.three_mf_archive.MAX_3MF_ARCHIVE_ENTRIES", 1)

    with pytest.raises(Invalid3mfArchiveError, match="safe 3MF"):
        validate_3mf_archive_bytes(
            _archive({"3D/model.model": b"<model/>", "extra.txt": b"extra"})
        )


def test_validate_3mf_archive_rejects_large_declared_content(monkeypatch):
    monkeypatch.setattr("core.three_mf_archive.MAX_3MF_UNCOMPRESSED_BYTES", 4)

    with pytest.raises(Invalid3mfArchiveError, match="archive size"):
        validate_3mf_archive_bytes(_archive({"3D/model.model": b"12345"}))
