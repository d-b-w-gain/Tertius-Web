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
    shielding_multiplier: number
    topographic_multiplier: number
    climate_change_multiplier: number | null
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
  verifier_hash: string
  formula: string
  verify_against: string
}

export type SiteWorkbenchResponse = {
  project_name: string
  filename: 'tertius_site.py'
  exists: boolean
  site_dict: SiteDefinition
  source: string
  calculation: SiteCalculation
}
