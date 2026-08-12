import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from '../../api/client'
import type { GisDirectionalWindMultiplierEvidence } from './contracts'
import { WindMultiplierEvidencePanel } from './WindMultiplierEvidencePanel'


vi.mock('../../api/client', () => ({ apiFetch: vi.fn() }))

const values = (value: number) => ({
  n: value, ne: value, e: value, se: value,
  s: value, sw: value, w: value, nw: value,
})

const evidence: GisDirectionalWindMultiplierEvidence = {
  schema_version: 'tertius.gis.wind-multipliers.v1',
  evidence_id: 'windv1-df468303892e2859cbbd2ca03ec7f747',
  latitude: -34.4125046,
  longitude: 150.8885637,
  tile_id: 'e150.3512s33.9946',
  terrain_reference_height_m: 10,
  terrain_height_multipliers: values(0.8),
  shielding_multipliers: values(0.9),
  topographic_multipliers: values(1),
  provider: 'Geoscience Australia',
  dataset: 'National wind multiplier dataset',
  dataset_version: 'Wind Multiplier Software 2.0 output (January 2016)',
  licence: 'CC BY 4.0',
  attribution: 'Geoscience Australia; data hosted by NCI',
  source_uri: 'https://thredds.nci.org.au/thredds/catalog/fj6/multipliers/catalog.html',
  method_status: 'indicative_hazard_evidence',
  review_required: true,
  review_note: 'Check applicability before use.',
}

afterEach(() => vi.clearAllMocks())

describe('WindMultiplierEvidencePanel', () => {
  it('automatically applies the eligible GA baseline without a review control', async () => {
    vi.mocked(apiFetch).mockImplementation(async () => new Response(JSON.stringify(evidence), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const onEvidence = vi.fn()

    render(
      <WindMultiplierEvidencePanel
        serverUrl="/api/site"
        getAccessToken={vi.fn()}
        latitude={evidence.latitude}
        longitude={evidence.longitude}
        referenceHeightM={1.6}
        terrainEvidenceId={null}
        structure={{
          footprint_length_m: 5,
          footprint_width_m: 3,
          front_bearing_degrees: 0,
          front_definition: 'long_wall_normal',
          orientation_status: 'suggested',
          placement_latitude: evidence.latitude,
          placement_longitude: evidence.longitude,
        }}
        windRegion="A2"
        adoptedEvidence={null}
        onEvidence={onEvidence}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Refresh baseline' }))

    expect(await screen.findByText(/Automatic best-available basis active/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Adopt suggestion' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use eligible GA values' })).not.toBeInTheDocument()

    await waitFor(() => expect(onEvidence).toHaveBeenCalledWith(['M_s', 'M_t'], evidence))
  })

  it('uses pinned local evidence and applies all three site multipliers', async () => {
    const localEvidence: GisDirectionalWindMultiplierEvidence = {
      ...evidence,
      schema_version: 'tertius.gis.local-wind.v1',
      evidence_id: 'windv1-11111111111111111111111111111111',
      terrain_evidence_id: 'gisv1-0123456789abcdef0123456789abcdef',
      building_evidence_id: 'buildingv1-0123456789abcdef0123456789abcdef',
      placement_latitude: evidence.latitude,
      placement_longitude: evidence.longitude,
      footprint_length_m: 5,
      footprint_width_m: 3,
      front_bearing_degrees: 15,
      wind_region: 'A2',
      terrain_reference_height_m: 3,
      provider: 'Tertius GIS cache',
      dataset: 'Pinned local wind evidence',
      method_status: 'automated_local_analysis',
      tile_id: undefined,
    }
    vi.mocked(apiFetch).mockImplementation(async () => new Response(JSON.stringify(localEvidence), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const onEvidence = vi.fn()

    render(
      <WindMultiplierEvidencePanel
        serverUrl="/api/site"
        getAccessToken={vi.fn()}
        latitude={evidence.latitude}
        longitude={evidence.longitude}
        referenceHeightM={3}
        terrainEvidenceId="gisv1-0123456789abcdef0123456789abcdef"
        structure={{
          footprint_length_m: 5,
          footprint_width_m: 3,
          front_bearing_degrees: 15,
          front_definition: 'long_wall_normal',
          orientation_status: 'suggested',
          placement_latitude: evidence.latitude,
          placement_longitude: evidence.longitude,
        }}
        windRegion="A2"
        adoptedEvidence={null}
        onEvidence={onEvidence}
      />,
    )

    await waitFor(() => expect(onEvidence).toHaveBeenCalledWith(
      ['M_z_cat', 'M_s', 'M_t'],
      localEvidence,
    ))
    expect(vi.mocked(apiFetch).mock.calls.some((call) => String(call[0]).includes('/local-wind?'))).toBe(true)
  })
})
