from __future__ import annotations

import json
import math
import struct
from typing import Any, Iterable


Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]
Bounds3 = tuple[list[float], list[float]]


def _identity() -> Matrix4:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _multiply(left: Matrix4, right: Matrix4) -> Matrix4:
    return tuple(
        tuple(
            sum(left[row][index] * right[index][column] for index in range(4))
            for column in range(4)
        )
        for row in range(4)
    )  # type: ignore[return-value]


def _node_matrix(node: dict[str, Any]) -> Matrix4:
    authored = node.get("matrix")
    if isinstance(authored, list) and len(authored) == 16:
        values = [float(value) for value in authored]
        # glTF stores matrices column-major.
        return tuple(
            tuple(values[column * 4 + row] for column in range(4))
            for row in range(4)
        )  # type: ignore[return-value]

    translation = node.get("translation")
    tx, ty, tz = (
        tuple(float(value) for value in translation)
        if isinstance(translation, list) and len(translation) == 3
        else (0.0, 0.0, 0.0)
    )
    scale = node.get("scale")
    sx, sy, sz = (
        tuple(float(value) for value in scale)
        if isinstance(scale, list) and len(scale) == 3
        else (1.0, 1.0, 1.0)
    )
    rotation = node.get("rotation")
    qx, qy, qz, qw = (
        tuple(float(value) for value in rotation)
        if isinstance(rotation, list) and len(rotation) == 4
        else (0.0, 0.0, 0.0, 1.0)
    )
    magnitude = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if magnitude > 0:
        qx, qy, qz, qw = (
            qx / magnitude,
            qy / magnitude,
            qz / magnitude,
            qw / magnitude,
        )
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    return (
        ((1 - 2 * (yy + zz)) * sx, (2 * (xy - wz)) * sy, (2 * (xz + wy)) * sz, tx),
        ((2 * (xy + wz)) * sx, (1 - 2 * (xx + zz)) * sy, (2 * (yz - wx)) * sz, ty),
        ((2 * (xz - wy)) * sx, (2 * (yz + wx)) * sy, (1 - 2 * (xx + yy)) * sz, tz),
        (0.0, 0.0, 0.0, 1.0),
    )


def _transform_point(matrix: Matrix4, point: Iterable[float]) -> list[float]:
    x, y, z = (float(value) for value in point)
    return [
        matrix[row][0] * x
        + matrix[row][1] * y
        + matrix[row][2] * z
        + matrix[row][3]
        for row in range(3)
    ]


def _merge_bounds(current: Bounds3 | None, candidate: Bounds3) -> Bounds3:
    if current is None:
        return ([*candidate[0]], [*candidate[1]])
    return (
        [min(current[0][axis], candidate[0][axis]) for axis in range(3)],
        [max(current[1][axis], candidate[1][axis]) for axis in range(3)],
    )


def _transform_bounds(bounds: Bounds3, matrix: Matrix4) -> Bounds3:
    corners = [
        _transform_point(matrix, (x, y, z))
        for x in (bounds[0][0], bounds[1][0])
        for y in (bounds[0][1], bounds[1][1])
        for z in (bounds[0][2], bounds[1][2])
    ]
    return (
        [min(point[axis] for point in corners) for axis in range(3)],
        [max(point[axis] for point in corners) for axis in range(3)],
    )


def _gltf_document(kind: str, content: bytes) -> dict[str, Any] | None:
    if kind == "gltf":
        value = json.loads(content.decode("utf-8"))
        return value if isinstance(value, dict) else None
    if kind != "glb" or len(content) < 20:
        return None
    magic, version, _length = struct.unpack("<4sII", content[:12])
    chunk_length, chunk_type = struct.unpack("<I4s", content[12:20])
    if magic != b"glTF" or version != 2 or chunk_type != b"JSON":
        return None
    chunk_end = 20 + chunk_length
    if chunk_end > len(content):
        return None
    value = json.loads(content[20:chunk_end].decode("utf-8").rstrip(" \t\r\n\x00"))
    return value if isinstance(value, dict) else None


def _numeric_triplet(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    values = [float(item) for item in value[:3]]
    return values if all(math.isfinite(item) for item in values) else None


def _mesh_bounds(document: dict[str, Any]) -> dict[int, Bounds3]:
    accessors = document.get("accessors")
    meshes = document.get("meshes")
    if not isinstance(accessors, list) or not isinstance(meshes, list):
        return {}
    resolved: dict[int, Bounds3] = {}
    for mesh_index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            continue
        bounds: Bounds3 | None = None
        for primitive in mesh.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes")
            accessor_index = attributes.get("POSITION") if isinstance(attributes, dict) else None
            if not isinstance(accessor_index, int) or not (0 <= accessor_index < len(accessors)):
                continue
            accessor = accessors[accessor_index]
            if not isinstance(accessor, dict):
                continue
            minimum = _numeric_triplet(accessor.get("min"))
            maximum = _numeric_triplet(accessor.get("max"))
            if minimum is None or maximum is None:
                continue
            bounds = _merge_bounds(bounds, (minimum, maximum))
        if bounds is not None:
            resolved[mesh_index] = bounds
    return resolved


def _roof_node(node: dict[str, Any], inherited: bool) -> bool:
    if inherited:
        return True
    extras = node.get("extras")
    if isinstance(extras, dict):
        role = str(extras.get("tertiusSiteRole") or "").strip().lower()
        if role == "roof":
            return True
    name = str(node.get("name") or "").lower().replace("_", "-")
    return "roof" in {token for token in name.replace(" ", "-").split("-") if token}


def _metadata_dimensions(document: dict[str, Any]) -> dict[str, Any] | None:
    extras = document.get("extras")
    metadata = extras.get("tertiusModelGeometry") if isinstance(extras, dict) else None
    if not isinstance(metadata, dict):
        return None
    required = ("footprint_length_m", "footprint_width_m", "overall_height_m")
    if not all(isinstance(metadata.get(field), (int, float)) for field in required):
        return None
    return dict(metadata)


def model_site_dimensions(kind: str, content: bytes) -> dict[str, Any] | None:
    """Return map-scale site dimensions from the active glTF scene tree.

    glTF is Y-up. The Open CASCADE exporter places the CAD Z-up conversion on
    the root node, so evaluated scene X/Z are the two plan axes and scene Y is
    height. This uses accessor bounds and authored node transforms, not a
    guessed default or a screen-space measurement.
    """

    document = _gltf_document(kind, content)
    if document is None:
        return None
    compiled = _metadata_dimensions(document)
    if compiled is not None:
        return compiled

    nodes = document.get("nodes")
    meshes = _mesh_bounds(document)
    if not isinstance(nodes, list) or not meshes:
        return None
    scenes = document.get("scenes")
    scene_index = document.get("scene", 0)
    roots: list[int] = []
    if (
        isinstance(scenes, list)
        and isinstance(scene_index, int)
        and 0 <= scene_index < len(scenes)
        and isinstance(scenes[scene_index], dict)
    ):
        roots = [
            value
            for value in (scenes[scene_index].get("nodes") or [])
            if isinstance(value, int)
        ]
    if not roots:
        referenced = {
            child
            for node in nodes
            if isinstance(node, dict)
            for child in (node.get("children") or [])
            if isinstance(child, int)
        }
        roots = [index for index in range(len(nodes)) if index not in referenced]

    overall: Bounds3 | None = None
    roof: Bounds3 | None = None
    visiting: set[int] = set()

    def visit(index: int, parent: Matrix4, inherited_roof: bool) -> None:
        nonlocal overall, roof
        if index in visiting or not (0 <= index < len(nodes)):
            return
        node = nodes[index]
        if not isinstance(node, dict):
            return
        visiting.add(index)
        world = _multiply(parent, _node_matrix(node))
        is_roof = _roof_node(node, inherited_roof)
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int) and mesh_index in meshes:
            transformed = _transform_bounds(meshes[mesh_index], world)
            overall = _merge_bounds(overall, transformed)
            if is_roof:
                roof = _merge_bounds(roof, transformed)
        for child in node.get("children") or []:
            if isinstance(child, int):
                visit(child, world, is_roof)
        visiting.remove(index)

    for root in roots:
        visit(root, _identity(), False)
    if overall is None:
        return None

    plan_x = overall[1][0] - overall[0][0]
    height = overall[1][1] - overall[0][1]
    plan_z = overall[1][2] - overall[0][2]
    if not all(math.isfinite(value) and value > 0 for value in (plan_x, plan_z, height)):
        return None
    length, width = max(plan_x, plan_z), min(plan_x, plan_z)
    roof_eave = None
    roof_ridge = None
    reference_height = height
    reference_basis = "overall model height (conservative fallback; no roof role found)"
    if roof is not None:
        roof_eave = max(0.0, roof[0][1] - overall[0][1])
        roof_ridge = max(roof_eave, roof[1][1] - overall[0][1])
        if roof_ridge > 0:
            reference_height = (roof_eave + roof_ridge) / 2.0
            reference_basis = "mid-height of roof components in the authored glTF tree"

    return {
        "schema_version": "tertius.model-site-dimensions.v1",
        "footprint_length_m": round(length, 6),
        "footprint_width_m": round(width, 6),
        "overall_height_m": round(height, 6),
        "reference_height_m": round(reference_height, 6),
        "roof_eave_height_m": round(roof_eave, 6) if roof_eave is not None else None,
        "roof_ridge_height_m": round(roof_ridge, 6) if roof_ridge is not None else None,
        "reference_height_basis": reference_basis,
        "source": "active candidate glTF scene bounds",
    }
