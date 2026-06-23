from __future__ import annotations

TERTIUS_BOM_HELPER_SOURCE = r'''
from __future__ import annotations

from contextlib import contextmanager
import inspect
import re
from pathlib import Path
from typing import Any

_scopes: list[dict[str, Any]] = []
_components: list[dict[str, Any]] = []
_requirements: list[dict[str, Any]] = []
_diagnostics: list[dict[str, Any]] = []
_scope_stack: list[str] = []


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or "scope"


def _unique_id(base: str, existing: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _source_location(stack_offset: int = 2) -> dict[str, Any]:
    frame = inspect.currentframe()
    for _ in range(stack_offset):
        if frame is None:
            break
        frame = frame.f_back
    if frame is None:
        return {"source_file": None, "source_line": None}
    filename = frame.f_code.co_filename
    try:
        filename = Path(filename).name
    except Exception:
        pass
    return {"source_file": filename, "source_line": frame.f_lineno}


def _diagnostic(code: str, severity: str, message: str, **fields: Any) -> None:
    _diagnostics.append({
        "code": code,
        "severity": severity,
        "message": message,
        **fields,
    })


@contextmanager
def bom_scope(label: str, id: str | None = None):
    existing_ids = {scope["id"] for scope in _scopes}
    scope_id = _unique_id(str(id or _slug(str(label))), existing_ids)
    if id and scope_id != id:
        _diagnostic(
            "duplicate_scope_id",
            "warning",
            f"Duplicate BoM scope id {id!r}; stored as {scope_id!r}.",
            scope_id=scope_id,
            requested_id=id,
            **_source_location(),
        )
    scope = {
        "id": scope_id,
        "label": str(label),
        "parent_id": _scope_stack[-1] if _scope_stack else None,
        **_source_location(),
    }
    _scopes.append(scope)
    _scope_stack.append(scope_id)
    try:
        yield scope_id
    finally:
        _scope_stack.pop()


def requirement(
    *,
    part_number: str | None = None,
    quantity: float = 1,
    unit: str = "each",
    dimensions: dict[str, Any] | None = None,
    material: str | None = None,
    finish: str | None = None,
    grade: str | None = None,
    standard: str | None = None,
    package: dict[str, Any] | None = None,
    package_type: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "part_number": part_number,
        "quantity": quantity,
        "unit": unit,
        "dimensions": dimensions or {},
        "material": material,
        "finish": finish,
        "grade": grade,
        "standard": standard,
        "package": package or ({"type": package_type} if package_type else None),
        **extra,
        **_source_location(),
    }
    return payload


def _set_visual_label(target: Any, visual_label: str) -> bool:
    if target is None:
        return False
    try:
        setattr(target, "label", visual_label)
        return True
    except Exception:
        return False


def _visual_targets(component: Any) -> list[Any]:
    if isinstance(component, (list, tuple, set)):
        return list(component)
    part = getattr(component, "part", None)
    if part is not None:
        return [part]
    return [component]


def bom_component(
    component: Any,
    *,
    id: str,
    role: str,
    requirements: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    label: str | None = None,
    purchasable_kit: bool = False,
    **metadata: Any,
) -> Any:
    component_id = str(id)
    display_label = str(label or role or component_id)
    visual_label = f"bom:{component_id}:{display_label}"
    visual_node_ids: list[str] = []

    for target in _visual_targets(component):
        if _set_visual_label(target, visual_label):
            visual_node_ids.append(visual_label)

    if not visual_node_ids:
        _diagnostic(
            "requirement_without_visual_link",
            "warning",
            f"BoM component {component_id!r} could not be labelled in the visual model.",
            component_id=component_id,
            **_source_location(),
        )

    if any(existing["id"] == component_id for existing in _components):
        _diagnostic(
            "duplicate_component_id",
            "warning",
            f"Duplicate BoM component id {component_id!r}.",
            component_id=component_id,
            **_source_location(),
        )

    component_record = {
        "id": component_id,
        "scope_id": _scope_stack[-1] if _scope_stack else None,
        "label": display_label,
        "role": str(role),
        "visual_node_ids": sorted(set(visual_node_ids)),
        "purchasable_kit": bool(purchasable_kit),
        "metadata": metadata,
        **_source_location(),
    }
    _components.append(component_record)

    component_requirements = list(requirements or [])
    if not component_requirements:
        _diagnostic(
            "component_missing_requirements",
            "error",
            f"BoM component {component_id!r} has no procurement requirements.",
            component_id=component_id,
            **_source_location(),
        )

    for index, req in enumerate(component_requirements):
        req_record = dict(req or {})
        req_record.setdefault("dimensions", {})
        req_record.setdefault("unit", "each")
        req_record["id"] = f"{component_id}.requirement.{index + 1}"
        req_record["component_id"] = component_id
        req_record["scope_id"] = component_record["scope_id"]

        part_number = req_record.get("part_number")
        if not isinstance(part_number, str) or not part_number.strip():
            _diagnostic(
                "requirement_missing_part_number",
                "warning",
                f"Requirement {req_record['id']!r} is missing a part number.",
                component_id=component_id,
                requirement_id=req_record["id"],
                **_source_location(),
            )
        quantity = req_record.get("quantity")
        try:
            valid_quantity = float(quantity) > 0
        except Exception:
            valid_quantity = False
        if not valid_quantity:
            _diagnostic(
                "requirement_invalid_quantity",
                "warning",
                f"Requirement {req_record['id']!r} has an invalid quantity.",
                component_id=component_id,
                requirement_id=req_record["id"],
                **_source_location(),
            )
        if not str(req_record.get("unit") or "").strip():
            _diagnostic(
                "requirement_missing_unit",
                "warning",
                f"Requirement {req_record['id']!r} is missing a unit.",
                component_id=component_id,
                requirement_id=req_record["id"],
                **_source_location(),
            )
        _requirements.append(req_record)

    return component


def get_manifest(source_snapshot_hash: str = "", visual_path_map: dict[str, Any] | None = None) -> dict[str, Any]:
    diagnostics = list(_diagnostics)
    if not _components:
        diagnostics.append({
            "code": "no_bom_metadata",
            "severity": "error",
            "message": "No tertius_bom components were declared. Procurement will not invent BoM rows from geometry names.",
        })

    return {
        "version": 1,
        "source_snapshot_hash": source_snapshot_hash,
        "scopes": list(_scopes),
        "components": list(_components),
        "requirements": list(_requirements),
        "visual_path_map": visual_path_map or {},
        "diagnostics": diagnostics,
    }
'''
