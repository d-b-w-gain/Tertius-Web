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
  component_id: string
  pressure_kPa: number
  area_m2: number
  direction: Vector3
  provenance: string
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
      assumption: string
    }>
    load_cases: StructuralSnapshot['load_cases']
    member_loads: MemberPointLoad[]
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
    category: 'dead' | 'live' | 'wind' | 'fixture'
  }>
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
  }
  capabilities: CapabilityState[]
  warnings: string[]
}
