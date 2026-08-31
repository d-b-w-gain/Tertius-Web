from __future__ import annotations

from functools import lru_cache
import json
from math import cos, isfinite, radians, sin, sqrt
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import build123d as bd

from tertius import (
    DrawingFacet,
    PortPlacement,
    ProcurementFacet,
    ProductDefinition,
    StructuralFacet,
    managed_component,
)


CATALOGUE_RESOURCE_PREFIX = "lysaght_zc_v2.part"
CONNECTION_FAMILY = "lysaght-zc-bolted-end"


def _normalized_part_number(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    if not normalized:
        raise ValueError("Lysaght part number must not be empty")
    return normalized


@lru_cache(maxsize=1)
def _catalogue() -> dict[str, Any]:
    resource_root = Path(__file__).resolve().parent
    catalogue_parts = sorted(
        resource_root.glob(f"{CATALOGUE_RESOURCE_PREFIX}*.json"),
        key=lambda resource: resource.name,
    )
    if not catalogue_parts:
        raise ValueError("Packaged Lysaght Z/C catalogue data is missing")
    payload = json.loads(
        "".join(resource.read_text(encoding="utf-8") for resource in catalogue_parts)
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
        raise ValueError("Lysaght Z/C catalogue must contain a sections array")
    return payload


@lru_cache(maxsize=1)
def _section_index() -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    rows = [
        raw_row
        for raw_row in _catalogue()["sections"]
        if isinstance(raw_row, dict)
        and str(raw_row.get("type") or "").upper() in {"C", "Z"}
    ]
    primary_keys: dict[str, Mapping[str, Any]] = {}
    for raw_row in rows:
        key = str(raw_row.get("key") or "").strip()
        token = key.split(" ", 1)[0]
        normalized_token = _normalized_part_number(token)
        if normalized_token in primary_keys:
            raise ValueError(f"Duplicate Lysaght catalogue section key {token!r}")
        primary_keys[normalized_token] = raw_row
    index.update(primary_keys)

    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        key = str(raw_row.get("key") or "").strip()
        token = key.split(" ", 1)[0]
        aliases = {str(raw_row.get("part_number") or "")}
        raw_aliases = raw_row.get("part_number_alias")
        if isinstance(raw_aliases, str):
            aliases.add(raw_aliases)
        elif isinstance(raw_aliases, list):
            aliases.update(str(alias) for alias in raw_aliases)
        for alias in aliases:
            if alias:
                normalized_alias = _normalized_part_number(alias)
                if normalized_alias not in primary_keys:
                    index.setdefault(normalized_alias, raw_row)
    return index


def catalogue_part_numbers(*, family: str | None = None) -> tuple[str, ...]:
    """Return the exact orderable Z/C tokens known by the packaged catalogue."""

    selected_family = str(family).upper() if family is not None else None
    if selected_family not in {None, "C", "Z"}:
        raise ValueError("Lysaght family must be 'C' or 'Z'")
    tokens = {
        str(row["key"]).split(" ", 1)[0]
        for row in _section_index().values()
        if selected_family is None or str(row["type"]).upper() == selected_family
    }
    return tuple(sorted(tokens))


def _section_row(part_number: str) -> Mapping[str, Any]:
    normalized = _normalized_part_number(part_number)
    try:
        return _section_index()[normalized]
    except KeyError as exc:
        available = ", ".join(catalogue_part_numbers()[:12])
        raise KeyError(
            f"Unknown Lysaght Z/C part number {part_number!r}; available examples: {available}"
        ) from exc


def _geometry_facet(row: Mapping[str, Any]) -> dict[str, Any]:
    family = str(row["type"]).upper()
    geometry: dict[str, Any] = {
        "profile": family,
        "member_axis": "local_z",
        "depth_mm": float(row["depth_mm"]),
        "thickness_mm": float(row["t_mm"]),
        "lip_mm": float(row["lip_mm"]),
        "inside_radius_mm": float(row["ri_mm"]),
    }
    if family == "C":
        geometry["flange_width_mm"] = float(row["flange_mm"])
    else:
        geometry["flange_broad_mm"] = float(row["flange_broad_mm"])
        geometry["flange_narrow_mm"] = float(row["flange_narrow_mm"])
    return geometry


def _structural_facet(row: Mapping[str, Any]) -> StructuralFacet:
    family = str(row["type"]).upper()
    if family == "C":
        iy_field = "Iy_mm4"
        iz_field = "Ix_mm4"
        effective_modulus_field = "Zxe_mm3"
    else:
        iy_field = "Iy1_mm4"
        iz_field = "Ix1_mm4"
        effective_modulus_field = "Zx1e_mm3"
    source = str(row.get("source") or _catalogue()["source"])
    return StructuralFacet(
        kind="member",
        material={
            "label": f"{row['grade']} cold-formed steel",
            "grade": row["grade"],
            "yield_strength_pa": float(row["fy_MPa"]) * 1_000_000.0,
            "tensile_strength_pa": float(row["fu_MPa"]) * 1_000_000.0,
            "elastic_modulus_pa": float(row["E_MPa"]) * 1_000_000.0,
            "shear_modulus_pa": float(row["G_MPa"]) * 1_000_000.0,
            "poisson_ratio": 0.3,
            "density_kg_m3": 7850.0,
        },
        section={
            "area_m2": float(row["A_mm2"]) * 1e-6,
            "iy_m4": float(row[iy_field]) * 1e-12,
            "iz_m4": float(row[iz_field]) * 1e-12,
            "torsion_j_m4": float(row["J_mm4"]) * 1e-12,
            "mass_kg_m": float(row["mass_kg_m"]),
            "effective_section_modulus_m3": float(row[effective_modulus_field]) * 1e-9,
        },
        properties={
            "catalogue_section_key": row["key"],
            "catalogue_source": source,
            "axis_mapping": {
                "local_y_inertia": iy_field,
                "local_z_inertia": iz_field,
                "member_axis": "catalogue longitudinal axis",
            },
            "catalogue_row_validated": bool(row.get("validated")),
        },
        evidence_status="verified" if bool(row.get("validated")) else "candidate",
        evidence_basis=(
            f"Section and material properties are transcribed from {source}. "
            "This verifies the product facts only; member capacity, restraint, "
            "connections, loads, combinations, and regulatory checks remain project analyses."
        ),
    )


@lru_cache(maxsize=None)
def lysaght_zc_product(part_number: str) -> ProductDefinition:
    """Resolve one immutable product whose facets share the exact catalogue row."""

    row = _section_row(part_number)
    token = str(row["key"]).split(" ", 1)[0]
    family = str(row["type"]).upper()
    catalogue = _catalogue()
    standard = str(row.get("standard") or catalogue["standard"])
    return ProductDefinition(
        key=f"{catalogue['id']}:{token}",
        label=str(row["label"]),
        catalogue_id=str(catalogue["id"]),
        catalogue_revision=str(catalogue["version"]),
        catalogue_row=dict(row),
        geometry=_geometry_facet(row),
        procurement=ProcurementFacet(
            part_number=token,
            manufacturer=str(row.get("manufacturer") or "Lysaght"),
            material=f"{row['grade']} cold-formed steel",
            standard=standard,
            ordering={
                "basis": "ordered_length_mm",
                "form": "cut-to-length section",
                "mass_kg_m": float(row["mass_kg_m"]),
            },
        ),
        structural=_structural_facet(row),
        drawing=DrawingFacet(
            name=str(row["label"]),
            attributes={
                "section_family": family,
                "catalogue_section_key": row["key"],
                **_geometry_facet(row),
            },
        ),
        port_families={
            "start": [CONNECTION_FAMILY],
            "end": [CONNECTION_FAMILY],
        },
    )


def _point3(label: str, value: Iterable[float]) -> tuple[float, float, float]:
    point = tuple(float(coordinate) for coordinate in value)
    if len(point) != 3 or not all(isfinite(coordinate) for coordinate in point):
        raise ValueError(f"{label} requires three finite millimetre coordinates")
    return point


def _member_frame(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> tuple[float, tuple[float, float, float], tuple[float, float, float]]:
    delta = tuple(end[index] - start[index] for index in range(3))
    length = sqrt(sum(coordinate * coordinate for coordinate in delta))
    if length <= 1e-6:
        raise ValueError("Lysaght member endpoints must define a non-zero cut length")
    axis = (
        delta[0] / length,
        delta[1] / length,
        delta[2] / length,
    )
    reference = (1.0, 0.0, 0.0)
    if abs(sum(axis[index] * reference[index] for index in range(3))) > 0.95:
        reference = (0.0, 1.0, 0.0)
    projection = sum(axis[index] * reference[index] for index in range(3))
    x_direction_raw = tuple(
        reference[index] - projection * axis[index] for index in range(3)
    )
    x_length = sqrt(sum(coordinate * coordinate for coordinate in x_direction_raw))
    x_direction = (
        x_direction_raw[0] / x_length,
        x_direction_raw[1] / x_length,
        x_direction_raw[2] / x_length,
    )
    return length, axis, x_direction


def _local_profile(product: ProductDefinition, length_mm: float) -> bd.Shape:
    geometry = product.geometry
    family = str(geometry["profile"])
    thickness = float(geometry["thickness_mm"])
    depth = float(geometry["depth_mm"])
    lip = float(geometry["lip_mm"])
    inside_radius = float(geometry["inside_radius_mm"])

    with bd.BuildPart() as member:
        with bd.BuildSketch() as profile:
            if family == "C":
                flange = float(geometry["flange_width_mm"])
                bd.Rectangle(flange, depth, align=(bd.Align.MIN, bd.Align.CENTER))
                with bd.Locations((thickness, 0)):
                    bd.Rectangle(
                        flange - 2 * thickness,
                        depth - 2 * thickness,
                        align=(bd.Align.MIN, bd.Align.CENTER),
                        mode=bd.Mode.SUBTRACT,
                    )
                with bd.Locations((flange, 0)):
                    bd.Rectangle(
                        thickness,
                        depth - 2 * lip,
                        align=(bd.Align.MAX, bd.Align.CENTER),
                        mode=bd.Mode.SUBTRACT,
                    )
                outer_vertices = [
                    vertex
                    for vertex in profile.vertices()
                    if (abs(vertex.X) < 1e-3 or abs(vertex.X - flange) < 1e-3)
                    and abs(abs(vertex.Y) - depth / 2) < 1e-3
                ]
                if outer_vertices:
                    bd.fillet(outer_vertices, radius=inside_radius + thickness)
                inner_vertices = [
                    vertex
                    for vertex in profile.vertices()
                    if (
                        abs(vertex.X - thickness) < 1e-3
                        or abs(vertex.X - (flange - thickness)) < 1e-3
                    )
                    and abs(abs(vertex.Y) - (depth / 2 - thickness)) < 1e-3
                ]
                if inner_vertices:
                    bd.fillet(inner_vertices, radius=inside_radius)
            else:
                broad = float(geometry["flange_broad_mm"])
                narrow = float(geometry["flange_narrow_mm"])
                with bd.Locations((thickness / 2, 0)):
                    bd.Rectangle(thickness, depth)
                with bd.Locations((narrow / 2, depth / 2 - thickness / 2)):
                    bd.Rectangle(narrow, thickness)
                with bd.Locations((thickness - broad / 2, -depth / 2 + thickness / 2)):
                    bd.Rectangle(broad, thickness)
                with bd.Locations((narrow - thickness / 2, depth / 2 - lip / 2)):
                    bd.Rectangle(thickness, lip)
                with bd.Locations(
                    (thickness - broad + thickness / 2, -depth / 2 + lip / 2)
                ):
                    bd.Rectangle(thickness, lip)
                bend_vertices = [
                    vertex
                    for vertex in profile.vertices()
                    if (
                        abs(vertex.X - thickness) < 1e-3
                        and abs(vertex.Y - (depth / 2 - thickness)) < 1e-3
                    )
                    or (
                        abs(vertex.X) < 1e-3
                        and abs(vertex.Y - (-depth / 2 + thickness)) < 1e-3
                    )
                    or (
                        abs(vertex.X - (narrow - thickness)) < 1e-3
                        and abs(vertex.Y - (depth / 2 - thickness)) < 1e-3
                    )
                    or (
                        abs(vertex.X - (thickness - broad + thickness)) < 1e-3
                        and abs(vertex.Y - (-depth / 2 + thickness)) < 1e-3
                    )
                ]
                if bend_vertices:
                    bd.fillet(bend_vertices, radius=inside_radius)
        bd.extrude(amount=length_mm)
    if member.part is None:
        raise RuntimeError("Lysaght member profile did not produce a solid")
    return member.part


def _placed_member(
    part_number: str,
    *,
    expected_family: str,
    start_mm: Iterable[float],
    end_mm: Iterable[float],
    mark: str,
    role: str | None,
    ordered_length_mm: float | None,
    rotation_deg: float,
) -> bd.Shape:
    product = lysaght_zc_product(part_number)
    family = str(product.geometry["profile"])
    if family != expected_family:
        raise ValueError(
            f"{part_number!r} is a {family} section and cannot be used with "
            f"the {expected_family} member factory"
        )
    start = _point3("member start", start_mm)
    end = _point3("member end", end_mm)
    cut_length, axis, x_direction = _member_frame(start, end)
    ordered_length = (
        cut_length if ordered_length_mm is None else float(ordered_length_mm)
    )
    if not isfinite(ordered_length) or ordered_length + 1e-6 < cut_length:
        raise ValueError(
            "ordered_length_mm must be finite and at least the endpoint cut length"
        )
    rotation = float(rotation_deg)
    if not isfinite(rotation):
        raise ValueError("rotation_deg must be finite")
    rotation_radians = radians(rotation)
    frame_y_direction = (
        axis[1] * x_direction[2] - axis[2] * x_direction[1],
        axis[2] * x_direction[0] - axis[0] * x_direction[2],
        axis[0] * x_direction[1] - axis[1] * x_direction[0],
    )
    rotated_x_direction = (
        x_direction[0] * cos(rotation_radians)
        + frame_y_direction[0] * sin(rotation_radians),
        x_direction[1] * cos(rotation_radians)
        + frame_y_direction[1] * sin(rotation_radians),
        x_direction[2] * cos(rotation_radians)
        + frame_y_direction[2] * sin(rotation_radians),
    )

    shape = _local_profile(product, cut_length)
    if rotation:
        shape = shape.rotate(bd.Axis.Z, rotation)
    placement = bd.Plane(
        origin=bd.Vector(*start),
        x_dir=bd.Vector(*x_direction),
        z_dir=bd.Vector(*axis),
    ).location
    shape = shape.moved(placement)
    procurement = product.procurement
    assert procurement is not None
    shape.label = f"{mark} · {procurement.part_number} · L={cut_length:g}mm"
    return managed_component(
        shape,
        product=product,
        mark=mark,
        role=role,
        fabrication={
            "cut_length_mm": cut_length,
            "ordered_length_mm": ordered_length,
            "end_treatment": "square_cut",
            "rotation_deg": rotation,
        },
        ports={
            "start": PortPlacement(
                start,
                tuple(-coordinate for coordinate in axis),
                (CONNECTION_FAMILY,),
                x_direction=rotated_x_direction,
                engagement_length_mm=75.0,
            ),
            "end": PortPlacement(
                end,
                axis,
                (CONNECTION_FAMILY,),
                x_direction=rotated_x_direction,
                engagement_length_mm=75.0,
            ),
        },
    )


def cee_member(
    part_number: str,
    *,
    start_mm: Iterable[float],
    end_mm: Iterable[float],
    mark: str,
    role: str | None = None,
    ordered_length_mm: float | None = None,
    rotation_deg: float = 0.0,
) -> bd.Shape:
    """Create and implicitly register one catalogue Cee member instance."""

    return _placed_member(
        part_number,
        expected_family="C",
        start_mm=start_mm,
        end_mm=end_mm,
        mark=mark,
        role=role,
        ordered_length_mm=ordered_length_mm,
        rotation_deg=rotation_deg,
    )


def zed_member(
    part_number: str,
    *,
    start_mm: Iterable[float],
    end_mm: Iterable[float],
    mark: str,
    role: str | None = None,
    ordered_length_mm: float | None = None,
    rotation_deg: float = 0.0,
) -> bd.Shape:
    """Create and implicitly register one catalogue Zed member instance."""

    return _placed_member(
        part_number,
        expected_family="Z",
        start_mm=start_mm,
        end_mm=end_mm,
        mark=mark,
        role=role,
        ordered_length_mm=ordered_length_mm,
        rotation_deg=rotation_deg,
    )


__all__ = [
    "CONNECTION_FAMILY",
    "catalogue_part_numbers",
    "cee_member",
    "lysaght_zc_product",
    "zed_member",
]
