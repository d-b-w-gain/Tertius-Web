from __future__ import annotations

import ast
from copy import deepcopy
from hashlib import sha256
import json
from pprint import pformat
from typing import Any, Literal, Mapping, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.structural.site_wind import (
    REGION_SOURCE,
    TABLE_VERSION,
    SiteWindError,
    compute_site_wind,
)
from core.structural.wind_standard_tables import site_table_evidence


SITE_DEFINITION_FILENAME = "tertius_site.py"
BuildingFace = Literal["front", "right", "back", "left"]
BUILDING_FACES: tuple[BuildingFace, ...] = ("front", "right", "back", "left")


class SiteDefinitionError(ValueError):
    """Raised when a project site definition is unsafe or invalid."""


class SiteContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteActionStandards(SiteContract):
    combinations: str = "AS/NZS 1170.0:2002"
    permanent_and_imposed: str = "AS/NZS 1170.1:2002"
    wind: str = "AS/NZS 1170.2:2021"
    confirmed: bool = False

    @field_validator("combinations", "permanent_and_imposed", "wind", mode="before")
    @classmethod
    def remove_legacy_warning_from_reference(cls, value: Any) -> Any:
        """Migrate the old UI warning that was incorrectly stored in the value."""

        if not isinstance(value, str):
            return value
        clean = value.split(" — project edition to confirm", 1)[0].strip()
        legacy_defaults = {
            "AS/NZS 1170.0": "AS/NZS 1170.0:2002",
            "AS/NZS 1170.1": "AS/NZS 1170.1:2002",
        }
        return legacy_defaults.get(clean, clean)


class SiteProjectBasis(SiteContract):
    building_use: str = "Private shed"
    building_classification: str = "10a"
    importance_level: Literal["1", "2", "3", "4"] = "2"
    design_life_years: int = Field(default=50, gt=0)
    jurisdiction: str = "Australia / New South Wales"
    standards: SiteActionStandards = Field(default_factory=SiteActionStandards)

    @field_validator("building_classification", mode="before")
    @classmethod
    def normalize_building_classification(cls, value: Any) -> Any:
        """Accept existing human-readable values and store the stable NCC code."""

        if not isinstance(value, str):
            return value
        clean = value.strip()
        if clean.lower().startswith("class "):
            clean = clean[6:]
        return clean.split(" ", 1)[0].strip()


class SiteLocation(SiteContract):
    address: str = ""
    latitude: float = Field(default=-34.4125046, ge=-90, le=90)
    longitude: float = Field(default=150.8885637, ge=-180, le=180)


class SiteStructurePlacement(SiteContract):
    """Plan placement used to rotate cardinal wind into building directions.

    ``front_bearing_degrees`` is clockwise from true north and describes the
    outward normal of the nominated front face. The footprint is a site-plan
    aid only; it must not alter authored Build123D geometry.
    """

    footprint_length_m: float = Field(default=12.0, gt=0)
    footprint_width_m: float = Field(default=6.0, gt=0)
    front_bearing_degrees: float = Field(default=0.0, ge=0, lt=360)
    front_definition: Literal["long_wall_normal", "gable_ridge_normal", "manual"] = (
        "long_wall_normal"
    )
    orientation_status: Literal["suggested", "verified"] = "suggested"
    # The address point and the structure centre are different facts.  These
    # optional coordinates let a site plan place the candidate within its
    # parcel without moving the G-NAF/GIS evidence lookup point.  Existing site
    # files fall back to SiteLocation until the structure is positioned.
    placement_latitude: float | None = Field(default=None, ge=-90, le=90)
    placement_longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def require_complete_placement_coordinates(self):
        if (self.placement_latitude is None) != (self.placement_longitude is None):
            raise ValueError(
                "structure placement latitude and longitude must be supplied together"
            )
        return self


class SiteCardinalDirectionMultipliers(SiteContract):
    n: float = Field(default=1.0, gt=0)
    ne: float = Field(default=1.0, gt=0)
    e: float = Field(default=1.0, gt=0)
    se: float = Field(default=1.0, gt=0)
    s: float = Field(default=1.0, gt=0)
    sw: float = Field(default=1.0, gt=0)
    w: float = Field(default=1.0, gt=0)
    nw: float = Field(default=1.0, gt=0)


class SiteWindActionEnvelope(SiteContract):
    enclosure: Literal["enclosed", "open_sided"] = "enclosed"
    openings_operating_state: Literal["normally_closed", "normally_open"] = (
        "normally_closed"
    )
    opening_capacity_status: Literal["unverified", "verified"] = "unverified"
    coefficient_selection_policy: Literal[
        "worst_available_credible", "verified_only"
    ] = "worst_available_credible"


class SiteWindMultiplierEvidenceReference(SiteContract):
    evidence_id: str = Field(pattern=r"^windv1-[0-9a-f]{32}$")
    provider: str
    dataset: str
    dataset_version: str
    source_uri: str
    site_latitude: float
    site_longitude: float
    terrain_reference_height_m: float = Field(gt=0)
    method_status: Literal["indicative_hazard_evidence", "automated_local_analysis"] = (
        "indicative_hazard_evidence"
    )
    terrain_evidence_id: str | None = None
    placement_latitude: float | None = Field(default=None, ge=-90, le=90)
    placement_longitude: float | None = Field(default=None, ge=-180, le=180)
    footprint_length_m: float | None = Field(default=None, gt=0)
    footprint_width_m: float | None = Field(default=None, gt=0)
    front_bearing_degrees: float | None = Field(default=None, ge=0, lt=360)
    adopted_components: list[Literal["M_z_cat", "M_s", "M_t"]] = Field(
        default_factory=list
    )
    review_status: Literal["suggested", "verified"] = "suggested"
    review_reason: str = ""

    @model_validator(mode="after")
    def require_review_reason(self):
        if not self.adopted_components:
            raise ValueError("multiplier evidence must identify adopted components")
        if self.review_status == "verified" and not self.review_reason.strip():
            raise ValueError("verified multiplier evidence requires a review reason")
        return self


class SiteWindDefinition(SiteContract):
    basis_id: str = "project-site-wind"
    region: str = "A2"
    region_area: str = "NSW"
    region_source: str = REGION_SOURCE
    region_approximate: bool = True
    region_status: Literal["suggested", "verified"] = "suggested"
    table_status: Literal["starter", "verified"] = "starter"
    # This is an authored input, not merely a derived label.  A future table
    # revision must therefore be an explicit site-file migration instead of
    # silently changing a saved project's results.
    table_dataset_version: str = TABLE_VERSION
    terrain_category: Literal["1", "2", "2.5", "3", "4"] = "3"
    annual_probability_uls: str = ""
    reference_height_m: float = Field(default=3.0, gt=0)
    # Retained as the conservative/backward-compatible fallback for existing
    # tertius_site.py files. Once the eight cardinal values are authored, the
    # calculation envelope uses their maximum and reports face-specific cases.
    direction_multiplier: float = Field(default=1.0, gt=0)
    cardinal_direction_multipliers: SiteCardinalDirectionMultipliers | None = None
    cardinal_terrain_height_multipliers: SiteCardinalDirectionMultipliers | None = None
    shielding_multiplier: float = Field(default=1.0, gt=0)
    cardinal_shielding_multipliers: SiteCardinalDirectionMultipliers | None = None
    topographic_multiplier: float = Field(default=1.0, gt=0)
    cardinal_topographic_multipliers: SiteCardinalDirectionMultipliers | None = None
    climate_change_multiplier: float | None = Field(default=None, gt=0)
    multiplier_evidence: SiteWindMultiplierEvidenceReference | None = None
    action_envelope: SiteWindActionEnvelope = Field(
        default_factory=SiteWindActionEnvelope
    )

    @model_validator(mode="after")
    def populate_standard_table_starters(self):
        """Seed region-specific Md and Mc without claiming table verification."""

        if (
            self.cardinal_direction_multipliers is not None
            and self.climate_change_multiplier is not None
        ):
            return self

        evidence = site_table_evidence(self.region)
        if self.cardinal_direction_multipliers is None:
            self.cardinal_direction_multipliers = (
                SiteCardinalDirectionMultipliers.model_validate(
                    evidence["direction_multipliers"]
                )
            )
        if self.climate_change_multiplier is None:
            self.climate_change_multiplier = float(
                evidence["climate_change_multiplier"]
            )
        # Automatic starter values still require review against the licensed
        # project standard and its applicable amendments.
        self.table_status = "starter"
        return self


class SiteTerrainEvidenceReference(SiteContract):
    evidence_id: str = Field(pattern=r"^gisv1-[0-9a-f]{32}$")
    site_latitude: float = Field(ge=-90, le=90)
    site_longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=2000, ge=100, le=10000)


class SiteDefinition(SiteContract):
    schema_version: Literal["1.0"] = "1.0"
    project_basis: SiteProjectBasis = Field(default_factory=SiteProjectBasis)
    location: SiteLocation = Field(default_factory=SiteLocation)
    structure: SiteStructurePlacement = Field(default_factory=SiteStructurePlacement)
    wind: SiteWindDefinition = Field(default_factory=SiteWindDefinition)
    terrain_evidence: SiteTerrainEvidenceReference | None = None


def default_site_definition() -> SiteDefinition:
    return SiteDefinition()


def parse_site_definition(source: str) -> SiteDefinition:
    """Parse a literal ``site_dict`` without executing project code."""

    try:
        tree = ast.parse(source, filename=SITE_DEFINITION_FILENAME)
    except SyntaxError as exc:
        raise SiteDefinitionError(
            f"{SITE_DEFINITION_FILENAME} is not valid Python: {exc.msg}"
        ) from exc

    assignments: list[ast.AST] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
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
            if (
                len(node.names) != 1
                or node.names[0].name != "site_dict"
                or node.names[0].asname
            ):
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
    if site.wind.table_dataset_version != TABLE_VERSION:
        raise SiteWindError(
            "saved wind table dataset "
            f"{site.wind.table_dataset_version!r} does not match the active "
            f"calculation engine {TABLE_VERSION!r}; migrate the project basis explicitly"
        )
    cardinal_bearings = (
        ("N", 0.0, "n"),
        ("NE", 45.0, "ne"),
        ("E", 90.0, "e"),
        ("SE", 135.0, "se"),
        ("S", 180.0, "s"),
        ("SW", 225.0, "sw"),
        ("W", 270.0, "w"),
        ("NW", 315.0, "nw"),
    )
    multiplier_evidence_stale = bool(
        site.wind.multiplier_evidence is not None
        and (
            abs(site.wind.multiplier_evidence.site_latitude - site.location.latitude)
            > 1e-6
            or abs(
                site.wind.multiplier_evidence.site_longitude - site.location.longitude
            )
            > 1e-6
            or (
                site.wind.multiplier_evidence.terrain_evidence_id is not None
                and (
                    site.terrain_evidence is None
                    or site.wind.multiplier_evidence.terrain_evidence_id
                    != site.terrain_evidence.evidence_id
                )
            )
            or (
                site.wind.multiplier_evidence.placement_latitude is not None
                and abs(
                    site.wind.multiplier_evidence.placement_latitude
                    - (
                        site.structure.placement_latitude
                        if site.structure.placement_latitude is not None
                        else site.location.latitude
                    )
                )
                > 1e-6
            )
            or (
                site.wind.multiplier_evidence.placement_longitude is not None
                and abs(
                    site.wind.multiplier_evidence.placement_longitude
                    - (
                        site.structure.placement_longitude
                        if site.structure.placement_longitude is not None
                        else site.location.longitude
                    )
                )
                > 1e-6
            )
            or (
                site.wind.multiplier_evidence.method_status
                == "automated_local_analysis"
                and site.wind.multiplier_evidence.terrain_reference_height_m
                != site.wind.reference_height_m
            )
            or (
                site.wind.multiplier_evidence.footprint_length_m is not None
                and site.wind.multiplier_evidence.footprint_length_m
                != site.structure.footprint_length_m
            )
            or (
                site.wind.multiplier_evidence.footprint_width_m is not None
                and site.wind.multiplier_evidence.footprint_width_m
                != site.structure.footprint_width_m
            )
            or (
                site.wind.multiplier_evidence.front_bearing_degrees is not None
                and site.wind.multiplier_evidence.front_bearing_degrees
                != site.structure.front_bearing_degrees
            )
        )
    )
    authored_multipliers = site.wind.cardinal_direction_multipliers

    def directional_values(
        authored: SiteCardinalDirectionMultipliers | None,
        fallback: float | None,
    ) -> dict[str, float | None]:
        if authored is not None:
            return authored.model_dump(mode="json")
        return {key: fallback for _, _, key in cardinal_bearings}

    direction_multipliers = cast(
        dict[str, float],
        directional_values(authored_multipliers, site.wind.direction_multiplier),
    )
    terrain_height_multipliers = directional_values(
        site.wind.cardinal_terrain_height_multipliers,
        None,
    )
    shielding_multipliers = cast(
        dict[str, float],
        directional_values(
            site.wind.cardinal_shielding_multipliers,
            site.wind.shielding_multiplier,
        ),
    )
    topographic_multipliers = cast(
        dict[str, float],
        directional_values(
            site.wind.cardinal_topographic_multipliers,
            site.wind.topographic_multiplier,
        ),
    )
    directional_modes = {
        "direction": (
            "cardinal" if authored_multipliers is not None else "single_conservative"
        ),
        "terrain_height": (
            "cardinal"
            if site.wind.cardinal_terrain_height_multipliers is not None
            else "terrain_category_height_table"
        ),
        "shielding": (
            "cardinal"
            if site.wind.cardinal_shielding_multipliers is not None
            else "single_conservative"
        ),
        "topographic": (
            "cardinal"
            if site.wind.cardinal_topographic_multipliers is not None
            else "single_conservative"
        ),
    }
    directional_mode = directional_modes["direction"]

    cardinal_wind_speeds: list[dict[str, Any]] = []
    for direction, bearing, key in cardinal_bearings:
        sector = compute_site_wind(
            region=site.wind.region,
            terrain_category=site.wind.terrain_category,
            importance_level=site.project_basis.importance_level,
            annual_probability_uls=site.wind.annual_probability_uls,
            reference_height_m=site.wind.reference_height_m,
            direction_multiplier=direction_multipliers[key],
            terrain_height_multiplier=terrain_height_multipliers[key],
            shielding_multiplier=shielding_multipliers[key],
            topographic_multiplier=topographic_multipliers[key],
            climate_change_multiplier=site.wind.climate_change_multiplier,
        )
        cardinal_wind_speeds.append(
            {
                "direction": direction,
                "bearing_degrees": bearing,
                "direction_multiplier": sector["direction_multiplier"],
                "terrain_height_multiplier": sector["terrain_height_multiplier"],
                "shielding_multiplier": sector["shielding_multiplier"],
                "topographic_multiplier": sector["topographic_multiplier"],
                "site_wind_speed_m_s": sector["site_wind_speed_m_s"],
                "q_z_kPa": sector["q_z_kPa"],
            }
        )

    def angular_distance(first: float, second: float) -> float:
        return abs((first - second + 180.0) % 360.0 - 180.0)

    building_face_wind_speeds: list[dict[str, Any]] = []
    for face, offset in (
        ("front", 0.0),
        ("right", 90.0),
        ("back", 180.0),
        ("left", 270.0),
    ):
        bearing = (site.structure.front_bearing_degrees + offset) % 360.0
        contributing = [
            sector
            for sector in cardinal_wind_speeds
            if angular_distance(float(sector["bearing_degrees"]), bearing) <= 45.0
        ]
        governing = max(contributing, key=lambda value: value["site_wind_speed_m_s"])
        building_face_wind_speeds.append(
            {
                "face": face,
                "bearing_degrees": round(bearing, 6),
                "site_wind_speed_m_s": governing["site_wind_speed_m_s"],
                "q_z_kPa": governing["q_z_kPa"],
                "governing_cardinal_direction": governing["direction"],
                "contributing_cardinal_directions": [
                    sector["direction"] for sector in contributing
                ],
            }
        )

    governing_sector = max(
        cardinal_wind_speeds,
        key=lambda value: value["site_wind_speed_m_s"],
    )
    calculation = compute_site_wind(
        region=site.wind.region,
        terrain_category=site.wind.terrain_category,
        importance_level=site.project_basis.importance_level,
        annual_probability_uls=site.wind.annual_probability_uls,
        reference_height_m=site.wind.reference_height_m,
        direction_multiplier=governing_sector["direction_multiplier"],
        terrain_height_multiplier=governing_sector["terrain_height_multiplier"],
        shielding_multiplier=governing_sector["shielding_multiplier"],
        topographic_multiplier=governing_sector["topographic_multiplier"],
        climate_change_multiplier=site.wind.climate_change_multiplier,
    )
    working_basis_ready = bool(
        site.location.address.strip() and not multiplier_evidence_stale
    )
    certification_ready = bool(
        working_basis_ready
        and site.structure.orientation_status == "verified"
        and authored_multipliers is not None
        and site.wind.region_status == "verified"
        and site.wind.table_status == "verified"
        and site.project_basis.standards.confirmed
        and (
            site.wind.multiplier_evidence is None
            or site.wind.multiplier_evidence.review_status == "verified"
        )
    )
    return {
        "revision": site_definition_revision(site),
        # `site_ready` is retained for existing clients and means ready for
        # certification. Working calculations are available independently from
        # the human verification workflow.
        "site_ready": certification_ready,
        "working_basis_ready": working_basis_ready,
        "certification_ready": certification_ready,
        "site_address": site.location.address,
        "latitude": site.location.latitude,
        "longitude": site.location.longitude,
        "structure": site.structure.model_dump(mode="json"),
        "directional_mode": directional_mode,
        "directional_multiplier_modes": directional_modes,
        "multiplier_evidence_stale": multiplier_evidence_stale,
        "cardinal_wind_speeds": cardinal_wind_speeds,
        "building_face_wind_speeds": building_face_wind_speeds,
        "governing_cardinal_direction": governing_sector["direction"],
        "region_area": site.wind.region_area,
        "region_source": site.wind.region_source,
        "region_approximate": site.wind.region_approximate,
        "region_status": site.wind.region_status,
        "table_status": site.wind.table_status,
        "action_envelope": site.wind.action_envelope.model_dump(mode="json"),
        "standard_table_evidence": site_table_evidence(site.wind.region),
        **calculation,
    }


def site_wind_basis(
    site: SiteDefinition,
    *,
    basis_id: str | None = None,
    face: BuildingFace | None = None,
    annual_probability: str | None = None,
    design_event: Literal["serviceability", "ultimate"] = "ultimate",
) -> dict[str, Any]:
    calculation = calculate_site_definition(site)
    face_record = None
    if face is not None:
        face_record = next(
            value
            for value in calculation["building_face_wind_speeds"]
            if value["face"] == face
        )
        governing_sector = next(
            value
            for value in calculation["cardinal_wind_speeds"]
            if value["direction"] == face_record["governing_cardinal_direction"]
        )
        calculation = {
            **calculation,
            **compute_site_wind(
                region=site.wind.region,
                terrain_category=site.wind.terrain_category,
                importance_level=site.project_basis.importance_level,
                annual_probability_uls=(
                    annual_probability or site.wind.annual_probability_uls
                ),
                reference_height_m=site.wind.reference_height_m,
                direction_multiplier=governing_sector["direction_multiplier"],
                terrain_height_multiplier=governing_sector["terrain_height_multiplier"],
                shielding_multiplier=governing_sector["shielding_multiplier"],
                topographic_multiplier=governing_sector["topographic_multiplier"],
                climate_change_multiplier=site.wind.climate_change_multiplier,
            ),
        }
    elif annual_probability is not None:
        governing_sector = next(
            value
            for value in calculation["cardinal_wind_speeds"]
            if value["direction"] == calculation["governing_cardinal_direction"]
        )
        calculation = {
            **calculation,
            **compute_site_wind(
                region=site.wind.region,
                terrain_category=site.wind.terrain_category,
                importance_level=site.project_basis.importance_level,
                annual_probability_uls=annual_probability,
                reference_height_m=site.wind.reference_height_m,
                direction_multiplier=governing_sector["direction_multiplier"],
                terrain_height_multiplier=governing_sector["terrain_height_multiplier"],
                shielding_multiplier=governing_sector["shielding_multiplier"],
                topographic_multiplier=governing_sector["topographic_multiplier"],
                climate_change_multiplier=site.wind.climate_change_multiplier,
            ),
        }
    action_direction_by_face = {
        "front": "+Y",
        "right": "-X",
        "back": "-Y",
        "left": "+X",
    }
    return {
        "id": basis_id
        or (f"{site.wind.basis_id}-{face}" if face is not None else site.wind.basis_id),
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
        "design_event": design_event,
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
        "enclosure": site.wind.action_envelope.enclosure,
        "openings_operating_state": (
            site.wind.action_envelope.openings_operating_state
        ),
        "opening_capacity_status": (site.wind.action_envelope.opening_capacity_status),
        "coefficient_selection_policy": (
            site.wind.action_envelope.coefficient_selection_policy
        ),
        "building_face": face,
        "face_bearing_degrees": (
            None if face_record is None else face_record["bearing_degrees"]
        ),
        "structural_action_direction": (
            None if face is None else action_direction_by_face[face]
        ),
        "governing_cardinal_direction": (
            calculation["governing_cardinal_direction"]
            if face_record is None
            else face_record["governing_cardinal_direction"]
        ),
        "contributing_cardinal_directions": (
            []
            if face_record is None
            else face_record["contributing_cardinal_directions"]
        ),
        "verifier_hash": calculation["verifier_hash"],
        "provenance": (
            f"{SITE_DEFINITION_FILENAME} revision {calculation['revision']}; "
            "derived by the Tertius site-wind calculation engine"
        ),
    }


def apply_site_definition(
    declaration: dict[str, Any],
    site: SiteDefinition,
    *,
    annual_probability: str | None = None,
    basis_suffix: str = "",
    design_event: Literal["serviceability", "ultimate"] = "ultimate",
) -> dict[str, Any]:
    """Overlay site-derived actions onto a compiled geometry declaration."""

    value = deepcopy(declaration)
    existing_bases = value.get("wind_action_bases") or []
    target_basis_id = f"{site.wind.basis_id}{basis_suffix}"
    directional_case_faces = {
        "wind-plus-x": "left",
        "wind-minus-x": "right",
        "wind-plus-y": "front",
        "wind-minus-y": "back",
        "wind-sls-plus-x": "left",
        "wind-sls-minus-x": "right",
        "wind-sls-plus-y": "front",
        "wind-sls-minus-y": "back",
        "wind-uls-plus-x": "left",
        "wind-uls-minus-x": "right",
        "wind-uls-plus-y": "front",
        "wind-uls-minus-y": "back",
    }

    def load_face(load: Mapping[str, Any]) -> BuildingFace | None:
        case_id = str(load.get("case_id") or "")
        normalized_case_id = (
            case_id.removeprefix("case-") if case_id.startswith("case-") else case_id
        )
        return cast(BuildingFace | None, directional_case_faces.get(normalized_case_id))

    authored_face_ids = {
        str(basis.get("building_face")): str(basis.get("id"))
        for basis in existing_bases
        if basis.get("building_face") in {"front", "right", "back", "left"}
    }
    has_directional_loads = any(
        load.get("case") == "wind" and load_face(load) is not None
        for load in value.get("loads") or []
    )
    directional_overlay = len(authored_face_ids) == 4 or has_directional_loads

    if (
        len(existing_bases) == 1
        and target_basis_id
        not in {str(existing.get("id")) for existing in existing_bases}
        and not directional_overlay
    ):
        # Compatibility for projects compiled before the standard basis id was
        # introduced. The next CAD compile will adopt project-site-wind.
        target_basis_id = str(existing_bases[0].get("id") or target_basis_id)
    if directional_overlay:
        face_basis_ids = {
            face: authored_face_ids.get(face, f"{target_basis_id}-{face}")
            for face in BUILDING_FACES
        }
        value["wind_action_bases"] = [
            site_wind_basis(
                site,
                basis_id=face_basis_ids[face],
                face=face,
                annual_probability=annual_probability,
                design_event=design_event,
            )
            for face in BUILDING_FACES
        ]
        bases_by_id = {basis["id"]: basis for basis in value["wind_action_bases"]}
    else:
        value["wind_action_bases"] = [
            site_wind_basis(
                site,
                basis_id=target_basis_id,
                annual_probability=annual_probability,
                design_event=design_event,
            )
        ]
        face_basis_ids = {}
        bases_by_id = {target_basis_id: value["wind_action_bases"][0]}

    for load in value.get("loads") or []:
        if load.get("case") != "wind":
            continue
        face = load_face(load) if directional_overlay else None
        if face is not None:
            load["wind_basis_id"] = face_basis_ids[face]
        elif directional_overlay:
            load["wind_basis_id"] = max(
                value["wind_action_bases"],
                key=lambda basis: float(basis["q_z_kPa"]),
            )["id"]
        elif not directional_overlay:
            linked_basis = load.get("wind_basis_id")
            if linked_basis is None or len(existing_bases) == 1:
                load["wind_basis_id"] = target_basis_id
        linked_basis = bases_by_id.get(str(load.get("wind_basis_id")))
        if linked_basis is not None:
            q_z_kPa = float(linked_basis["q_z_kPa"])
            coefficient = load.get("net_pressure_coefficient")
            if coefficient is not None:
                load["pressure_kPa"] = abs(float(coefficient)) * q_z_kPa
                if (
                    load.get("coefficient_status") == "assumed"
                    and site.wind.action_envelope.coefficient_selection_policy
                    == "worst_available_credible"
                ):
                    load["coefficient_status"] = "working_conservative"
                provenance = str(load.get("provenance") or "")
                load["provenance"] = (
                    provenance.split("; site basis ")[0]
                    + f"; site basis {SITE_DEFINITION_FILENAME} "
                    f"revision {site_definition_revision(site)}; "
                    f"{design_event} wind event; "
                    "worst available credible case policy "
                    f"({site.wind.action_envelope.enclosure}, "
                    f"{site.wind.action_envelope.openings_operating_state}, "
                    "opening capacity "
                    f"{site.wind.action_envelope.opening_capacity_status})"
                )

    design_basis = value.get("design_basis")
    standards = site.project_basis.standards
    if isinstance(design_basis, dict):
        design_basis.update(
            {
                "framework_id": "AU-NCC-2022",
                "framework_label": (
                    "NCC 2022 Amendment 2 Australian structural verification"
                ),
                "framework_reference": (
                    "NCC 2022 Amendment 2, Volume Two Part H1 and ABCB Housing "
                    "Provisions Part 2.2"
                ),
                "building_classification": (
                    f"Class {site.project_basis.building_classification}"
                ),
                "importance_level": site.project_basis.importance_level,
                "design_life_years": site.project_basis.design_life_years,
                "compliance_pathway": "Engineered solution",
                "supplemental_methods": [
                    {
                        "id": "SCI-P399",
                        "label": "SCI P399 portal-frame stability workflow",
                        "reference": "Table 3.1 and Sections 4-12",
                        "role": (
                            "Supplemental analysis guidance; not the Australian "
                            "compliance basis"
                        ),
                    }
                ],
            }
        )
        design_basis["jurisdiction"] = site.project_basis.jurisdiction
        design_standards = dict(design_basis.get("standards") or {})

        def action_reference(reference: str) -> str:
            if standards.confirmed:
                return reference
            return reference + " — unconfirmed for this project"

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
