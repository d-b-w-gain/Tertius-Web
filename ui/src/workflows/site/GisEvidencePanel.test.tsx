import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GisEvidencePanel } from './GisEvidencePanel'


const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('../../api/client', () => ({ apiFetch }))

const manifest = {
  evidence_id: 'gisv1-2bfc4440ceed019f52fb90a1811c09a6',
  created_at: '2026-08-05T00:00:00Z',
  source: {
    provider: 'manual-upload',
    dataset: 'terrain.tif',
    dataset_version: 'manual-test',
    licence: 'Test only',
    attribution: 'Test fixture',
  },
  asset: {
    content_sha256: 'a'.repeat(64),
    relative_path: 'source/test.tif',
    media_type: 'image/tiff',
    size_bytes: 4096,
    width: 16,
    height: 16,
    band_count: 1,
    dtype: 'float32',
    crs: 'EPSG:4326',
    bounds: [150, -33.016, 150.016, -33],
    resolution: [0.001, 0.001],
    nodata: -9999,
  },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('GisEvidencePanel', () => {
  it('uploads test evidence and reads elevation at the current site point', async () => {
    apiFetch.mockImplementation(async (url: string, _token: unknown, init?: RequestInit) => {
      if (url.endsWith('/gis/health')) {
        return new Response(JSON.stringify({
          status: 'ready', free_bytes: 1_000_000, total_bytes: 2_000_000,
        }))
      }
      if (init?.method === 'POST') {
        return new Response(JSON.stringify(manifest), { status: 201 })
      }
      if (url.includes('/point?')) {
        return new Response(JSON.stringify({
          coordinates: [150.005, -33.005],
          values: [84],
          band_names: ['b1'],
        }))
      }
      throw new Error(`Unexpected request: ${url}`)
    })

    render(<GisEvidencePanel
      serverUrl="/api/site"
      getAccessToken={vi.fn()}
      latitude={-33.005}
      longitude={150.005}
    />)

    expect(await screen.findByText(/GIS cache ready/)).toBeInTheDocument()
    const file = new File([new Uint8Array([1, 2, 3])], 'terrain.tif', { type: 'image/tiff' })
    fireEvent.change(screen.getByLabelText('Elevation GeoTIFF'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Cache and inspect terrain' }))

    expect(await screen.findByText(manifest.evidence_id)).toBeInTheDocument()
    expect(screen.getByText('84.000 raster units')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Cached terrain raster preview' })).toHaveAttribute(
      'src',
      `/api/site/gis/evidence/${manifest.evidence_id}/preview.png`,
    )
    expect(screen.getByText('Test evidence is ready. No design input has been changed.')).toBeInTheDocument()

    const uploadCall = apiFetch.mock.calls.find((call) => call[2]?.method === 'POST')
    expect(uploadCall?.[0]).toBe('/api/site/gis/evidence')
    expect(uploadCall?.[2]?.body).toBeInstanceOf(FormData)
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(3))
  })
})
