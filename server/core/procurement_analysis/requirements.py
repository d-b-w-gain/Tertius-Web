from __future__ import annotations

import hashlib
import re
from typing import Any

from .model import positive_number
from .visual_tree import _human_label, _is_generated_or_default, _slug, _unique_id

GENERIC_MATCH_TOKENS = {
    "assembly",
    "building",
    "component",
    "components",
    "design",
    "group",
    "model",
    "part",
    "parts",
    "shed",
}


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) > 2}


def _match_tokens(value: str) -> set[str]:
    return _tokens(value) - GENERIC_MATCH_TOKENS


def _source_match_score(component: dict[str, Any], call: dict[str, Any]) -> tuple[int, str]:
    label_text = str(component.get("label", ""))
    component_path = str(component.get("path", ""))
    component_text = f"{label_text} {component_path}"
    assignment_text = " ".join(str(target) for target in call.get("assignment_targets", []) if target)
    call_text = f"{call.get('function', '')} {call.get('source_scope', '')} {call.get('bom_kind', '')} {assignment_text}"
    overlap = _match_tokens(component_text) & _match_tokens(call_text)
    score = len(overlap) * 3
    reasons = [f"token overlap: {', '.join(sorted(overlap))}"] if overlap else []
    scope_tokens = _match_tokens(str(call.get("source_scope", "")))
    component_path_tokens = _match_tokens(component_path)
    if scope_tokens:
        scope_overlap = component_path_tokens & scope_tokens
        if scope_overlap:
            score += len(scope_overlap) * 4
            reasons.append(f"scope overlap: {', '.join(sorted(scope_overlap))}")
        missing_scope_tokens = scope_tokens - component_path_tokens
        if missing_scope_tokens and scope_overlap:
            score -= len(missing_scope_tokens) * 3
            reasons.append(f"scope mismatch: {', '.join(sorted(missing_scope_tokens))}")
    label_tokens = _tokens(label_text)
    visual_part_number, _trace = _visual_label_part_number(component)
    function_name = str(call.get("function") or "").lower()
    if (
        any(token in function_name for token in ("screw", "fastener", "bolt", "nut"))
        and not visual_part_number
        and not (label_tokens & {"screw", "screws", "fastener", "fasteners", "bolt", "bolts", "nut", "nuts"})
    ):
        score -= 8
        reasons.append("fastener source rejected for non-fastener visual label")
    label = str(component.get("label", "")).lower()
    kind = str(call.get("bom_kind", "")).lower()
    if "bracket" in label and kind == "bracket":
        score += 4
        reasons.append("bracket kind match")
    if "column" in label and "column" in str(call.get("source_scope", "")).lower():
        score += 5
        reasons.append("column scope match")
    if "rafter" in label and "rafter" in str(call.get("source_scope", "")).lower():
        score += 5
        reasons.append("rafter scope match")
    return score, "; ".join(reasons)


def _best_source_call(component: dict[str, Any], source_analysis: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if isinstance(component.get("_source_call"), dict):
        return component["_source_call"], "source-only component candidate"

    calls = source_analysis.get("calls", [])
    best: tuple[dict[str, Any], int, str] | None = None
    for call in calls:
        score, reason = _source_match_score(component, call)
        if best is None or score > best[1]:
            best = (call, score, reason)
    has_visual_evidence = bool(component.get("visual_node_ids"))
    if not has_visual_evidence and (best is None or best[1] < 3) and len(calls) == 1:
        return calls[0], "single candidate source call"
    if best is None or best[1] < 3:
        return None, "no source match"
    return best[0], best[2] or "best source match"


def _source_call_key(call: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not isinstance(call, dict):
        return None
    return (
        call.get("source_file"),
        call.get("source_line"),
        call.get("function"),
        call.get("source_scope"),
    )


def _merge_source_scopes(
    assemblies: list[dict[str, Any]],
    components: list[dict[str, Any]],
    source_assemblies: list[dict[str, Any]],
    source_components: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    used_assembly_ids = {
        str(assembly.get("id"))
        for assembly in assemblies
        if assembly.get("id") is not None
    }
    used_component_ids = {
        str(component.get("id"))
        for component in components
        if component.get("id") is not None
    }
    id_map: dict[str, str] = {}
    merged_assemblies = list(assemblies)
    merged_components = list(components)

    for assembly in source_assemblies:
        old_id = str(assembly.get("id") or _slug(str(assembly.get("label") or "source-scope"), "source-scope"))
        new_id = old_id if old_id not in used_assembly_ids else _unique_id(old_id, used_assembly_ids)
        used_assembly_ids.add(new_id)
        id_map[old_id] = new_id
        merged = dict(assembly)
        merged["id"] = new_id
        parent_id = merged.get("parent_id")
        if isinstance(parent_id, str) and parent_id in id_map:
            merged["parent_id"] = id_map[parent_id]
        merged_assemblies.append(merged)

    for component in source_components:
        old_id = str(component.get("id") or _slug(str(component.get("label") or "source-component"), "source-component"))
        new_id = old_id if old_id not in used_component_ids else _unique_id(old_id, used_component_ids)
        used_component_ids.add(new_id)
        merged = dict(component)
        merged["id"] = new_id
        assembly_id = merged.get("assembly_id")
        if isinstance(assembly_id, str) and assembly_id in id_map:
            merged["assembly_id"] = id_map[assembly_id]
        merged_components.append(merged)

    return merged_assemblies, merged_components


def _manifest_scopes_to_assemblies(explicit_manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not explicit_manifest:
        return []
    assemblies: list[dict[str, Any]] = []
    for scope in explicit_manifest.get("scopes", []):
        if not isinstance(scope, dict):
            continue
        assemblies.append({
            "id": str(scope.get("id") or _slug(str(scope.get("label") or "scope"), "scope")),
            "label": str(scope.get("label") or scope.get("id") or "Scope"),
            "path": str(scope.get("label") or scope.get("id") or "Scope"),
            "parent_id": scope.get("parent_id"),
            "source_file": scope.get("source_file"),
            "source_line": scope.get("source_line"),
            "scope_source": scope.get("scope_source"),
        })
    return assemblies


def _source_scope_assemblies_and_components(source_analysis: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assemblies: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    assembly_ids: dict[str, str | None] = {"<module>": None}
    used_ids: set[str] = set()
    entrypoint = str(source_analysis.get("entrypoint") or "design.py")
    calls = [call for call in source_analysis.get("calls", []) if isinstance(call, dict) and not call.get("diagnostic")]

    def is_procurement_component_call(call: dict[str, Any]) -> bool:
        standard_inputs = call.get("standard_inputs") if isinstance(call.get("standard_inputs"), dict) else {}
        if not standard_inputs and call.get("bom_kind") == "component":
            return False
        return not (
            _resolved_input(call, "part_number") is None
            and call.get("bom_kind") != "bracket"
            and not _is_decomposable_fastener_assembly(call)
        )

    opaque_entrypoint_helpers = {
        str(call.get("function"))
        for call in calls
        if call.get("source_file") == entrypoint
        and call.get("function")
        and not is_procurement_component_call(call)
    }

    for call in calls:
        source_scope = str(call.get("source_scope") or "<module>")
        source_root = source_scope.split("::", 1)[0]
        if call.get("source_file") != entrypoint and source_root not in opaque_entrypoint_helpers:
            continue
        if not is_procurement_component_call(call):
            continue

        assembly_id = None
        if source_scope != "<module>":
            scope_parts = source_scope.split("::")
            parent_id = None
            scope_path: list[str] = []
            for scope_part in scope_parts:
                scope_path.append(scope_part)
                scope_key = "::".join(scope_path)
                if scope_key not in assembly_ids:
                    new_id = _unique_id(_slug(scope_key, "source-scope"), used_ids)
                    assembly_ids[scope_key] = new_id
                    assemblies.append({
                        "id": new_id,
                        "label": _human_label(scope_part),
                        "path": scope_key,
                        "parent_id": parent_id,
                        "source": "ast_source_scope",
                    })
                parent_id = assembly_ids[scope_key]
            assembly_id = parent_id

        label = str(call.get("function") or "source_call")
        component_id = _unique_id(
            _slug(f"{source_scope}-{label}-{call.get('source_line')}", "source-component"),
            used_ids,
        )
        components.append({
            "id": component_id,
            "label": label,
            "path": f"{source_scope}/{label}",
            "assembly_id": assembly_id,
            "visual_node_ids": [],
            "_source_call": call,
        })

    return assemblies, components


def _resolved_input(call: dict[str, Any], key: str) -> Any:
    value = call.get("standard_inputs", {}).get(key)
    if isinstance(value, dict):
        return value.get("resolved")
    return None


def _input_trace(call: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    if not call:
        return None
    value = call.get("standard_inputs", {}).get(key)
    return value if isinstance(value, dict) else None


def _notable_input_trace(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    if value.get("dependencies") or value.get("unresolved_reason"):
        return value
    return None


def _visual_label_part_number(component: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    label = str(component.get("label") or "").strip()
    if not label or _is_generated_or_default(label):
        return None, None
    if re.search(r"\s", label):
        return None, None
    if "_" in label:
        return None, None
    if not re.search(r"\d", label):
        return None, None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", label):
        return None, None
    normalized = label.upper()
    return normalized, {
        "raw": {"kind": "visual_label", "label": label},
        "resolved": normalized,
        "resolution": "visual_component_label",
        "source_file": None,
        "source_line": None,
    }


def _is_visual_container_without_procurement_identity(component: dict[str, Any], call: dict[str, Any] | None) -> bool:
    if call and (_resolved_input(call, "part_number") is not None or _resolved_input(call, "product_key") is not None):
        return False
    visual_part_number, _trace = _visual_label_part_number(component)
    visual_leaf_count = _visual_leaf_count(component)
    if visual_part_number and not (visual_leaf_count is not None and visual_leaf_count > 1):
        return False
    label_tokens = _tokens(str(component.get("label") or ""))
    if label_tokens & {"concrete", "rebar"}:
        return False
    return bool(visual_leaf_count is not None and visual_leaf_count > 1)


def _is_decomposable_fastener_assembly(call: dict[str, Any] | None) -> bool:
    if not call or call.get("bom_kind") != "fastener_assembly":
        return False
    return _resolved_input(call, "size") is not None and _resolved_input(call, "length_mm") is not None


def _make_generated_part_key(component: dict[str, Any], call: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    kind = str(call.get("bom_kind") if call else "component").upper().replace("_", "-")
    function_name = str(call.get("function") if call else component.get("label") or "component")
    standard_inputs = call.get("standard_inputs", {}) if call else {}
    resolved_inputs = {
        key: value.get("resolved")
        for key, value in standard_inputs.items()
        if isinstance(value, dict) and value.get("resolved") is not None
    }
    angle = resolved_inputs.get("roof_pitch_deg") or resolved_inputs.get("angle_deg")
    compact_key = _compact_identity_key(resolved_inputs)
    if compact_key:
        return compact_key, {
            "raw": {"kind": "generated", "fields": resolved_inputs},
            "resolved": compact_key,
            "resolution": "generated_compact_identity",
            "source_file": call.get("source_file") if call else None,
            "source_line": call.get("source_line") if call else None,
        }
    function_label = re.sub(r"^(make|build|create)[_\-\s]+", "", function_name, flags=re.IGNORECASE)
    prefix_parts = [kind, re.sub(r"[^A-Z0-9]+", "-", function_label.upper()).strip("-") or "COMPONENT"]
    if angle is not None:
        prefix_parts.append(f"{str(angle).replace('.', 'P')}DEG")
    seed = repr({
        "kind": kind,
        "function": call.get("function") if call else "",
        "inputs": sorted(resolved_inputs.items()),
    })
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()
    return "-".join(prefix_parts + [digest]), {
        "raw": {"kind": "generated", "seed": seed},
        "resolved": "-".join(prefix_parts + [digest]),
        "resolution": "generated_deterministic",
        "source_file": call.get("source_file") if call else None,
        "source_line": call.get("source_line") if call else None,
    }


def _compact_dimension(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        scaled = value / 100.0
        if abs(scaled - round(scaled)) < 0.000001:
            return str(int(round(scaled))).zfill(2)
        return f"{scaled:.1f}".rstrip("0").rstrip(".").replace(".", "P")
    text = str(value).strip()
    return text or None


def _compact_angle(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if abs(value - round(value)) < 0.000001:
            return str(int(round(value)))
        return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", "P")
    text = str(value).strip()
    return text or None


def _compact_identity_key(resolved_inputs: dict[str, Any]) -> str | None:
    mark = resolved_inputs.get("mark")
    if not mark:
        return None
    parts = [str(mark).strip().upper()]
    for key in ["length_mm", "width_mm", "height_mm", "thickness_mm"]:
        compact = _compact_dimension(resolved_inputs.get(key))
        if compact is not None:
            parts.append(compact)
    angle = _compact_angle(resolved_inputs.get("angle_deg") or resolved_inputs.get("roof_pitch_deg"))
    if angle is not None:
        parts.append(angle)
    if len(parts) == 1:
        return None
    return "-".join(part for part in parts if part)


def _stock_number(part_number: Any, dimensions: dict[str, Any]) -> str | None:
    if not part_number:
        return None
    length = dimensions.get("length_mm")
    if length is None:
        return None
    compact = _compact_dimension(length)
    if compact is None:
        return None
    return f"{part_number}-{compact}"


def _requirement_stock_number(part_number: Any, dimensions: dict[str, Any], resolution_trace: dict[str, Any]) -> str | None:
    part_trace = resolution_trace.get("part_number")
    if isinstance(part_trace, dict) and part_trace.get("resolution") == "static_product_table":
        return None
    return _stock_number(part_number, dimensions)


_positive_number = positive_number


COUNTABLE_UNITS = {
    "",
    "count",
    "ea",
    "each",
    "item",
    "items",
    "length",
    "lengths",
    "pc",
    "pcs",
    "piece",
    "pieces",
    "sheet",
    "sheets",
    "unit",
    "units",
}

NON_DISCRETE_QUANTITY_UNITS = {
    "cubic metre",
    "cubic metres",
    "cubic meter",
    "cubic meters",
    "kg",
    "kilogram",
    "kilograms",
    "l",
    "liter",
    "liters",
    "litre",
    "litres",
    "m",
    "m2",
    "m3",
    "metre",
    "metres",
    "meter",
    "meters",
    "sqm",
}


def _normalized_unit(unit: Any) -> str:
    return str(unit or "").strip().lower()


def _allows_visual_quantity(unit: Any) -> bool:
    if unit is None:
        return True
    return _normalized_unit(unit) in COUNTABLE_UNITS


def _is_non_discrete_quantity_unit(unit: Any) -> bool:
    return _normalized_unit(unit) in NON_DISCRETE_QUANTITY_UNITS


def _visual_instance_count(component: dict[str, Any]) -> int | None:
    visual_node_ids = component.get("visual_node_ids")
    if isinstance(visual_node_ids, list) and visual_node_ids:
        return 1
    explicit = _positive_number(component.get("visual_instance_count"))
    if explicit is not None:
        return int(explicit)
    return None


def _visual_leaf_count(component: dict[str, Any]) -> int | None:
    explicit = _positive_number(component.get("visual_instance_count"))
    if explicit is not None:
        return int(explicit)
    visual_node_ids = component.get("visual_node_ids")
    if isinstance(visual_node_ids, list) and visual_node_ids:
        return len(visual_node_ids)
    return None


def _assembly_multiplier(call: dict[str, Any] | None, source_analysis: dict[str, Any]) -> int:
    if not call:
        return 1
    source_scope = str(call.get("source_scope") or "")
    if not source_scope:
        return 1
    root_scope = source_scope.split("::", 1)[0]
    counts = source_analysis.get("function_instance_counts")
    if isinstance(counts, dict):
        value = _positive_number(counts.get(root_scope))
        if value is not None:
            return int(value)
    return 1


def _quantity_evidence(
    call: dict[str, Any] | None,
    component: dict[str, Any],
    source_analysis: dict[str, Any],
    *,
    analysis_mode: str,
) -> dict[str, Any]:
    explicit_quantity = _positive_number(_resolved_input(call, "quantity")) if call else None
    unit = _resolved_input(call, "unit") if call else None
    visual_count = _visual_instance_count(component)
    visual_count_is_quantity = _allows_visual_quantity(unit)
    non_discrete_quantity_unit = _is_non_discrete_quantity_unit(unit)
    assembly_multiplier = _assembly_multiplier(call, source_analysis)
    source_instance_count = int(_positive_number(call.get("source_instance_count")) or 1) if call else 1
    quantity: int | float
    orderable = True
    if analysis_mode == "visual_verified" and visual_count is not None and visual_count_is_quantity:
        quantity = visual_count
        source = "visual_instances"
        confidence = "verified"
    elif (
        analysis_mode == "visual_verified"
        and visual_count is not None
        and non_discrete_quantity_unit
        and explicit_quantity is not None
    ):
        quantity = explicit_quantity
        source = "metadata_quantity_non_discrete"
        confidence = "verified"
    else:
        quantity = 1
        source = "diagnostic_placeholder"
        confidence = "diagnostic"
        orderable = analysis_mode == "visual_verified" and visual_count is not None

    rolled_up_quantity = quantity

    trace: dict[str, Any] = {
        "explicit_quantity": explicit_quantity,
        "visual_instance_count": visual_count,
        "visual_leaf_count": _visual_leaf_count(component),
        "visual_count_is_quantity": visual_count_is_quantity,
        "non_discrete_quantity_unit": non_discrete_quantity_unit,
        "assembly_instance_multiplier": assembly_multiplier,
        "source_call_count": 1,
    }
    if unit is not None:
        trace["quantity_unit"] = unit
    if visual_count is not None and not visual_count_is_quantity:
        trace["visual_count_not_quantity"] = True
    if source_instance_count != 1:
        trace["source_instance_count"] = source_instance_count
        if source == "visual_instances" and source_instance_count != visual_count:
            trace["source_quantity_mismatch"] = {
                "source_instance_count": source_instance_count,
                "visual_instance_count": visual_count,
            }
    if (
        explicit_quantity not in {None, 1}
        and visual_count is not None
        and visual_count_is_quantity
        and explicit_quantity != visual_count
        and source == "visual_instances"
    ):
        confidence = "diagnostic"
        trace["mismatch"] = {
            "explicit_quantity": explicit_quantity,
            "visual_instance_count": visual_count,
        }

    return {
        "quantity": quantity,
        "rolled_up_quantity": rolled_up_quantity,
        "quantity_source": source,
        "quantity_confidence": confidence,
        "orderable": orderable,
        "visual_instance_count": visual_count,
        "assembly_instance_multiplier": assembly_multiplier,
        "source_call_count": 1,
        "count_trace": trace,
    }


def _generated_fastener_part_trace(
    *,
    part_number: str,
    standard: str,
    call: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "raw": {"kind": "generated", "standard": standard},
        "resolved": part_number,
        "resolution": "generated_standard_fastener",
        "source_file": call.get("source_file") if call else None,
        "source_line": call.get("source_line") if call else None,
    }


def _fastener_assembly_requirements(
    *,
    component: dict[str, Any],
    call: dict[str, Any],
    component_record: dict[str, Any],
    quantity_evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    size = _resolved_input(call, "size")
    length = _resolved_input(call, "length_mm")
    grip_length = _resolved_input(call, "grip_length_mm")
    size_text = str(size).strip().upper() if size is not None else None
    length_text = _compact_angle(length)

    bolt_part = f"DIN-6921-{size_text}X{length_text}" if size_text and length_text else None
    nut_part = f"DIN-6923-{size_text}" if size_text else None
    base_dimensions = {"size": size_text} if size_text else {}

    bolt_dimensions = dict(base_dimensions)
    if length is not None:
        bolt_dimensions["length_mm"] = length
    if grip_length is not None:
        bolt_dimensions["grip_length_mm"] = grip_length

    nut_dimensions = dict(base_dimensions)
    bolt_quantity = {
        **quantity_evidence,
        "orderable": bool(quantity_evidence.get("orderable")) and bool(bolt_part),
        "count_trace": dict(quantity_evidence.get("count_trace", {})),
    }
    nut_quantity = {
        **quantity_evidence,
        "orderable": bool(quantity_evidence.get("orderable")) and bool(nut_part),
        "count_trace": dict(quantity_evidence.get("count_trace", {})),
    }

    return [
        {
            "id": f"{component['id']}.bolt.requirement",
            "component_id": component["id"],
            "assembly_id": component.get("assembly_id"),
            "part_number": bolt_part,
            "stock_number": None,
            **bolt_quantity,
            "unit": "each",
            "dimensions": bolt_dimensions,
            "material": _resolved_input(call, "material"),
            "finish": _resolved_input(call, "finish"),
            "source_trace": {
                **component_record["source_trace"],
                "decomposed_from": "fastener_assembly",
                "procurement_item": "bolt",
            },
            "resolution_trace": {
                "part_number": _generated_fastener_part_trace(
                    part_number=bolt_part,
                    standard="DIN 6921 hex flange bolt",
                    call=call,
                ) if bolt_part else None,
            },
        },
        {
            "id": f"{component['id']}.nut.requirement",
            "component_id": component["id"],
            "assembly_id": component.get("assembly_id"),
            "part_number": nut_part,
            "stock_number": None,
            **nut_quantity,
            "unit": "each",
            "dimensions": nut_dimensions,
            "material": _resolved_input(call, "material"),
            "finish": _resolved_input(call, "finish"),
            "source_trace": {
                **component_record["source_trace"],
                "decomposed_from": "fastener_assembly",
                "procurement_item": "nut",
            },
            "resolution_trace": {
                "part_number": _generated_fastener_part_trace(
                    part_number=nut_part,
                    standard="DIN 6923 hex flange nut",
                    call=call,
                ) if nut_part else None,
            },
        },
    ]


def _requirement_group_key(requirement: dict[str, Any]) -> tuple[Any, ...]:
    dimensions_value = requirement.get("dimensions")
    dimensions = dimensions_value if isinstance(dimensions_value, dict) else {}
    return (
        requirement.get("assembly_id"),
        requirement.get("part_number"),
        requirement.get("stock_number"),
        requirement.get("unit"),
        tuple(sorted(dimensions.items())),
        requirement.get("material"),
        requirement.get("finish"),
    )


def _annotate_source_call_counts(requirements: list[dict[str, Any]]) -> None:
    counts: dict[tuple[Any, ...], int] = {}
    for requirement in requirements:
        key = _requirement_group_key(requirement)
        counts[key] = counts.get(key, 0) + 1
    for requirement in requirements:
        source_call_count = counts[_requirement_group_key(requirement)]
        if requirement.get("quantity_source") == "visual_instances":
            source_call_count = int(_positive_number(requirement.get("source_call_count")) or 1)
        requirement["source_call_count"] = source_call_count
        trace = requirement.get("count_trace")
        if isinstance(trace, dict):
            trace["source_call_count"] = source_call_count


def _normalize_explicit_manifest_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        dimensions_value = requirement.get("dimensions")
        dimensions = dimensions_value if isinstance(dimensions_value, dict) else {}
        if not requirement.get("part_number") and dimensions.get("component_label"):
            continue
        quantity = _positive_number(requirement.get("quantity")) or 1
        visual_count = _positive_number(requirement.get("visual_instance_count"))
        source_call_count = int(_positive_number(requirement.get("source_call_count")) or 1)
        normalized.append({
            **requirement,
            "quantity": quantity,
            "rolled_up_quantity": requirement.get("rolled_up_quantity") or quantity,
            "quantity_source": "explicit_manifest",
            "quantity_confidence": requirement.get("quantity_confidence") or "verified",
            "orderable": requirement.get("orderable") is not False,
            "visual_instance_count": int(visual_count) if visual_count is not None else None,
            "source_call_count": source_call_count,
            "count_trace": requirement.get("count_trace") or {
                "explicit_quantity": quantity,
                "visual_instance_count": int(visual_count) if visual_count is not None else None,
                "source_call_count": source_call_count,
            },
        })
    return normalized


def build_procurement_analysis(
    source_analysis: dict[str, Any],
    tree_analysis: dict[str, Any],
    explicit_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic procurement analysis artifact."""

    diagnostics = [
        *list(tree_analysis.get("diagnostics", [])),
        *list(source_analysis.get("diagnostics", [])),
    ]

    source_assemblies, source_components = _source_scope_assemblies_and_components(source_analysis)

    tree_components = list(tree_analysis.get("components", []))
    assemblies = list(tree_analysis.get("assemblies", []))
    analysis_mode = "visual_verified" if tree_components else "source_diagnostic"
    quantity_authority = "visual_tree" if analysis_mode == "visual_verified" else "diagnostic_only"
    if not tree_components and explicit_manifest and explicit_manifest.get("requirements"):
        explicit_requirements = _normalize_explicit_manifest_requirements(explicit_manifest.get("requirements", []))
        if explicit_requirements:
            return {
                "version": 1,
                "source": "explicit_manifest",
                "analysis_mode": "explicit_manifest",
                "quantity_authority": "explicit_manifest",
                "assemblies": explicit_manifest.get("scopes", []),
                "components": explicit_manifest.get("components", []),
                "requirements": explicit_requirements,
                "diagnostics": explicit_manifest.get("diagnostics", []),
            }
        diagnostics.append({
            "code": "explicit_manifest_visual_rows_ignored",
            "severity": "info",
            "message": "Explicit manifest requirements without part numbers were treated as visual/component coverage diagnostics, not procurement rows.",
        })
    elif tree_components and explicit_manifest and explicit_manifest.get("requirements"):
        diagnostics.append({
            "code": "explicit_manifest_ignored_for_visual_authority",
            "severity": "info",
            "message": "Explicit manifest requirements were ignored because GLB visual components are the quantity authority.",
        })

    if not assemblies and not source_assemblies:
        assemblies = _manifest_scopes_to_assemblies(explicit_manifest)
    if not tree_components and source_components:
        assemblies, tree_components = _merge_source_scopes(assemblies, [], source_assemblies, source_components)
        diagnostics.append({
            "code": "source_only_components_no_visual_tree",
            "message": "GLTF hierarchy did not expose named component groups; source metadata is diagnostic-only and not procurement quantity authority.",
        })
    elif tree_components and source_components:
        matched_call_keys: set[tuple[Any, ...]] = set()
        visual_part_numbers: set[str] = set()
        for component in tree_components:
            call, _match_reason = _best_source_call(component, source_analysis)
            call_key = _source_call_key(call)
            if call_key is not None:
                matched_call_keys.add(call_key)
            visual_part_number, _trace = _visual_label_part_number(component)
            part_number = (_resolved_input(call, "part_number") if call else None) or visual_part_number
            if part_number:
                visual_part_numbers.add(str(part_number))
        source_supplements = [
            component
            for component in source_components
            if _source_call_key(component.get("_source_call")) not in matched_call_keys
            and str(_resolved_input(component.get("_source_call"), "part_number") or "") not in visual_part_numbers
        ]
        if source_supplements:
            diagnostics.append({
                "code": "source_metadata_without_visual_component",
                "severity": "warning",
                "message": (
                    f"GLTF exposed {len(tree_components)} named component groups; "
                    f"{len(source_supplements)} source-derived procurement candidate(s) were not added as orderable rows "
                    "because visual components are the quantity authority."
                ),
                "visual_component_count": len(tree_components),
                "source_component_count": len(source_components),
                "unmatched_source_component_count": len(source_supplements),
            })

    requirements: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    for component in tree_components:
        call, match_reason = _best_source_call(component, source_analysis)
        public_component = {key: value for key, value in component.items() if not key.startswith("_")}
        component_record = {
            **public_component,
            "source_trace": {
                "function": call.get("function") if call else None,
                "source_file": call.get("source_file") if call else None,
                "source_scope": call.get("source_scope") if call else None,
                "source_line": call.get("source_line") if call else None,
                "match_reason": match_reason,
            },
        }
        components.append(component_record)

        visual_part_number, visual_part_trace = _visual_label_part_number(component)
        part_number = (_resolved_input(call, "part_number") if call else None) or visual_part_number
        generated_trace = None
        quantity_evidence = _quantity_evidence(call, component, source_analysis, analysis_mode=analysis_mode)
        if call is not None and _is_decomposable_fastener_assembly(call):
            fastener_requirements = _fastener_assembly_requirements(
                component=component,
                call=call,
                component_record=component_record,
                quantity_evidence=quantity_evidence,
            )
            requirements.extend(fastener_requirements)
            for fastener_requirement in fastener_requirements:
                if not fastener_requirement.get("part_number"):
                    diagnostics.append({
                        "code": "requirement_missing_part_number",
                        "message": f"{component.get('label')} {fastener_requirement['source_trace']['procurement_item']} has no resolved part number.",
                        "component_id": component["id"],
            })
            continue
        visual_container_without_identity = (
            (not part_number or (part_number == visual_part_number and (call is None or _resolved_input(call, "part_number") is None)))
            and _is_visual_container_without_procurement_identity(component, call)
        )
        if visual_container_without_identity:
            part_number = None
            visual_part_trace = None
            diagnostics.append({
                "code": "visual_container_without_procurement_identity",
                "severity": "warning",
                "message": (
                    f"{component.get('label')} aggregates "
                    f"{len(component.get('visual_node_ids') or []) or 1} visual leaf node(s), but it has no procurement-readable "
                    "product identity. It remains a procurement-required visual row unless marked reference/NTBO."
                ),
                "component_id": component["id"],
                "source_file": call.get("source_file") if call else None,
                "source_line": call.get("source_line") if call else None,
            })
        if not part_number and not visual_container_without_identity and call and call.get("bom_kind") in {"bracket", "component"}:
            part_number, generated_trace = _make_generated_part_key(component, call)

        dimensions = {
            key: _resolved_input(call, key)
            for key in ["length_mm", "width_mm", "height_mm", "thickness_mm", "diameter_mm", "grip_length_mm", "roof_pitch_deg", "angle_deg"]
            if call and _resolved_input(call, key) is not None
        }
        dimension_trace = {
            key: trace
            for key in dimensions
            if (trace := _notable_input_trace(_input_trace(call, key))) is not None
        }
        quantity_trace = _notable_input_trace(_input_trace(call, "quantity"))
        resolution_trace = {
            "part_number": generated_trace or (call.get("standard_inputs", {}).get("part_number") if call and _resolved_input(call, "part_number") else None) or visual_part_trace,
        }
        if dimension_trace:
            resolution_trace["dimensions"] = dimension_trace
        if quantity_trace:
            resolution_trace["quantity"] = quantity_trace
        if not part_number and quantity_evidence.get("quantity_source") in {"visual_instances", "diagnostic_placeholder"}:
            label = str(component.get("label") or component.get("path") or component.get("id") or "").strip()
            if label:
                dimensions.setdefault("component_label", label)

        requirement = {
            "id": f"{component['id']}.requirement",
            "component_id": component["id"],
            "assembly_id": component.get("assembly_id"),
            "part_number": part_number,
            "stock_number": _requirement_stock_number(part_number, dimensions, resolution_trace),
            **quantity_evidence,
            "unit": _resolved_input(call, "unit") if call and _resolved_input(call, "unit") else "each",
            "dimensions": dimensions,
            "material": _resolved_input(call, "material") if call else None,
            "finish": _resolved_input(call, "finish") if call else None,
            "source_trace": component_record["source_trace"],
            "resolution_trace": resolution_trace,
        }
        requirements.append(requirement)

        if quantity_evidence["quantity_confidence"] == "diagnostic":
            diagnostics.append({
                "code": "quantity_evidence_mismatch",
                "message": f"{component.get('label')} has conflicting explicit and visual quantities.",
                "component_id": component["id"],
                "count_trace": quantity_evidence["count_trace"],
            })
        elif isinstance(quantity_evidence.get("count_trace"), dict) and quantity_evidence["count_trace"].get("source_quantity_mismatch"):
            diagnostics.append({
                "code": "source_quantity_ignored_for_visual_component",
                "severity": "info",
                "message": f"{component.get('label')} has source-derived count evidence, but GLB visual components are the quantity authority.",
                "component_id": component["id"],
                "count_trace": quantity_evidence["count_trace"],
            })

        if not part_number:
            diagnostics.append({
                "code": "requirement_missing_part_number",
                "message": f"{component.get('label')} has no resolved part number.",
                "component_id": component["id"],
            })

    _annotate_source_call_counts(requirements)

    return {
        "version": 1,
        "source": "diagnostic_source_analysis" if analysis_mode == "source_diagnostic" else "deterministic_analysis",
        "analysis_mode": analysis_mode,
        "quantity_authority": quantity_authority,
        "assemblies": assemblies,
        "components": components,
        "requirements": requirements,
        "diagnostics": diagnostics,
    }
