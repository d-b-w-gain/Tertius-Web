import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { StructuralWorkbench } from './StructuralWorkbench'
import type { ProjectStructuralCapture, StructuralSnapshot } from './contracts'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  login: vi.fn(),
}))

vi.mock('../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../auth/AuthProvider', () => ({
  useAuth: () => ({
    authMode: 'authenticated',
    getAccessToken: mocks.getAccessToken,
    login: mocks.login,
  }),
}))
vi.mock('../extus/ui/ViewerTab', () => ({
  LatestModelViewer: ({
    externalSelectedNodeIds,
    structuralOverlays,
  }: {
    externalSelectedNodeIds?: string[]
    structuralOverlays?: Array<{
      mode?: string
      status?: string
      stations: unknown[]
      loadArrows?: unknown[]
      nodes?: unknown[]
      reactions?: unknown[]
    }>
  }) => (
    <div>
      Viewer selection: {externalSelectedNodeIds?.join(',')}
      {' · '}
      Ribbon stations: {
        structuralOverlays?.reduce(
          (count, overlay) => count + overlay.stations.length,
          0,
        ) || 0
      }
      {' · '}
      Ribbon mode: {structuralOverlays?.[0]?.mode}
      {' · '}
      Ribbon status: {structuralOverlays?.[0]?.status}
      {' · '}
      Load arrows: {
        structuralOverlays?.reduce(
          (count, overlay) => count + (overlay.loadArrows?.length ?? 0),
          0,
        ) || 0
      }
      {' · '}
      Nodes: {
        structuralOverlays?.reduce(
          (count, overlay) => count + (overlay.nodes?.length ?? 0),
          0,
        ) || 0
      }
      {' · '}
      Reactions: {
        structuralOverlays?.reduce(
          (count, overlay) => count + (overlay.reactions?.length ?? 0),
          0,
        ) || 0
      }
    </div>
  ),
}))

const capture: ProjectStructuralCapture = {
  schema_version: '0.1',
  project_name: 'structural_test',
  design_hash: 'abc123',
  title: 'Structural Workbench — C100 wall connection microcosm',
  authoring_mode: 'generated',
  design_basis: {
    framework_id: 'SCI-P399',
    framework_label: 'SCI P399 verification process',
    framework_reference: 'Table 3.1 and Sections 4–12',
    jurisdiction: 'Australia',
    analysis_method: '3D first-order elastic frame analysis',
    standards: { wind: 'AS/NZS 1170.2 test mapping' },
  },
  components: [
    {
      id: 'sheet',
      label: 'Custom Orb roofing iron',
      kind: 'surface',
      visual_node_id: 'sheet',
      grounded: false,
      part_number: 'CUSTOM-ORB',
    },
    {
      id: 'screws',
      label: 'Roof sheet Tek screws',
      kind: 'connector',
      visual_node_id: 'screws',
      grounded: false,
      part_number: 'TEK',
    },
    {
      id: 'purlin',
      label: 'Lysaght C10019 purlin',
      kind: 'member',
      visual_node_id: 'purlin',
      grounded: false,
      part_number: 'C10019',
    },
    {
      id: 'gpb',
      label: 'Lysaght 100GPB bracket',
      kind: 'support',
      visual_node_id: 'gpb',
      grounded: false,
      part_number: '100GPB',
    },
    {
      id: 'anchors',
      label: 'M12 masonry anchor bolts',
      kind: 'connector',
      visual_node_id: 'anchors',
      grounded: false,
      part_number: 'M12X100',
    },
    {
      id: 'block',
      label: 'Grounded concrete block',
      kind: 'ground',
      visual_node_id: 'block',
      grounded: true,
      part_number: null,
    },
  ],
  connections: [
    {
      id: 'sheet-purlin',
      label: 'Roofing iron fixed to C100 flange',
      from_component_id: 'sheet',
      to_component_id: 'purlin',
      connector_component_ids: ['screws'],
      transfers: ['wind_normal', 'force', 'shear'],
    },
    {
      id: 'purlin-gpb',
      label: 'C100 web bolted to 100GPB',
      from_component_id: 'purlin',
      to_component_id: 'gpb',
      connector_component_ids: [],
      transfers: ['force', 'shear', 'moment'],
    },
    {
      id: 'gpb-ground',
      label: '100GPB anchored to concrete',
      from_component_id: 'gpb',
      to_component_id: 'block',
      connector_component_ids: ['anchors'],
      transfers: ['force', 'shear', 'moment'],
    },
  ],
  loads: [
    {
      id: 'wind',
      label: 'Illustrative inward wind pressure on roofing iron',
      case: 'wind',
      case_id: 'case-wind-inward',
      component_id: 'sheet',
      pressure_kPa: 0.8,
      area_m2: 0.9144,
      direction: { x: 0, y: -1, z: 0 },
      provenance: 'Illustrative parser example',
    },
  ],
  load_paths: [
    {
      load_id: 'wind',
      status: 'complete',
      component_ids: ['sheet', 'purlin', 'gpb', 'block'],
      connection_ids: ['sheet-purlin', 'purlin-gpb', 'gpb-ground'],
      grounded_component_id: 'block',
      detail: 'Load reaches grounded component Grounded concrete block.',
    },
  ],
  analysis: null,
  capabilities: [
    {
      id: 'capture',
      label: 'Design capture',
      status: 'online',
      detail: 'Parsed.',
    },
    {
      id: 'checks',
      label: 'Member checks',
      status: 'pending',
      detail: 'Not solved.',
    },
  ],
  warnings: ['LOAD PATH CAPTURE ONLY'],
}

const analysis: StructuralSnapshot = {
  schema_version: '1.0',
  mode: 'design',
  title: capture.title,
  subtitle: 'Active-project first-order elastic member demand',
  source: {
    kind: 'design',
    label: 'structural_test',
    design_id: 'structural_test',
    design_hash: 'abc123',
  },
  design_basis: capture.design_basis,
  units: {
    length: 'm',
    force: 'kN',
    moment: 'kN.m',
    displacement: 'mm',
    render_length: 'mm',
  },
  nodes: [
    {
      id: 'purlin-start',
      label: 'Purlin start',
      position: { x: 0, y: 0, z: 0 },
      restraints: { dx: true, dy: true, dz: true, rx: true, ry: true, rz: true },
      visual_node_id: 'purlin',
    },
    {
      id: 'purlin-end',
      label: 'Purlin end',
      position: { x: 0, y: 0, z: 1.6 },
      restraints: { dx: false, dy: false, dz: false, rx: false, ry: false, rz: false },
      visual_node_id: 'purlin',
    },
  ],
  members: [
    {
      id: 'purlin-axis',
      label: 'Lysaght C10019 purlin',
      start_node_id: 'purlin-start',
      end_node_id: 'purlin-end',
      section_id: 'c10019',
      material_id: 'steel',
      visual_node_id: 'purlin',
    },
  ],
  sections: [
    {
      id: 'c10019',
      label: 'C10019',
      area_m2: 409e-6,
      iy_m4: 142000e-12,
      iz_m4: 673000e-12,
      torsion_j_m4: 492e-12,
      mass_kg_m: 3.29,
      bending_reference_kNm: 5.535,
      bending_reference_axis: 'local_z',
      bending_reference_basis: 'Nominal Zxe × fy yield reference only.',
      catalog: {
        catalog_id: 'lysaght-zc-v2',
        catalog_version: '2.0',
        section_key: 'C10019 (100x1.9)',
        source: 'Lysaght guide p.7-8',
        record_sha256: 'a'.repeat(64),
        axis_mapping: {
          local_y_inertia: 'Iy_mm4',
          local_z_inertia: 'Ix_mm4',
        },
        properties: {
          A_mm2: 409,
          fy_MPa: 450,
          Zxe_mm3: 12300,
        },
      },
    },
  ],
  materials: [
    {
      id: 'steel',
      label: 'Steel',
      elastic_modulus_kN_m2: 200000000,
      shear_modulus_kN_m2: 80000000,
      poisson_ratio: 0.3,
      density_kg_m3: 7850,
    },
  ],
  load_cases: [
    { id: 'case-wind-inward', label: 'Inward wind pressure', category: 'wind' },
    { id: 'case-wind-outward', label: 'Outward wind suction', category: 'wind' },
    { id: 'case-dead', label: 'Dead load', category: 'dead' },
  ],
  load_combinations: [
    {
      id: 'SLS-1.0',
      label: 'Serviceability actions',
      limit_state: 'serviceability',
      factors: { 'case-wind-inward': 1 },
    },
    {
      id: 'SLS-G',
      label: 'Permanent actions',
      limit_state: 'serviceability',
      factors: { 'case-dead': 1 },
    },
    {
      id: 'DEMO-OVERLOAD',
      label: 'Deliberate overload',
      limit_state: 'ultimate',
      factors: { 'case-dead': 1, 'case-wind-inward': 12 },
    },
  ],
  loads: [],
  member_loads: [0.35, 0.8, 1.25].map((distance, index) => ({
    id: `wind-${index}`,
    label: `Wind ${index}`,
    member_id: 'purlin-axis',
    case_id: 'case-wind-inward',
    distance_m: distance,
    force: { x: 0, y: -0.24384, z: 0 },
    moment: { x: 0, y: 0, z: 0 },
    source_load_id: 'wind',
    provenance: 'Equal screws',
  })),
  member_distributed_loads: [],
  reactions: [
    {
      node_id: 'purlin-start',
      combination_id: 'SLS-1.0',
      force: { x: 0, y: 0.73152, z: 0 },
      moment: { x: -0.585216, y: 0, z: 0 },
    },
  ],
  member_results: [
    {
      member_id: 'purlin-axis',
      combination_id: 'SLS-1.0',
      max_moment_kNm: 0.585216,
      max_shear_kN: 0.73152,
      max_axial_kN: 0,
      max_displacement_mm: 2.61231263,
    },
  ],
  member_diagrams: [
    {
      member_id: 'purlin-axis',
      visual_node_id: 'purlin',
      stations: [
        {
          distance_m: 0,
          position: { x: 0, y: 0, z: 0 },
          moment_kNm: { x: -0.585216, y: 0, z: 0 },
          shear_kN: { x: 0, y: 0.73152, z: 0 },
          displacement_mm: { x: 0, y: 0, z: 0 },
        },
        {
          distance_m: 1.6,
          position: { x: 0, y: 0, z: 1.6 },
          moment_kNm: { x: 0, y: 0, z: 0 },
          shear_kN: { x: 0, y: 0, z: 0 },
          displacement_mm: { x: 0, y: -2.61231263, z: 0 },
        },
      ],
    },
  ],
  member_checks: [
    {
      member_id: 'purlin-axis',
      label: 'C100 bending demand',
      demand_kNm: 0.585216,
      capacity_kNm: 5.535,
      utilisation: 0.1057301,
      status: 'not_checked',
      basis: 'RENDERER REFERENCE ONLY — nominal Zxe × fy.',
    },
  ],
  serviceability_checks: [
    {
      member_id: 'purlin-axis',
      label: 'C100 deflection',
      combination_id: 'SLS-1.0',
      displacement_mm: 2.61231263,
      limit_mm: 6.4,
      utilisation: 0.408,
      status: 'pass',
      basis: 'Project demonstration criterion L/250.',
    },
  ],
  load_summary: {
    member_mass_kg: 0,
    self_weight_kN: 0,
    additional_dead_load_kN: 0,
    imposed_load_kN: 0,
    wind_load_kN: 0.73152,
  },
  equilibrium: {
    force_residual_kN: { x: 0, y: 0, z: 0 },
    moment_residual_kNm: { x: 0, y: 0, z: 0 },
    tolerance: 1e-8,
    status: 'pass',
  },
  solver: {
    name: 'PyNiteFEA',
    version: '2.4.1',
    analysis: '3D first-order elastic',
    combination_id: 'SLS-1.0',
  },
  verification_stages: [
    {
      id: 'geometry',
      order: 1,
      label: 'Geometry',
      p399_reference: '§3, §6.1',
      status: 'pass',
      summary: 'One member, two nodes, one support.',
      sheet_ids: ['sheet-p399-geometry'],
      blocking_stage_ids: [],
    },
    {
      id: 'stability',
      order: 5,
      label: 'Global stability',
      p399_reference: '§7.2–§7.8',
      status: 'blocked',
      summary: 'Imperfections and second-order effects are missing.',
      sheet_ids: [],
      blocking_stage_ids: ['analysis'],
    },
  ],
  calculation_sheets: [
    {
      id: 'sheet-p399-geometry',
      stage_id: 'geometry',
      title: 'Geometry and analytical scheme',
      status: 'pass',
      p399_reference: 'SCI P399 Sections 3 and 6.1',
      purpose: 'Prove which design.py geometry became nodes, members, and supports.',
      assumptions: ['Fixed base is an authored analysis assumption.'],
      inputs: [
        {
          symbol: 'n_member',
          label: 'Analytical members',
          value: 1,
          unit: null,
          source: 'design.py member_axis',
        },
      ],
      equations: [
        {
          label: 'Purlin length',
          expression: 'L = |x_j - x_i|',
          substitution: '|1.6 - 0|',
          result: 1.6,
          unit: 'm',
        },
      ],
      outputs: [],
      references: ['SCI P399'],
      related_member_ids: ['purlin-axis'],
      related_node_ids: ['purlin-start', 'purlin-end'],
      related_load_case_ids: [],
      related_combination_ids: [],
    },
  ],
  capabilities: [
    {
      id: 'solver',
      label: 'PyNite demand',
      status: 'online',
      detail: 'Solved.',
    },
  ],
  warnings: ['ELASTIC MEMBER DEMAND ONLY'],
}

const overloadAnalysis: StructuralSnapshot = {
  ...analysis,
  member_results: analysis.member_results.map((result) => ({
    ...result,
    combination_id: 'DEMO-OVERLOAD',
    max_moment_kNm: 7.0226,
  })),
  member_checks: analysis.member_checks.map((check) => ({
    ...check,
    demand_kNm: 7.0226,
    utilisation: 1.2688,
    status: 'not_checked',
  })),
  solver: {
    ...analysis.solver,
    combination_id: 'DEMO-OVERLOAD',
  },
}

describe('StructuralWorkbench', () => {
  afterEach(cleanup)

  beforeEach(() => {
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      new Response(JSON.stringify(url.includes('/active/analysis') ? analysis : capture), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))
  })

  it('uses the active project capture and never presents connectivity as a capacity check', async () => {
    render(<StructuralWorkbench isActive />)

    await waitFor(() => {
      expect(screen.getByText('structural_test')).toBeInTheDocument()
    })
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/structural/active/capture',
      mocks.getAccessToken,
    )
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/structural/active/analysis',
      mocks.getAccessToken,
    )
    expect(screen.getAllByText('Custom Orb roofing iron').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Lysaght C10019 purlin').length).toBeGreaterThan(0)
    expect(screen.getByText('Reaches ground')).toBeInTheDocument()
    expect(screen.getByText('0.732 kN')).toBeInTheDocument()
    expect(screen.getByText('HANDLE-AUTHORED')).toBeInTheDocument()
    expect(screen.getByText('Inward wind pressure')).toBeInTheDocument()
    expect(screen.getByText('Outward wind suction')).toBeInTheDocument()
    expect(screen.getByText('Design capacity status: NOT CHECKED')).toBeInTheDocument()
    expect(screen.getByText(/Ribbon stations: 2/)).toBeInTheDocument()
    expect(screen.getByText(/Ribbon mode: moment/)).toBeInTheDocument()
    expect(screen.getByText(/Ribbon status: not_checked/)).toBeInTheDocument()
    expect(screen.getByText(/Load arrows: 3/)).toBeInTheDocument()
    expect(screen.getByText(/Nodes: 2/)).toBeInTheDocument()
    expect(screen.getByText(/Reactions: 1/)).toBeInTheDocument()
    expect(screen.getByText('P399 verification spine')).toBeInTheDocument()
    expect(screen.getByText('Geometry and analytical scheme')).toBeInTheDocument()
    expect(screen.getByText(/Global stability/)).toBeInTheDocument()
    expect(screen.getByText('0.5852 kN·m')).toBeInTheDocument()
    expect(screen.getByText('Equilibrium pass')).toBeInTheDocument()
    expect(screen.getByText('Validated catalogue section')).toBeInTheDocument()
    expect(screen.getByText('C10019 (100x1.9)')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: /Grounded concrete block/ })[0]!)
    expect(screen.getByText(/Viewer selection: block/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'displacement' }))
    expect(screen.getByText(/Ribbon mode: displacement/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'moment' }))
    expect(screen.getByText(/Ribbon mode: moment/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Load combination'), {
      target: { value: 'SLS-G' },
    })
    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/structural/active/analysis?combination_id=SLS-G',
        mocks.getAccessToken,
      )
    })
  })

  it('reloads the structural declaration when the shared active project changes', async () => {
    render(<StructuralWorkbench isActive />)
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalled())
    const requestCountBeforeChange = mocks.apiFetch.mock.calls.length

    window.dispatchEvent(
      new CustomEvent('tertius:active-project-changed', {
        detail: { activeProject: 'another-project' },
      }),
    )

    await waitFor(() => {
      expect(mocks.apiFetch.mock.calls.length).toBeGreaterThan(requestCountBeforeChange)
    })
  })

  it('keeps an exceeded renderer reference not-checked until P399 stages pass', async () => {
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      new Response(JSON.stringify(
        url.includes('combination_id=DEMO-OVERLOAD')
          ? overloadAnalysis
          : url.includes('/active/analysis')
            ? analysis
            : capture,
      ), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))
    render(<StructuralWorkbench isActive />)
    await waitFor(() => {
      expect(screen.getAllByText('structural_test').length).toBeGreaterThan(0)
    })

    fireEvent.change(screen.getByLabelText('Load combination'), {
      target: { value: 'DEMO-OVERLOAD' },
    })

    expect(await screen.findByText(/Ribbon status: not_checked/)).toBeInTheDocument()
    expect(screen.getByText('126.9% reference utilisation')).toBeInTheDocument()
  })
})
