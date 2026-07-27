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
  member_checks: Array<{
    member_id: string
    label: string
    demand_kNm: number
    capacity_kNm: number
    utilisation: number
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
