import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StructuralWorkbench } from './StructuralWorkbench'
import type { ProjectStructuralCapture } from './contracts'

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
  }: {
    externalSelectedNodeIds?: string[]
  }) => <div>Viewer selection: {externalSelectedNodeIds?.join(',')}</div>,
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

describe('StructuralWorkbench', () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockResolvedValue(
      new Response(JSON.stringify(capture), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
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
    expect(screen.getAllByText('Custom Orb roofing iron').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Lysaght C10019 purlin').length).toBeGreaterThan(0)
    expect(screen.getByText('Reaches ground')).toBeInTheDocument()
    expect(screen.getByText('0.732 kN')).toBeInTheDocument()
    expect(screen.getByText('HANDLE-AUTHORED')).toBeInTheDocument()
    expect(screen.getByText('Capacity status: NOT CHECKED')).toBeInTheDocument()
    expect(screen.getByText('Viewer selection: sheet')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: /Grounded concrete block/ })[0]!)
    expect(screen.getByText('Viewer selection: block')).toBeInTheDocument()
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
