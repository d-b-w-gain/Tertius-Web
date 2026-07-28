import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SiteWindPanel } from './SiteWindPanel'
import type { ProjectStructuralCapture } from './contracts'


const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  onCompiled: vi.fn(),
}))

vi.mock('../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('./WindRegionMap', () => ({
  WindRegionMap: () => <div>Wind region map</div>,
}))

const capture: ProjectStructuralCapture = {
  schema_version: '0.1',
  project_name: 'structural_test',
  design_hash: 'site-hash',
  title: 'Site wind test',
  authoring_mode: 'generated',
  design_basis: null,
  wind_action_bases: [
    {
      id: 'site-wind-porter',
      site_address: '14 Porter st, North Wollongong, NSW, 2500',
      latitude: -34.4125046,
      longitude: 150.8885637,
      region: 'C',
      region_area: 'NSW',
      region_source: 'FBD manual selection',
      region_approximate: true,
      region_status: 'suggested',
      standard: 'AS/NZS 1170.2:2021',
      table_version: 'AS1170.2-2021-starter-v1',
      table_status: 'starter',
      importance_level: '2',
      annual_recurrence_interval_years: 500,
      terrain_category: '3',
      reference_height_m: 1.6,
      regional_wind_speed_m_s: 66,
      climate_change_multiplier: 1.05,
      direction_multiplier: 1,
      terrain_height_multiplier: 0.75,
      shielding_multiplier: 1,
      topographic_multiplier: 1,
      site_wind_speed_m_s: 51.975,
      q_z_kPa: 1.62084,
      verifier_hash: 'e05a40abf7bc',
      provenance: 'FBD test fixture',
    },
  ],
  components: [],
  connections: [],
  loads: [],
  load_paths: [],
  analysis: null,
  capabilities: [],
  warnings: [],
}

describe('SiteWindPanel', () => {
  afterEach(cleanup)

  beforeEach(() => {
    mocks.apiFetch.mockReset()
    mocks.onCompiled.mockReset()
    mocks.apiFetch.mockResolvedValue(new Response(JSON.stringify({
      site_address: '14 Porter st, North Wollongong, NSW, 2500',
      latitude: -34.4125046,
      longitude: 150.8885637,
      region_area: 'NSW',
      region_source: 'Geoscience Australia',
      region_approximate: true,
      region_status: 'suggested',
      suggested_region: 'A2',
      selected_region: 'C',
      region_conflict: true,
      region_detail: 'Suggested from simplified overlay.',
      standard: 'AS/NZS 1170.2:2021',
      table_version: 'AS1170.2-2021-starter-v1',
      table_status: 'starter',
      region: 'C',
      terrain_category: '3',
      importance_level: '2',
      annual_recurrence_interval_years: 500,
      reference_height_m: 1.6,
      regional_wind_speed_m_s: 66,
      climate_change_multiplier: 1.05,
      direction_multiplier: 1,
      terrain_height_multiplier: 0.75,
      shielding_multiplier: 1,
      topographic_multiplier: 1,
      site_wind_speed_m_s: 51.975,
      q_z_kPa: 1.62084,
      verifier_hash: 'e05a40abf7bc',
      formula: 'q_z = 0.5 rho V_sit^2',
      verify_against: 'Verify against Standard.',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  })

  it('shows the recovered FBD site and exposes a region conflict before apply', async () => {
    render(
      <SiteWindPanel
        capture={capture}
        serverUrl="/api/structural"
        intusServerUrl="/api/intus"
        artusServerUrl="/api/artus"
        getAccessToken={mocks.getAccessToken}
        onCompiled={mocks.onCompiled}
      />,
    )

    expect(screen.getByDisplayValue('14 Porter st, North Wollongong, NSW, 2500')).toBeInTheDocument()
    expect(screen.getByText('Wind region map')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Calculate draft' }))

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/structural/wind/site',
        mocks.getAccessToken,
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(await screen.findByText(/Selected C · map A2/)).toBeInTheDocument()
    expect(screen.getAllByText(/1.620840 kPa/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Conflict: map suggests A2; design selects C/)).toBeInTheDocument()
  })
})
