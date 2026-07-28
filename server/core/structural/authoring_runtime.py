from __future__ import annotations

from collections.abc import Mapping
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
from typing import Any, Literal, Sequence

import build123d as bd


ComponentKind = Literal["ground", "member", "surface", "connector", "support"]
TransferKind = Literal["force", "shear", "moment", "wind_normal"]
LoadCategory = Literal["dead", "live", "wind"]
DistributedLoadSource = Literal["self_weight", "surface", "authored"]


class StructuralAuthoringError(ValueError):
    """Raised when structural CAD authoring would create an ambiguous manifest."""


@dataclass(frozen=True)
class StructuralPart:
    """A registered Build123D shape and its structural identity."""

    shape: bd.Shape
    component_id: str
    kind: ComponentKind


@dataclass(frozen=True)
class StructuralMaterialSpec:
    """A registered elastic material used by an analytical member."""

    id: str


@dataclass(frozen=True)
class StructuralSectionSpec:
    """A registered member section used by an analytical member."""

    id: str


@dataclass(frozen=True)
class StructuralCatalogSectionSpec:
    """Section and material handles resolved from one immutable catalogue record."""

    section: StructuralSectionSpec
    material: StructuralMaterialSpec


@dataclass(frozen=True)
class StructuralSurfaceLoad:
    """A registered surface load that can be distributed to member handles."""

    id: str


class StructuralModel:
    """Build a structural manifest from the same handles used to assemble CAD."""

    def __init__(self, *, title: str) -> None:
        self.title = _required_text("model title", title)
        self._design_basis: dict[str, Any] | None = None
        self._components: list[dict[str, Any]] = []
        self._parts_by_id: dict[str, StructuralPart] = {}
        self._connections: list[dict[str, Any]] = []
        self._loads: list[dict[str, Any]] = []
        self._materials: list[dict[str, Any]] = []
        self._material_handles: dict[str, StructuralMaterialSpec] = {}
        self._sections: list[dict[str, Any]] = []
        self._section_handles: dict[str, StructuralSectionSpec] = {}
        self._analytical_members: list[dict[str, Any]] = []
        self._member_loads: list[dict[str, Any]] = []
        self._member_distributed_loads: list[dict[str, Any]] = []
        self._load_combinations: list[dict[str, Any]] = []
        self._load_case_categories: dict[str, LoadCategory] = {}
        self._load_case_labels: dict[str, str] = {}
        self._surface_load_handles: dict[str, StructuralSurfaceLoad] = {}
        self._assembled_ids: list[str] | None = None
        self._assembly: bd.Compound | None = None

    def design_basis(
        self,
        *,
        framework_id: str,
        framework_label: str,
        framework_reference: str,
        jurisdiction: str,
        analysis_method: str,
        standards: dict[str, str],
    ) -> None:
        """Declare the verification framework without hiding local design rules."""
        if self._design_basis is not None:
            raise StructuralAuthoringError("the structural design basis is already defined")
        if not isinstance(standards, dict) or not standards:
            raise StructuralAuthoringError(
                "the structural design basis requires at least one named standard"
            )
        self._design_basis = {
            "framework_id": _required_text("framework ID", framework_id),
            "framework_label": _required_text("framework label", framework_label),
            "framework_reference": _required_text(
                "framework reference", framework_reference
            ),
            "jurisdiction": _required_text("jurisdiction", jurisdiction),
            "analysis_method": _required_text("analysis method", analysis_method),
            "standards": {
                _required_text("standard role", str(role)): _required_text(
                    "standard reference", str(reference)
                )
                for role, reference in standards.items()
            },
        }

    def ground(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="ground",
            visual_node_id=visual_node_id,
            part_number=part_number,
            grounded=True,
        )

    def member(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="member",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def surface(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="surface",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def connector(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="connector",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def support(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        visual_node_id: str | None = None,
        part_number: str | None = None,
    ) -> StructuralPart:
        return self._register(
            shape,
            id=id,
            label=label,
            kind="support",
            visual_node_id=visual_node_id,
            part_number=part_number,
        )

    def connect(
        self,
        from_component: StructuralPart,
        to_component: StructuralPart,
        *,
        via: Sequence[StructuralPart] = (),
        id: str,
        label: str,
        transfers: Sequence[TransferKind],
    ) -> None:
        source = self._require_registered(from_component)
        target = self._require_registered(to_component)
        if source.component_id == target.component_id:
            raise StructuralAuthoringError(
                f"connection {id!r} connects a component to itself"
            )
        connection_id = _required_text("connection ID", id)
        if any(item["id"] == connection_id for item in self._connections):
            raise StructuralAuthoringError(
                f"connection ID {connection_id!r} is already registered"
            )

        connector_ids: list[str] = []
        for connector in via:
            registered = self._require_registered(connector)
            if registered.kind != "connector":
                raise StructuralAuthoringError(
                    f"connection {connection_id!r} via component "
                    f"{registered.component_id!r} is not a connector"
                )
            if registered.component_id in connector_ids:
                raise StructuralAuthoringError(
                    f"connection {connection_id!r} repeats connector "
                    f"{registered.component_id!r}"
                )
            connector_ids.append(registered.component_id)

        transfer_values = [
            _required_text("connection transfer", item) for item in transfers
        ]
        allowed_transfers = {"force", "shear", "moment", "wind_normal"}
        invalid_transfers = sorted(set(transfer_values) - allowed_transfers)
        if invalid_transfers:
            raise StructuralAuthoringError(
                f"connection {connection_id!r} has unsupported transfers "
                f"{invalid_transfers}"
            )
        if not transfer_values:
            raise StructuralAuthoringError(
                f"connection {connection_id!r} must declare transferred actions"
            )

        self._connections.append(
            {
                "id": connection_id,
                "label": _required_text("connection label", label),
                "from_component_id": source.component_id,
                "to_component_id": target.component_id,
                "connector_component_ids": connector_ids,
                "transfers": transfer_values,
            }
        )

    def surface_load(
        self,
        component: StructuralPart,
        *,
        id: str,
        label: str,
        case: Literal["dead", "live", "wind"],
        case_id: str | None = None,
        case_label: str | None = None,
        pressure_kPa: float,
        area_m2: float,
        direction: Sequence[float] | dict[str, float],
        provenance: str,
    ) -> StructuralSurfaceLoad:
        registered = self._require_registered(component)
        if registered.kind != "surface":
            raise StructuralAuthoringError(
                f"surface load component {registered.component_id!r} is not a surface"
            )
        load_id = _required_text("load ID", id)
        if any(item["id"] == load_id for item in self._loads):
            raise StructuralAuthoringError(f"load ID {load_id!r} is already registered")
        if case not in {"dead", "live", "wind"}:
            raise StructuralAuthoringError(f"unsupported load case {case!r}")
        pressure = float(pressure_kPa)
        area = float(area_m2)
        if pressure == 0:
            raise StructuralAuthoringError(f"load {load_id!r} has zero pressure")
        if area <= 0:
            raise StructuralAuthoringError(f"load {load_id!r} has non-positive area")

        vector = _vector3(direction)
        if vector == {"x": 0.0, "y": 0.0, "z": 0.0}:
            raise StructuralAuthoringError(f"load {load_id!r} has a zero direction")
        resolved_case_id = _load_case_id(case_id or case)
        resolved_case_label = (
            _required_text("load case label", case_label)
            if case_label is not None
            else f"{case.title()} load"
        )
        existing_category = self._load_case_categories.get(resolved_case_id)
        if existing_category is not None and existing_category != case:
            raise StructuralAuthoringError(
                f"load case {resolved_case_id!r} is already registered as "
                f"{existing_category!r}"
            )
        existing_label = self._load_case_labels.get(resolved_case_id)
        if existing_label is not None and existing_label != resolved_case_label:
            raise StructuralAuthoringError(
                f"load case {resolved_case_id!r} is already labelled {existing_label!r}"
            )
        self._loads.append(
            {
                "id": load_id,
                "label": _required_text("load label", label),
                "case": case,
                "case_id": resolved_case_id,
                "component_id": registered.component_id,
                "pressure_kPa": pressure,
                "area_m2": area,
                "direction": vector,
                "provenance": _required_text("load provenance", provenance),
            }
        )
        self._load_case_categories[resolved_case_id] = case
        self._load_case_labels[resolved_case_id] = resolved_case_label
        handle = StructuralSurfaceLoad(id=load_id)
        self._surface_load_handles[load_id] = handle
        return handle

    def material(
        self,
        *,
        id: str,
        label: str,
        elastic_modulus_kN_m2: float,
        shear_modulus_kN_m2: float,
        poisson_ratio: float,
        density_kg_m3: float,
    ) -> StructuralMaterialSpec:
        material_id = _required_text("material ID", id)
        if material_id in self._material_handles:
            raise StructuralAuthoringError(
                f"material ID {material_id!r} is already registered"
            )
        values = {
            "elastic_modulus_kN_m2": float(elastic_modulus_kN_m2),
            "shear_modulus_kN_m2": float(shear_modulus_kN_m2),
            "density_kg_m3": float(density_kg_m3),
        }
        if any(value <= 0 for value in values.values()):
            raise StructuralAuthoringError(
                f"material {material_id!r} properties must be positive"
            )
        poisson = float(poisson_ratio)
        if not -1 < poisson < 0.5:
            raise StructuralAuthoringError(
                f"material {material_id!r} has invalid Poisson ratio"
            )
        handle = StructuralMaterialSpec(id=material_id)
        self._material_handles[material_id] = handle
        self._materials.append(
            {
                "id": material_id,
                "label": _required_text("material label", label),
                **values,
                "poisson_ratio": poisson,
            }
        )
        return handle

    def section(
        self,
        *,
        id: str,
        label: str,
        area_m2: float,
        iy_m4: float,
        iz_m4: float,
        torsion_j_m4: float,
        mass_kg_m: float | None = None,
        bending_reference_kNm: float | None = None,
        bending_reference_axis: Literal[
            "local_y",
            "local_z",
            "resultant",
        ]
        | None = None,
        bending_reference_basis: str | None = None,
        catalog: Mapping[str, Any] | None = None,
    ) -> StructuralSectionSpec:
        section_id = _required_text("section ID", id)
        if section_id in self._section_handles:
            raise StructuralAuthoringError(
                f"section ID {section_id!r} is already registered"
            )
        values = {
            "area_m2": float(area_m2),
            "iy_m4": float(iy_m4),
            "iz_m4": float(iz_m4),
            "torsion_j_m4": float(torsion_j_m4),
        }
        if any(value <= 0 for value in values.values()):
            raise StructuralAuthoringError(
                f"section {section_id!r} properties must be positive"
            )
        handle = StructuralSectionSpec(id=section_id)
        self._section_handles[section_id] = handle
        mass = None if mass_kg_m is None else float(mass_kg_m)
        if mass is not None and mass <= 0:
            raise StructuralAuthoringError(
                f"section {section_id!r} mass_kg_m must be positive"
            )
        bending_reference = (
            None if bending_reference_kNm is None else float(bending_reference_kNm)
        )
        reference_fields = (
            bending_reference,
            bending_reference_axis,
            bending_reference_basis,
        )
        if any(value is None for value in reference_fields) and any(
            value is not None for value in reference_fields
        ):
            raise StructuralAuthoringError(
                f"section {section_id!r} bending reference requires "
                "bending_reference_kNm, bending_reference_axis, and "
                "bending_reference_basis"
            )
        if bending_reference is not None and bending_reference <= 0:
            raise StructuralAuthoringError(
                f"section {section_id!r} bending reference must be positive"
            )
        if bending_reference_axis not in {
            None,
            "local_y",
            "local_z",
            "resultant",
        }:
            raise StructuralAuthoringError(
                f"section {section_id!r} has unsupported bending reference axis"
            )
        self._sections.append(
            {
                "id": section_id,
                "label": _required_text("section label", label),
                **values,
                **({"mass_kg_m": mass} if mass is not None else {}),
                **(
                    {
                        "bending_reference_kNm": bending_reference,
                        "bending_reference_axis": bending_reference_axis,
                        "bending_reference_basis": _required_text(
                            "section bending reference basis",
                            bending_reference_basis,
                        ),
                    }
                    if bending_reference is not None
                    else {}
                ),
                **(
                    {"catalog": _json_mapping("section catalogue", catalog)}
                    if catalog
                    else {}
                ),
            }
        )
        return handle

    def section_from_catalog(
        self,
        *,
        id: str,
        material_id: str,
        record: Mapping[str, Any],
    ) -> StructuralCatalogSectionSpec:
        """Register solver properties and provenance from a normalized catalogue record."""

        normalized = _json_mapping("catalogue section record", record)
        if normalized.get("schema_version") != "1.0":
            raise StructuralAuthoringError(
                "catalogue section record schema_version must be '1.0'"
            )
        catalog = _required_mapping(normalized, "catalog")
        solver = _required_mapping(normalized, "solver")
        material_values = _required_mapping(normalized, "material")
        properties = _required_mapping(normalized, "properties")
        axis_mapping = _required_mapping(normalized, "axis_mapping")
        label = _required_text("catalogue section label", normalized.get("label"))
        catalog_reference = {
            "catalog_id": _required_text("catalogue ID", catalog.get("id")),
            "catalog_version": _required_text(
                "catalogue version", catalog.get("version")
            ),
            "section_key": _required_text(
                "catalogue section key", catalog.get("section_key")
            ),
            "source": _required_text("catalogue source", catalog.get("source")),
            "record_sha256": sha256(
                json.dumps(
                    properties,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
            "axis_mapping": {
                str(key): _required_text(f"catalogue axis mapping {key!r}", value)
                for key, value in axis_mapping.items()
            },
            "properties": properties,
        }
        material = self.material(
            id=material_id,
            label=_required_text(
                "catalogue material label", material_values.get("label")
            ),
            elastic_modulus_kN_m2=_required_number(
                material_values, "elastic_modulus_kN_m2"
            ),
            shear_modulus_kN_m2=_required_number(
                material_values, "shear_modulus_kN_m2"
            ),
            poisson_ratio=_required_number(material_values, "poisson_ratio"),
            density_kg_m3=_required_number(material_values, "density_kg_m3"),
        )
        section = self.section(
            id=id,
            label=label,
            area_m2=_required_number(solver, "area_m2"),
            iy_m4=_required_number(solver, "iy_m4"),
            iz_m4=_required_number(solver, "iz_m4"),
            torsion_j_m4=_required_number(solver, "torsion_j_m4"),
            mass_kg_m=_optional_positive_number(
                solver,
                "mass_kg_m",
                fallback=properties.get("mass_kg_m"),
            ),
            bending_reference_kNm=_optional_positive_number(
                solver,
                "bending_reference_kNm",
            ),
            bending_reference_axis=_optional_bending_reference_axis(solver),
            bending_reference_basis=(
                str(solver["bending_reference_basis"])
                if solver.get("bending_reference_basis") is not None
                else None
            ),
            catalog=catalog_reference,
        )
        return StructuralCatalogSectionSpec(section=section, material=material)

    def member_axis(
        self,
        component: StructuralPart,
        *,
        id: str,
        label: str,
        start: Sequence[float] | dict[str, float],
        end: Sequence[float] | dict[str, float],
        section: StructuralSectionSpec,
        material: StructuralMaterialSpec,
        start_restraints: Sequence[bool] | dict[str, bool] = (),
        end_restraints: Sequence[bool] | dict[str, bool] = (),
        rotation_deg: float = 0.0,
        start_releases: Sequence[bool] | dict[str, bool] = (),
        end_releases: Sequence[bool] | dict[str, bool] = (),
        deflection_limit_ratio: float | None = None,
        deflection_limit_mm: float | None = None,
        deflection_limit_basis: str | None = None,
        assumption: str,
    ) -> None:
        registered = self._require_registered(component)
        if registered.kind != "member":
            raise StructuralAuthoringError(
                f"analytical component {registered.component_id!r} is not a member"
            )
        member_id = _required_text("analytical member ID", id)
        if any(item["id"] == member_id for item in self._analytical_members):
            raise StructuralAuthoringError(
                f"analytical member ID {member_id!r} is already registered"
            )
        if any(
            item["component_id"] == registered.component_id
            for item in self._analytical_members
        ):
            raise StructuralAuthoringError(
                f"component {registered.component_id!r} already has an analytical axis"
            )
        section_spec = self._require_section(section)
        material_spec = self._require_material(material)
        start_vector = _vector3(start)
        end_vector = _vector3(end)
        if start_vector == end_vector:
            raise StructuralAuthoringError(
                f"analytical member {member_id!r} has zero length"
            )
        limit_ratio = (
            None if deflection_limit_ratio is None else float(deflection_limit_ratio)
        )
        limit_mm = None if deflection_limit_mm is None else float(deflection_limit_mm)
        if limit_ratio is not None and limit_ratio <= 0:
            raise StructuralAuthoringError(
                f"analytical member {member_id!r} deflection limit ratio "
                "must be positive"
            )
        if limit_mm is not None and limit_mm <= 0:
            raise StructuralAuthoringError(
                f"analytical member {member_id!r} deflection limit must be positive"
            )
        if (
            limit_ratio is not None or limit_mm is not None
        ) and not deflection_limit_basis:
            raise StructuralAuthoringError(
                f"analytical member {member_id!r} deflection limit requires a basis"
            )
        self._analytical_members.append(
            {
                "id": member_id,
                "label": _required_text("analytical member label", label),
                "component_id": registered.component_id,
                "start": start_vector,
                "end": end_vector,
                "start_restraints": _restraints(start_restraints),
                "end_restraints": _restraints(end_restraints),
                "rotation_deg": float(rotation_deg),
                "start_releases": _restraints(start_releases),
                "end_releases": _restraints(end_releases),
                "deflection_limit_ratio": limit_ratio,
                "deflection_limit_mm": limit_mm,
                "deflection_limit_basis": (
                    _required_text(
                        "analytical member deflection limit basis",
                        deflection_limit_basis,
                    )
                    if deflection_limit_basis is not None
                    else None
                ),
                "section_id": section_spec.id,
                "material_id": material_spec.id,
                "assumption": _required_text(
                    "analytical member assumption", assumption
                ),
            }
        )

    def distribute_surface_load(
        self,
        load: StructuralSurfaceLoad,
        member: StructuralPart,
        *,
        id: str,
        label: str,
        positions_m: Sequence[float],
        weights: Sequence[float] = (),
        provenance: str,
    ) -> None:
        source = self._require_surface_load(load)
        registered_member = self._require_registered(member)
        analytical_member = next(
            (
                item
                for item in self._analytical_members
                if item["component_id"] == registered_member.component_id
            ),
            None,
        )
        if analytical_member is None:
            raise StructuralAuthoringError(
                f"component {registered_member.component_id!r} has no analytical axis"
            )
        distribution_id = _required_text("load distribution ID", id)
        if any(
            item["id"] == distribution_id
            or item["id"].startswith(f"{distribution_id}-")
            for item in self._member_loads
        ):
            raise StructuralAuthoringError(
                f"load distribution ID {distribution_id!r} is already registered"
            )
        positions = [float(position) for position in positions_m]
        if not positions or len(positions) != len(set(positions)):
            raise StructuralAuthoringError(
                f"load distribution {distribution_id!r} needs unique positions"
            )
        start = analytical_member["start"]
        end = analytical_member["end"]
        member_length = sqrt(
            sum((end[axis] - start[axis]) ** 2 for axis in ("x", "y", "z"))
        )
        if any(position <= 0 or position >= member_length for position in positions):
            raise StructuralAuthoringError(
                f"load distribution {distribution_id!r} positions must lie within "
                f"the {member_length:g} m member"
            )
        weight_values = (
            [1.0] * len(positions)
            if not weights
            else [float(value) for value in weights]
        )
        if len(weight_values) != len(positions) or any(
            value <= 0 for value in weight_values
        ):
            raise StructuralAuthoringError(
                f"load distribution {distribution_id!r} weights must be positive "
                "and match the positions"
            )
        weight_total = sum(weight_values)
        source_data = next(item for item in self._loads if item["id"] == source.id)
        direction = source_data["direction"]
        direction_length = sqrt(sum(direction[axis] ** 2 for axis in ("x", "y", "z")))
        resultant = source_data["pressure_kPa"] * source_data["area_m2"]
        case_id = source_data["case_id"]
        load_label = _required_text("load distribution label", label)
        load_provenance = _required_text("load distribution provenance", provenance)
        for index, (position, weight) in enumerate(
            zip(positions, weight_values, strict=True),
            start=1,
        ):
            scale = resultant * weight / weight_total / direction_length
            self._member_loads.append(
                {
                    "id": f"{distribution_id}-{index}",
                    "label": f"{load_label} {index}",
                    "member_id": analytical_member["id"],
                    "case_id": case_id,
                    "distance_m": position,
                    "force": {
                        axis: direction[axis] * scale for axis in ("x", "y", "z")
                    },
                    "moment": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "source_load_id": source.id,
                    "provenance": load_provenance,
                }
            )

    def member_distributed_load(
        self,
        member: StructuralPart,
        *,
        id: str,
        label: str,
        case: LoadCategory,
        start_force_kN_m: Sequence[float] | dict[str, float],
        end_force_kN_m: Sequence[float] | dict[str, float] | None = None,
        start_distance_m: float = 0.0,
        end_distance_m: float | None = None,
        source_kind: DistributedLoadSource = "authored",
        source_load: StructuralSurfaceLoad | None = None,
        provenance: str,
    ) -> None:
        """Apply a global line load to the analytical axis of a CAD member."""

        if case not in {"dead", "live", "wind"}:
            raise StructuralAuthoringError(f"unsupported load case {case!r}")
        if source_kind not in {"self_weight", "surface", "authored"}:
            raise StructuralAuthoringError(
                f"unsupported distributed load source {source_kind!r}"
            )
        registered_member = self._require_registered(member)
        analytical_member = self._analytical_member(registered_member)
        load_id = _required_text("distributed load ID", id)
        if any(
            item["id"] == load_id
            for item in (*self._member_loads, *self._member_distributed_loads)
        ):
            raise StructuralAuthoringError(
                f"member load ID {load_id!r} is already registered"
            )
        member_length = _member_length(analytical_member)
        start_distance = float(start_distance_m)
        end_distance = (
            member_length if end_distance_m is None else float(end_distance_m)
        )
        if not 0 <= start_distance < end_distance <= member_length:
            raise StructuralAuthoringError(
                f"distributed load {load_id!r} must lie within the "
                f"{member_length:g} m member"
            )
        start_force = _vector3(start_force_kN_m)
        end_force = (
            dict(start_force) if end_force_kN_m is None else _vector3(end_force_kN_m)
        )
        if all(
            start_force[axis] == 0 and end_force[axis] == 0 for axis in ("x", "y", "z")
        ):
            raise StructuralAuthoringError(
                f"distributed load {load_id!r} has zero line force"
            )
        source_load_id = None
        if source_load is not None:
            source = self._require_surface_load(source_load)
            source_data = next(item for item in self._loads if item["id"] == source.id)
            if source_data["case"] != case:
                raise StructuralAuthoringError(
                    f"distributed load {load_id!r} case does not match its source load"
                )
            source_load_id = source.id
        if source_kind == "surface" and source_load_id is None:
            raise StructuralAuthoringError(
                f"surface distributed load {load_id!r} requires source_load"
            )
        if source_kind != "surface" and source_load_id is not None:
            raise StructuralAuthoringError(
                f"distributed load {load_id!r} source_load requires source_kind='surface'"
            )
        if source_kind == "self_weight" and case != "dead":
            raise StructuralAuthoringError(
                "member self-weight must use the dead load case"
            )
        case_id = _load_case_id(case)
        self._load_case_categories[case_id] = case
        self._load_case_labels.setdefault(case_id, f"{case.title()} load")
        self._member_distributed_loads.append(
            {
                "id": load_id,
                "label": _required_text("distributed load label", label),
                "member_id": analytical_member["id"],
                "case_id": case_id,
                "start_distance_m": start_distance,
                "end_distance_m": end_distance,
                "start_force_kN_m": start_force,
                "end_force_kN_m": end_force,
                "source_kind": source_kind,
                "source_load_id": source_load_id,
                "provenance": _required_text(
                    "distributed load provenance",
                    provenance,
                ),
            }
        )

    def member_self_weight(
        self,
        member: StructuralPart,
        *,
        id: str,
        label: str,
        direction: Sequence[float] | dict[str, float] = (0.0, 0.0, -1.0),
        gravity_m_s2: float = 9.80665,
        provenance: str = "Section mass per metre multiplied by standard gravity.",
    ) -> None:
        """Apply catalogue-derived member self-weight as a global line load."""

        registered_member = self._require_registered(member)
        analytical_member = self._analytical_member(registered_member)
        section = next(
            item
            for item in self._sections
            if item["id"] == analytical_member["section_id"]
        )
        mass_kg_m = section.get("mass_kg_m")
        if mass_kg_m is None:
            raise StructuralAuthoringError(
                f"section {section['id']!r} has no validated mass_kg_m"
            )
        gravity = float(gravity_m_s2)
        if gravity <= 0:
            raise StructuralAuthoringError("gravity_m_s2 must be positive")
        load_direction = _vector3(direction)
        direction_length = sqrt(
            sum(load_direction[axis] ** 2 for axis in ("x", "y", "z"))
        )
        if direction_length == 0:
            raise StructuralAuthoringError("self-weight direction must be non-zero")
        magnitude = float(mass_kg_m) * gravity / 1000.0
        line_force = {
            axis: load_direction[axis] * magnitude / direction_length
            for axis in ("x", "y", "z")
        }
        self.member_distributed_load(
            member,
            id=id,
            label=label,
            case="dead",
            start_force_kN_m=line_force,
            source_kind="self_weight",
            provenance=provenance,
        )

    def distribute_surface_load_uniform(
        self,
        load: StructuralSurfaceLoad,
        member: StructuralPart,
        *,
        id: str,
        label: str,
        start_distance_m: float = 0.0,
        end_distance_m: float | None = None,
        tributary_fraction: float = 1.0,
        provenance: str,
    ) -> None:
        """Convert a surface resultant into a uniform line load on one member."""

        source = self._require_surface_load(load)
        source_data = next(item for item in self._loads if item["id"] == source.id)
        registered_member = self._require_registered(member)
        analytical_member = self._analytical_member(registered_member)
        member_length = _member_length(analytical_member)
        start_distance = float(start_distance_m)
        end_distance = (
            member_length if end_distance_m is None else float(end_distance_m)
        )
        loaded_length = end_distance - start_distance
        fraction = float(tributary_fraction)
        if not 0 < fraction <= 1:
            raise StructuralAuthoringError(
                "surface-load tributary_fraction must be greater than zero and at most one"
            )
        if loaded_length <= 0:
            raise StructuralAuthoringError(
                "surface-load distribution requires a positive loaded length"
            )
        direction = source_data["direction"]
        direction_length = sqrt(sum(direction[axis] ** 2 for axis in ("x", "y", "z")))
        resultant = source_data["pressure_kPa"] * source_data["area_m2"] * fraction
        line_force = {
            axis: direction[axis] * resultant / direction_length / loaded_length
            for axis in ("x", "y", "z")
        }
        self.member_distributed_load(
            member,
            id=id,
            label=label,
            case=source_data["case"],
            start_force_kN_m=line_force,
            start_distance_m=start_distance,
            end_distance_m=end_distance,
            source_kind="surface",
            source_load=load,
            provenance=provenance,
        )

    def load_combination(
        self,
        *,
        id: str,
        label: str,
        limit_state: Literal["serviceability", "ultimate"],
        factors: Mapping[str, float],
    ) -> None:
        combination_id = _required_text("load combination ID", id)
        if any(item["id"] == combination_id for item in self._load_combinations):
            raise StructuralAuthoringError(
                f"load combination ID {combination_id!r} is already registered"
            )
        if limit_state not in {"serviceability", "ultimate"}:
            raise StructuralAuthoringError(
                f"unsupported load combination limit state {limit_state!r}"
            )
        normalized_factors: dict[str, float] = {}
        for key, raw_factor in factors.items():
            case_id = _load_case_id(str(key))
            factor = float(raw_factor)
            if factor == 0:
                continue
            normalized_factors[case_id] = factor
        if not normalized_factors:
            raise StructuralAuthoringError(
                f"load combination {combination_id!r} requires non-zero factors"
            )
        self._load_combinations.append(
            {
                "id": combination_id,
                "label": _required_text("load combination label", label),
                "limit_state": limit_state,
                "factors": normalized_factors,
            }
        )

    def assembly(
        self,
        parts: Sequence[StructuralPart],
        *,
        label: str,
    ) -> bd.Compound:
        if self._assembly is not None:
            raise StructuralAuthoringError("the structural assembly is already defined")

        registered_parts = [self._require_registered(part) for part in parts]
        assembled_ids = [part.component_id for part in registered_parts]
        if len(assembled_ids) != len(set(assembled_ids)):
            raise StructuralAuthoringError(
                "the structural assembly contains a registered component more than once"
            )
        missing_ids = sorted(set(self._parts_by_id) - set(assembled_ids))
        if missing_ids:
            raise StructuralAuthoringError(
                f"registered structural components are missing from the assembly: {missing_ids}"
            )
        if set(assembled_ids) != set(self._parts_by_id):
            raise StructuralAuthoringError(
                "the structural assembly contains unregistered components"
            )

        assembly_shapes = [part.shape for part in registered_parts]
        assembly = bd.Compound(
            assembly_shapes,
            label=_required_text("assembly label", label),
            children=assembly_shapes,
        )
        self._assembled_ids = assembled_ids
        self._assembly = assembly
        return assembly

    def manifest(self) -> dict[str, Any]:
        if self._assembly is None or self._assembled_ids is None:
            raise StructuralAuthoringError(
                "call StructuralModel.assembly(...) before generating the manifest"
            )
        self._validate_topology()
        manifest = {
            "title": self.title,
            "design_basis": (
                dict(self._design_basis) if self._design_basis is not None else None
            ),
            "authoring": {
                "mode": "generated",
                "assembly_component_ids": list(self._assembled_ids),
            },
            "components": [dict(component) for component in self._components],
            "connections": [
                {
                    **connection,
                    "connector_component_ids": list(
                        connection["connector_component_ids"]
                    ),
                    "transfers": list(connection["transfers"]),
                }
                for connection in self._connections
            ],
            "loads": [
                {**load, "direction": dict(load["direction"])} for load in self._loads
            ],
            "analysis": {
                "materials": [dict(material) for material in self._materials],
                "sections": [dict(section) for section in self._sections],
                "members": [
                    {
                        **member,
                        "start": dict(member["start"]),
                        "end": dict(member["end"]),
                        "start_restraints": dict(member["start_restraints"]),
                        "end_restraints": dict(member["end_restraints"]),
                        "start_releases": dict(member["start_releases"]),
                        "end_releases": dict(member["end_releases"]),
                    }
                    for member in self._analytical_members
                ],
                "load_cases": [
                    {
                        "id": case_id,
                        "label": self._load_case_labels.get(
                            case_id,
                            f"{category.title()} load",
                        ),
                        "category": category,
                    }
                    for case_id, category in self._load_case_categories.items()
                ],
                "load_combinations": (
                    [dict(combination) for combination in self._load_combinations]
                    if self._load_combinations
                    else [
                        {
                            "id": "SLS-1.0",
                            "label": "Serviceability — all authored actions at 1.0",
                            "limit_state": "serviceability",
                            "factors": {
                                case_id: 1.0 for case_id in self._load_case_categories
                            },
                        }
                    ]
                ),
                "member_loads": [
                    {
                        **load,
                        "force": dict(load["force"]),
                        "moment": dict(load["moment"]),
                    }
                    for load in self._member_loads
                ],
                "member_distributed_loads": [
                    {
                        **load,
                        "start_force_kN_m": dict(load["start_force_kN_m"]),
                        "end_force_kN_m": dict(load["end_force_kN_m"]),
                    }
                    for load in self._member_distributed_loads
                ],
            },
        }
        self._assembly.tertius_structural_manifest = manifest
        self._assembly.tertius_structural_component_ids = tuple(self._assembled_ids)
        return manifest

    def _register(
        self,
        shape: bd.Shape,
        *,
        id: str,
        label: str,
        kind: ComponentKind,
        visual_node_id: str | None,
        part_number: str | None,
        grounded: bool = False,
    ) -> StructuralPart:
        if self._assembly is not None:
            raise StructuralAuthoringError(
                "components cannot be registered after the assembly is defined"
            )
        if not isinstance(shape, bd.Shape):
            raise StructuralAuthoringError(
                f"component {id!r} must wrap a Build123D Shape"
            )
        component_id = _required_text("component ID", id)
        if component_id in self._parts_by_id:
            raise StructuralAuthoringError(
                f"component ID {component_id!r} is already registered"
            )
        node_id = _required_text(
            "visual node ID",
            visual_node_id if visual_node_id is not None else component_id,
        )
        if any(
            component["visual_node_id"] == node_id for component in self._components
        ):
            raise StructuralAuthoringError(
                f"visual node ID {node_id!r} is already registered"
            )

        shape.label = node_id
        part = StructuralPart(shape=shape, component_id=component_id, kind=kind)
        component: dict[str, Any] = {
            "id": component_id,
            "label": _required_text("component label", label),
            "kind": kind,
            "visual_node_id": node_id,
            "grounded": grounded,
        }
        if part_number is not None:
            component["part_number"] = _required_text("part number", part_number)
        self._parts_by_id[component_id] = part
        self._components.append(component)
        return part

    def _analytical_member(
        self,
        member: StructuralPart,
    ) -> dict[str, Any]:
        analytical_member = next(
            (
                item
                for item in self._analytical_members
                if item["component_id"] == member.component_id
            ),
            None,
        )
        if analytical_member is None:
            raise StructuralAuthoringError(
                f"component {member.component_id!r} has no analytical axis"
            )
        return analytical_member

    def _require_registered(self, part: StructuralPart) -> StructuralPart:
        if not isinstance(part, StructuralPart):
            raise StructuralAuthoringError(
                "structural assemblies and connections accept registered "
                "StructuralPart handles only"
            )
        registered = self._parts_by_id.get(part.component_id)
        if registered is not part:
            raise StructuralAuthoringError(
                f"component handle {part.component_id!r} is not registered with this model"
            )
        return registered

    def _require_material(
        self, material: StructuralMaterialSpec
    ) -> StructuralMaterialSpec:
        if (
            not isinstance(material, StructuralMaterialSpec)
            or self._material_handles.get(material.id) is not material
        ):
            raise StructuralAuthoringError(
                "analytical members accept registered material handles only"
            )
        return material

    def _require_section(self, section: StructuralSectionSpec) -> StructuralSectionSpec:
        if (
            not isinstance(section, StructuralSectionSpec)
            or self._section_handles.get(section.id) is not section
        ):
            raise StructuralAuthoringError(
                "analytical members accept registered section handles only"
            )
        return section

    def _require_surface_load(
        self, load: StructuralSurfaceLoad
    ) -> StructuralSurfaceLoad:
        if (
            not isinstance(load, StructuralSurfaceLoad)
            or self._surface_load_handles.get(load.id) is not load
        ):
            raise StructuralAuthoringError(
                "load distributions accept registered surface-load handles only"
            )
        return load

    def _validate_topology(self) -> None:
        connected_ids = {
            component_id
            for connection in self._connections
            for component_id in (
                connection["from_component_id"],
                connection["to_component_id"],
            )
        }
        unconnected = sorted(
            component["id"]
            for component in self._components
            if component["kind"] not in {"ground", "connector"}
            and component["id"] not in connected_ids
        )
        if unconnected:
            raise StructuralAuthoringError(
                f"structural components have no declared connection: {unconnected}"
            )

        used_connectors = {
            connector_id
            for connection in self._connections
            for connector_id in connection["connector_component_ids"]
        }
        unused_connectors = sorted(
            component["id"]
            for component in self._components
            if component["kind"] == "connector"
            and component["id"] not in used_connectors
        )
        if unused_connectors:
            raise StructuralAuthoringError(
                f"connector components are not used by a connection: {unused_connectors}"
            )

        grounded_ids = {
            component["id"] for component in self._components if component["grounded"]
        }
        outgoing: dict[str, list[str]] = {}
        for connection in self._connections:
            outgoing.setdefault(connection["from_component_id"], []).append(
                connection["to_component_id"]
            )
        for load in self._loads:
            if not _reaches_ground(load["component_id"], grounded_ids, outgoing):
                raise StructuralAuthoringError(
                    f"load {load['id']!r} does not reach a grounded component"
                )
        if (
            self._analytical_members
            and not self._member_loads
            and not self._member_distributed_loads
        ):
            raise StructuralAuthoringError(
                "analytical members require at least one member load"
            )
        declared_case_ids = set(self._load_case_categories)
        for combination in self._load_combinations:
            missing_cases = sorted(set(combination["factors"]) - declared_case_ids)
            if missing_cases:
                raise StructuralAuthoringError(
                    f"load combination {combination['id']!r} references missing "
                    f"load cases {missing_cases}"
                )


def helper_source() -> str:
    """Return this standalone module for injection into a compile workspace."""

    return Path(__file__).read_text(encoding="utf-8")


def _required_text(label: str, value: Any) -> str:
    if value is None:
        raise StructuralAuthoringError(f"{label} must not be empty")
    text = str(value).strip()
    if not text:
        raise StructuralAuthoringError(f"{label} must not be empty")
    return text


def _load_case_id(value: str) -> str:
    case_id = _required_text("load case ID", value)
    return case_id if case_id.startswith("case-") else f"case-{case_id}"


def _json_mapping(label: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StructuralAuthoringError(f"{label} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise StructuralAuthoringError(
            f"{label} must contain JSON-serializable finite values"
        ) from exc
    if not isinstance(decoded, dict):
        raise StructuralAuthoringError(f"{label} must be a mapping")
    return decoded


def _required_mapping(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise StructuralAuthoringError(
            f"catalogue section record requires a {key!r} mapping"
        )
    return _json_mapping(f"catalogue section {key}", nested)


def _required_number(value: Mapping[str, Any], key: str) -> float:
    raw = value.get(key)
    if raw is None or isinstance(raw, bool):
        raise StructuralAuthoringError(
            f"catalogue section value {key!r} must be numeric"
        )
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise StructuralAuthoringError(
            f"catalogue section value {key!r} must be numeric"
        ) from exc
    return number


def _optional_positive_number(
    value: Mapping[str, Any],
    key: str,
    *,
    fallback: Any = None,
) -> float | None:
    raw = value.get(key, fallback)
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise StructuralAuthoringError(
            f"catalogue section value {key!r} must be numeric"
        )
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise StructuralAuthoringError(
            f"catalogue section value {key!r} must be numeric"
        ) from exc
    if number <= 0:
        raise StructuralAuthoringError(
            f"catalogue section value {key!r} must be positive"
        )
    return number


def _optional_bending_reference_axis(
    value: Mapping[str, Any],
) -> Literal["local_y", "local_z", "resultant"] | None:
    raw = value.get("bending_reference_axis")
    if raw is None:
        return None
    axis = str(raw)
    if axis == "local_y":
        return "local_y"
    if axis == "local_z":
        return "local_z"
    if axis == "resultant":
        return "resultant"
    raise StructuralAuthoringError(
        "catalogue section bending_reference_axis must be "
        "'local_y', 'local_z', or 'resultant'"
    )


def _member_length(member: Mapping[str, Any]) -> float:
    start = member["start"]
    end = member["end"]
    return sqrt(sum((end[axis] - start[axis]) ** 2 for axis in ("x", "y", "z")))


def _vector3(value: Sequence[float] | dict[str, float]) -> dict[str, float]:
    if isinstance(value, dict):
        if set(value) != {"x", "y", "z"}:
            raise StructuralAuthoringError(
                "vector mapping must contain exactly x, y, and z"
            )
        return {axis: float(value[axis]) for axis in ("x", "y", "z")}
    if isinstance(value, (str, bytes)) or len(value) != 3:
        raise StructuralAuthoringError("vector must contain three values")
    return {
        "x": float(value[0]),
        "y": float(value[1]),
        "z": float(value[2]),
    }


def _restraints(
    value: Sequence[bool] | dict[str, bool],
) -> dict[str, bool]:
    axes = ("dx", "dy", "dz", "rx", "ry", "rz")
    if isinstance(value, dict):
        if set(value) != set(axes):
            raise StructuralAuthoringError(
                "restraint mapping must contain exactly dx, dy, dz, rx, ry, and rz"
            )
        return {axis: bool(value[axis]) for axis in axes}
    if not value:
        return {axis: False for axis in axes}
    if isinstance(value, (str, bytes)) or len(value) != len(axes):
        raise StructuralAuthoringError("restraints must contain six boolean values")
    return {axis: bool(value[index]) for index, axis in enumerate(axes)}


def _reaches_ground(
    start_id: str,
    grounded_ids: set[str],
    outgoing: dict[str, list[str]],
) -> bool:
    queue = deque([start_id])
    visited = {start_id}
    while queue:
        component_id = queue.popleft()
        if component_id in grounded_ids:
            return True
        for next_id in outgoing.get(component_id, []):
            if next_id not in visited:
                visited.add(next_id)
                queue.append(next_id)
    return False
