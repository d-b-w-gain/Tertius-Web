import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
    structuralOverlay,
  }: {
    externalSelectedNodeIds?: string[]
    structuralOverlay?: { stations: unknown[] }
  }) => (
    <div>
      Viewer selection: {externalSelectedNodeIds?.join(',')}
      {' · '}
      Ribbon stations: {structuralOverlay?.stations.length || 0}
    </div>
  ),
}))

const capture: ProjectStructuralCapture = {
  schema_version: '0.1',
  project_name: 'structural_test',
  design_hash: 'abc123',
  title: 'Structural Workbench — C100 wall connection microcosm',
  authoring_mode: 'generated',
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
  load_cases: [{ id: 'case-wind', label: 'Wind load', category: 'wind' }],
  loads: [],
  member_loads: [0.35, 0.8, 1.25].map((distance, index) => ({
    id: `wind-${index}`,
    label: `Wind ${index}`,
    member_id: 'purlin-axis',
    case_id: 'case-wind',
    distance_m: distance,
    force: { x: 0, y: -0.24384, z: 0 },
    moment: { x: 0, y: 0, z: 0 },
    source_load_id: 'wind',
    provenance: 'Equal screws',
  })),
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
      capacity_kNm: null,
      utilisation: null,
      status: 'not_checked',
      basis: 'Elastic demand only — no AS 4600 member capacity is connected.',
    },
  ],
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

describe('StructuralWorkbench', () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      new Response(JSON.stringify(url.endsWith('/active/analysis') ? analysis : capture), {
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
    expect(screen.getByText('Capacity status: NOT CHECKED')).toBeInTheDocument()
    expect(screen.getByText(/Ribbon stations: 2/)).toBeInTheDocument()
    expect(screen.getByText('0.5852 kN·m')).toBeInTheDocument()
    expect(screen.getByText('Equilibrium pass')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: /Grounded concrete block/ })[0]!)
    expect(screen.getByText(/Viewer selection: block/)).toBeInTheDocument()
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
})
