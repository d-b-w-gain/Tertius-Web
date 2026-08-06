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
      building_classification: '10a',
      importance_level: '2',
      design_life_years: 50,
      jurisdiction: 'Australia / New South Wales',
      standards: {
        combinations: 'AS/NZS 1170.0:2002',
        permanent_and_imposed: 'AS/NZS 1170.1:2002',
        wind: 'AS/NZS 1170.2:2021',
        confirmed: true,
      },
    },
    location: {
      address: '14 Porter St',
      latitude: -34.4125046,
      longitude: 150.8885637,
    },
    structure: {
      footprint_length_m: 12,
      footprint_width_m: 6,
      front_bearing_degrees: 20,
      front_definition: 'long_wall_normal',
      orientation_status: 'verified',
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
      cardinal_direction_multipliers: {
        n: 0.9, ne: 0.85, e: 0.8, se: 0.85,
        s: 0.95, sw: 1, w: 0.9, nw: 0.85,
      },
      shielding_multiplier: 1,
      topographic_multiplier: 1,
      climate_change_multiplier: null,
      action_envelope: {
        enclosure: 'enclosed',
        openings_operating_state: 'normally_closed',
        opening_capacity_status: 'unverified',
        coefficient_selection_policy: 'worst_available_credible',
      },
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
    structure: {
      footprint_length_m: 12,
      footprint_width_m: 6,
      front_bearing_degrees: 20,
      front_definition: 'long_wall_normal',
      orientation_status: 'verified',
    },
    directional_mode: 'cardinal',
    cardinal_wind_speeds: [
      { direction: 'N', bearing_degrees: 0, direction_multiplier: 0.9, site_wind_speed_m_s: 30.375, q_z_kPa: 0.553584 },
      { direction: 'NE', bearing_degrees: 45, direction_multiplier: 0.85, site_wind_speed_m_s: 28.6875, q_z_kPa: 0.493807 },
      { direction: 'E', bearing_degrees: 90, direction_multiplier: 0.8, site_wind_speed_m_s: 27, q_z_kPa: 0.4374 },
      { direction: 'SE', bearing_degrees: 135, direction_multiplier: 0.85, site_wind_speed_m_s: 28.6875, q_z_kPa: 0.493807 },
      { direction: 'S', bearing_degrees: 180, direction_multiplier: 0.95, site_wind_speed_m_s: 32.0625, q_z_kPa: 0.616802 },
      { direction: 'SW', bearing_degrees: 225, direction_multiplier: 1, site_wind_speed_m_s: 33.75, q_z_kPa: 0.683438 },
      { direction: 'W', bearing_degrees: 270, direction_multiplier: 0.9, site_wind_speed_m_s: 30.375, q_z_kPa: 0.553584 },
      { direction: 'NW', bearing_degrees: 315, direction_multiplier: 0.85, site_wind_speed_m_s: 28.6875, q_z_kPa: 0.493807 },
    ],
    building_face_wind_speeds: [
      { face: 'front', bearing_degrees: 20, site_wind_speed_m_s: 30.375, q_z_kPa: 0.553584, governing_cardinal_direction: 'N', contributing_cardinal_directions: ['N', 'NE'] },
      { face: 'right', bearing_degrees: 110, site_wind_speed_m_s: 28.6875, q_z_kPa: 0.493807, governing_cardinal_direction: 'SE', contributing_cardinal_directions: ['E', 'SE'] },
      { face: 'back', bearing_degrees: 200, site_wind_speed_m_s: 33.75, q_z_kPa: 0.683438, governing_cardinal_direction: 'SW', contributing_cardinal_directions: ['S', 'SW'] },
      { face: 'left', bearing_degrees: 290, site_wind_speed_m_s: 30.375, q_z_kPa: 0.553584, governing_cardinal_direction: 'W', contributing_cardinal_directions: ['W', 'NW'] },
    ],
    governing_cardinal_direction: 'SW',
    verifier_hash: 'verify123',
    formula: 'qz',
    verify_against: 'project standard',
    action_envelope: {
      enclosure: 'enclosed',
      openings_operating_state: 'normally_closed',
      opening_capacity_status: 'unverified',
      coefficient_selection_policy: 'worst_available_credible',
    },
    standard_table_evidence: {
      dataset_version: 'key-changes-2021-v1',
      standard_reference: 'AS/NZS 1170.2:2021',
      source: {
        title: 'Key changes to AS/NZS 1170.2-2021',
        author: 'Chris Hackney',
        published_date: '2021-10-28',
        filename: 'Key-Changes-to-AS-NZS-1170.2-2021.pdf',
        sha256: 'fixture-sha',
        source_type: 'secondary_summary_presentation',
      },
      verification: {
        status: 'requires_licensed_standard_check',
        message: 'Verify against the licensed standard.',
      },
      region: 'A2',
      direction_multipliers: {
        n: 0.85, ne: 0.75, e: 0.85, se: 0.95,
        s: 0.95, sw: 0.95, w: 1, nw: 0.95,
      },
      climate_change_multiplier: 1,
      applied_tables: [
        { id: 'md', table_number: '3.2(A)', title: 'Direction Md', source_page: 5, applicability: [] },
        { id: 'mc', table_number: '3.3', title: 'Climate Mc', source_page: 6, applicability: [] },
      ],
      report_table_index: [
        { id: 'md', table_number: '3.2(A)', title: 'Direction Md', source_page: 5 },
        { id: 'mc', table_number: '3.3', title: 'Climate Mc', source_page: 6 },
      ],
    },
  },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('SiteWorkbench', () => {
  it('shows NCC classification choices and identifies the exact missing confirmation', async () => {
    mocks.apiFetch.mockImplementation(async (url: string) => new Response(JSON.stringify(
      url.endsWith('/gis/health') ? {
        status: 'ready', free_bytes: 1_000_000, total_bytes: 2_000_000,
      } : {
      ...response,
      site_dict: {
        ...response.site_dict,
        project_basis: {
          ...response.site_dict.project_basis,
          standards: { ...response.site_dict.project_basis.standards, confirmed: false },
        },
      },
      calculation: { ...response.calculation, site_ready: false },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    render(<SiteWorkbench isActive />)

    expect(await screen.findByRole('option', {
      name: 'Class 10a — non-habitable garage, carport or shed',
    })).toBeInTheDocument()
    expect(screen.getByText('NCC working recommendation: Importance Level 2')).toBeInTheDocument()
    expect(screen.getByText('Site Explorer')).toBeInTheDocument()
    expect(screen.getByRole('separator', { name: 'Resize derived-results panel' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide derived-results panel' })).toBeInTheDocument()
    expect(screen.getByText('Structure orientation & directional wind')).toBeInTheDocument()
    expect(screen.getByText('Front bearing 20° true')).toBeInTheDocument()
    expect(screen.getByText('Governing SW · qz 0.683 kPa')).toBeInTheDocument()
    expect(screen.getByRole('option', {
      name: 'Auto-select worst available credible service case',
    })).toBeInTheDocument()
    expect(screen.getByText('Missing: confirm these editions for this project')).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'confirm the three selected action-standard editions',
    })).toBeInTheDocument()
  })

  it('creates tertius_site.py and emits a structural refresh without compiling CAD', async () => {
    mocks.apiFetch.mockImplementation(async (url: string, _token: unknown, init?: RequestInit) => (
      new Response(JSON.stringify(url.endsWith('/gis/health') ? {
        status: 'ready', free_bytes: 1_000_000, total_bytes: 2_000_000,
      } : {
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

    expect(mocks.apiFetch).toHaveBeenCalledTimes(3)
    const saveCall = mocks.apiFetch.mock.calls.find((call) => call[2]?.method === 'PUT')
    expect(saveCall).toBeDefined()
    expect(saveCall?.[0]).toBe('/api/site/active')
    expect(saveCall?.[2]).toMatchObject({ method: 'PUT' })
    expect(
      mocks.apiFetch.mock.calls.some(([url]) => String(url).includes('/compile')),
    ).toBe(false)
  })

  it('authors a true-north bearing and expands the fallback Md into eight cardinal inputs', async () => {
    mocks.apiFetch.mockImplementation(async (url: string) => new Response(JSON.stringify(
      url.endsWith('/gis/health')
        ? { status: 'ready', free_bytes: 1_000_000, total_bytes: 2_000_000 }
        : url.endsWith('/calculate')
          ? response.calculation
          : {
            ...response,
            site_dict: {
              ...response.site_dict,
              structure: { ...response.site_dict.structure, orientation_status: 'suggested' },
              wind: {
                ...response.site_dict.wind,
                cardinal_direction_multipliers: null,
              },
            },
          },
    ), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    render(<SiteWorkbench isActive />)

    const northMultiplier = await screen.findByRole('spinbutton', {
      name: 'N direction multiplier',
    })
    expect(northMultiplier).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Enable cardinal inputs' }))
    expect(northMultiplier).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Front bearing degrees true' }), {
      target: { value: '135' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }))

    await waitFor(() => expect(
      mocks.apiFetch.mock.calls.some((call) => call[2]?.method === 'POST'),
    ).toBe(true))
    const calculateCall = mocks.apiFetch.mock.calls.find((call) => call[2]?.method === 'POST')
    const submitted = JSON.parse(String(calculateCall?.[2]?.body))
    expect(submitted.structure.front_bearing_degrees).toBe(135)
    expect(submitted.wind.cardinal_direction_multipliers).toEqual({
      n: 1, ne: 1, e: 1, se: 1, s: 1, sw: 1, w: 1, nw: 1,
    })
  })

  it('applies digitised regional Md and Mc values without marking the tables verified', async () => {
    mocks.apiFetch.mockImplementation(async (url: string) => (
      new Response(JSON.stringify(url.endsWith('/calculate')
        ? response.calculation
        : url.endsWith('/gis/health')
          ? { status: 'ready', free_bytes: 1_000_000, total_bytes: 2_000_000 }
          : response), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ))

    render(<SiteWorkbench isActive />)

    const apply = await screen.findByRole('button', {
      name: 'Use Table 3.2(A) Md and Table 3.3 Mc',
    })
    fireEvent.click(apply)
    expect(screen.getByText(/licensed-standard verification is still required/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }))

    await waitFor(() => expect(
      mocks.apiFetch.mock.calls.some((call) => String(call[0]).endsWith('/calculate')),
    ).toBe(true))
    const calculateCall = mocks.apiFetch.mock.calls.find((call) => String(call[0]).endsWith('/calculate'))
    const submitted = JSON.parse(String(calculateCall?.[2]?.body))
    expect(submitted.wind.cardinal_direction_multipliers).toEqual({
      n: 0.85, ne: 0.75, e: 0.85, se: 0.95,
      s: 0.95, sw: 0.95, w: 1, nw: 0.95,
    })
    expect(submitted.wind.climate_change_multiplier).toBe(1)
    expect(submitted.wind.table_status).toBe('starter')
  })
})
