from __future__ import annotations

from collections.abc import Mapping
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
from typing import Any, Literal, Sequence, cast

import build123d as bd


ComponentKind = Literal["ground", "member", "surface", "connector", "support"]
TransferKind = Literal["force", "shear", "moment", "wind_normal"]
LoadCategory = Literal["dead", "live", "wind", "imperfection"]
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
class StructuralConnection:
    """A registered physical connection that can be reused by checks."""

    id: str
    from_component_id: str
    to_component_id: str
    connector_component_ids: tuple[str, ...]
    transfers: tuple[TransferKind, ...]


@dataclass(frozen=True)
class StructuralMemberGeometry:
    """Placed CAD member with the analytical axis derived by its builder."""

    shape: bd.Shape
    label: str
    part_number: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    rotation_deg: float = 0.0


@dataclass(frozen=True)
class StructuralSurfaceGeometry:
    """Placed panel geometry and its physical loaded area."""

    shape: bd.Shape
    label: str
    part_number: str
    area_m2: float

    def moved(self, location: bd.Location) -> "StructuralSurfaceGeometry":
        return StructuralSurfaceGeometry(
            shape=self.shape.moved(location),
            label=self.label,
            part_number=self.part_number,
            area_m2=self.area_m2,
        )


@dataclass(frozen=True)
class StructuralConnectorGeometry:
    """Placed fastener or connection assembly supplied by a builder."""

    shape: bd.Shape
    label: str
    part_number: str

    def moved(self, location: bd.Location) -> "StructuralConnectorGeometry":
        return StructuralConnectorGeometry(
            shape=self.shape.moved(location),
            label=self.label,
            part_number=self.part_number,
        )


@dataclass(frozen=True)
class StructuralMaterialSpec:
    """A registered elastic material used by an analytical member."""

    id: str


@dataclass(frozen=True)
class StructuralSectionSpec:
    """A registered member section used by an analytical member."""

    id: str


@dataclass(frozen=True)
class StructuralAnalyticalMemberSpec:
    """A specific solver axis belonging to one rendered member component."""

    id: str
    component_id: str


@dataclass(frozen=True)
class StructuralCatalogSectionSpec:
    """Section and material handles resolved from one immutable catalogue record."""

    section: StructuralSectionSpec
    material: StructuralMaterialSpec


@dataclass(frozen=True)
class StructuralSurfaceLoad:
    """A registered surface load that can be distributed to member handles."""

    id: str


@dataclass(frozen=True)
class StructuralWindActionBasisSpec:
    """A site wind snapshot consumed by one or more authored surface loads."""

    id: str
    q_z_kPa: float


class StructuralModel:
    """Build a structural manifest from the same handles used to assemble CAD."""

    def __init__(self, *, title: str) -> None:
        self.title = _required_text("model title", title)
        self._design_basis: dict[str, Any] | None = None
        self._wind_action_bases: list[dict[str, Any]] = []
        self._wind_action_basis_handles: dict[
            str,
            StructuralWindActionBasisSpec,
        ] = {}
        self._stability: dict[str, Any] | None = None
        self._cross_section_verification: dict[str, Any] | None = None
        self._member_stability_verification: dict[str, Any] | None = None
        self._components: list[dict[str, Any]] = []
        self._parts_by_id: dict[str, StructuralPart] = {}
        self._connections: list[dict[str, Any]] = []
        self._connection_handles: dict[str, StructuralConnection] = {}
        self._member_geometry_by_component_id: dict[str, StructuralMemberGeometry] = {}
        self._member_restraint_candidates: list[dict[str, Any]] = []
        self._loads: list[dict[str, Any]] = []
        self._materials: list[dict[str, Any]] = []
        self._material_handles: dict[str, StructuralMaterialSpec] = {}
        self._sections: list[dict[str, Any]] = []
        self._section_handles: dict[str, StructuralSectionSpec] = {}
        self._analytical_members: list[dict[str, Any]] = []
        self._analytical_member_handles: dict[str, StructuralAnalyticalMemberSpec] = {}
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
            raise StructuralAuthoringError(
                "the structural design basis is already defined"
            )
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

    def member_component_from_geometry(
        self,
        geometry: StructuralMemberGeometry,
        *,
        component_id: str,
    ) -> StructuralPart:
        """Register builder-authored member CAD without promoting it to the solver."""

        if not isinstance(geometry, StructuralMemberGeometry):
            raise StructuralAuthoringError(
                "member_component_from_geometry requires StructuralMemberGeometry"
            )
        part = self.member(
            geometry.shape,
            id=component_id,
            label=geometry.label,
            part_number=geometry.part_number,
        )
        self._member_geometry_by_component_id[part.component_id] = geometry
        return part

    def member_from_geometry(
        self,
        geometry: StructuralMemberGeometry,
        *,
        component_id: str,
        member_id: str,
        section: StructuralSectionSpec,
        material: StructuralMaterialSpec,
        start_restraints: Sequence[bool] | dict[str, bool] = (),
        end_restraints: Sequence[bool] | dict[str, bool] = (),
        start_releases: Sequence[bool] | dict[str, bool] = (),
        end_releases: Sequence[bool] | dict[str, bool] = (),
        tension_only: bool = False,
        compression_only: bool = False,
        deflection_limit_ratio: float | None = None,
        deflection_limit_mm: float | None = None,
        deflection_limit_basis: str | None = None,
        assumption: str,
    ) -> StructuralPart:
        """Register CAD and analysis from one builder-authored member value."""

        if not isinstance(geometry, StructuralMemberGeometry):
            raise StructuralAuthoringError(
                "member_from_geometry requires StructuralMemberGeometry"
            )
        part = self.member_component_from_geometry(
            geometry,
            component_id=component_id,
        )
        self.member_axis(
            part,
            id=member_id,
            label=geometry.label,
            start=geometry.start,
            end=geometry.end,
            section=section,
            material=material,
            start_restraints=start_restraints,
            end_restraints=end_restraints,
            rotation_deg=geometry.rotation_deg,
            start_releases=start_releases,
            end_releases=end_releases,
            tension_only=tension_only,
            compression_only=compression_only,
            deflection_limit_ratio=deflection_limit_ratio,
            deflection_limit_mm=deflection_limit_mm,
            deflection_limit_basis=deflection_limit_basis,
            assumption=assumption,
        )
        return part

    def surface_from_geometry(
        self,
        geometry: StructuralSurfaceGeometry,
        *,
        component_id: str,
    ) -> StructuralPart:
        """Register a panel while retaining its builder-authored loaded area."""

        if not isinstance(geometry, StructuralSurfaceGeometry):
            raise StructuralAuthoringError(
                "surface_from_geometry requires StructuralSurfaceGeometry"
            )
        if float(geometry.area_m2) <= 0:
            raise StructuralAuthoringError(
                "StructuralSurfaceGeometry area_m2 must be positive"
            )
        return self.surface(
            geometry.shape,
            id=component_id,
            label=geometry.label,
            part_number=geometry.part_number,
        )

    def connector_from_geometry(
        self,
        geometry: StructuralConnectorGeometry,
        *,
        component_id: str,
    ) -> StructuralPart:
        """Register the fastener assembly rendered by its product builder."""

        if not isinstance(geometry, StructuralConnectorGeometry):
            raise StructuralAuthoringError(
                "connector_from_geometry requires StructuralConnectorGeometry"
            )
        return self.connector(
            geometry.shape,
            id=component_id,
            label=geometry.label,
            part_number=geometry.part_number,
        )

    def stability(
        self,
        *,
        method: Literal["p_delta"],
        stability_combination_id: str,
        imperfection_case_id: str,
        imperfection_basis: str,
        base_stiffness_basis: str,
        base_stiffness_status: Literal["verified", "assumed"],
        amplification_warning_ratio: float = 1.1,
        direction_cases: Sequence[Mapping[str, str]] = (),
        eaves_member_ids: Sequence[str] = (),
        rafter_member_ids: Sequence[str] = (),
        column_height_m: float | None = None,
        analysis_base_model: Literal[
            "unspecified", "perfectly_pinned", "rotational_spring", "fixed"
        ] = "unspecified",
        analysis_basis_status: Literal[
            "assumed", "verified", "verified_conservative"
        ] = "assumed",
        physical_connection_stiffness_status: Literal[
            "not_checked", "not_relied_upon", "verified"
        ] = "not_checked",
    ) -> None:
        """Declare the assumptions and acceptance trigger for a second-order solve."""

        if self._stability is not None:
            raise StructuralAuthoringError(
                "the structural stability basis is already defined"
            )
        if method != "p_delta":
            raise StructuralAuthoringError(
                f"unsupported structural stability method {method!r}"
            )
        if base_stiffness_status not in {"verified", "assumed"}:
            raise StructuralAuthoringError(
                "base_stiffness_status must be 'verified' or 'assumed'"
            )
        warning_ratio = float(amplification_warning_ratio)
        if warning_ratio <= 1.0:
            raise StructuralAuthoringError(
                "amplification_warning_ratio must be greater than 1.0"
            )
        if analysis_base_model not in {
            "unspecified",
            "perfectly_pinned",
            "rotational_spring",
            "fixed",
        }:
            raise StructuralAuthoringError("unsupported analysis_base_model")
        if analysis_basis_status not in {
            "assumed",
            "verified",
            "verified_conservative",
        }:
            raise StructuralAuthoringError("unsupported analysis_basis_status")
        if physical_connection_stiffness_status not in {
            "not_checked",
            "not_relied_upon",
            "verified",
        }:
            raise StructuralAuthoringError(
                "unsupported physical_connection_stiffness_status"
            )
        if column_height_m is not None and float(column_height_m) <= 0:
            raise StructuralAuthoringError("column_height_m must be positive")
        normalized_direction_cases: list[dict[str, str]] = []
        for direction in direction_cases:
            if not isinstance(direction, Mapping):
                raise StructuralAuthoringError(
                    "each stability direction case must be a mapping"
                )
            normalized_direction_cases.append(
                {
                    "id": _required_text("stability direction ID", direction.get("id")),
                    "stability_combination_id": _required_text(
                        "direction stability combination ID",
                        direction.get("stability_combination_id"),
                    ),
                    "imperfection_case_id": _load_case_id(
                        _required_text(
                            "direction imperfection case ID",
                            direction.get("imperfection_case_id"),
                        )
                    ),
                    "nhf_combination_id": _required_text(
                        "direction NHF combination ID",
                        direction.get("nhf_combination_id"),
                    ),
                    "horizontal_axis": _required_text(
                        "direction horizontal axis",
                        direction.get("horizontal_axis", "x"),
                    ),
                }
            )
        direction_ids = [direction["id"] for direction in normalized_direction_cases]
        if len(direction_ids) != len(set(direction_ids)):
            raise StructuralAuthoringError("stability direction IDs must be unique")
        if any(
            direction["horizontal_axis"] not in {"x", "y"}
            for direction in normalized_direction_cases
        ):
            raise StructuralAuthoringError(
                "stability direction horizontal_axis must be 'x' or 'y'"
            )
        self._stability = {
            "method": method,
            "stability_combination_id": _required_text(
                "stability combination ID", stability_combination_id
            ),
            "imperfection_case_id": _load_case_id(imperfection_case_id),
            "imperfection_basis": _required_text(
                "imperfection basis", imperfection_basis
            ),
            "base_stiffness_basis": _required_text(
                "base stiffness basis", base_stiffness_basis
            ),
            "base_stiffness_status": base_stiffness_status,
            "amplification_warning_ratio": warning_ratio,
            "direction_cases": normalized_direction_cases,
            "eaves_member_ids": [
                _required_text("eaves member ID", member_id)
                for member_id in eaves_member_ids
            ],
            "rafter_member_ids": [
                _required_text("rafter member ID", member_id)
                for member_id in rafter_member_ids
            ],
            "column_height_m": (
                None if column_height_m is None else float(column_height_m)
            ),
            "analysis_base_model": analysis_base_model,
            "analysis_basis_status": analysis_basis_status,
            "physical_connection_stiffness_status": (
                physical_connection_stiffness_status
            ),
        }

    def wind_action_basis(
        self,
        *,
        id: str,
        site_address: str,
        latitude: float,
        longitude: float,
        region: str,
        region_area: str,
        region_source: str,
        region_approximate: bool,
        region_status: Literal["suggested", "verified"],
        standard: str,
        table_version: str,
        table_status: Literal["starter", "verified"],
        importance_level: str,
        annual_recurrence_interval_years: int,
        terrain_category: str,
        reference_height_m: float,
        regional_wind_speed_m_s: float,
        climate_change_multiplier: float,
        direction_multiplier: float,
        terrain_height_multiplier: float,
        shielding_multiplier: float,
        topographic_multiplier: float,
        site_wind_speed_m_s: float,
        q_z_kPa: float,
        verifier_hash: str,
        provenance: str,
    ) -> StructuralWindActionBasisSpec:
        """Register an immutable, externally derived site-wind snapshot.

        The structural helper checks the dimensional arithmetic. The Tertius
        backend additionally recomputes the snapshot against the named table
        version before treating it as calculation evidence.
        """

        basis_id = _required_text("wind action basis ID", id)
        if basis_id in self._wind_action_basis_handles:
            raise StructuralAuthoringError(
                f"wind action basis ID {basis_id!r} is already registered"
            )
        if region_status not in {"suggested", "verified"}:
            raise StructuralAuthoringError(
                "wind region status must be 'suggested' or 'verified'"
            )
        if table_status not in {"starter", "verified"}:
            raise StructuralAuthoringError(
                "wind table status must be 'starter' or 'verified'"
            )
        if int(annual_recurrence_interval_years) <= 0:
            raise StructuralAuthoringError(
                "wind annual recurrence interval must be positive"
            )

        multiplier_values = {
            "climate_change_multiplier": float(climate_change_multiplier),
            "direction_multiplier": float(direction_multiplier),
            "terrain_height_multiplier": float(terrain_height_multiplier),
            "shielding_multiplier": float(shielding_multiplier),
            "topographic_multiplier": float(topographic_multiplier),
        }
        speed = float(regional_wind_speed_m_s)
        site_speed = float(site_wind_speed_m_s)
        pressure = float(q_z_kPa)
        reference_height = float(reference_height_m)
        if (
            min(
                speed,
                site_speed,
                pressure,
                reference_height,
                *multiplier_values.values(),
            )
            <= 0
        ):
            raise StructuralAuthoringError(
                "wind speeds, pressure, height, and multipliers must be positive"
            )
        derived_site_speed = speed
        for multiplier in multiplier_values.values():
            derived_site_speed *= multiplier
        if abs(derived_site_speed - site_speed) > 1e-6:
            raise StructuralAuthoringError(
                f"wind basis {basis_id!r} site speed does not match "
                "V_R multiplied by its authored multipliers"
            )
        derived_pressure = 0.5 * 1.2 * site_speed**2 / 1000.0
        if abs(derived_pressure - pressure) > 1e-6:
            raise StructuralAuthoringError(
                f"wind basis {basis_id!r} q_z does not match 0.5 rho V_sit^2"
            )

        value = {
            "id": basis_id,
            "site_address": _required_text("site address", site_address),
            "latitude": float(latitude),
            "longitude": float(longitude),
            "region": _required_text("wind region", region).upper(),
            "region_area": str(region_area),
            "region_source": _required_text("wind region source", region_source),
            "region_approximate": bool(region_approximate),
            "region_status": region_status,
            "standard": _required_text("wind standard", standard),
            "table_version": _required_text(
                "wind table version",
                table_version,
            ),
            "table_status": table_status,
            "importance_level": _required_text(
                "importance level",
                importance_level,
            ),
            "annual_recurrence_interval_years": int(annual_recurrence_interval_years),
            "terrain_category": _required_text(
                "terrain category",
                terrain_category,
            ),
            "reference_height_m": reference_height,
            "regional_wind_speed_m_s": speed,
            **multiplier_values,
            "site_wind_speed_m_s": site_speed,
            "q_z_kPa": pressure,
            "verifier_hash": _required_text(
                "wind verifier hash",
                verifier_hash,
            ),
            "provenance": _required_text("wind basis provenance", provenance),
        }
        handle = StructuralWindActionBasisSpec(id=basis_id, q_z_kPa=pressure)
        self._wind_action_bases.append(value)
        self._wind_action_basis_handles[basis_id] = handle
        return handle

    def cross_section_verification(
        self,
        *,
        pack_id: Literal["as_nzs_4600_2018_ewm"],
        combination_ids: Sequence[str],
        members: Sequence[StructuralPart | StructuralAnalyticalMemberSpec] = (),
        off_axis_tolerance: float = 1e-6,
    ) -> None:
        """Select a versioned section-capacity pack and its ULS envelope."""

        if self._cross_section_verification is not None:
            raise StructuralAuthoringError(
                "the cross-section verification basis is already defined"
            )
        normalized_combination_ids = [
            _required_text("cross-section load combination ID", combination_id)
            for combination_id in combination_ids
        ]
        if not normalized_combination_ids:
            raise StructuralAuthoringError(
                "cross-section verification requires at least one load combination"
            )
        if len(normalized_combination_ids) != len(set(normalized_combination_ids)):
            raise StructuralAuthoringError(
                "cross-section verification load combination IDs must be unique"
            )
        tolerance = float(off_axis_tolerance)
        if tolerance < 0:
            raise StructuralAuthoringError(
                "cross-section off_axis_tolerance must not be negative"
            )
        selected_member_ids: list[str] = []
        for member in members:
            analytical_member = self._analytical_member(member)
            if analytical_member["id"] in selected_member_ids:
                raise StructuralAuthoringError(
                    f"cross-section member {analytical_member['id']!r} is repeated"
                )
            selected_member_ids.append(analytical_member["id"])
        self._cross_section_verification = {
            "pack_id": pack_id,
            "combination_ids": normalized_combination_ids,
            "member_ids": selected_member_ids,
            "off_axis_tolerance": tolerance,
        }

    def member_stability_verification(
        self,
        *,
        pack_id: Literal["as_nzs_4600_2018_ewm_member"],
        combination_ids: Sequence[str],
        segments: Sequence[Mapping[str, Any]] | None = None,
        members: Sequence[StructuralPart | StructuralAnalyticalMemberSpec] = (),
        distortional_buckling_status: Literal["unverified", "verified"] = "unverified",
        distortional_buckling_basis: str | None = None,
        off_axis_tolerance: float = 1e-6,
    ) -> None:
        """Declare or derive restraint-defined member segments for Stage 7."""

        if self._member_stability_verification is not None:
            raise StructuralAuthoringError(
                "the member-stability verification basis is already defined"
            )
        if pack_id != "as_nzs_4600_2018_ewm_member":
            raise StructuralAuthoringError(
                f"unsupported member-stability pack {pack_id!r}"
            )
        normalized_combination_ids = [
            _required_text("member-stability load combination ID", combination_id)
            for combination_id in combination_ids
        ]
        if not normalized_combination_ids:
            raise StructuralAuthoringError(
                "member-stability verification requires at least one load combination"
            )
        if len(normalized_combination_ids) != len(set(normalized_combination_ids)):
            raise StructuralAuthoringError(
                "member-stability verification load combination IDs must be unique"
            )
        tolerance = float(off_axis_tolerance)
        if tolerance < 0:
            raise StructuralAuthoringError(
                "member-stability off_axis_tolerance must not be negative"
            )

        member_lengths = {
            member["id"]: sqrt(
                sum(
                    (float(member["end"][axis]) - float(member["start"][axis])) ** 2
                    for axis in ("x", "y", "z")
                )
            )
            for member in self._analytical_members
        }
        if segments is not None and members:
            raise StructuralAuthoringError(
                "member-stability verification accepts authored segments or member "
                "handles, not both"
            )
        if segments is None and not members:
            raise StructuralAuthoringError(
                "member-stability verification requires segments or member handles"
            )
        if distortional_buckling_status not in {"unverified", "verified"}:
            raise StructuralAuthoringError(
                "distortional_buckling_status must be unverified or verified"
            )

        selected_member_ids: list[str] = []
        if members:
            for member in members:
                analytical_member = self._analytical_member(member)
                if analytical_member["id"] in selected_member_ids:
                    raise StructuralAuthoringError(
                        f"member-stability member {analytical_member['id']!r} is repeated"
                    )
                selected_member_ids.append(analytical_member["id"])
            shared_distortional_basis = _required_text(
                "member-stability distortional buckling basis",
                distortional_buckling_basis,
            )
            derived_segments: list[dict[str, Any]] = []
            for member_id in selected_member_ids:
                member_length = member_lengths[member_id]
                candidates = sorted(
                    (
                        candidate
                        for candidate in self._member_restraint_candidates
                        if candidate["member_id"] == member_id
                    ),
                    key=lambda candidate: float(candidate["distance_m"]),
                )
                boundary_groups: list[dict[str, Any]] = [
                    {"distance": 0.0, "candidate_ids": []}
                ]
                for candidate in candidates:
                    distance = min(
                        member_length,
                        max(0.0, float(candidate["distance_m"])),
                    )
                    if abs(distance - float(boundary_groups[-1]["distance"])) <= 1e-6:
                        boundary_groups[-1]["candidate_ids"].append(candidate["id"])
                    else:
                        boundary_groups.append(
                            {
                                "distance": distance,
                                "candidate_ids": [candidate["id"]],
                            }
                        )
                if abs(member_length - float(boundary_groups[-1]["distance"])) <= 1e-6:
                    boundary_groups[-1]["distance"] = member_length
                else:
                    boundary_groups.append(
                        {"distance": member_length, "candidate_ids": []}
                    )
                for index, (start, end) in enumerate(
                    zip(boundary_groups, boundary_groups[1:]),
                    start=1,
                ):
                    start_distance = float(start["distance"])
                    end_distance = float(end["distance"])
                    if end_distance - start_distance <= 1e-9:
                        continue
                    start_ids = list(start["candidate_ids"])
                    end_ids = list(end["candidate_ids"])
                    derived_segments.append(
                        {
                            "id": f"segment-{member_id}-{index}",
                            "member_id": member_id,
                            "start_distance_m": start_distance,
                            "end_distance_m": end_distance,
                            "minor_axis_effective_length_factor": 1.0,
                            "torsional_effective_length_factor": 1.0,
                            "lateral_bending_restraint": "unverified",
                            "restraint_status": "assumed",
                            "restraint_basis": (
                                "Segment boundaries were derived from the actual "
                                "builder-authored bracing-member axes and registered "
                                "portal connections. Candidate IDs at start/end: "
                                f"{start_ids or ['none']} / {end_ids or ['none']}. "
                                "Candidate geometry is not restraint-capacity evidence."
                            ),
                            "distortional_buckling_status": (
                                distortional_buckling_status
                            ),
                            "distortional_buckling_basis": shared_distortional_basis,
                            "start_restraint_candidate_ids": start_ids,
                            "end_restraint_candidate_ids": end_ids,
                        }
                    )
            segments = derived_segments

        normalized_segments: list[dict[str, Any]] = []
        segment_ids: set[str] = set()
        candidate_ids = {
            candidate["id"] for candidate in self._member_restraint_candidates
        }
        for raw_segment in segments or ():
            if not isinstance(raw_segment, Mapping):
                raise StructuralAuthoringError(
                    "member-stability segments must be mappings"
                )
            segment_id = _required_text(
                "member-stability segment ID",
                raw_segment.get("id"),
            )
            if segment_id in segment_ids:
                raise StructuralAuthoringError(
                    f"member-stability segment ID {segment_id!r} is duplicated"
                )
            segment_ids.add(segment_id)
            member_id = _required_text(
                "member-stability segment member ID",
                raw_segment.get("member_id"),
            )
            if member_id not in member_lengths:
                raise StructuralAuthoringError(
                    f"member-stability segment {segment_id!r} references missing "
                    f"member {member_id!r}"
                )
            start_distance_m = float(raw_segment.get("start_distance_m", 0.0))
            end_distance_m = float(
                raw_segment.get("end_distance_m", member_lengths[member_id])
            )
            if not (
                0
                <= start_distance_m
                < end_distance_m
                <= member_lengths[member_id] + 1e-9
            ):
                raise StructuralAuthoringError(
                    f"member-stability segment {segment_id!r} lies outside "
                    f"member {member_id!r}"
                )
            lateral_restraint = str(
                raw_segment.get("lateral_bending_restraint", "unverified")
            )
            if lateral_restraint not in {
                "unverified",
                "continuous_compression_flange",
            }:
                raise StructuralAuthoringError(
                    "lateral_bending_restraint must be 'unverified' or "
                    "'continuous_compression_flange'"
                )
            restraint_status = str(raw_segment.get("restraint_status", "assumed"))
            if restraint_status not in {"assumed", "verified"}:
                raise StructuralAuthoringError(
                    "restraint_status must be 'assumed' or 'verified'"
                )
            if (
                lateral_restraint == "continuous_compression_flange"
                and restraint_status != "verified"
            ):
                raise StructuralAuthoringError(
                    "continuous compression-flange restraint requires "
                    "restraint_status='verified'"
                )
            distortional_status = str(
                raw_segment.get("distortional_buckling_status", "unverified")
            )
            if distortional_status not in {"unverified", "verified"}:
                raise StructuralAuthoringError(
                    "distortional_buckling_status must be 'unverified' or 'verified'"
                )
            minor_factor = float(
                raw_segment.get("minor_axis_effective_length_factor", 1.0)
            )
            torsional_factor = float(
                raw_segment.get("torsional_effective_length_factor", 1.0)
            )
            if min(minor_factor, torsional_factor) <= 0:
                raise StructuralAuthoringError(
                    "member-stability effective-length factors must be positive"
                )
            start_candidate_ids = [
                _required_text("start restraint candidate ID", candidate_id)
                for candidate_id in raw_segment.get("start_restraint_candidate_ids", ())
            ]
            end_candidate_ids = [
                _required_text("end restraint candidate ID", candidate_id)
                for candidate_id in raw_segment.get("end_restraint_candidate_ids", ())
            ]
            missing_candidate_ids = sorted(
                (set(start_candidate_ids) | set(end_candidate_ids)) - candidate_ids
            )
            if missing_candidate_ids:
                raise StructuralAuthoringError(
                    f"member-stability segment {segment_id!r} references missing "
                    f"restraint candidates {missing_candidate_ids}"
                )
            normalized_segments.append(
                {
                    "id": segment_id,
                    "member_id": member_id,
                    "start_distance_m": start_distance_m,
                    "end_distance_m": end_distance_m,
                    "minor_axis_effective_length_factor": minor_factor,
                    "torsional_effective_length_factor": torsional_factor,
                    "lateral_bending_restraint": lateral_restraint,
                    "restraint_status": restraint_status,
                    "restraint_basis": _required_text(
                        "member-stability restraint basis",
                        raw_segment.get("restraint_basis"),
                    ),
                    "distortional_buckling_status": distortional_status,
                    "distortional_buckling_basis": _required_text(
                        "member-stability distortional buckling basis",
                        raw_segment.get("distortional_buckling_basis"),
                    ),
                    "start_restraint_candidate_ids": start_candidate_ids,
                    "end_restraint_candidate_ids": end_candidate_ids,
                }
            )
        if not normalized_segments:
            raise StructuralAuthoringError(
                "member-stability verification requires at least one segment"
            )
        self._member_stability_verification = {
            "pack_id": pack_id,
            "combination_ids": normalized_combination_ids,
            "segments": normalized_segments,
            "restraint_candidates": [
                dict(candidate)
                for candidate in self._member_restraint_candidates
                if candidate["member_id"]
                in {segment["member_id"] for segment in normalized_segments}
            ],
            "off_axis_tolerance": tolerance,
        }

    def site_wind_basis(
        self,
        site_dict: Mapping[str, Any],
    ) -> StructuralWindActionBasisSpec:
        """Link a project ``tertius_site.py`` dictionary to wind loads.

        The compile helper records a dimensionally coherent placeholder so CAD
        compilation remains deterministic and self-contained. The authenticated
        Structural API replaces it with the validated, current site calculation
        whenever capture or analysis is requested. Consequently site-only edits
        never require a Build123D rebuild.
        """

        site = _json_mapping("site_dict", site_dict)
        project_basis = _json_mapping(
            "site_dict.project_basis",
            site.get("project_basis", {}),
        )
        location = _json_mapping(
            "site_dict.location",
            site.get("location", {}),
        )
        wind = _json_mapping("site_dict.wind", site.get("wind", {}))
        region_status_text = str(wind.get("region_status", "suggested"))
        if region_status_text not in {"suggested", "verified"}:
            raise StructuralAuthoringError(
                "site_dict wind region_status must be 'suggested' or 'verified'"
            )
        region_status = cast(
            Literal["suggested", "verified"],
            region_status_text,
        )
        table_status_text = str(wind.get("table_status", "starter"))
        if table_status_text not in {"starter", "verified"}:
            raise StructuralAuthoringError(
                "site_dict wind table_status must be 'starter' or 'verified'"
            )
        table_status = cast(Literal["starter", "verified"], table_status_text)
        importance_level = _required_text(
            "site importance level",
            project_basis.get("importance_level", "2"),
        )
        ari_by_importance = {"1": 100, "2": 500, "3": 1000, "4": 2500}
        annual_probability = str(wind.get("annual_probability_uls") or "").strip()
        try:
            annual_recurrence_interval = (
                int(float(annual_probability.split("/", 1)[-1]))
                if annual_probability
                else ari_by_importance[importance_level]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StructuralAuthoringError(
                "site_dict has an invalid importance level or annual probability"
            ) from exc

        placeholder_speed = 40.0
        placeholder_pressure = 0.5 * 1.2 * placeholder_speed**2 / 1000.0
        return self.wind_action_basis(
            id=_required_text(
                "site wind basis ID",
                wind.get("basis_id", "project-site-wind"),
            ),
            site_address=_required_text(
                "site address",
                location.get("address") or "Site address pending",
            ),
            latitude=float(location.get("latitude", 0.0)),
            longitude=float(location.get("longitude", 0.0)),
            region=_required_text("wind region", wind.get("region", "A2")),
            region_area=str(wind.get("region_area", "")),
            region_source=_required_text(
                "wind region source",
                wind.get("region_source", "tertius_site.py"),
            ),
            region_approximate=bool(wind.get("region_approximate", True)),
            region_status=region_status,
            standard="Tertius site calculation overlay",
            table_version="compile-placeholder-v1",
            table_status=table_status,
            importance_level=importance_level,
            annual_recurrence_interval_years=annual_recurrence_interval,
            terrain_category=_required_text(
                "terrain category",
                wind.get("terrain_category", "3"),
            ),
            reference_height_m=float(wind.get("reference_height_m", 3.0)),
            regional_wind_speed_m_s=placeholder_speed,
            climate_change_multiplier=1.0,
            direction_multiplier=1.0,
            terrain_height_multiplier=1.0,
            shielding_multiplier=1.0,
            topographic_multiplier=1.0,
            site_wind_speed_m_s=placeholder_speed,
            q_z_kPa=placeholder_pressure,
            verifier_hash="compile-placeholder",
            provenance=(
                "Compile-time placeholder linked to tertius_site.py; replaced "
                "by the Structural API before analysis"
            ),
        )

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
    ) -> StructuralConnection:
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

        connection = StructuralConnection(
            id=connection_id,
            from_component_id=source.component_id,
            to_component_id=target.component_id,
            connector_component_ids=tuple(connector_ids),
            transfers=tuple(cast(TransferKind, item) for item in transfer_values),
        )
        self._connection_handles[connection_id] = connection
        self._connections.append(
            {
                "id": connection.id,
                "label": _required_text("connection label", label),
                "from_component_id": connection.from_component_id,
                "to_component_id": connection.to_component_id,
                "connector_component_ids": list(connection.connector_component_ids),
                "transfers": list(connection.transfers),
            }
        )
        return connection

    def member_restraint_from_connection(
        self,
        member: StructuralPart | StructuralAnalyticalMemberSpec,
        bracing_member: StructuralPart,
        *,
        connection: StructuralConnection,
        id: str | None = None,
        restrains_lateral_translation: bool,
        restrains_twist: bool,
        restrained_flange: Literal[
            "auto",
            "positive_local_y",
            "negative_local_y",
            "both",
        ] = "auto",
        demand_model: Literal[
            "not_defined",
            "aisi_2004_d3_2_2_eccentric_load_couple",
        ] = "aisi_2004_d3_2_2_eccentric_load_couple",
        demand_factor: float = 1.5,
        design_force_capacity_kN: float | None = None,
        design_moment_capacity_kNm: float | None = None,
        stiffness_status: Literal["unverified", "verified"] = "unverified",
        evidence_status: Literal["candidate", "verified", "unsupported"] = "candidate",
        evidence_pack_id: str | None = None,
        anchorage_connections: Sequence[StructuralConnection] = (),
        anchorage_status: Literal["unverified", "verified"] = "unverified",
        anchorage_basis: str = "No longitudinal anchorage evidence is declared.",
        evidence_basis: str,
        capacity_basis: str | None = None,
        provenance: str | None = None,
        maximum_axis_separation_m: float = 0.25,
    ) -> None:
        """Derive a Stage 7 restraint location from connected member geometry.

        The analytical member, bracing-member axis, connector identity, and
        restraint location all come from already registered handles. This keeps
        a hand-maintained list of member IDs and restraint distances out of the
        design file.
        """

        primary = self._member_component(member)
        brace = self._require_registered(bracing_member)
        if primary.kind != "member" or brace.kind != "member":
            raise StructuralAuthoringError(
                "member restraints require member and bracing-member components"
            )
        registered_connection = self._connection_handles.get(connection.id)
        if registered_connection != connection:
            raise StructuralAuthoringError(
                f"member restraint references unregistered connection {connection.id!r}"
            )
        if {connection.from_component_id, connection.to_component_id} != {
            primary.component_id,
            brace.component_id,
        }:
            raise StructuralAuthoringError(
                f"connection {connection.id!r} does not join member "
                f"{primary.component_id!r} to brace {brace.component_id!r}"
            )
        analytical_member = self._analytical_member(member)
        brace_geometry = self._member_geometry_by_component_id.get(brace.component_id)
        if brace_geometry is None:
            raise StructuralAuthoringError(
                f"bracing member {brace.component_id!r} has no builder-authored axis"
            )
        if not (restrains_lateral_translation or restrains_twist):
            raise StructuralAuthoringError(
                "member restraint must declare lateral-translation or twist capability"
            )
        if evidence_status not in {"candidate", "verified", "unsupported"}:
            raise StructuralAuthoringError(
                "member restraint evidence_status must be candidate, verified, "
                "or unsupported"
            )
        if restrained_flange not in {
            "auto",
            "positive_local_y",
            "negative_local_y",
            "both",
        }:
            raise StructuralAuthoringError(
                "member restraint restrained_flange must be auto, "
                "positive_local_y, negative_local_y, or both"
            )
        if demand_model not in {
            "not_defined",
            "aisi_2004_d3_2_2_eccentric_load_couple",
        }:
            raise StructuralAuthoringError(
                "member restraint demand_model is not supported"
            )
        if float(demand_factor) <= 0:
            raise StructuralAuthoringError(
                "member restraint demand_factor must be positive"
            )
        if stiffness_status not in {"unverified", "verified"}:
            raise StructuralAuthoringError(
                "member restraint stiffness_status must be unverified or verified"
            )
        if anchorage_status not in {"unverified", "verified"}:
            raise StructuralAuthoringError(
                "member restraint anchorage_status must be unverified or verified"
            )
        for label, value in (
            ("design force capacity", design_force_capacity_kN),
            ("design moment capacity", design_moment_capacity_kNm),
        ):
            if value is not None and float(value) <= 0:
                raise StructuralAuthoringError(
                    f"member restraint {label} must be positive"
                )
        if evidence_status == "verified" and (
            design_force_capacity_kN is None
            or design_moment_capacity_kNm is None
            or stiffness_status != "verified"
        ):
            raise StructuralAuthoringError(
                "verified member restraint requires verified stiffness and "
                "positive force/moment capacities"
            )

        anchorage_component_ids = [brace.component_id]
        anchorage_connection_ids: list[str] = []
        anchorage_cursor = brace.component_id
        for anchorage_connection in anchorage_connections:
            registered_anchorage_connection = self._connection_handles.get(
                anchorage_connection.id
            )
            if registered_anchorage_connection != anchorage_connection:
                raise StructuralAuthoringError(
                    "member restraint anchorage references an unregistered "
                    f"connection {anchorage_connection.id!r}"
                )
            endpoints = {
                anchorage_connection.from_component_id,
                anchorage_connection.to_component_id,
            }
            if anchorage_cursor not in endpoints:
                raise StructuralAuthoringError(
                    f"member restraint anchorage connection "
                    f"{anchorage_connection.id!r} does not continue from "
                    f"{anchorage_cursor!r}"
                )
            anchorage_cursor = next(
                component_id
                for component_id in endpoints
                if component_id != anchorage_cursor
            )
            anchorage_connection_ids.append(anchorage_connection.id)
            anchorage_component_ids.append(anchorage_cursor)
        anchorage_grounded_component_id = (
            anchorage_cursor
            if self._component_record(anchorage_cursor).get("grounded") is True
            else None
        )
        if anchorage_status == "verified" and anchorage_grounded_component_id is None:
            raise StructuralAuthoringError(
                "verified member restraint anchorage must terminate at a grounded "
                "component"
            )
        maximum_separation = float(maximum_axis_separation_m)
        if maximum_separation <= 0:
            raise StructuralAuthoringError(
                "member restraint maximum_axis_separation_m must be positive"
            )

        primary_start = _vector_tuple(analytical_member["start"])
        primary_end = _vector_tuple(analytical_member["end"])
        brace_start = tuple(float(value) for value in brace_geometry.start)
        brace_end = tuple(float(value) for value in brace_geometry.end)
        primary_fraction, _brace_fraction, primary_point, brace_point = (
            _closest_points_on_segments(
                primary_start,
                primary_end,
                brace_start,
                brace_end,
            )
        )
        separation = sqrt(
            sum((brace_point[index] - primary_point[index]) ** 2 for index in range(3))
        )
        if separation > maximum_separation + 1e-9:
            raise StructuralAuthoringError(
                f"member restraint {connection.id!r} axes are {separation:.6f} m "
                f"apart, beyond the {maximum_separation:.6f} m authoring tolerance"
            )
        member_length = sqrt(
            sum((primary_end[index] - primary_start[index]) ** 2 for index in range(3))
        )
        candidate_id = (
            _required_text("member-restraint candidate ID", id)
            if id is not None
            else f"restraint-{connection.id}"
        )
        if any(
            candidate["id"] == candidate_id
            for candidate in self._member_restraint_candidates
        ):
            raise StructuralAuthoringError(
                f"member-restraint candidate {candidate_id!r} is already registered"
            )
        self._member_restraint_candidates.append(
            {
                "id": candidate_id,
                "member_id": analytical_member["id"],
                "bracing_component_id": brace.component_id,
                "connection_id": connection.id,
                "connector_component_ids": list(connection.connector_component_ids),
                "member_position": _vector_dict(primary_point),
                "brace_position": _vector_dict(brace_point),
                "distance_m": primary_fraction * member_length,
                "axis_separation_m": separation,
                "restrains_lateral_translation": bool(restrains_lateral_translation),
                "restrains_twist": bool(restrains_twist),
                "restrained_flange": restrained_flange,
                "demand_model": demand_model,
                "demand_factor": float(demand_factor),
                "design_force_capacity_kN": (
                    float(design_force_capacity_kN)
                    if design_force_capacity_kN is not None
                    else None
                ),
                "design_moment_capacity_kNm": (
                    float(design_moment_capacity_kNm)
                    if design_moment_capacity_kNm is not None
                    else None
                ),
                "stiffness_status": stiffness_status,
                "evidence_status": evidence_status,
                "evidence_basis": _required_text(
                    "member restraint evidence basis", evidence_basis
                ),
                "capacity_basis": _required_text(
                    "member restraint capacity basis",
                    capacity_basis or evidence_basis,
                ),
                "provenance": _required_text(
                    "member restraint provenance",
                    provenance or evidence_basis,
                ),
                "evidence_pack_id": (
                    _required_text(
                        "member restraint evidence pack ID", evidence_pack_id
                    )
                    if evidence_pack_id is not None
                    else None
                ),
                "configuration": {
                    "primary_part_number": self._component_part_number(
                        primary.component_id
                    ),
                    "bracing_part_number": self._component_part_number(
                        brace.component_id
                    ),
                    "connector_part_numbers": sorted(
                        filter(
                            None,
                            (
                                self._component_part_number(component_id)
                                for component_id in connection.connector_component_ids
                            ),
                        )
                    ),
                },
                "anchorage_status": anchorage_status,
                "anchorage_component_ids": anchorage_component_ids,
                "anchorage_connection_ids": anchorage_connection_ids,
                "anchorage_grounded_component_id": (anchorage_grounded_component_id),
                "anchorage_basis": _required_text(
                    "member restraint anchorage basis", anchorage_basis
                ),
            }
        )

    def member_boundary_restraint_from_connection(
        self,
        member: StructuralPart | StructuralAnalyticalMemberSpec,
        *,
        connection: StructuralConnection,
        at: Literal["start", "end"],
        restrained_flange: Literal[
            "positive_local_y",
            "negative_local_y",
            "both",
        ],
        restrains_lateral_translation: bool,
        restrains_twist: bool,
        evidence_status: Literal["candidate", "verified", "unsupported"] = "candidate",
        evidence_basis: str,
        capacity_basis: str | None = None,
        provenance: str | None = None,
        design_force_capacity_kN: float | None = None,
        design_moment_capacity_kNm: float | None = None,
        stiffness_status: Literal["unverified", "verified"] = "unverified",
    ) -> None:
        """Register a connection-derived restraint at a member end.

        Knee and apex joints do not have an offset bracing-member axis from which
        the restrained flange can be inferred. The member endpoint and connected
        physical components remain handle-derived, while the flange capability is
        an explicit, auditable property of the connection detail.
        """

        primary = self._member_component(member)
        if primary.kind != "member":
            raise StructuralAuthoringError(
                "member boundary restraints require a member component"
            )
        registered_connection = self._connection_handles.get(connection.id)
        if registered_connection != connection:
            raise StructuralAuthoringError(
                f"member boundary restraint references unregistered connection "
                f"{connection.id!r}"
            )
        if primary.component_id not in {
            connection.from_component_id,
            connection.to_component_id,
        }:
            raise StructuralAuthoringError(
                f"connection {connection.id!r} does not include member "
                f"{primary.component_id!r}"
            )
        analytical_member = self._analytical_member(member)
        if at not in {"start", "end"}:
            raise StructuralAuthoringError(
                "member boundary restraint location must be start or end"
            )
        if restrained_flange not in {
            "positive_local_y",
            "negative_local_y",
            "both",
        }:
            raise StructuralAuthoringError(
                "member boundary restrained_flange must identify a local flange or both"
            )
        if not (restrains_lateral_translation or restrains_twist):
            raise StructuralAuthoringError(
                "member boundary restraint must declare lateral or twist capability"
            )
        if evidence_status not in {"candidate", "verified", "unsupported"}:
            raise StructuralAuthoringError(
                "member boundary restraint evidence_status must be candidate, "
                "verified, or unsupported"
            )
        if stiffness_status not in {"unverified", "verified"}:
            raise StructuralAuthoringError(
                "member boundary restraint stiffness_status must be unverified or verified"
            )
        for label, value in (
            ("design force capacity", design_force_capacity_kN),
            ("design moment capacity", design_moment_capacity_kNm),
        ):
            if value is not None and float(value) <= 0:
                raise StructuralAuthoringError(
                    f"member boundary restraint {label} must be positive"
                )
        if evidence_status == "verified" and (
            design_force_capacity_kN is None
            or design_moment_capacity_kNm is None
            or stiffness_status != "verified"
        ):
            raise StructuralAuthoringError(
                "verified member boundary restraint requires verified stiffness "
                "and positive force/moment capacities"
            )
        primary_start = _vector_tuple(analytical_member["start"])
        primary_end = _vector_tuple(analytical_member["end"])
        member_length = sqrt(
            sum((primary_end[index] - primary_start[index]) ** 2 for index in range(3))
        )
        point = primary_start if at == "start" else primary_end
        other_component_id = (
            connection.to_component_id
            if connection.from_component_id == primary.component_id
            else connection.from_component_id
        )
        candidate_id = f"restraint-{connection.id}-{analytical_member['id']}"
        if any(
            candidate["id"] == candidate_id
            for candidate in self._member_restraint_candidates
        ):
            raise StructuralAuthoringError(
                f"member-restraint candidate {candidate_id!r} is already registered"
            )
        self._member_restraint_candidates.append(
            {
                "id": candidate_id,
                "member_id": analytical_member["id"],
                "bracing_component_id": other_component_id,
                "connection_id": connection.id,
                "connector_component_ids": list(connection.connector_component_ids),
                "member_position": _vector_dict(point),
                "brace_position": _vector_dict(point),
                "distance_m": 0.0 if at == "start" else member_length,
                "axis_separation_m": 0.0,
                "restrains_lateral_translation": bool(restrains_lateral_translation),
                "restrains_twist": bool(restrains_twist),
                "restrained_flange": restrained_flange,
                "demand_model": "not_defined",
                "demand_factor": 1.5,
                "design_force_capacity_kN": (
                    float(design_force_capacity_kN)
                    if design_force_capacity_kN is not None
                    else None
                ),
                "design_moment_capacity_kNm": (
                    float(design_moment_capacity_kNm)
                    if design_moment_capacity_kNm is not None
                    else None
                ),
                "stiffness_status": stiffness_status,
                "evidence_status": evidence_status,
                "evidence_basis": _required_text(
                    "member boundary restraint evidence basis", evidence_basis
                ),
                "capacity_basis": _required_text(
                    "member boundary restraint capacity basis",
                    capacity_basis or evidence_basis,
                ),
                "provenance": _required_text(
                    "member boundary restraint provenance",
                    provenance or evidence_basis,
                ),
                "evidence_pack_id": None,
                "configuration": {
                    "primary_part_number": self._component_part_number(
                        primary.component_id
                    ),
                    "bracing_part_number": self._component_part_number(
                        other_component_id
                    ),
                    "connector_part_numbers": sorted(
                        filter(
                            None,
                            (
                                self._component_part_number(component_id)
                                for component_id in connection.connector_component_ids
                            ),
                        )
                    ),
                },
                "anchorage_status": "unverified",
                "anchorage_component_ids": [other_component_id],
                "anchorage_connection_ids": [],
                "anchorage_grounded_component_id": None,
                "anchorage_basis": (
                    "No longitudinal anchorage path is declared for this member "
                    "boundary connection."
                ),
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

    def wind_surface_load(
        self,
        component: StructuralPart,
        *,
        basis: StructuralWindActionBasisSpec,
        id: str,
        label: str,
        case_id: str,
        case_label: str,
        net_pressure_coefficient: float,
        coefficient_status: Literal["assumed", "verified"],
        area_m2: float,
        direction: Sequence[float] | dict[str, float],
        provenance: str,
    ) -> StructuralSurfaceLoad:
        registered_basis = self._wind_action_basis_handles.get(getattr(basis, "id", ""))
        if registered_basis is not basis:
            raise StructuralAuthoringError(
                "wind surface loads accept registered wind-action basis handles only"
            )
        if coefficient_status not in {"assumed", "verified"}:
            raise StructuralAuthoringError(
                "wind coefficient status must be 'assumed' or 'verified'"
            )
        coefficient = float(net_pressure_coefficient)
        if coefficient == 0:
            raise StructuralAuthoringError(
                "wind net pressure coefficient must be non-zero"
            )
        handle = self.surface_load(
            component,
            id=id,
            label=label,
            case="wind",
            case_id=case_id,
            case_label=case_label,
            pressure_kPa=abs(coefficient) * basis.q_z_kPa,
            area_m2=area_m2,
            direction=direction,
            provenance=provenance,
        )
        load = next(item for item in self._loads if item["id"] == handle.id)
        load.update(
            {
                "wind_basis_id": basis.id,
                "net_pressure_coefficient": coefficient,
                "coefficient_status": coefficient_status,
            }
        )
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
        tension_only: bool = False,
        compression_only: bool = False,
        deflection_limit_ratio: float | None = None,
        deflection_limit_mm: float | None = None,
        deflection_limit_basis: str | None = None,
        assumption: str,
    ) -> StructuralAnalyticalMemberSpec:
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
        section_spec = self._require_section(section)
        material_spec = self._require_material(material)
        start_vector = _vector3(start)
        end_vector = _vector3(end)
        if start_vector == end_vector:
            raise StructuralAuthoringError(
                f"analytical member {member_id!r} has zero length"
            )
        if tension_only and compression_only:
            raise StructuralAuthoringError(
                f"analytical member {member_id!r} cannot be both tension-only "
                "and compression-only"
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
                "tension_only": bool(tension_only),
                "compression_only": bool(compression_only),
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
        handle = StructuralAnalyticalMemberSpec(
            id=member_id,
            component_id=registered.component_id,
        )
        self._analytical_member_handles[member_id] = handle
        return handle

    def distribute_surface_load(
        self,
        load: StructuralSurfaceLoad,
        member: StructuralPart | StructuralAnalyticalMemberSpec,
        *,
        id: str,
        label: str,
        positions_m: Sequence[float],
        weights: Sequence[float] = (),
        provenance: str,
    ) -> None:
        source = self._require_surface_load(load)
        analytical_member = self._analytical_member(member)
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

    def member_point_load(
        self,
        member: StructuralPart | StructuralAnalyticalMemberSpec,
        *,
        id: str,
        label: str,
        case: LoadCategory,
        distance_m: float,
        force: Sequence[float] | dict[str, float],
        moment: Sequence[float] | dict[str, float] = (0.0, 0.0, 0.0),
        case_id: str | None = None,
        case_label: str | None = None,
        provenance: str,
    ) -> None:
        """Apply an authored global point force/moment to a CAD-linked member axis."""

        if case not in {"dead", "live", "wind", "imperfection"}:
            raise StructuralAuthoringError(f"unsupported load case {case!r}")
        analytical_member = self._analytical_member(member)
        load_id = _required_text("member point load ID", id)
        if any(
            item["id"] == load_id
            for item in (*self._member_loads, *self._member_distributed_loads)
        ):
            raise StructuralAuthoringError(
                f"member load ID {load_id!r} is already registered"
            )
        member_length = _member_length(analytical_member)
        distance = float(distance_m)
        if not 0 <= distance <= member_length:
            raise StructuralAuthoringError(
                f"member point load {load_id!r} must lie within the "
                f"{member_length:g} m member"
            )
        force_vector = _vector3(force)
        moment_vector = _vector3(moment)
        if all(
            force_vector[axis] == 0 and moment_vector[axis] == 0
            for axis in ("x", "y", "z")
        ):
            raise StructuralAuthoringError(
                f"member point load {load_id!r} has zero force and moment"
            )
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
        self._load_case_categories[resolved_case_id] = case
        self._load_case_labels[resolved_case_id] = resolved_case_label
        self._member_loads.append(
            {
                "id": load_id,
                "label": _required_text("member point load label", label),
                "member_id": analytical_member["id"],
                "case_id": resolved_case_id,
                "distance_m": distance,
                "force": force_vector,
                "moment": moment_vector,
                "source_load_id": None,
                "provenance": _required_text(
                    "member point load provenance", provenance
                ),
            }
        )

    def member_distributed_load(
        self,
        member: StructuralPart | StructuralAnalyticalMemberSpec,
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
        analytical_member = self._analytical_member(member)
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
        member: StructuralPart | StructuralAnalyticalMemberSpec,
        *,
        id: str,
        label: str,
        direction: Sequence[float] | dict[str, float] = (0.0, 0.0, -1.0),
        gravity_m_s2: float = 9.80665,
        provenance: str = "Section mass per metre multiplied by standard gravity.",
    ) -> None:
        """Apply catalogue-derived member self-weight as a global line load."""

        analytical_member = self._analytical_member(member)
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
        member: StructuralPart | StructuralAnalyticalMemberSpec,
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
        analytical_member = self._analytical_member(member)
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
            "wind_action_bases": [dict(basis) for basis in self._wind_action_bases],
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
                "stability": (
                    dict(self._stability) if self._stability is not None else None
                ),
                "cross_section_verification": (
                    dict(self._cross_section_verification)
                    if self._cross_section_verification is not None
                    else None
                ),
                "member_stability_verification": (
                    {
                        **self._member_stability_verification,
                        "segments": [
                            dict(segment)
                            for segment in self._member_stability_verification[
                                "segments"
                            ]
                        ],
                        "restraint_candidates": [
                            {
                                **candidate,
                                "member_position": dict(candidate["member_position"]),
                                "brace_position": dict(candidate["brace_position"]),
                                "connector_component_ids": list(
                                    candidate["connector_component_ids"]
                                ),
                            }
                            for candidate in self._member_stability_verification[
                                "restraint_candidates"
                            ]
                        ],
                    }
                    if self._member_stability_verification is not None
                    else None
                ),
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
        member: StructuralPart | StructuralAnalyticalMemberSpec,
    ) -> dict[str, Any]:
        if isinstance(member, StructuralAnalyticalMemberSpec):
            registered_handle = self._analytical_member_handles.get(member.id)
            if registered_handle is not member:
                raise StructuralAuthoringError(
                    f"analytical member handle {member.id!r} is not registered "
                    "with this model"
                )
            return next(
                item for item in self._analytical_members if item["id"] == member.id
            )

        registered = self._require_registered(member)
        matches = [
            item
            for item in self._analytical_members
            if item["component_id"] == registered.component_id
        ]
        if not matches:
            raise StructuralAuthoringError(
                f"component {registered.component_id!r} has no analytical axis"
            )
        if len(matches) > 1:
            raise StructuralAuthoringError(
                f"component {registered.component_id!r} has multiple analytical axes; "
                "pass the StructuralAnalyticalMemberSpec returned by member_axis"
            )
        return matches[0]

    def _member_component(
        self,
        member: StructuralPart | StructuralAnalyticalMemberSpec,
    ) -> StructuralPart:
        if isinstance(member, StructuralAnalyticalMemberSpec):
            analytical_member = self._analytical_member(member)
            return self._parts_by_id[analytical_member["component_id"]]
        return self._require_registered(member)

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

    def _component_record(self, component_id: str) -> dict[str, Any]:
        component = next(
            (item for item in self._components if item["id"] == component_id),
            None,
        )
        if component is None:
            raise StructuralAuthoringError(
                f"component {component_id!r} is not registered with this model"
            )
        return component

    def _component_part_number(self, component_id: str) -> str | None:
        part_number = self._component_record(component_id).get("part_number")
        return str(part_number) if part_number is not None else None

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
        if self._stability is not None:
            combination_ids = {
                combination["id"] for combination in self._load_combinations
            }
            direction_cases = self._stability["direction_cases"] or [
                {
                    "stability_combination_id": self._stability[
                        "stability_combination_id"
                    ],
                    "imperfection_case_id": self._stability["imperfection_case_id"],
                    "nhf_combination_id": None,
                }
            ]
            for direction in direction_cases:
                if direction["stability_combination_id"] not in combination_ids:
                    raise StructuralAuthoringError(
                        "stability direction must reference an authored stability "
                        "load combination"
                    )
                if (
                    direction["nhf_combination_id"] is not None
                    and direction["nhf_combination_id"] not in combination_ids
                ):
                    raise StructuralAuthoringError(
                        "stability direction must reference an authored NHF "
                        "load combination"
                    )
                imperfection_case_id = direction["imperfection_case_id"]
                if (
                    self._load_case_categories.get(imperfection_case_id)
                    != "imperfection"
                ):
                    raise StructuralAuthoringError(
                        "stability direction imperfection_case_id must reference "
                        "an authored imperfection load"
                    )
            member_ids = {member["id"] for member in self._analytical_members}
            missing_stability_members = sorted(
                (
                    set(self._stability["eaves_member_ids"])
                    | set(self._stability["rafter_member_ids"])
                )
                - member_ids
            )
            if missing_stability_members:
                raise StructuralAuthoringError(
                    "stability definition references missing analytical members "
                    f"{missing_stability_members}"
                )
        if self._cross_section_verification is not None:
            combinations_by_id = {
                combination["id"]: combination
                for combination in self._load_combinations
            }
            for combination_id in self._cross_section_verification["combination_ids"]:
                verification_combination = combinations_by_id.get(combination_id)
                if verification_combination is None:
                    raise StructuralAuthoringError(
                        "cross-section verification references missing load "
                        f"combination {combination_id!r}"
                    )
                if verification_combination["limit_state"] != "ultimate":
                    raise StructuralAuthoringError(
                        "cross-section verification combinations must use the "
                        f"ultimate limit state; {combination_id!r} does not"
                    )
        if self._member_stability_verification is not None:
            combinations_by_id = {
                combination["id"]: combination
                for combination in self._load_combinations
            }
            for combination_id in self._member_stability_verification[
                "combination_ids"
            ]:
                verification_combination = combinations_by_id.get(combination_id)
                if verification_combination is None:
                    raise StructuralAuthoringError(
                        "member-stability verification references missing load "
                        f"combination {combination_id!r}"
                    )
                if verification_combination["limit_state"] != "ultimate":
                    raise StructuralAuthoringError(
                        "member-stability verification combinations must use the "
                        f"ultimate limit state; {combination_id!r} does not"
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


def _vector_tuple(value: Sequence[float] | Mapping[str, float]) -> tuple[float, ...]:
    if isinstance(value, Mapping):
        return tuple(float(value[axis]) for axis in ("x", "y", "z"))
    return tuple(float(item) for item in value)


def _vector_dict(value: Sequence[float]) -> dict[str, float]:
    return {axis: float(value[index]) for index, axis in enumerate(("x", "y", "z"))}


def _closest_points_on_segments(
    first_start: Sequence[float],
    first_end: Sequence[float],
    second_start: Sequence[float],
    second_end: Sequence[float],
) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
    """Return closest normalized parameters and points on two 3D segments."""

    epsilon = 1e-12
    first_delta = tuple(
        float(first_end[index]) - float(first_start[index]) for index in range(3)
    )
    second_delta = tuple(
        float(second_end[index]) - float(second_start[index]) for index in range(3)
    )
    between_starts = tuple(
        float(first_start[index]) - float(second_start[index]) for index in range(3)
    )
    first_length_sq = sum(value * value for value in first_delta)
    second_length_sq = sum(value * value for value in second_delta)
    if first_length_sq <= epsilon or second_length_sq <= epsilon:
        raise StructuralAuthoringError(
            "member-restraint axes must both have positive length"
        )
    first_second = sum(first_delta[index] * second_delta[index] for index in range(3))
    first_offset = sum(first_delta[index] * between_starts[index] for index in range(3))
    second_offset = sum(
        second_delta[index] * between_starts[index] for index in range(3)
    )
    denominator = first_length_sq * second_length_sq - first_second**2
    if denominator > epsilon:
        first_fraction = max(
            0.0,
            min(
                1.0,
                (first_second * second_offset - first_offset * second_length_sq)
                / denominator,
            ),
        )
    else:
        first_fraction = 0.0
    second_fraction = (first_second * first_fraction + second_offset) / second_length_sq
    if second_fraction < 0.0:
        second_fraction = 0.0
        first_fraction = max(0.0, min(1.0, -first_offset / first_length_sq))
    elif second_fraction > 1.0:
        second_fraction = 1.0
        first_fraction = max(
            0.0,
            min(1.0, (first_second - first_offset) / first_length_sq),
        )
    first_point = tuple(
        float(first_start[index]) + first_fraction * first_delta[index]
        for index in range(3)
    )
    second_point = tuple(
        float(second_start[index]) + second_fraction * second_delta[index]
        for index in range(3)
    )
    return first_fraction, second_fraction, first_point, second_point


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
