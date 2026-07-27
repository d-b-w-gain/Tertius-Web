import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StructuralWorkbench } from './StructuralWorkbench'
import type { StructuralSnapshot } from './contracts'

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
  ModelViewerCanvas: ({ externalSelectedNodeIds }: { externalSelectedNodeIds?: string[] }) => (
    <div>Viewer selection: {externalSelectedNodeIds?.join(',')}</div>
  ),
}))

const snapshot: StructuralSnapshot = {
  schema_version: '1.0',
  mode: 'fixture',
  title: 'Structural Workbench — Cantilever',
  subtitle: 'Deterministic end-to-end fixture',
  source: {
    kind: 'fixture',
    label: 'Known 2 m cantilever; not a shed design',
    design_id: 'structural-fixture/cantilever-v1',
    design_hash: 'fixture:cantilever-v1',
  },
  units: { length: 'm', force: 'kN', moment: 'kN.m', displacement: 'mm', render_length: 'mm' },
  nodes: [
    {
      id: 'fixture-node-base',
      label: 'Fixed base',
      position: { x: 0, y: 0, z: 0 },
      restraints: { dx: true, dy: true, dz: true, rx: true, ry: true, rz: true },
      visual_node_id: 'fixture-node-base',
    },
    {
      id: 'fixture-node-free',
      label: 'Loaded tip',
      position: { x: 0, y: 0, z: 2 },
      restraints: { dx: false, dy: false, dz: false, rx: false, ry: false, rz: false },
      visual_node_id: 'fixture-node-free',
    },
  ],
  members: [
    {
      id: 'fixture-member-cantilever',
      label: 'Fixture cantilever',
      start_node_id: 'fixture-node-base',
      end_node_id: 'fixture-node-free',
      section_id: 'fixture-section-100x100',
      material_id: 'fixture-material-steel',
      visual_node_id: 'fixture-member-cantilever',
    },
  ],
  sections: [
    {
      id: 'fixture-section-100x100',
      label: '100 × 100 mm fixture section',
      area_m2: 0.01,
      iy_m4: 8.333e-6,
      iz_m4: 8.333e-6,
      torsion_j_m4: 1.667e-5,
    },
  ],
  materials: [
    {
      id: 'fixture-material-steel',
      label: 'Fixture steel',
      elastic_modulus_kN_m2: 200_000_000,
      shear_modulus_kN_m2: 76_923_000,
      poisson_ratio: 0.3,
      density_kg_m3: 7850,
    },
  ],
  load_cases: [{ id: 'fixture-lateral', label: '1 kN lateral tip load', category: 'fixture' }],
  loads: [
    {
      id: 'fixture-load-tip-x',
      label: '+X tip load',
      node_id: 'fixture-node-free',
      case_id: 'fixture-lateral',
      force: { x: 1, y: 0, z: 0 },
      moment: { x: 0, y: 0, z: 0 },
      visual_node_id: 'fixture-load-tip-x',
    },
  ],
  reactions: [
    {
      node_id: 'fixture-node-base',
      combination_id: 'fixture-sls',
      force: { x: -1, y: 0, z: 0 },
      moment: { x: 0, y: -2, z: 0 },
    },
  ],
  member_results: [
    {
      member_id: 'fixture-member-cantilever',
      combination_id: 'fixture-sls',
      max_moment_kNm: 2,
      max_shear_kN: 1,
      max_axial_kN: 0,
      max_displacement_mm: 1.600064,
    },
  ],
  member_checks: [
    {
      member_id: 'fixture-member-cantilever',
      label: 'Illustrative bending check',
      demand_kNm: 2,
      capacity_kNm: 2.5,
      utilisation: 0.8,
      status: 'pass',
      basis: 'Fixture capacity only — no AS 4600 section check',
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
    combination_id: 'fixture-sls',
  },
  capabilities: [
    { id: 'geometry', label: 'Build123D geometry', status: 'online', detail: 'Linked IDs.' },
    { id: 'reports', label: 'Calculation reports', status: 'pending', detail: 'Not connected.' },
  ],
  warnings: ['DEMONSTRATION FIXTURE — NOT FOR DESIGN, CERTIFICATION, OR ORDERING.'],
}

describe('StructuralWorkbench', () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockResolvedValue(new Response(JSON.stringify(snapshot), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  })

  it('shows a traceable solved fixture without implying it is order-ready', async () => {
    render(<StructuralWorkbench isActive />)

    expect(screen.getByText('DEMONSTRATION FIXTURE — NOT FOR DESIGN, CERTIFICATION, OR ORDERING')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText('Fixture cantilever')).toHaveLength(2))
    expect(screen.getByText('PyNiteFEA 2.4.1')).toBeInTheDocument()
    expect(screen.getByText('1.600 mm')).toBeInTheDocument()
    expect(screen.getByText('Equilibrium pass')).toBeInTheDocument()
    expect(screen.getByText('Viewer selection: fixture-member-cantilever')).toBeInTheDocument()
    expect(screen.getByText('Fixture capacity only — no AS 4600 section check')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Applied load/ }))
    expect(screen.getByText('Viewer selection: fixture-load-tip-x')).toBeInTheDocument()
  })
})
