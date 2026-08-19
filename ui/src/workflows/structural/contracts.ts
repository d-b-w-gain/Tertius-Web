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
  building_classification: string | null
  importance_level: string | null
  design_life_years: number | null
  compliance_pathway: string
  standards: Record<string, string>
  supplemental_methods: Array<{
    id: string
    label: string
    reference: string
    role: string
  }>
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
  design_event: 'serviceability' | 'ultimate'
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
  building_face?: 'front' | 'right' | 'back' | 'left' | null
  face_bearing_degrees?: number | null
  structural_action_direction?: '+X' | '-X' | '+Y' | '-Y' | null
  governing_cardinal_direction?: 'N' | 'NE' | 'E' | 'SE' | 'S' | 'SW' | 'W' | 'NW' | null
  contributing_cardinal_directions?: Array<'N' | 'NE' | 'E' | 'SE' | 'S' | 'SW' | 'W' | 'NW'>
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
  primary_reference: string
  supplemental_references: string[]
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
  primary_reference: string
  supplemental_references: string[]
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
  role?: string | null
}

export type DesignConnection = {
  id: string
  label: string
  from_component_id: string
  to_component_id: string
  component_ports?: Record<string, string>
  connector_component_ids: string[]
  transfers: Array<'force' | 'shear' | 'moment' | 'wind_normal'>
  joint_model?: {
    analysis_model: 'pinned' | 'rigid_zone' | 'semi_rigid'
    stiffness_status: 'assumed' | 'candidate' | 'verified'
    stiffness_basis: string
    member_engagements: Array<{
      role: string
      component_id: string
      member_end: 'start' | 'end'
      joint_point: Vector3
      flexible_axis_end: Vector3
      engagement_length_m: number
      plate_length_m: number
      bolt_line_distances_m: number[]
    }>
  } | null
  resistance?: {
    pack_id: string
    version: string
    status: 'unverified' | 'candidate' | 'verified'
    basis: string
    connector_part_numbers: string[]
    source: string | null
    source_sha256: string | null
    design_axial_capacity_kN: number | null
    design_shear_capacity_kN: number | null
    design_moment_capacity_kNm: number | null
    assumptions: string[]
  } | null
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
  analysis_configuration_revision?: number | null
  analysis_configuration_digest?: string | null
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
      start_node_key?: string | null
      end_node_key?: string | null
      start_restraints: StructuralNode['restraints']
      end_restraints: StructuralNode['restraints']
      section_id: string
      material_id: string
      rotation_deg: number
      start_releases: StructuralNode['restraints']
      end_releases: StructuralNode['restraints']
      tension_only?: boolean
      compression_only?: boolean
      analytical_role?: 'physical' | 'rigid_zone'
      source_connection_id?: string | null
      tension_capacity_status?: 'not_checked' | 'candidate' | 'verified'
      tension_capacity_kN?: number | null
      tension_capacity_basis?: string | null
      end_fastener_count?: number | null
      end_connection_capacity_kN?: number | null
      end_connection_basis?: string | null
      deflection_limit_ratio: number | null
      deflection_limit_mm: number | null
      deflection_limit_basis: string | null
      assumption: string
    }>
    load_cases: StructuralSnapshot['load_cases']
    load_combinations: LoadCombination[]
    unavailable_load_combinations?: UnavailableLoadCombination[]
    action_standard_pack?: ActionStandardPackEvidence | null
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
  purpose?: 'design' | 'stability_probe'
}

export type CertificationGate = {
  id: string
  order: number
  label: string
  status: VerificationStatus
  primary_reference: string
  summary: string
  stage_ids: string[]
}

export type CertificationReadiness = {
  scheme_id: 'AU-NCC-2022'
  scheme_label: string
  document_status:
    | 'analysis_incomplete'
    | 'engineering_review_draft'
    | 'certificate_ready'
  draft_document_label: string
  ready_for_engineering_review: boolean
  ready_for_certificate: boolean
  ready_for_order: boolean
  conclusion: string
  blocking_gate_ids: string[]
  blocking_reasons: string[]
  gates: CertificationGate[]
}

export type UnavailableLoadCombination = {
  id: string
  label: string
  limit_state: 'serviceability' | 'ultimate'
  family: 'action_standard' | 'global_stability'
  missing_inputs: string[]
  reason: string
}

export type ActionStandardPackEvidence = {
  pack_id: string
  pack_version: string
  standard_reference: string
  status: 'working' | 'verified'
  source_document_sha256: string | null
  applicability: string[]
  exclusions: string[]
  combination_ids: string[]
  basis: string
}

export type MemberDiagramStation = {
  distance_m: number
  position: Vector3
  moment_kNm: Vector3
  major_moment_kNm: Vector3
  minor_moment_kNm: Vector3
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
  tension_only?: boolean
  compression_only?: boolean
  analytical_role?: 'physical' | 'rigid_zone'
  source_connection_id?: string | null
}

export type StructuralSnapshot = {
  schema_version: '2.0'
  mode: 'fixture' | 'design'
  title: string
  subtitle: string
  source: {
    kind: 'fixture' | 'design'
    label: string
    design_id: string | null
    design_hash: string | null
    analysis_configuration_revision?: number | null
    analysis_configuration_digest?: string | null
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
    tension_width_mm?: number | null
    tension_thickness_mm?: number | null
    tension_hole_diameter_mm?: number | null
    tension_holes_in_critical_section?: number | null
    tension_force_distribution_factor?: number | null
    end_fastener_nominal_diameter_mm?: number | null
    end_fastener_spacing_mm?: number | null
    end_fastener_edge_distance_mm?: number | null
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
    yield_strength_MPa?: number | null
    tensile_strength_MPa?: number | null
  }>
  load_cases: Array<{
    id: string
    label: string
    category: 'dead' | 'live' | 'wind' | 'imperfection' | 'fixture'
  }>
  load_combinations: LoadCombination[]
  unavailable_load_combinations?: UnavailableLoadCombination[]
  action_standard_pack?: ActionStandardPackEvidence | null
  loads: Array<{
    id: string
    label: string
    node_id: string
    case_id: string
    force: Vector3
    moment: Vector3
    visual_node_id: string
    provenance?: string | null
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
  connection_checks?: Array<{
    connection_id: string
    label: string
    status: 'pass' | 'fail' | 'not_checked' | 'unsupported'
    evidence_status: 'unverified' | 'candidate' | 'verified'
    pack_id: string
    pack_version: string
    identity_status: 'not_declared' | 'pass' | 'fail'
    identity_mismatches: string[]
    governing_combination_id: string | null
    governing_member_id: string | null
    axial_demand_kN: number
    shear_demand_kN: number
    moment_demand_kNm: number
    design_axial_capacity_kN: number | null
    design_shear_capacity_kN: number | null
    design_moment_capacity_kNm: number | null
    axial_utilisation: number | null
    shear_utilisation: number | null
    moment_utilisation: number | null
    governing_utilisation: number | null
    expected_connector_part_numbers: string[]
    rendered_connector_part_numbers: string[]
    source: string | null
    source_sha256: string | null
    anchor_group?: {
      status: 'pass' | 'fail' | 'unsupported'
      evidence_status: 'unverified' | 'candidate' | 'verified'
      pack_id: string
      pack_version: string
      anchor_part_number: string
      anchor_count: number
      effective_anchor_count: number
      substrate_type: string | null
      substrate_status: 'unverified' | 'candidate' | 'verified'
      tension_demand_kN: number
      shear_demand_kN: number
      tension_capacity_kN: number | null
      shear_capacity_kN: number | null
      interaction_utilisation: number | null
      installed_effective_embedment_mm: number | null
      reference_embedment_mm: number | null
      minimum_edge_distance_mm: number | null
      required_edge_distance_mm: number | null
      minimum_spacing_mm: number | null
      required_spacing_mm: number | null
      embedment_status: 'pass' | 'fail' | 'not_checked'
      edge_distance_status: 'pass' | 'fail' | 'not_checked'
      spacing_status: 'pass' | 'fail' | 'not_required' | 'not_checked'
      source: string | null
      source_sha256: string | null
      basis: string
      blockers: string[]
    } | null
    basis: string
    assumptions: string[]
  }>
  tension_member_checks?: Array<{
    member_id: string
    label: string
    status: 'pass' | 'fail' | 'not_checked' | 'unsupported'
    capacity_status: 'not_checked' | 'candidate' | 'verified'
    member_capacity_status: 'not_checked' | 'candidate' | 'verified'
    connection_capacity_status: 'not_checked' | 'candidate' | 'verified'
    pack_id: 'as_nzs_4600_2005_a1_tension' | null
    governing_combination_id: string | null
    tension_demand_kN: number
    tension_capacity_kN: number | null
    end_connection_capacity_kN: number | null
    governing_capacity_kN: number | null
    member_utilisation: number | null
    connection_utilisation: number | null
    governing_utilisation: number | null
    end_fastener_count: number | null
    rendered_end_connection_count: number
    rendered_end_fastener_counts: number[]
    required_force_per_end_fastener_kN: number | null
    gross_area_mm2: number | null
    net_area_mm2: number | null
    gross_yield_capacity_kN: number | null
    net_fracture_capacity_kN: number | null
    connected_part_net_capacity_kN: number | null
    end_bearing_capacity_kN: number | null
    end_tearout_capacity_kN: number | null
    end_fastener_part_numbers: string[]
    end_fastener_product_keys: string[]
    end_fastener_product_definition_digests: string[]
    fastener_tested_single_shear_strength_kN: number | null
    fastener_required_single_shear_strength_kN: number | null
    fastener_shear_qualification_status: 'not_checked' | 'candidate' | 'pass' | 'fail'
    fastener_evidence_status: 'unverified' | 'candidate' | 'verified' | null
    fastener_evidence_source: string | null
    fastener_evidence_revision: string | null
    fastener_evidence_url: string | null
    spacing_status: 'not_checked' | 'pass' | 'fail'
    edge_distance_status: 'not_checked' | 'pass' | 'fail'
    standard_reference: string | null
    standard_status: string | null
    standard_source_sha256: string | null
    developments_supplement_sha256: string | null
    basis: string
    assumptions: string[]
  }>
  bracing_load_path_traces?: Array<{
    id: string
    member_id: string
    component_id: string
    governing_combination_id: string | null
    status: 'pass' | 'fail' | 'candidate' | 'blocked'
    tension_demand_kN: number
    component_ids: string[]
    connection_ids: string[]
    grounded_component_ids: string[]
    blockers: string[]
    basis: string
  }>
  cross_section_checks?: Array<{
    member_id: string
    label: string
    pack_id: 'as_nzs_4600_2005_a1_ewm'
    status: 'pass' | 'fail' | 'not_checked' | 'unsupported'
    governing_combination_id: string | null
    governing_station_m: number | null
    axial_kN: number | null
    major_moment_kNm: number | null
    minor_moment_kNm: number | null
    web_shear_kN: number | null
    off_axis_shear_kN: number | null
    torsion_kNm: number | null
    design_compression_capacity_kN: number | null
    design_major_bending_capacity_kNm: number | null
    design_minor_bending_capacity_kNm: number | null
    design_web_shear_capacity_kN: number | null
    design_off_axis_shear_capacity_kN: number | null
    design_st_venant_torsion_capacity_kNm: number | null
    axial_bending_utilisation: number | null
    biaxial_axial_bending_utilisation: number | null
    bending_shear_utilisation: number | null
    minor_bending_shear_utilisation: number | null
    torsion_utilisation: number | null
    governing_utilisation: number | null
    section_record_sha256: string | null
    capacity_factors: Record<string, number>
    web_slenderness: number | null
    shear_regime: 'stocky' | 'inelastic_buckling' | 'elastic_buckling' | null
    standard_reference: string | null
    standard_status: string | null
    standard_source_sha256: string | null
    developments_supplement_sha256: string | null
    off_axis_load_path_status: 'not_declared' | 'candidate' | 'verified'
    off_axis_required_reaction_kN: number | null
    off_axis_source_component_ids: string[]
    off_axis_source_connection_ids: string[]
    off_axis_collector_component_ids: string[]
    off_axis_collector_connection_ids: string[]
    off_axis_grounded_component_id: string | null
    off_axis_load_path_basis: string | null
    basis: string
    assumptions: string[]
  }>
  member_stability_checks?: Array<{
    segment_id: string
    member_id: string
    label: string
    pack_id: 'as_nzs_4600_2005_a1_member'
    status: 'pass' | 'fail' | 'not_checked' | 'unsupported'
    governing_combination_id: string | null
    governing_station_m: number | null
    segment_start_m: number
    segment_end_m: number
    unbraced_length_m: number
    axial_kN: number | null
    major_moment_kNm: number | null
    minor_moment_kNm: number | null
    web_shear_kN: number | null
    off_axis_shear_kN: number | null
    torsion_kNm: number | null
    elastic_flexural_buckling_stress_MPa: number | null
    elastic_torsional_buckling_stress_MPa: number | null
    elastic_flexural_torsional_buckling_stress_MPa: number | null
    elastic_distortional_compression_stress_MPa: number | null
    elastic_distortional_bending_stress_MPa: number | null
    elastic_lateral_torsional_buckling_moment_kNm: number | null
    elastic_minor_lateral_torsional_buckling_moment_kNm: number | null
    elastic_major_axis_flexural_buckling_load_kN: number | null
    elastic_minor_axis_flexural_buckling_load_kN: number | null
    nominal_global_buckling_stress_MPa: number | null
    nominal_global_compression_capacity_kN: number | null
    nominal_distortional_compression_capacity_kN: number | null
    nominal_lateral_torsional_bending_capacity_kNm: number | null
    nominal_distortional_bending_capacity_kNm: number | null
    nominal_minor_lateral_torsional_bending_capacity_kNm: number | null
    design_member_compression_capacity_kN: number | null
    design_major_bending_capacity_kNm: number | null
    design_minor_bending_capacity_kNm: number | null
    design_global_compression_capacity_kN: number | null
    design_distortional_compression_capacity_kN: number | null
    design_lateral_torsional_bending_capacity_kNm: number | null
    design_distortional_bending_capacity_kNm: number | null
    design_section_minor_bending_capacity_kNm: number | null
    design_minor_lateral_torsional_bending_capacity_kNm: number | null
    design_web_shear_capacity_kN: number | null
    design_off_axis_shear_capacity_kN: number | null
    design_st_venant_torsion_capacity_kNm: number | null
    governing_compression_mode: 'section' | 'global' | 'distortional' | null
    governing_bending_mode: 'section' | 'lateral_torsional' | 'distortional' | null
    governing_minor_bending_mode: 'section' | 'lateral_torsional' | null
    axial_utilisation: number | null
    axial_bending_utilisation: number | null
    major_bending_utilisation: number | null
    minor_bending_utilisation: number | null
    web_shear_utilisation: number | null
    off_axis_shear_utilisation: number | null
    torsion_utilisation: number | null
    major_axis_amplification_factor: number | null
    minor_axis_amplification_factor: number | null
    biaxial_member_interaction_utilisation: number | null
    major_bending_shear_utilisation: number | null
    minor_bending_shear_utilisation: number | null
    governing_utilisation: number | null
    lateral_bending_restraint:
      | 'unverified'
      | 'continuous_compression_flange'
    restraint_status: 'missing' | 'candidate' | 'inadequate' | 'assumed' | 'verified'
    compression_flange:
      | 'positive_local_y'
      | 'negative_local_y'
      | 'none'
      | 'mixed'
    restraint_candidate_ids: string[]
    distortional_buckling_status: 'unverified' | 'verified'
    section_record_sha256: string | null
    standard_reference: string | null
    standard_status: string | null
    standard_source_sha256: string | null
    developments_supplement_sha256: string | null
    basis: string
    assumptions: string[]
  }>
  member_restraint_candidate_checks?: Array<{
    id: string
    candidate_id: string
    member_id: string
    connection_id: string
    combination_id: string
    contact_flange: 'positive_local_y' | 'negative_local_y' | 'both' | 'none'
    status: 'unsupported' | 'candidate' | 'pass' | 'fail' | 'not_required'
  demand_model:
    | 'not_defined'
    | 'aisi_2004_d3_2_2_eccentric_load_couple'
    | 'as_nzs_4600_2005_4_3_2_flange_force'
    transferred_load_kN: number | null
    load_eccentricity_m: number | null
    member_depth_m: number | null
    required_force_kN: number | null
    required_moment_kNm: number | null
    available_force_kN: number | null
    available_moment_kNm: number | null
    force_utilisation: number | null
    moment_utilisation: number | null
    stiffness_status: 'unverified' | 'verified'
    evidence_pack_id: string | null
    evidence_pack_version: string | null
    identity_status: 'not_declared' | 'pass' | 'fail'
    identity_mismatches: string[]
    evidence_references: string[]
    anchorage_status: 'unverified' | 'verified'
    anchorage_component_ids: string[]
    anchorage_connection_ids: string[]
    anchorage_grounded_component_id: string | null
    anchorage_basis: string
    anchorage_blockers?: string[]
    mechanism: string
    provenance: string
    basis: string
  }>
  member_restraint_traces?: Array<{
    id: string
    member_id: string
    combination_id: string
    segment_start_m: number
    segment_end_m: number
    start_position: Vector3
    end_position: Vector3
    compression_flange: 'positive_local_y' | 'negative_local_y' | 'none'
    status: 'missing' | 'candidate' | 'inadequate' | 'verified' | 'not_required'
    start_restraint_candidate_ids: string[]
    end_restraint_candidate_ids: string[]
    effective_restraint_candidate_ids: string[]
    governing_candidate_check_ids: string[]
    required_restraint_force_kN: number | null
    available_restraint_force_kN: number | null
    restraint_force_utilisation: number | null
    basis: string
  }>
  serviceability_checks: Array<{
    member_id: string
    physical_member_id?: string | null
    analytical_member_ids?: string[]
    span_m?: number | null
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
  certification_readiness: CertificationReadiness | null
  capabilities: CapabilityState[]
  warnings: string[]
}

export type StructuralAnalysisCacheInfo = {
  status: 'hit' | 'calculated'
  key_digest: string
  engine_version: string
  calculated_at: string
  calculation_duration_seconds: number
}

export type ActiveStructuralWorkbenchResponse = {
  capture: ProjectStructuralCapture
  analysis: StructuralSnapshot | null
  analysis_error: string | null
  cache: StructuralAnalysisCacheInfo | null
}
