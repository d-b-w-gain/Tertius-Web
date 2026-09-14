from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(
        min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$"
    )
    dataset: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(default="unknown", min_length=1, max_length=120)
    licence: str = Field(min_length=1, max_length=200)
    attribution: str = Field(min_length=1, max_length=500)
    source_uri: HttpUrl | None = None


class RasterAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: str
    media_type: Literal["image/tiff; application=geotiff; profile=cloud-optimized"]
    size_bytes: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    band_count: int = Field(ge=1)
    dtype: str
    crs: str
    bounds: tuple[float, float, float, float]
    resolution: tuple[float, float]
    nodata: float | int | None


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tertius.gis.evidence.v1"] = "tertius.gis.evidence.v1"
    evidence_id: str = Field(pattern=r"^gisv1-[0-9a-f]{32}$")
    created_at: datetime
    source: SourceMetadata
    asset: RasterAsset


class GeocodeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    latitude: float
    longitude: float
    address_pid: str
    geocode_type: str
    confidence: int | None = None
    source: Literal["G-NAF"] = "G-NAF"
    quality: Literal["address_point"] = "address_point"
    dataset_version: str
    attribution: str


class TerrainSiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-44.5, le=-9.0)
    longitude: float = Field(ge=112.0, le=154.0)
    radius_m: int | None = Field(default=None, ge=100)


class CardinalMultiplierValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: float = Field(gt=0, le=5)
    ne: float = Field(gt=0, le=5)
    e: float = Field(gt=0, le=5)
    se: float = Field(gt=0, le=5)
    s: float = Field(gt=0, le=5)
    sw: float = Field(gt=0, le=5)
    w: float = Field(gt=0, le=5)
    nw: float = Field(gt=0, le=5)


class DirectionalWindMultiplierEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tertius.gis.wind-multipliers.v1"] = (
        "tertius.gis.wind-multipliers.v1"
    )
    evidence_id: str = Field(pattern=r"^windv1-[0-9a-f]{32}$")
    latitude: float = Field(ge=-44.5, le=-9.0)
    longitude: float = Field(ge=112.0, le=154.0)
    tile_id: str = Field(pattern=r"^e\d{3}\.\d{4}s\d{2}\.\d{4}$")
    terrain_reference_height_m: float = 10.0
    terrain_height_multipliers: CardinalMultiplierValues
    shielding_multipliers: CardinalMultiplierValues
    topographic_multipliers: CardinalMultiplierValues
    provider: Literal["Geoscience Australia"] = "Geoscience Australia"
    dataset: Literal["National wind multiplier dataset"] = (
        "National wind multiplier dataset"
    )
    dataset_version: str = "Wind Multiplier Software 2.0 output (January 2016)"
    licence: str = "Creative Commons Attribution 4.0 International"
    attribution: str = "Geoscience Australia; data hosted by NCI"
    source_uri: HttpUrl = HttpUrl(
        "https://thredds.nci.org.au/thredds/catalog/fj6/multipliers/catalog.html"
    )
    method_status: Literal["indicative_hazard_evidence"] = "indicative_hazard_evidence"
    review_required: bool = True
    review_note: str = (
        "The hosted grids were produced in January 2016 and are not treated as "
        "a verified implementation of AS/NZS 1170.2:2021. Review dataset lineage, "
        "reference height and project-standard applicability before adoption."
    )


class SiteBoundaryEvidence(BaseModel):
    """Cached property polygon containing the requested NSW site point."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tertius.gis.site-boundary.v1"] = (
        "tertius.gis.site-boundary.v1"
    )
    evidence_id: str = Field(pattern=r"^parcelv1-[0-9a-f]{32}$")
    fetched_at: datetime
    provider: Literal["NSW Spatial Services"] = "NSW Spatial Services"
    dataset: Literal["NSW Land Parcel Property Theme — Property"] = (
        "NSW Land Parcel Property Theme — Property"
    )
    dataset_version: str
    attribution: str = "NSW Spatial Services"
    source_uri: HttpUrl
    query_point: tuple[float, float]
    feature: dict[str, Any]


class BuildingFeatureSource(BaseModel):
    """Source attribution retained for one conflated building property."""

    model_config = ConfigDict(extra="forbid")

    property: str | None = None
    dataset: str
    licence: str | None = None
    record_id: str | None = None
    update_time: str | None = None
    confidence: float | None = None


class BuildingEvidenceQuality(BaseModel):
    """Whole-query completeness indicators; directional gates remain separate."""

    model_config = ConfigDict(extra="forbid")

    source_fusion: bool = False
    height_coverage_ratio: float = Field(default=0, ge=0, le=1)
    confidence_coverage_ratio: float = Field(default=0, ge=0, le=1)
    suitable_for_local_shielding: bool = False
    warnings: list[str] = Field(default_factory=list)


class BuildingHeightObservation(BaseModel):
    """One auditable height measurement attached to a building footprint."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["classified_lidar", "source_estimate", "source_storeys"]
    confidence_class: Literal["measured", "modelled", "unknown"]
    provider: str
    dataset: str
    dataset_version: str
    licence: str
    source_uri: HttpUrl
    metadata_uri: HttpUrl | None = None
    acquired_at: str | None = None
    ground_elevation_m: float | None = None
    eave_height_m: float | None = Field(default=None, gt=0, le=1_000)
    roof_median_height_m: float | None = Field(default=None, gt=0, le=1_000)
    ridge_height_m: float | None = Field(default=None, gt=0, le=1_000)
    height_lower_m: float | None = Field(default=None, gt=0, le=1_000)
    height_best_m: float | None = Field(default=None, gt=0, le=1_000)
    height_upper_m: float | None = Field(default=None, gt=0, le=1_000)
    roof_point_count: int = Field(default=0, ge=0)
    ground_point_count: int = Field(default=0, ge=0)
    point_density_per_m2: float | None = Field(default=None, ge=0)
    vegetation_fraction: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class BuildingFeature(BaseModel):
    """One reusable building footprint and any supplied height evidence."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    height_m: float | None = Field(default=None, gt=0, le=1_000)
    height_lower_m: float | None = Field(default=None, gt=0, le=1_000)
    height_upper_m: float | None = Field(default=None, gt=0, le=1_000)
    confidence: float | None = None
    outline_source: str | None = None
    height_source: str | None = None
    num_floors: int | None = Field(default=None, gt=0)
    roof_height_m: float | None = Field(default=None, gt=0)
    roof_shape: str | None = None
    sources: list[BuildingFeatureSource] = Field(default_factory=list)
    height_observations: list[BuildingHeightObservation] = Field(default_factory=list)
    geometry: dict[str, Any]


class BuildingEvidence(BaseModel):
    """Cached open building data intersecting a site neighbourhood."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tertius.gis.buildings.v1"] = "tertius.gis.buildings.v1"
    evidence_id: str = Field(pattern=r"^buildingv1-[0-9a-f]{32}$")
    fetched_at: datetime
    provider: str = "Microsoft"
    dataset: str = "Global ML Building Footprints"
    dataset_version: str
    licence: str = "CDLA Permissive 2.0"
    attribution: str = "Microsoft Global ML Building Footprints"
    source_uri: HttpUrl
    query_point: tuple[float, float]
    query_radius_m: float = Field(gt=0, le=5_000)
    features: list[BuildingFeature]
    footprint_count: int = Field(ge=0)
    measured_height_count: int = Field(ge=0)
    height_observation_count: int = Field(default=0, ge=0)
    source_counts: dict[str, int] = Field(default_factory=dict)
    height_source_counts: dict[str, int] = Field(default_factory=dict)
    height_method_counts: dict[str, int] = Field(default_factory=dict)
    quality: BuildingEvidenceQuality = Field(default_factory=BuildingEvidenceQuality)


class DirectionalTerrainProfile(BaseModel):
    """Elevation samples extending from the site into an incoming wind sector."""

    model_config = ConfigDict(extra="forbid")

    direction: Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"]
    bearing_degrees: Literal[0, 45, 90, 135, 180, 225, 270, 315]
    distances_m: list[float]
    elevations_m: list[float | None]
    site_elevation_m: float
    minimum_elevation_m: float
    maximum_elevation_m: float
    maximum_elevation_distance_m: float
    endpoint_elevation_m: float | None


class CardinalTerrainProfileEvidence(BaseModel):
    """Eight directional x-z profiles sampled from one immutable terrain asset."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tertius.gis.terrain-profiles.v1"] = (
        "tertius.gis.terrain-profiles.v1"
    )
    evidence_id: str = Field(pattern=r"^gisv1-[0-9a-f]{32}$")
    latitude: float = Field(ge=-44.5, le=-9.0)
    longitude: float = Field(ge=112.0, le=154.0)
    distance_m: float = Field(ge=100, le=5_000)
    sample_interval_m: float = Field(ge=2, le=100)
    profiles: dict[
        Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"],
        DirectionalTerrainProfile,
    ]


class DirectionalLocalWindAssessment(BaseModel):
    """Auditable local observations and proposed multipliers for one 45 degree sector."""

    model_config = ConfigDict(extra="forbid")

    direction: Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"]
    bearing_degrees: Literal[0, 45, 90, 135, 180, 225, 270, 315]
    terrain_category: float = Field(ge=1, le=4)
    terrain_height_multiplier: float = Field(gt=0, le=5)
    ga_terrain_height_multiplier_10m: float = Field(gt=0, le=5)
    terrain_building_fraction: float = Field(ge=0)
    terrain_buildings_per_hectare: float = Field(ge=0)
    terrain_reason: str
    shielding_multiplier: float = Field(gt=0, le=1)
    ga_shielding_multiplier_2016: float | None = Field(default=None, gt=0, le=1)
    local_shielding_multiplier: float | None = Field(default=None, gt=0, le=1)
    shielding_basis: Literal["ga_2016_baseline", "local_improvement"] = (
        "ga_2016_baseline"
    )
    shielding_parameter: float | None = Field(default=None, ge=0)
    shielding_building_count: int = Field(ge=0)
    shielding_candidate_count: int = Field(default=0, ge=0)
    shielding_height_coverage: float = Field(ge=0, le=1)
    shielding_height_decision_coverage: float = Field(default=0, ge=0, le=1)
    shielding_definitely_eligible_count: int = Field(default=0, ge=0)
    shielding_definitely_ineligible_count: int = Field(default=0, ge=0)
    shielding_uncertain_building_ids: list[str] = Field(default_factory=list)
    shielding_average_height_m: float | None = Field(default=None, gt=0)
    shielding_average_breadth_m: float | None = Field(default=None, gt=0)
    shielding_building_ids: list[str]
    shielding_reason: str
    topographic_multiplier: float = Field(ge=1, le=5)
    topographic_feature_height_m: float | None = Field(default=None, ge=0)
    topographic_crest_distance_m: float | None = Field(default=None, ge=0)
    topographic_lu_m: float | None = Field(default=None, ge=0)
    topographic_l1_m: float | None = Field(default=None, ge=0)
    topographic_l2_m: float | None = Field(default=None, ge=0)
    topographic_mh: float = Field(default=1.0, ge=1, le=5)
    topographic_feature_type: str | None = None
    topographic_cross_section_bearing_degrees: float | None = Field(
        default=None, ge=0, lt=360
    )
    topographic_site_position: Literal["upwind", "downwind", "crest"] | None = None
    topographic_slope: float | None = Field(default=None, ge=0)
    topographic_crest_offset_m: float | None = None
    topographic_crest_elevation_m: float | None = None
    topographic_base_elevation_m: float | None = None
    topographic_half_height_distance_m: float | None = Field(default=None, ge=0)
    topographic_threshold_m: float = Field(default=0, ge=0)
    topographic_candidate_count: int = Field(default=0, ge=0)
    topographic_search_radius_m: float = Field(default=0, ge=0)
    topographic_search_complete: bool = False
    topographic_profile_distances_m: list[float] = Field(default_factory=list)
    topographic_profile_elevations_m: list[float | None] = Field(default_factory=list)
    topographic_standard_basis: str = "AS/NZS 1170.2:2021 Amd 2:2024 Clause 4.4.2"
    topographic_reason: str


class LocalDirectionalWindEvidence(BaseModel):
    """Pinned local GIS analysis that can reproduce all directional proposals."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tertius.gis.local-wind.v1"] = "tertius.gis.local-wind.v1"
    evidence_id: str = Field(pattern=r"^windv1-[0-9a-f]{32}$")
    latitude: float = Field(ge=-44.5, le=-9.0)
    longitude: float = Field(ge=112.0, le=154.0)
    placement_latitude: float = Field(ge=-44.5, le=-9.0)
    placement_longitude: float = Field(ge=112.0, le=154.0)
    terrain_evidence_id: str = Field(pattern=r"^gisv1-[0-9a-f]{32}$")
    topographic_terrain_evidence_id: str | None = Field(
        default=None, pattern=r"^gisv1-[0-9a-f]{32}$"
    )
    building_evidence_id: str = Field(pattern=r"^buildingv1-[0-9a-f]{32}$")
    wind_region: str
    terrain_reference_height_m: float = Field(gt=0, le=200)
    footprint_length_m: float = Field(gt=0)
    footprint_width_m: float = Field(gt=0)
    front_bearing_degrees: float = Field(ge=0, lt=360)
    terrain_height_multipliers: CardinalMultiplierValues
    shielding_multipliers: CardinalMultiplierValues
    topographic_multipliers: CardinalMultiplierValues
    directions: dict[
        Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"],
        DirectionalLocalWindAssessment,
    ]
    provider: Literal["Tertius GIS cache"] = "Tertius GIS cache"
    dataset: Literal["Pinned local wind evidence"] = "Pinned local wind evidence"
    dataset_version: str
    licence: str = "Source-specific licences recorded by evidence IDs"
    attribution: str = (
        "Geoscience Australia; Microsoft Global ML Building Footprints; "
        "terrain provider recorded by the pinned evidence manifest"
    )
    source_uri: HttpUrl = HttpUrl("https://github.com/d-b-w-gain/Tertius-Web")
    method_status: Literal["automated_local_analysis"] = "automated_local_analysis"
    review_required: bool = True
    review_note: str = (
        "Automatically adopted as a reproducible working basis. The terrain-category "
        "and building-height refinements remain engineering evidence, not a survey or "
        "licensed-standard certification. The January 2016 GA directional shielding "
        "grid is the baseline; conservative current-building evidence may reduce Ms, "
        "while incomplete reconstruction cannot worsen that baseline."
    )
