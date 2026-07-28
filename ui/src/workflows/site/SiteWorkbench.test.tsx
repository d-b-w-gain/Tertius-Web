import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SiteWorkbench } from './SiteWorkbench'
import type { SiteWorkbenchResponse } from './contracts'


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
vi.mock('../structural/WindRegionMap', () => ({
  WindRegionMap: () => <div>Wind map</div>,
}))

const response: SiteWorkbenchResponse = {
  project_name: 'structural_test',
  filename: 'tertius_site.py',
  exists: false,
  site_dict: {
    schema_version: '1.0',
    project_basis: {
      building_use: 'Private shed',
      building_classification: 'Class 10a',
      importance_level: '2',
      design_life_years: 50,
      jurisdiction: 'Australia / New South Wales',
      standards: {
        combinations: 'AS/NZS 1170.0',
        permanent_and_imposed: 'AS/NZS 1170.1',
        wind: 'AS/NZS 1170.2:2021',
        confirmed: true,
      },
    },
    location: {
      address: '14 Porter St',
      latitude: -34.4125046,
      longitude: 150.8885637,
    },
    wind: {
      basis_id: 'project-site-wind',
      region: 'A2',
      region_area: 'NSW',
      region_source: 'Test overlay',
      region_approximate: true,
      region_status: 'verified',
      table_status: 'verified',
      terrain_category: '3',
      annual_probability_uls: '',
      reference_height_m: 1.6,
      direction_multiplier: 1,
      shielding_multiplier: 1,
      topographic_multiplier: 1,
      climate_change_multiplier: null,
    },
  },
  source: "site_dict = {'schema_version': '1.0'}\n",
  calculation: {
    revision: 'abc123',
    site_ready: true,
    standard: 'AS/NZS 1170.2:2021',
    table_version: 'starter',
    region: 'A2',
    annual_recurrence_interval_years: 500,
    regional_wind_speed_m_s: 45,
    terrain_height_multiplier: 0.75,
    site_wind_speed_m_s: 33.75,
    q_z_kPa: 0.683438,
    verifier_hash: 'verify123',
    formula: 'qz',
    verify_against: 'project standard',
  },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('SiteWorkbench', () => {
  it('creates tertius_site.py and emits a structural refresh without compiling CAD', async () => {
    mocks.apiFetch.mockImplementation(async (_url: string, _token: unknown, init?: RequestInit) => (
      new Response(JSON.stringify({
        ...response,
        exists: init?.method === 'PUT',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ))
    const changed = vi.fn()
    window.addEventListener('tertius:site-basis-changed', changed)
    try {
      render(<SiteWorkbench isActive />)

      expect(await screen.findByText('Wind map')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Create tertius_site.py' }))

      await waitFor(() => expect(changed).toHaveBeenCalledTimes(1))
    } finally {
      window.removeEventListener('tertius:site-basis-changed', changed)
    }

    expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    const saveCall = mocks.apiFetch.mock.calls[1]
    expect(saveCall).toBeDefined()
    expect(saveCall?.[0]).toBe('/api/site/active')
    expect(saveCall?.[2]).toMatchObject({ method: 'PUT' })
    expect(
      mocks.apiFetch.mock.calls.some(([url]) => String(url).includes('/compile')),
    ).toBe(false)
  })
})
