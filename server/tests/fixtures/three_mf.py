from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable


_UNIT_NAMES = {
    "MC": "micron",
    "MM": "millimeter",
    "CM": "centimeter",
    "M": "meter",
    "IN": "inch",
    "FT": "foot",
}

TRANSLATED_BUILD_TRANSFORM = "1 0 0 0 1 0 0 0 1 10 0 0"
UNSUPPORTED_BUILD_GRAPH_CASES = (
    ("transform", {"build_transform": TRANSLATED_BUILD_TRANSFORM}),
    ("repeated-build-object", {"build_object_ids": [1, 1]}),
    (
        "component-assembly",
        {"component_object_ids": [1, 2], "build_object_ids": [3]},
    ),
    ("mesh-subset", {"build_object_ids": [1]}),
    ("missing-object", {"build_object_ids": [99]}),
    (
        "non-mesh-object",
        {"include_non_mesh_object": True, "build_object_ids": [3]},
    ),
    ("missing-build", {"include_build": False}),
    ("no-mesh-objects", {"include_mesh_objects": False}),
)


def make_3mf(
    *,
    unit: str = "MM",
    objects: Iterable[
        tuple[str, list[tuple[float, float, float]], list[tuple[int, int, int]]]
    ],
    extra_entries: dict[str, bytes] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
    relationship_target: str = "/3D/3dmodel.model",
    include_relationship: bool = True,
    build_transform: str | None = None,
    build_object_ids: Iterable[int] | None = None,
    component_object_ids: Iterable[int] | None = None,
    include_non_mesh_object: bool = False,
    include_build: bool = True,
    include_mesh_objects: bool = True,
    model_document: bytes | None = None,
    relationships_document: bytes | None = None,
) -> bytes:
    object_xml: list[str] = []
    mesh_object_ids: list[int] = []
    source_objects = objects if include_mesh_objects else []
    for object_id, (name, vertices, triangles) in enumerate(source_objects, 1):
        mesh_object_ids.append(object_id)
        vertex_xml = "".join(
            f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices
        )
        triangle_xml = "".join(
            f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles
        )
        escaped_name = (
            name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        )
        object_xml.append(
            f'<object id="{object_id}" type="model" name="{escaped_name}">'
            f"<mesh><vertices>{vertex_xml}</vertices><triangles>{triangle_xml}</triangles></mesh>"
            "</object>"
        )
    next_object_id = len(mesh_object_ids) + 1
    if component_object_ids is not None:
        component_xml = "".join(
            f'<component objectid="{object_id}"/>' for object_id in component_object_ids
        )
        object_xml.append(
            f'<object id="{next_object_id}" type="model">'
            f"<components>{component_xml}</components></object>"
        )
        next_object_id += 1
    if include_non_mesh_object:
        object_xml.append(f'<object id="{next_object_id}" type="model"/>')
    selected_build_ids = (
        list(build_object_ids) if build_object_ids is not None else mesh_object_ids
    )
    transform = f' transform="{build_transform}"' if build_transform else ""
    build_xml = "".join(
        f'<item objectid="{object_id}"{transform}/>' for object_id in selected_build_ids
    )
    build = f"<build>{build_xml}</build>" if include_build else ""
    model = (
        model_document
        or (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<model unit="{_UNIT_NAMES[unit]}" xml:lang="en-US" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            f"<resources>{''.join(object_xml)}</resources>"
            f"{build}</model>"
        ).encode()
    )
    content_types = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        b"</Types>"
    )
    relationships = (
        relationships_document
        or (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Target="{relationship_target}" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
            "</Relationships>"
        ).encode()
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        if include_relationship:
            archive.writestr("_rels/.rels", relationships)
        archive.writestr("3D/3dmodel.model", model)
        for path, content in (extra_entries or {}).items():
            archive.writestr(path, content)
    return output.getvalue()


def box_mesh(size: float = 1.0):
    vertices = [
        (0, 0, 0),
        (size, 0, 0),
        (size, size, 0),
        (0, size, 0),
        (0, 0, size),
        (size, 0, size),
        (size, size, size),
        (0, size, size),
    ]
    triangles = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]
    return vertices, triangles


def make_box_3mf(*, unit: str = "MM", size: float = 1.0, name: str = "Box") -> bytes:
    vertices, triangles = box_mesh(size)
    return make_3mf(unit=unit, objects=[(name, vertices, triangles)])


def make_open_shell_3mf(*, name: str = "Panel") -> bytes:
    return make_3mf(objects=[(name, [(0, 0, 0), (10, 0, 0), (0, 10, 0)], [(0, 1, 2)])])


def make_invalid_multishell_solid_3mf(*, name: str = "Disconnected") -> bytes:
    first_vertices, first_triangles = box_mesh()
    second_vertices = [(x + 2, y, z) for x, y, z in first_vertices]
    return make_3mf(
        objects=[
            (
                name,
                first_vertices + second_vertices,
                first_triangles
                + [(a + 8, b + 8, c + 8) for a, b, c in first_triangles],
            )
        ]
    )
