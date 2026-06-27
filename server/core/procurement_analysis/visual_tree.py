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
    return _is_mesh(node) or any(_has_mesh_descendant(child) for child in _children(node))


def _is_group(node: dict[str, Any]) -> bool:
    return not _is_mesh(node) and bool(_children(node))


def _is_named_group(node: dict[str, Any]) -> bool:
    return _is_group(node) and not _is_generated_or_default(_node_name(node))


def _has_group_child_with_meshes(node: dict[str, Any]) -> bool:
    return any(_is_group(child) and _has_mesh_descendant(child) for child in _children(node))


def _has_named_mesh_child(node: dict[str, Any]) -> bool:
    return any(
        _is_mesh(child) and not _is_generated_or_default(_node_name(child))
        for child in _children(node)
    )


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
                for child in _children(current)
                if _is_named_group(child) and _has_mesh_descendant(child)
            ]
            if named_mesh_children and all(
                _is_generated_or_default(_node_name(child))
                for child in named_mesh_children
            ):
                for child in named_mesh_children:
                    visual_ids.append(str(child.get("id") or path_for([*current_ancestors, current], child)))
                return

            for child in _children(current):
                collect(child, [*current_ancestors, current])

        collect(node, ancestors)
        return visual_ids or [str(node.get("id") or path_for(ancestors, node))]

    def visit(node: dict[str, Any], ancestors: list[dict[str, Any]]) -> None:
        name = _node_name(node)
        if _is_mesh(node):
            if name and not _is_generated_or_default(name):
                parent_id = next((object_to_assembly[id(item)] for item in reversed(ancestors) if id(item) in object_to_assembly), None)
                path = path_for(ancestors, node)
                component_id = _unique_id(_slug(path, "component"), used_ids)
                components.append({
                    "id": component_id,
                    "label": name,
                    "path": path,
                    "assembly_id": parent_id,
                    "visual_node_ids": [str(node.get("id") or path)],
                    "visual_instance_count": None,
                })
            return
        if _is_group(node) and _has_mesh_descendant(node):
            if _has_group_child_with_meshes(node) or _has_named_mesh_child(node):
                parent_id = next((object_to_assembly[id(item)] for item in reversed(ancestors) if id(item) in object_to_assembly), None)
                path = path_for(ancestors, node)
                assembly_id = _unique_id(_slug(path, "assembly"), used_ids)
                object_to_assembly[id(node)] = assembly_id
                assemblies.append({
                    "id": assembly_id,
                    "label": name,
                    "path": path,
                    "parent_id": parent_id,
                })
            else:
                parent_id = next((object_to_assembly[id(item)] for item in reversed(ancestors) if id(item) in object_to_assembly), None)
                path = path_for(ancestors, node)
                component_id = _unique_id(_slug(path, "component"), used_ids)
                visual_node_ids = visual_node_ids_for(node, ancestors)
                components.append({
                    "id": component_id,
                    "label": name,
                    "path": path,
                    "assembly_id": parent_id,
                    "visual_node_ids": visual_node_ids,
                    "visual_instance_count": None,
                })
        elif name and not _is_generated_or_default(name):
            diagnostics.append({
                "code": "ignored_named_node_without_meshes",
                "message": f"{name} is named but has no mesh descendants.",
                "path": path_for(ancestors, node),
            })

        for child in _children(node):
            visit(child, [*ancestors, node])

    for root in roots:
        visit(root, [])

    return {
        "assemblies": assemblies,
        "components": components,
        "diagnostics": diagnostics,
    }
