from __future__ import annotations

import re
from typing import Any

from .model import GENERATED_NAME_PATTERNS

def _node_name(node: dict[str, Any]) -> str:
    return str(node.get("name") or "")


def _is_mesh(node: dict[str, Any]) -> bool:
    return bool(node.get("isMesh")) or node.get("type") == "Mesh" or "mesh" in node


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children")
    return children if isinstance(children, list) else []


def _is_generated_or_default(name: str) -> bool:
    return any(pattern.match(name.strip()) for pattern in GENERATED_NAME_PATTERNS)


def _has_mesh_descendant(node: dict[str, Any]) -> bool:
    return not _bom_disabled(node) and (
        _is_mesh(node) or any(_has_mesh_descendant(child) for child in _children(node))
    )


def _is_group(node: dict[str, Any]) -> bool:
    return not _is_mesh(node) and bool(_children(node))


def _is_named_group(node: dict[str, Any]) -> bool:
    return _is_group(node) and not _is_generated_or_default(_node_name(node))


def _tertius_bom_metadata(node: dict[str, Any]) -> dict[str, Any] | None:
    extras = node.get("extras")
    if not isinstance(extras, dict):
        return None
    metadata = extras.get("tertiusBom")
    return metadata if isinstance(metadata, dict) else None


def _bom_disabled(node: dict[str, Any]) -> bool:
    metadata = _tertius_bom_metadata(node)
    return metadata is not None and metadata.get("bom") is False


def _bom_enabled_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [child for child in _children(node) if not _bom_disabled(child)]


def _has_group_child_with_meshes(node: dict[str, Any]) -> bool:
    return any(_is_group(child) and _has_mesh_descendant(child) for child in _bom_enabled_children(node))


def _has_named_mesh_child(node: dict[str, Any]) -> bool:
    return any(
        _is_mesh(child) and not _is_generated_or_default(_node_name(child))
        for child in _bom_enabled_children(node)
    )


def _mesh_descendants(node: dict[str, Any]) -> list[dict[str, Any]]:
    meshes: list[dict[str, Any]] = []

    def collect(current: dict[str, Any]) -> None:
        if _bom_disabled(current):
            return
        if _is_mesh(current):
            meshes.append(current)
            return
        for child in _bom_enabled_children(current):
            collect(child)

    collect(node)
    return meshes


def _is_plural_bucket_name(name: str) -> bool:
    words = [word.lower() for word in re.split(r"[^A-Za-z0-9]+", name.strip()) if word]
    if not words:
        return False
    last = words[-1]
    return len(last) > 3 and last.endswith("s") and not last.endswith("ss")


def _normalize_transform_number(value: Any) -> float:
    number = round(float(value), 6)
    return 0.0 if number == -0.0 else number


def _transform_signature(node: dict[str, Any]) -> tuple[tuple[str, tuple[float, ...]], ...] | None:
    signature: list[tuple[str, tuple[float, ...]]] = []
    for key in ("translation", "rotation", "scale", "matrix"):
        value = node.get(key)
        if isinstance(value, list):
            try:
                signature.append((key, tuple(_normalize_transform_number(item) for item in value)))
            except (TypeError, ValueError):
                continue
    return tuple(signature) or None


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


def _human_label(value: str) -> str:
    text = re.sub(r"^(make|build)_", "", value.strip())
    text = text.replace("_", " ").replace("-", " ")
    words = [word for word in text.split() if word]
    if not words:
        return value
    acronyms = {"zc": "Z/C", "gpb": "GPB", "cp": "CP"}
    return " ".join(acronyms.get(word.lower(), word.capitalize()) for word in words)


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def analyze_gltf_tree(gltf: dict[str, Any]) -> dict[str, Any]:
    """Classify a simplified GLTF tree or scene tree into assemblies/components."""

    roots = _children(gltf) or [gltf]
    assemblies: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    object_to_assembly: dict[int, str] = {}
    used_ids: set[str] = set()

    def path_for(ancestors: list[dict[str, Any]], node: dict[str, Any]) -> str:
        parts = [_node_name(item) or str(item.get("type") or "node") for item in [*ancestors, node]]
        return "/".join(part.replace("/", "_") for part in parts if part)

    def visual_node_ids_for(node: dict[str, Any], ancestors: list[dict[str, Any]]) -> list[str]:
        visual_ids: list[str] = []

        def collect(current: dict[str, Any], current_ancestors: list[dict[str, Any]]) -> None:
            if _is_mesh(current):
                visual_ids.append(str(current.get("id") or path_for(current_ancestors, current)))
                return

            named_mesh_children = [
                child
                for child in _bom_enabled_children(current)
                if _is_named_group(child) and _has_mesh_descendant(child)
            ]
            if named_mesh_children and all(
                _is_generated_or_default(_node_name(child))
                for child in named_mesh_children
            ):
                for child in named_mesh_children:
                    visual_ids.append(str(child.get("id") or path_for([*current_ancestors, current], child)))
                return

            for child in _bom_enabled_children(current):
                collect(child, [*current_ancestors, current])

        collect(node, ancestors)
        return visual_ids or [str(node.get("id") or path_for(ancestors, node))]

    def visual_node_ids_for_children(
        children: list[dict[str, Any]],
        ancestors: list[dict[str, Any]],
        parent: dict[str, Any],
    ) -> list[str]:
        visual_ids: list[str] = []
        for child in children:
            if _bom_disabled(child):
                continue
            visual_ids.extend(visual_node_ids_for(child, [*ancestors, parent]))
        return visual_ids

    def add_component(
        *,
        label: str,
        path: str,
        assembly_id: str | None,
        visual_node_ids: list[str],
        bom_metadata: dict[str, Any] | None = None,
    ) -> None:
        component_id = _unique_id(_slug(path, "component"), used_ids)
        components.append({
            "id": component_id,
            "label": label,
            "path": path,
            "assembly_id": assembly_id,
            "visual_node_ids": visual_node_ids,
            "visual_instance_count": None,
            **({"bom_metadata": bom_metadata} if bom_metadata is not None else {}),
        })

    def parent_assembly_id(ancestors: list[dict[str, Any]]) -> str | None:
        return next((object_to_assembly[id(item)] for item in reversed(ancestors) if id(item) in object_to_assembly), None)

    def add_assembly(node: dict[str, Any], ancestors: list[dict[str, Any]]) -> str:
        existing = object_to_assembly.get(id(node))
        if existing:
            return existing
        name = _node_name(node)
        path = path_for(ancestors, node)
        assembly_id = _unique_id(_slug(path, "assembly"), used_ids)
        object_to_assembly[id(node)] = assembly_id
        assemblies.append({
            "id": assembly_id,
            "label": name,
            "path": path,
            "parent_id": parent_assembly_id(ancestors),
        })
        return assembly_id

    def is_single_mesh_component_wrapper(node: dict[str, Any]) -> bool:
        name = _node_name(node)
        if not _is_group(node) or not name or _is_generated_or_default(name):
            return False
        if any(_is_named_group(child) and _has_mesh_descendant(child) for child in _bom_enabled_children(node)):
            return False
        return len(_mesh_descendants(node)) == 1

    def direct_named_mesh_children_by_label(node: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for child in _bom_enabled_children(node):
            child_name = _node_name(child)
            if _is_mesh(child) and child_name and not _is_generated_or_default(child_name):
                grouped.setdefault(child_name, []).append(child)
        return grouped

    def direct_generated_mesh_children(node: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            child
            for child in _bom_enabled_children(node)
            if _is_mesh(child) and _is_generated_or_default(_node_name(child))
        ]

    def direct_named_fragments_by_label_and_transform(node: dict[str, Any]) -> dict[tuple[str, tuple[tuple[str, tuple[float, ...]], ...]], list[dict[str, Any]]]:
        grouped: dict[tuple[str, tuple[tuple[str, tuple[float, ...]], ...]], list[dict[str, Any]]] = {}
        for child in _bom_enabled_children(node):
            child_name = _node_name(child)
            signature = _transform_signature(child)
            if not child_name or _is_generated_or_default(child_name) or signature is None:
                continue
            if not (
                _is_mesh(child)
                or _tertius_bom_metadata(child) is not None
                or is_single_mesh_component_wrapper(child)
            ):
                continue
            grouped.setdefault((child_name, signature), []).append(child)
        return grouped

    def visit(node: dict[str, Any], ancestors: list[dict[str, Any]]) -> None:
        name = _node_name(node)
        if _bom_disabled(node):
            return
        bom_metadata = _tertius_bom_metadata(node)
        if _is_mesh(node):
            if bom_metadata is not None or (name and not _is_generated_or_default(name)):
                path = path_for(ancestors, node)
                add_component(
                    label=name,
                    path=path,
                    assembly_id=parent_assembly_id(ancestors),
                    visual_node_ids=[str(node.get("id") or path)],
                    bom_metadata=bom_metadata,
                )
            return
        if _is_group(node) and _has_mesh_descendant(node):
            if bom_metadata is not None:
                path = path_for(ancestors, node)
                add_component(
                    label=name or str(bom_metadata.get("part_number") or bom_metadata.get("product_key") or "BoM Item"),
                    path=path,
                    assembly_id=parent_assembly_id(ancestors),
                    visual_node_ids=visual_node_ids_for(node, ancestors),
                    bom_metadata=bom_metadata,
                )
                return

            if is_single_mesh_component_wrapper(node):
                path = path_for(ancestors, node)
                add_component(
                    label=name,
                    path=path,
                    assembly_id=parent_assembly_id(ancestors),
                    visual_node_ids=visual_node_ids_for(node, ancestors),
                )
                return

            generated_mesh_children = direct_generated_mesh_children(node)
            if (
                name
                and not _is_generated_or_default(name)
                and _is_plural_bucket_name(name)
                and len(generated_mesh_children) > 1
                and len(generated_mesh_children) == len(_bom_enabled_children(node))
            ):
                path = path_for(ancestors, node)
                assembly_id = add_assembly(node, ancestors)
                for child in generated_mesh_children:
                    child_path = path_for([*ancestors, node], child)
                    add_component(
                        label=name,
                        path=child_path,
                        assembly_id=assembly_id,
                        visual_node_ids=[str(child.get("id") or child_path)],
                    )
                return

            grouped_fragments = {
                key: children
                for key, children in direct_named_fragments_by_label_and_transform(node).items()
                if len(children) > 1
            }
            if grouped_fragments:
                parent_id = parent_assembly_id(ancestors)
                path = path_for(ancestors, node)
                if name and not _is_generated_or_default(name) and (
                    _has_group_child_with_meshes(node) or _has_named_mesh_child(node)
                ):
                    parent_id = add_assembly(node, ancestors)

                grouped_child_ids = {
                    id(child)
                    for children in grouped_fragments.values()
                    for child in children
                }
                for (label, _signature), children in grouped_fragments.items():
                    add_component(
                        label=label,
                        path=f"{path}/{label}",
                        assembly_id=parent_id,
                        visual_node_ids=visual_node_ids_for_children(children, ancestors, node),
                    )
                for child in _bom_enabled_children(node):
                    if id(child) not in grouped_child_ids:
                        visit(child, [*ancestors, node])
                return

            grouped_mesh_children = direct_named_mesh_children_by_label(node)
            repeated_named_mesh_children = {
                label: children
                for label, children in grouped_mesh_children.items()
                if len(children) > 1
            }
            if _is_generated_or_default(name) and repeated_named_mesh_children:
                parent_id = next((object_to_assembly[id(item)] for item in reversed(ancestors) if id(item) in object_to_assembly), None)
                current_path = path_for(ancestors, node)
                grouped_child_ids = {
                    id(child)
                    for children in repeated_named_mesh_children.values()
                    for child in children
                }
                for label, children in repeated_named_mesh_children.items():
                    add_component(
                        label=label,
                        path=f"{current_path}/{label}",
                        assembly_id=parent_id,
                        visual_node_ids=[
                            str(child.get("id") or path_for([*ancestors, node], child))
                            for child in children
                        ],
                    )
                for child in _bom_enabled_children(node):
                    if id(child) not in grouped_child_ids:
                        visit(child, [*ancestors, node])
                return

            if _has_group_child_with_meshes(node) or _has_named_mesh_child(node):
                if name and not _is_generated_or_default(name):
                    add_assembly(node, ancestors)
            elif name and not _is_generated_or_default(name):
                add_assembly(node, ancestors)
        elif name and not _is_generated_or_default(name):
            diagnostics.append({
                "code": "ignored_named_node_without_meshes",
                "message": f"{name} is named but has no mesh descendants.",
                "path": path_for(ancestors, node),
            })

        for child in _bom_enabled_children(node):
            visit(child, [*ancestors, node])

    def collect_mesh_fallbacks(node: dict[str, Any], ancestors: list[dict[str, Any]]) -> None:
        if _bom_disabled(node):
            return
        if _is_mesh(node):
            path = path_for(ancestors, node)
            label = _node_name(node)
            if not label or _is_generated_or_default(label):
                label = "Visual Component"
            add_component(
                label=label,
                path=path,
                assembly_id=None,
                visual_node_ids=[str(node.get("id") or path)],
            )
            return
        for child in _bom_enabled_children(node):
            collect_mesh_fallbacks(child, [*ancestors, node])

    for root in roots:
        visit(root, [])

    if not components and any(_has_mesh_descendant(root) for root in roots):
        for root in roots:
            collect_mesh_fallbacks(root, [])
        diagnostics.append({
            "code": "visual_tree_without_named_component_groups",
            "severity": "warning",
            "message": "GLB contains visible mesh geometry but no named component groups; generated mesh leaves were emitted as placeholder visual components.",
            "component_count": len(components),
        })

    assembly_by_id = {assembly["id"]: assembly for assembly in assemblies}
    retained_assembly_ids: set[str] = set()
    pending_assembly_ids = [
        assembly_id
        for component in components
        if isinstance(assembly_id := component.get("assembly_id"), str)
    ]
    while pending_assembly_ids:
        assembly_id = pending_assembly_ids.pop()
        if assembly_id in retained_assembly_ids:
            continue
        assembly = assembly_by_id.get(assembly_id)
        if not assembly:
            continue
        retained_assembly_ids.add(assembly_id)
        parent_id = assembly.get("parent_id")
        if isinstance(parent_id, str):
            pending_assembly_ids.append(parent_id)
    assemblies = [assembly for assembly in assemblies if assembly["id"] in retained_assembly_ids]

    return {
        "assemblies": assemblies,
        "components": components,
        "diagnostics": diagnostics,
    }
