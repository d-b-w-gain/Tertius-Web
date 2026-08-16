from __future__ import annotations


TERTIUS_MODEL_GEOMETRY_HELPER_SOURCE = r'''
def _shape_bounds_m(value):
    bbox = value.bounding_box()
    return {
        "min": [bbox.min.X / 1000.0, bbox.min.Y / 1000.0, bbox.min.Z / 1000.0],
        "max": [bbox.max.X / 1000.0, bbox.max.Y / 1000.0, bbox.max.Z / 1000.0],
    }


def visual_metadata_tree(value, *, bd, source_call_ids, root=False):
    component_id = getattr(value, "tertius_component_id", None)
    product_key = getattr(value, "tertius_product_key", None)
    product_digest = getattr(value, "tertius_product_definition_digest", None)
    connection_id = getattr(value, "tertius_connection_id", None)
    connection_digest = getattr(value, "tertius_connection_definition_digest", None)
    source_ids = source_call_ids(value)
    label = str(getattr(value, "label", "") or "")
    children = []
    for child in getattr(value, "children", ()) or ():
        if isinstance(child, bd.Shape):
            children.append(
                visual_metadata_tree(child, bd=bd, source_call_ids=source_call_ids)
            )
    result = {
        "label": label,
        "component_id": str(component_id) if component_id else None,
        "product_key": str(product_key) if product_key else None,
        "product_definition_digest": str(product_digest) if product_digest else None,
        "connection_id": str(connection_id) if connection_id else None,
        "connection_definition_digest": str(connection_digest) if connection_digest else None,
        "source_call_ids": source_ids,
        "children": children,
    }
    normalised_label = label.lower().replace("_", "-").replace(" ", "-")
    label_tokens = {token for token in normalised_label.split("-") if token}
    if root or ("roof" in label_tokens and normalised_label.startswith("surface-")):
        result["bounds_m"] = _shape_bounds_m(value)
        if not root:
            result["site_role"] = "roof"
    return result


def model_geometry_metadata(visual_metadata):
    root_bounds = visual_metadata.get("bounds_m")
    if not isinstance(root_bounds, dict):
        return None
    minimum = root_bounds.get("min")
    maximum = root_bounds.get("max")
    if not (
        isinstance(minimum, list)
        and isinstance(maximum, list)
        and len(minimum) == 3
        and len(maximum) == 3
    ):
        return None
    sizes = [float(maximum[index]) - float(minimum[index]) for index in range(3)]
    plan_length, plan_width = max(sizes[0], sizes[1]), min(sizes[0], sizes[1])
    roof_bounds = []

    def collect_roofs(node):
        bounds = node.get("bounds_m")
        if node.get("site_role") == "roof" and isinstance(bounds, dict):
            roof_bounds.append(bounds)
            return
        for child in node.get("children") or []:
            if isinstance(child, dict):
                collect_roofs(child)

    collect_roofs(visual_metadata)
    eave_height = None
    ridge_height = None
    if roof_bounds:
        eave_height = min(float(item["min"][2]) for item in roof_bounds) - float(
            minimum[2]
        )
        ridge_height = max(float(item["max"][2]) for item in roof_bounds) - float(
            minimum[2]
        )
    overall_height = sizes[2]
    reference_height = (
        (eave_height + ridge_height) / 2.0
        if eave_height is not None and ridge_height is not None
        else overall_height
    )
    return {
        "schema_version": "tertius.model-site-dimensions.v1",
        "footprint_length_m": round(plan_length, 6),
        "footprint_width_m": round(plan_width, 6),
        "overall_height_m": round(overall_height, 6),
        "reference_height_m": round(reference_height, 6),
        "roof_eave_height_m": round(eave_height, 6) if eave_height is not None else None,
        "roof_ridge_height_m": round(ridge_height, 6) if ridge_height is not None else None,
        "reference_height_basis": (
            "mid-height of roof components in the authored Build123D tree"
            if eave_height is not None
            else "overall model height (conservative fallback; no roof role found)"
        ),
        "source": "compiled Build123D analytic bounds",
    }
'''
