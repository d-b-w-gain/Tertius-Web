import io
import zipfile

import pytest

from core import three_mf_archive as archive_validator
from core.three_mf_archive import Invalid3mfArchiveError, validate_3mf_archive_bytes
from tests.fixtures.three_mf import (
    UNSUPPORTED_BUILD_GRAPH_CASES,
    box_mesh,
    make_3mf,
    make_box_3mf,
)


def _archive(entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_validate_3mf_archive_accepts_generated_fixture():
    validate_3mf_archive_bytes(make_box_3mf())


def test_validate_3mf_archive_accepts_identity_multi_part_fixture():
    vertices, triangles = box_mesh()

    validate_3mf_archive_bytes(
        make_3mf(
            objects=[
                ("First", vertices, triangles),
                ("Second", vertices, triangles),
            ]
        )
    )


@pytest.mark.parametrize(
    ("case", "options"),
    UNSUPPORTED_BUILD_GRAPH_CASES,
    ids=[case for case, _options in UNSUPPORTED_BUILD_GRAPH_CASES],
)
def test_validate_3mf_archive_rejects_unsupported_build_graph(case, options):
    vertices, triangles = box_mesh()
    content = make_3mf(
        objects=[("First", vertices, triangles), ("Second", vertices, triangles)],
        **options,
    )

    with pytest.raises(
        Invalid3mfArchiveError, match="unsupported 3MF build graph"
    ) as raised:
        validate_3mf_archive_bytes(content)

    assert type(raised.value).__name__ == "Unsupported3mfBuildGraphError"


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


def test_validate_3mf_archive_enforces_xml_byte_limit(monkeypatch):
    monkeypatch.setattr(archive_validator, "MAX_3MF_XML_BYTES", 128)

    with pytest.raises(Invalid3mfArchiveError, match="archive size"):
        validate_3mf_archive_bytes(make_box_3mf())


def test_validate_3mf_archive_rejects_excessive_xml_depth(monkeypatch):
    monkeypatch.setattr(archive_validator, "MAX_3MF_XML_DEPTH", 8, raising=False)
    vertices, triangles = box_mesh()
    deep_metadata = "<metadata>" * 9 + "value" + "</metadata>" * 9
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f"{deep_metadata}"
        '<resources><object id="1" type="model"><mesh><vertices/>'
        '<triangles/></mesh></object></resources><build><item objectid="1"/></build>'
        "</model>"
    ).encode()

    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(
            make_3mf(
                objects=[("Box", vertices, triangles)],
                model_document=model,
            )
        )


def test_validate_3mf_archive_rejects_dtd_declaration():
    vertices, triangles = box_mesh()
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE model [<!ELEMENT model ANY>]>'
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources><object id="1" type="model"><mesh><vertices/>'
        '<triangles/></mesh></object></resources><build><item objectid="1"/></build>'
        "</model>"
    ).encode()

    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(
            make_3mf(
                objects=[("Box", vertices, triangles)],
                model_document=model,
            )
        )


def test_validate_3mf_archive_rejects_external_entity():
    vertices, triangles = box_mesh()
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE model [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        "<metadata>&xxe;</metadata>"
        '<resources><object id="1" type="model"><mesh><vertices/>'
        '<triangles/></mesh></object></resources><build><item objectid="1"/></build>'
        "</model>"
    ).encode()

    with pytest.raises(Invalid3mfArchiveError, match="valid 3MF"):
        validate_3mf_archive_bytes(
            make_3mf(
                objects=[("Box", vertices, triangles)],
                model_document=model,
            )
        )


def test_validate_3mf_archive_streams_and_clears_large_irrelevant_xml(monkeypatch):
    vertices, triangles = box_mesh()
    metadata = "".join(f'<metadata name="item-{index}">value</metadata>' for index in range(2000))
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f"{metadata}"
        '<resources><object id="1" type="model"><mesh><vertices/>'
        '<triangles/></mesh></object></resources><build><item objectid="1"/></build>'
        "</model>"
    ).encode()
    ended_elements = []
    retained_root_children = []
    root_element = None
    real_iterparse = archive_validator.DefusedElementTree.iterparse

    def tracking_iterparse(*args, **kwargs):
        nonlocal root_element
        for event, element in real_iterparse(*args, **kwargs):
            if event == "start" and element.tag.endswith("}model"):
                root_element = element
            if event == "end":
                ended_elements.append(element)
            yield event, element
            if event == "end" and root_element is not None:
                retained_root_children.append(len(root_element))

    monkeypatch.setattr(
        archive_validator.DefusedElementTree,
        "iterparse",
        tracking_iterparse,
    )

    validate_3mf_archive_bytes(
        make_3mf(
            objects=[("Box", vertices, triangles)],
            model_document=model,
        )
    )

    assert len(ended_elements) > 2000
    assert all(not element.attrib and not element.text and len(element) == 0 for element in ended_elements)
    assert max(retained_root_children) < 512
