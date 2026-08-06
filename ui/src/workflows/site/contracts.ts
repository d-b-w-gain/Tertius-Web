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
  }
  wind: {
    basis_id: string
    region: string
    region_area: string
    region_source: string
    region_approximate: boolean
    region_status: 'suggested' | 'verified'
    table_status: 'starter' | 'verified'
    terrain_category: '1' | '2' | '2.5' | '3' | '4'
    annual_probability_uls: string
    reference_height_m: number
    direction_multiplier: number
    cardinal_direction_multipliers: {
      n: number
      ne: number
      e: number
      se: number
      s: number
      sw: number
      w: number
      nw: number
    } | null
    shielding_multiplier: number
    topographic_multiplier: number
    climate_change_multiplier: number | null
    action_envelope: {
      enclosure: 'enclosed' | 'open_sided'
      openings_operating_state: 'normally_closed' | 'normally_open'
      opening_capacity_status: 'unverified' | 'verified'
      coefficient_selection_policy: 'worst_available_credible' | 'verified_only'
    }
  }
}

export type SiteCalculation = {
  revision: string
  site_ready: boolean
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
  cardinal_wind_speeds: Array<{
    direction: 'N' | 'NE' | 'E' | 'SE' | 'S' | 'SW' | 'W' | 'NW'
    bearing_degrees: number
    direction_multiplier: number
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
