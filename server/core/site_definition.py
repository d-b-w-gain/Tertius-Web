from __future__ import annotations

import ast
from copy import deepcopy
from hashlib import sha256
import json
from pprint import pformat
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.structural.site_wind import (
    REGION_SOURCE,
    compute_site_wind,
)


SITE_DEFINITION_FILENAME = "tertius_site.py"


class SiteDefinitionError(ValueError):
    """Raised when a project site definition is unsafe or invalid."""


class SiteContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteActionStandards(SiteContract):
    combinations: str = "AS/NZS 1170.0 — project edition to confirm"
    permanent_and_imposed: str = "AS/NZS 1170.1 — project edition to confirm"
    wind: str = "AS/NZS 1170.2:2021"
    confirmed: bool = False


class SiteProjectBasis(SiteContract):
    building_use: str = "Private shed"
    building_classification: str = "Class 10a — confirm for project"
    importance_level: Literal["1", "2", "3", "4"] = "2"
    design_life_years: int = Field(default=50, gt=0)
    jurisdiction: str = "Australia / New South Wales"
    standards: SiteActionStandards = Field(default_factory=SiteActionStandards)


class SiteLocation(SiteContract):
    address: str = ""
    latitude: float = Field(default=-34.4125046, ge=-90, le=90)
    longitude: float = Field(default=150.8885637, ge=-180, le=180)


class SiteWindDefinition(SiteContract):
    basis_id: str = "project-site-wind"
    region: str = "A2"
    region_area: str = "NSW"
    region_source: str = REGION_SOURCE
    region_approximate: bool = True
    region_status: Literal["suggested", "verified"] = "suggested"
    table_status: Literal["starter", "verified"] = "starter"
    terrain_category: Literal["1", "2", "2.5", "3", "4"] = "3"
    annual_probability_uls: str = ""
    reference_height_m: float = Field(default=3.0, gt=0)
    direction_multiplier: float = Field(default=1.0, gt=0)
    shielding_multiplier: float = Field(default=1.0, gt=0)
    topographic_multiplier: float = Field(default=1.0, gt=0)
    climate_change_multiplier: float | None = Field(default=None, gt=0)


class SiteDefinition(SiteContract):
    schema_version: Literal["1.0"] = "1.0"
    project_basis: SiteProjectBasis = Field(default_factory=SiteProjectBasis)
    location: SiteLocation = Field(default_factory=SiteLocation)
    wind: SiteWindDefinition = Field(default_factory=SiteWindDefinition)


def default_site_definition() -> SiteDefinition:
    return SiteDefinition()


def parse_site_definition(source: str) -> SiteDefinition:
    """Parse a literal ``site_dict`` without executing project code."""

    try:
        tree = ast.parse(source, filename=SITE_DEFINITION_FILENAME)
    except SyntaxError as exc:
        raise SiteDefinitionError(f"{SITE_DEFINITION_FILENAME} is not valid Python: {exc.msg}") from exc

    assignments: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "site_dict"
            for target in node.targets
        ):
            assignments.append(node.value)
            continue
        raise SiteDefinitionError(
            f"{SITE_DEFINITION_FILENAME} may only contain a module docstring and "
            "one literal site_dict assignment"
        )

    if len(assignments) != 1:
        raise SiteDefinitionError(
            f"{SITE_DEFINITION_FILENAME} must contain exactly one site_dict assignment"
        )
    try:
        value = ast.literal_eval(assignments[0])
    except (TypeError, ValueError) as exc:
        raise SiteDefinitionError(
            "site_dict must contain literals only; function calls and computed values "
            "belong in the workbench calculation engine"
        ) from exc
    if not isinstance(value, dict):
        raise SiteDefinitionError("site_dict must be a dictionary")
    try:
        return SiteDefinition.model_validate(value)
    except ValueError as exc:
        raise SiteDefinitionError(str(exc)) from exc


def validate_design_site_usage(design_source: str) -> None:
    """Ensure site inputs cannot silently become Build123D geometry inputs."""

    try:
        tree = ast.parse(design_source, filename="design.py")
    except SyntaxError as exc:
        raise SiteDefinitionError(f"design.py is not valid Python: {exc.msg}") from exc
    parents: dict[ast.AST, ast.AST] = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "tertius_site" for alias in node.names
        ):
            raise SiteDefinitionError(
                "design.py must use 'from tertius_site import site_dict'; "
                "module-style access could make site inputs affect CAD geometry"
            )
        if isinstance(node, ast.ImportFrom) and node.module == "tertius_site":
            if len(node.names) != 1 or node.names[0].name != "site_dict" or node.names[0].asname:
                raise SiteDefinitionError(
                    "design.py may import only the unaliased site_dict from tertius_site"
                )
        if not (
            isinstance(node, ast.Name)
            and node.id == "site_dict"
            and isinstance(node.ctx, ast.Load)
        ):
            continue
        parent = parents.get(node)
        allowed = (
            isinstance(parent, ast.Call)
            and node in parent.args
            and isinstance(parent.func, ast.Attribute)
            and parent.func.attr == "site_wind_basis"
        )
        if not allowed:
            raise SiteDefinitionError(
                "site_dict may only be passed to "
                "StructuralModel.site_wind_basis(site_dict); site inputs must not "
                "alter Build123D geometry"
            )


def render_site_definition(site: SiteDefinition) -> str:
    """Render a deterministic, reviewable project-owned Python module."""

    payload = site.model_dump(mode="json")
    body = pformat(payload, indent=4, sort_dicts=False, width=88)
    return (
        '"""Project site and design-basis inputs owned by the Tertius Site Workbench.\n\n'
        "Derived wind speeds, pressures, and member loads are intentionally not stored here.\n"
        '"""\n\n'
        f"site_dict = {body}\n"
    )


def site_definition_revision(site: SiteDefinition) -> str:
    payload = json.dumps(
        site.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:12]


def calculate_site_definition(site: SiteDefinition) -> dict[str, Any]:
    calculation = compute_site_wind(
        region=site.wind.region,
        terrain_category=site.wind.terrain_category,
        importance_level=site.project_basis.importance_level,
        annual_probability_uls=site.wind.annual_probability_uls,
        reference_height_m=site.wind.reference_height_m,
        direction_multiplier=site.wind.direction_multiplier,
        shielding_multiplier=site.wind.shielding_multiplier,
        topographic_multiplier=site.wind.topographic_multiplier,
        climate_change_multiplier=site.wind.climate_change_multiplier,
    )
    return {
        "revision": site_definition_revision(site),
        "site_ready": bool(
            site.location.address.strip()
            and site.wind.region_status == "verified"
            and site.wind.table_status == "verified"
            and site.project_basis.standards.confirmed
        ),
        "site_address": site.location.address,
        "latitude": site.location.latitude,
        "longitude": site.location.longitude,
        "region_area": site.wind.region_area,
        "region_source": site.wind.region_source,
        "region_approximate": site.wind.region_approximate,
        "region_status": site.wind.region_status,
        "table_status": site.wind.table_status,
        **calculation,
    }


def site_wind_basis(site: SiteDefinition, *, basis_id: str | None = None) -> dict[str, Any]:
    calculation = calculate_site_definition(site)
    return {
        "id": basis_id or site.wind.basis_id,
        "site_address": site.location.address,
        "latitude": site.location.latitude,
        "longitude": site.location.longitude,
        "region": calculation["region"],
        "region_area": site.wind.region_area,
        "region_source": site.wind.region_source,
        "region_approximate": site.wind.region_approximate,
        "region_status": site.wind.region_status,
        "standard": calculation["standard"],
        "table_version": calculation["table_version"],
        "table_status": site.wind.table_status,
        "importance_level": calculation["importance_level"],
        "annual_recurrence_interval_years": calculation[
            "annual_recurrence_interval_years"
        ],
        "terrain_category": calculation["terrain_category"],
        "reference_height_m": calculation["reference_height_m"],
        "regional_wind_speed_m_s": calculation["regional_wind_speed_m_s"],
        "climate_change_multiplier": calculation["climate_change_multiplier"],
        "direction_multiplier": calculation["direction_multiplier"],
        "terrain_height_multiplier": calculation["terrain_height_multiplier"],
        "shielding_multiplier": calculation["shielding_multiplier"],
        "topographic_multiplier": calculation["topographic_multiplier"],
        "site_wind_speed_m_s": calculation["site_wind_speed_m_s"],
        "q_z_kPa": calculation["q_z_kPa"],
        "verifier_hash": calculation["verifier_hash"],
        "provenance": (
            f"{SITE_DEFINITION_FILENAME} revision {calculation['revision']}; "
            "derived by the Tertius site-wind calculation engine"
        ),
    }


def apply_site_definition(
    declaration: dict[str, Any],
    site: SiteDefinition,
) -> dict[str, Any]:
    """Overlay site-derived actions onto a compiled geometry declaration."""

    value = deepcopy(declaration)
    existing_bases = value.get("wind_action_bases") or []
    target_basis_id = site.wind.basis_id
    if (
        len(existing_bases) == 1
        and target_basis_id not in {
            str(existing.get("id")) for existing in existing_bases
        }
    ):
        # Compatibility for projects compiled before the standard basis id was
        # introduced. The next CAD compile will adopt project-site-wind.
        target_basis_id = str(existing_bases[0].get("id") or target_basis_id)
    value["wind_action_bases"] = [
        site_wind_basis(site, basis_id=target_basis_id)
    ]

    q_z_kPa = float(value["wind_action_bases"][0]["q_z_kPa"])
    for load in value.get("loads") or []:
        if load.get("case") != "wind":
            continue
        linked_basis = load.get("wind_basis_id")
        if linked_basis is None or len(existing_bases) == 1:
            load["wind_basis_id"] = target_basis_id
        if load.get("wind_basis_id") == target_basis_id:
            coefficient = load.get("net_pressure_coefficient")
            if coefficient is not None:
                load["pressure_kPa"] = abs(float(coefficient)) * q_z_kPa
                provenance = str(load.get("provenance") or "")
                load["provenance"] = (
                    provenance.split("; site basis ")[0]
                    + f"; site basis {SITE_DEFINITION_FILENAME} "
                    f"revision {site_definition_revision(site)}"
                )

    design_basis = value.get("design_basis")
    standards = site.project_basis.standards
    if isinstance(design_basis, dict):
        design_basis["jurisdiction"] = site.project_basis.jurisdiction
        design_standards = dict(design_basis.get("standards") or {})
        def action_reference(reference: str) -> str:
            if standards.confirmed or "confirm" in reference.lower():
                return reference
            return reference + " — project edition to confirm"

        design_standards.update(
            {
                "action_combinations": action_reference(standards.combinations),
                "permanent_and_imposed_actions": action_reference(
                    standards.permanent_and_imposed
                ),
                "wind_actions": action_reference(standards.wind),
            }
        )
        design_basis["standards"] = design_standards
    return value
