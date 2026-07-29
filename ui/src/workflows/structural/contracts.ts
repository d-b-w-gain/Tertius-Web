export type Vector3 = {
  x: number
  y: number
  z: number
}

export type CapabilityState = {
  id: string
  label: string
  status: 'fixture' | 'online' | 'pending' | 'blocked'
  detail: string
}

export type VerificationStatus =
  | 'pass'
  | 'fail'
  | 'warning'
  | 'not_checked'
  | 'unsupported'
  | 'blocked'

export type StructuralDesignBasis = {
  framework_id: string
  framework_label: string
  framework_reference: string
  jurisdiction: string
  analysis_method: string
  standards: Record<string, string>
}

export type StructuralWindActionBasis = {
  id: string
  site_address: string
  latitude: number
  longitude: number
  region: string
  region_area: string
  region_source: string
  region_approximate: boolean
  region_status: 'suggested' | 'verified'
  standard: string
  table_version: string
  table_status: 'starter' | 'verified'
  importance_level: string
  annual_recurrence_interval_years: number
  terrain_category: string
  reference_height_m: number
  regional_wind_speed_m_s: number
  climate_change_multiplier: number
  direction_multiplier: number
  terrain_height_multiplier: number
  shielding_multiplier: number
  topographic_multiplier: number
  site_wind_speed_m_s: number
  q_z_kPa: number
  enclosure?: 'enclosed' | 'open_sided' | null
  openings_operating_state?: 'normally_closed' | 'normally_open' | null
  opening_capacity_status?: 'unverified' | 'verified' | null
  coefficient_selection_policy?:
    | 'worst_available_credible'
    | 'verified_only'
    | null
  verifier_hash: string
  provenance: string
}

export type CalculationInput = {
  symbol: string
  label: string
  value: number | string | boolean
  unit: string | null
  source: string
}

export type CalculationEquation = {
  label: string
  expression: string
  substitution: string
  result: number | string
  unit: string | null
}

export type CalculationSheet = {
  id: string
  stage_id: string
  title: string
  status: VerificationStatus
  p399_reference: string
  purpose: string
  assumptions: string[]
  inputs: CalculationInput[]
  equations: CalculationEquation[]
  outputs: CalculationInput[]
  references: string[]
  related_member_ids: string[]
  related_node_ids: string[]
  related_load_case_ids: string[]
  related_combination_ids: string[]
}

export type VerificationStage = {
  id: string
  order: number
  label: string
  p399_reference: string
  status: VerificationStatus
  summary: string
  sheet_ids: string[]
  blocking_stage_ids: string[]
}

export type DesignComponent = {
  id: string
  label: string
  kind: 'ground' | 'member' | 'surface' | 'connector' | 'support'
  visual_node_id: string
  grounded: boolean
  part_number: string | null
}

export type DesignConnection = {
  id: string
  label: string
  from_component_id: string
  to_component_id: string
  connector_component_ids: string[]
  transfers: Array<'force' | 'shear' | 'moment' | 'wind_normal'>
}

export type DesignSurfaceLoad = {
  id: string
  label: string
  case: 'dead' | 'live' | 'wind'
  case_id: string | null
  component_id: string
  pressure_kPa: number
  area_m2: number
  direction: Vector3
  provenance: string
  wind_basis_id: string | null
  net_pressure_coefficient: number | null
  coefficient_status: 'assumed' | 'working_conservative' | 'verified' | null
}

export type DesignLoadPath = {
  load_id: string
  status: 'complete' | 'blocked'
  component_ids: string[]
  connection_ids: string[]
  grounded_component_id: string | null
  detail: string
}

export type ProjectStructuralCapture = {
  schema_version: '0.1'
  project_name: string
  design_hash: string
  title: string
  authoring_mode: 'legacy' | 'generated'
  design_basis: StructuralDesignBasis | null
  wind_action_bases: StructuralWindActionBasis[]
  components: DesignComponent[]
  connections: DesignConnection[]
  loads: DesignSurfaceLoad[]
  load_paths: DesignLoadPath[]
  analysis: {
    materials: StructuralSnapshot['materials']
    sections: StructuralSnapshot['sections']
    members: Array<{
      id: string
      label: string
      component_id: string
      start: Vector3
      end: Vector3
      start_restraints: StructuralNode['restraints']
      end_restraints: StructuralNode['restraints']
      section_id: string
      material_id: string
      rotation_deg: number
      start_releases: StructuralNode['restraints']
      end_releases: StructuralNode['restraints']
      deflection_limit_ratio: number | null
      deflection_limit_mm: number | null
      deflection_limit_basis: string | null
      assumption: string
    }>
    load_cases: StructuralSnapshot['load_cases']
    load_combinations: LoadCombination[]
    member_loads: MemberPointLoad[]
    member_distributed_loads: MemberDistributedLoad[]
  } | null
  capabilities: CapabilityState[]
  warnings: string[]
}

export type MemberPointLoad = {
  id: string
  label: string
  member_id: string
  case_id: string
  distance_m: number
  force: Vector3
  moment: Vector3
  source_load_id: string
  provenance: string
}

export type MemberDistributedLoad = {
  id: string
  label: string
  member_id: string
  case_id: string
  start_distance_m: number
  end_distance_m: number
  start_force_kN_m: Vector3
  end_force_kN_m: Vector3
  source_kind: 'self_weight' | 'surface' | 'authored'
  source_load_id: string | null
  provenance: string
}

export type LoadCombination = {
  id: string
  label: string
  limit_state: 'serviceability' | 'ultimate'
  factors: Record<string, number>
}

export type MemberDiagramStation = {
  distance_m: number
  position: Vector3
  moment_kNm: Vector3
  shear_kN: Vector3
  displacement_mm: Vector3
}

export type StructuralNode = {
  id: string
  label: string
  position: Vector3
  restraints: {
    dx: boolean
    dy: boolean
    dz: boolean
    rx: boolean
    ry: boolean
    rz: boolean
  }
  visual_node_id: string
}

export type StructuralMember = {
  id: string
  label: string
  start_node_id: string
  end_node_id: string
  section_id: string
  material_id: string
  visual_node_id: string
}

export type StructuralSnapshot = {
  schema_version: '1.0'
  mode: 'fixture' | 'design'
  title: string
  subtitle: string
  source: {
    kind: 'fixture' | 'design'
    label: string
    design_id: string | null
    design_hash: string | null
  }
  design_basis: StructuralDesignBasis | null
  wind_action_bases: StructuralWindActionBasis[]
  units: {
    length: 'm'
    force: 'kN'
    moment: 'kN.m'
    displacement: 'mm'
    render_length: 'mm'
  }
  nodes: StructuralNode[]
  members: StructuralMember[]
  sections: Array<{
    id: string
    label: string
    area_m2: number
    iy_m4: number
    iz_m4: number
    torsion_j_m4: number
    mass_kg_m: number | null
    bending_reference_kNm: number | null
    bending_reference_axis: 'local_y' | 'local_z' | 'resultant' | null
    bending_reference_basis: string | null
    catalog?: {
      catalog_id: string
      catalog_version: string
      section_key: string
      source: string
      record_sha256: string
      axis_mapping: Record<string, string>
      properties: Record<string, unknown>
    } | null
  }>
  materials: Array<{
    id: string
    label: string
    elastic_modulus_kN_m2: number
    shear_modulus_kN_m2: number
    poisson_ratio: number
    density_kg_m3: number
  }>
  load_cases: Array<{
    id: string
    label: string
    category: 'dead' | 'live' | 'wind' | 'imperfection' | 'fixture'
  }>
  load_combinations: LoadCombination[]
  loads: Array<{
    id: string
    label: string
    node_id: string
    case_id: string
    force: Vector3
    moment: Vector3
    visual_node_id: string
  }>
  member_loads: MemberPointLoad[]
  member_distributed_loads: MemberDistributedLoad[]
  reactions: Array<{
    node_id: string
    combination_id: string
    force: Vector3
    moment: Vector3
  }>
  member_results: Array<{
    member_id: string
    combination_id: string
    max_moment_kNm: number
    max_shear_kN: number
    max_axial_kN: number
    max_displacement_mm: number
  }>
  member_diagrams: Array<{
    member_id: string
    visual_node_id: string
    stations: MemberDiagramStation[]
  }>
  member_checks: Array<{
    member_id: string
    label: string
    demand_kNm: number
    capacity_kNm: number | null
    utilisation: number | null
    status: 'pass' | 'fail' | 'not_checked'
    basis: string
  }>
  serviceability_checks: Array<{
    member_id: string
    label: string
    combination_id: string
    displacement_mm: number
    limit_mm: number | null
    utilisation: number | null
    status: 'pass' | 'fail' | 'not_checked'
    basis: string
  }>
  load_summary: {
    member_mass_kg: number
    self_weight_kN: number
    additional_dead_load_kN: number
    imposed_load_kN: number
    wind_load_kN: number
  }
  equilibrium: {
    force_residual_kN: Vector3
    moment_residual_kNm: Vector3
    tolerance: number
    status: 'pass' | 'fail'
  }
  solver: {
    name: string
    version: string
    analysis: string
    combination_id: string
    combination_selection?:
      | 'requested'
      | 'default'
      | 'governing_working_envelope'
  }
  stability?: {
    method: 'p_delta'
    combination_id: string
    imperfection_case_id: string
    converged: boolean
    amplification_warning_ratio: number
    governing_moment_amplification: number
    governing_displacement_amplification: number
    member_comparisons: Array<{
      member_id: string
      first_order_max_moment_kNm: number
      second_order_max_moment_kNm: number
      moment_amplification: number
      first_order_max_displacement_mm: number
      second_order_max_displacement_mm: number
      displacement_amplification: number
    }>
    direction_results?: Array<{
      id: string
      combination_id: string
      imperfection_case_id: string
      nhf_combination_id: string
      horizontal_axis: 'x' | 'y'
      converged: boolean
      governing_moment_amplification: number
      governing_displacement_amplification: number
      nhf_eaves_displacement_mm: number
      alpha_cr: number | null
      member_comparisons: Array<{
        member_id: string
        first_order_max_moment_kNm: number
        second_order_max_moment_kNm: number
        moment_amplification: number
        first_order_max_displacement_mm: number
        second_order_max_displacement_mm: number
        displacement_amplification: number
      }>
    }>
    governing_direction_id?: string | null
    minimum_alpha_cr?: number | null
    second_order_required?: boolean | null
    rafter_design_axial_kN?: number | null
    rafter_elastic_critical_load_kN?: number | null
    rafter_axial_limit_kN?: number | null
    rafter_axial_force_significant?: boolean | null
    simplified_alpha_cr_applicable?: boolean | null
  } | null
  verification_stages: VerificationStage[]
  calculation_sheets: CalculationSheet[]
  capabilities: CapabilityState[]
  warnings: string[]
}
