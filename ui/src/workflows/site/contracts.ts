export type CardinalMultiplierValues = {
  n: number
  ne: number
  e: number
  se: number
  s: number
  sw: number
  w: number
  nw: number
}

export type CandidateModelSiteDimensions = {
  schema_version: 'tertius.model-site-dimensions.v1'
  model_artifact_id: string
  footprint_length_m: number
  footprint_width_m: number
  overall_height_m: number
  reference_height_m: number
  roof_eave_height_m?: number | null
  roof_ridge_height_m?: number | null
  reference_height_basis: string
  source: string
}

export type SiteDefinition = {
  schema_version: '1.0'
  project_basis: {
    building_use: string
    building_classification: string
    importance_level: '1' | '2' | '3' | '4'
    design_life_years: number
    jurisdiction: string
    standards: {
      combinations: string
      permanent_and_imposed: string
      wind: string
      confirmed: boolean
    }
  }
  location: {
    address: string
    latitude: number
    longitude: number
  }
  structure: {
    footprint_length_m: number
    footprint_width_m: number
    front_bearing_degrees: number
    front_definition: 'long_wall_normal' | 'gable_ridge_normal' | 'manual'
    orientation_status: 'suggested' | 'verified'
    placement_latitude?: number | null
    placement_longitude?: number | null
  }
  wind: {
    basis_id: string
    region: string
    region_area: string
    region_source: string
    region_approximate: boolean
    region_status: 'suggested' | 'verified'
    table_status: 'starter' | 'verified'
    table_dataset_version: string
    terrain_category: '1' | '2' | '2.5' | '3' | '4'
    annual_probability_uls: string
    reference_height_m: number
    direction_multiplier: number
    cardinal_direction_multipliers: CardinalMultiplierValues | null
    cardinal_terrain_height_multipliers?: CardinalMultiplierValues | null
    shielding_multiplier: number
    cardinal_shielding_multipliers?: CardinalMultiplierValues | null
    topographic_multiplier: number
    cardinal_topographic_multipliers?: CardinalMultiplierValues | null
    climate_change_multiplier: number | null
    multiplier_evidence?: {
      evidence_id: string
      provider: string
      dataset: string
      dataset_version: string
      source_uri: string
      site_latitude: number
      site_longitude: number
      terrain_reference_height_m: number
      method_status: 'indicative_hazard_evidence' | 'automated_local_analysis'
      terrain_evidence_id?: string | null
      placement_latitude?: number | null
      placement_longitude?: number | null
      footprint_length_m?: number | null
      footprint_width_m?: number | null
      front_bearing_degrees?: number | null
      adopted_components: Array<'M_z_cat' | 'M_s' | 'M_t'>
      review_status: 'suggested' | 'verified'
      review_reason: string
    } | null
    action_envelope: {
      enclosure: 'enclosed' | 'open_sided'
      openings_operating_state: 'normally_closed' | 'normally_open'
      opening_capacity_status: 'unverified' | 'verified'
      coefficient_selection_policy: 'worst_available_credible' | 'verified_only'
    }
  }
  terrain_evidence?: {
    evidence_id: string
    site_latitude: number
    site_longitude: number
    radius_m: number
  } | null
}

export type SiteCalculation = {
  revision: string
  site_ready: boolean
  working_basis_ready?: boolean
  certification_ready?: boolean
  standard: string
  table_version: string
  region: string
  annual_recurrence_interval_years: number
  regional_wind_speed_m_s: number
  terrain_height_multiplier: number
  site_wind_speed_m_s: number
  q_z_kPa: number
  structure: SiteDefinition['structure']
  directional_mode: 'single_conservative' | 'cardinal'
  directional_multiplier_modes?: {
    direction: 'single_conservative' | 'cardinal'
    terrain_height: 'terrain_category_height_table' | 'cardinal'
    shielding: 'single_conservative' | 'cardinal'
    topographic: 'single_conservative' | 'cardinal'
  }
  multiplier_evidence_stale?: boolean
  cardinal_wind_speeds: Array<{
    direction: 'N' | 'NE' | 'E' | 'SE' | 'S' | 'SW' | 'W' | 'NW'
    bearing_degrees: number
    direction_multiplier: number
    terrain_height_multiplier?: number
    shielding_multiplier?: number
    topographic_multiplier?: number
    site_wind_speed_m_s: number
    q_z_kPa: number
  }>
  building_face_wind_speeds: Array<{
    face: 'front' | 'right' | 'back' | 'left'
    bearing_degrees: number
    site_wind_speed_m_s: number
    q_z_kPa: number
    governing_cardinal_direction: string
    contributing_cardinal_directions: string[]
  }>
  governing_cardinal_direction: string
  verifier_hash: string
  formula: string
  verify_against: string
  action_envelope: SiteDefinition['wind']['action_envelope']
  standard_table_evidence?: WindStandardEvidence
}

export type WindStandardEvidence = {
  dataset_version: string
  standard_reference: string
  source: {
    title: string
    author: string
    published_date: string
    filename: string
    sha256: string
    source_type: 'secondary_summary_presentation'
  }
  verification: {
    status: 'requires_licensed_standard_check'
    message: string
  }
  region: string
  direction_multipliers: NonNullable<SiteDefinition['wind']['cardinal_direction_multipliers']>
  climate_change_multiplier: number
  applied_tables: Array<{
    id: string
    table_number: string
    title: string
    source_page: number
    applicability: string[]
  }>
  report_table_index: Array<{
    id: string
    table_number: string
    title: string
    source_page: number
  }>
}

export type SiteWorkbenchResponse = {
  project_name: string
  filename: 'tertius_site.py'
  exists: boolean
  site_dict: SiteDefinition
  source: string
  calculation: SiteCalculation
}

export type GisCacheHealth = {
  status: 'ready'
  free_bytes: number
  total_bytes: number
}

export type GisEvidenceManifest = {
  evidence_id: string
  created_at: string
  source: {
    provider: string
    dataset: string
    dataset_version: string
    licence: string
    attribution: string
    source_uri?: string | null
  }
  asset: {
    content_sha256: string
    relative_path: string
    media_type: string
    size_bytes: number
    width: number
    height: number
    band_count: number
    dtype: string
    crs: string
    bounds: [number, number, number, number]
    resolution: [number, number]
    nodata: number | null
  }
}

export type GisPointResult = {
  coordinates: [number, number]
  values: Array<number | null>
  band_names: string[]
}

export type GisGeocodeCandidate = {
  address: string
  latitude: number
  longitude: number
  address_pid: string
  geocode_type: string
  confidence: number | null
  source: 'G-NAF'
  quality: 'address_point'
  dataset_version: string
  attribution: string
}

export type GisSiteBoundaryEvidence = {
  schema_version: 'tertius.gis.site-boundary.v1'
  evidence_id: string
  fetched_at: string
  provider: 'NSW Spatial Services'
  dataset: 'NSW Land Parcel Property Theme — Property'
  dataset_version: string
  attribution: string
  source_uri: string
  query_point: [number, number]
  feature: {
    type: 'Feature'
    properties: {
      propid: number | null
      housenumber: string | null
      address: string | null
      lastupdate: number | null
      shapeuuid: string | null
    }
    geometry: {
      type: 'Polygon' | 'MultiPolygon'
      coordinates: unknown
    }
  }
}

export type GisBuildingEvidence = {
  schema_version: 'tertius.gis.buildings.v1'
  evidence_id: string
  fetched_at: string
  provider: string
  dataset: string
  dataset_version: string
  licence: string
  attribution: string
  source_uri: string
  query_point: [number, number]
  query_radius_m: number
  footprint_count: number
  measured_height_count: number
  height_observation_count: number
  source_counts: Record<string, number>
  height_source_counts: Record<string, number>
  height_method_counts: Record<string, number>
  quality: {
    source_fusion: boolean
    height_coverage_ratio: number
    confidence_coverage_ratio: number
    suitable_for_local_shielding: boolean
    warnings: string[]
  }
  features: Array<{
    source_id: string
    height_m: number | null
    height_lower_m: number | null
    height_upper_m: number | null
    confidence: number | null
    outline_source: string | null
    height_source: string | null
    num_floors: number | null
    roof_height_m: number | null
    roof_shape: string | null
    sources: Array<{
      property: string | null
      dataset: string
      licence: string | null
      record_id: string | null
      update_time: string | null
      confidence: number | null
    }>
    height_observations: Array<{
      method: 'classified_lidar' | 'source_estimate' | 'source_storeys'
      confidence_class: 'measured' | 'modelled' | 'unknown'
      provider: string
      dataset: string
      dataset_version: string
      licence: string
      source_uri: string
      metadata_uri: string | null
      acquired_at: string | null
      ground_elevation_m: number | null
      eave_height_m: number | null
      roof_median_height_m: number | null
      ridge_height_m: number | null
      height_lower_m: number | null
      height_best_m: number | null
      height_upper_m: number | null
      roof_point_count: number
      ground_point_count: number
      point_density_per_m2: number | null
      vegetation_fraction: number | null
      warnings: string[]
    }>
    geometry: {
      type: 'Polygon' | 'MultiPolygon'
      coordinates: unknown
    }
  }>
}

export type GisDirectionalWindMultiplierEvidence = {
  schema_version: 'tertius.gis.wind-multipliers.v1' | 'tertius.gis.local-wind.v1'
  evidence_id: string
  latitude: number
  longitude: number
  tile_id?: string
  terrain_evidence_id?: string
  building_evidence_id?: string
  topographic_terrain_evidence_id?: string
  placement_latitude?: number
  placement_longitude?: number
  footprint_length_m?: number
  footprint_width_m?: number
  front_bearing_degrees?: number
  wind_region?: string
  terrain_reference_height_m: number
  terrain_height_multipliers: CardinalMultiplierValues
  shielding_multipliers: CardinalMultiplierValues
  topographic_multipliers: CardinalMultiplierValues
  provider: 'Geoscience Australia' | 'Tertius GIS cache'
  dataset: 'National wind multiplier dataset' | 'Pinned local wind evidence'
  dataset_version: string
  licence: string
  attribution: string
  source_uri: string
  method_status: 'indicative_hazard_evidence' | 'automated_local_analysis'
  review_required: true
  review_note: string
  directions?: Record<keyof CardinalMultiplierValues, {
    direction: keyof CardinalMultiplierValues
    bearing_degrees: number
    terrain_category: number
    terrain_height_multiplier: number
    ga_terrain_height_multiplier_10m: number
    terrain_building_fraction: number
    terrain_buildings_per_hectare: number
    terrain_reason: string
    shielding_multiplier: number
    ga_shielding_multiplier_2016?: number | null
    local_shielding_multiplier?: number | null
    shielding_basis?: 'ga_2016_baseline' | 'local_improvement'
    shielding_parameter: number | null
    shielding_building_count: number
    shielding_candidate_count: number
    shielding_height_coverage: number
    shielding_height_decision_coverage: number
    shielding_definitely_eligible_count: number
    shielding_definitely_ineligible_count: number
    shielding_uncertain_building_ids: string[]
    shielding_average_height_m: number | null
    shielding_average_breadth_m: number | null
    shielding_building_ids: string[]
    shielding_reason: string
    topographic_multiplier: number
    topographic_feature_height_m: number | null
    topographic_crest_distance_m: number | null
    topographic_lu_m: number | null
    topographic_l1_m: number | null
    topographic_l2_m: number | null
    topographic_mh?: number
    topographic_feature_type?: string | null
    topographic_cross_section_bearing_degrees?: number | null
    topographic_site_position?: 'upwind' | 'downwind' | 'crest' | null
    topographic_slope?: number | null
    topographic_crest_offset_m?: number | null
    topographic_crest_elevation_m?: number | null
    topographic_base_elevation_m?: number | null
    topographic_half_height_distance_m?: number | null
    topographic_threshold_m?: number
    topographic_candidate_count?: number
    topographic_search_radius_m?: number
    topographic_search_complete?: boolean
    topographic_profile_distances_m?: number[]
    topographic_profile_elevations_m?: Array<number | null>
    topographic_standard_basis?: string
    topographic_reason: string
  }>
}
