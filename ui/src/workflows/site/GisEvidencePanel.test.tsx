import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:terrain-preview'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: vi.fn(),
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('GisEvidencePanel', () => {
  it('fetches and inspects the best official terrain window for the site', async () => {
    apiFetch.mockImplementation(async (url: string, _token: unknown, init?: RequestInit) => {
      if (url.endsWith('/gis/health')) {
        return new Response(JSON.stringify({
          status: 'ready', free_bytes: 1_000_000, total_bytes: 2_000_000,
        }))
      }
      if (url.includes('/gis/terrain/site?') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          ...manifest,
          source: {
            ...manifest.source,
            provider: 'NSW Spatial Services',
            dataset: 'NSW 5 metre Digital Elevation Model',
          },
        }), { status: 201 })
      }
      if (url.includes('/point?')) {
        return new Response(JSON.stringify({
          coordinates: [150.8886, -34.4125], values: [52], band_names: ['b1'],
        }))
      }
      if (url.endsWith('/preview.png')) {
        return new Response(new Uint8Array([137, 80, 78, 71]), {
          headers: { 'Content-Type': 'image/png' },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    })

    render(<GisEvidencePanel
      serverUrl="/api/site"
      getAccessToken={vi.fn()}
      latitude={-34.4125}
      longitude={150.8886}
    />)

    expect(await screen.findByText(/GIS cache ready/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Fetch best terrain for this site' }))

    expect(await screen.findByText('52.000 raster units')).toBeInTheDocument()
    expect(screen.getByText(/NSW Spatial Services terrain evidence is ready/)).toBeInTheDocument()
    const fetchCall = apiFetch.mock.calls.find((call) => String(call[0]).includes('/gis/terrain/site?'))
    expect(fetchCall?.[0]).toContain('radius_m=2000')
  })

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
      if (url.endsWith('/preview.png')) {
        return new Response(new Uint8Array([137, 80, 78, 71]), {
          headers: { 'Content-Type': 'image/png' },
        })
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
    expect(await screen.findByRole('img', { name: 'Cached terrain raster preview' })).toHaveAttribute(
      'src',
      'blob:terrain-preview',
    )
    expect(screen.getByText('Test evidence is ready. No design input has been changed.')).toBeInTheDocument()

    const uploadCall = apiFetch.mock.calls.find((call) => call[2]?.method === 'POST')
    expect(uploadCall?.[0]).toBe('/api/site/gis/evidence')
    expect(uploadCall?.[2]?.body).toBeInstanceOf(FormData)
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(4))
  })
})
