import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SiteExplorer } from './SiteExplorer'


const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('../../api/client', () => ({ apiFetch: mocks.apiFetch }))

vi.mock('../structural/WindRegionMap', () => ({
  WindRegionMap: ({ overlayMode, terrainEvidenceId, baseMapMode }: {
    overlayMode: string
    terrainEvidenceId: string | null
    baseMapMode: string
  }) => <div>Leaflet {baseMapMode} {overlayMode} {terrainEvidenceId || 'no terrain'}</div>,
}))

vi.mock('./RichSiteMap', () => ({
  RichSiteMap: ({ overlayMode, baseMapMode, terrainMode, candidateModelUrl }: {
    overlayMode: string
    baseMapMode: string
    terrainMode: string
    candidateModelUrl: string | null
  }) => <div>Rich map {baseMapMode} {overlayMode} {terrainMode} {candidateModelUrl || 'no model'}</div>,
}))

const props = {
  serverUrl: '/api/site',
  extusServerUrl: '/api/extus',
  getAccessToken: vi.fn().mockResolvedValue(''),
  latitude: -34.4,
  longitude: 150.8,
  footprintLengthM: 12,
  footprintWidthM: 6,
  frontBearingDegrees: 20,
  referenceHeightM: 4,
  cardinalMultipliers: null,
  terrainEvidenceId: 'gisv1-2bfc4440ceed019f52fb90a1811c09a6',
  terrainEvidenceBounds: [150.7, -34.5, 150.9, -34.3] as [number, number, number, number],
  siteBoundary: null,
  onPick: vi.fn(),
}

beforeEach(() => {
  mocks.apiFetch.mockResolvedValue(new Response(JSON.stringify({ mtime: 1234 }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('WebGL2RenderingContext', class WebGL2RenderingContext {})
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as RenderingContext)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('SiteExplorer', () => {
  it('loads active model dimensions without requiring the 3D renderer', async () => {
    const onCandidateDimensions = vi.fn()
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({
      mtime: 1234,
      site_dimensions: {
        schema_version: 'tertius.model-site-dimensions.v1',
        model_artifact_id: 'model-1',
        footprint_length_m: 5.2,
        footprint_width_m: 3.1,
        overall_height_m: 3.05,
        reference_height_m: 2.72,
        roof_eave_height_m: 2.4,
        roof_ridge_height_m: 3.04,
        reference_height_basis: 'mid-height of authored roof components',
        source: 'compiled Build123D analytic bounds',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    render(<SiteExplorer {...props} onCandidateDimensions={onCandidateDimensions} />)

    expect(await screen.findByText('active model · 5.20 × 3.10 m · h 2.72 m')).toBeInTheDocument()
    await waitFor(() => expect(onCandidateDimensions).toHaveBeenCalledWith(
      expect.objectContaining({ model_artifact_id: 'model-1', reference_height_m: 2.72 }),
    ))
    expect(screen.queryByTestId('site-map-3d-shell')).not.toBeInTheDocument()
  })

  it('shares layer selection between fast 2D and capable 3D modes', async () => {
    render(<SiteExplorer {...props} />)

    expect(screen.getByText(/Leaflet nsw terrain/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Site map layer'), { target: { value: 'terrain' } })
    expect(screen.getByText(/Leaflet nsw terrain/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '3D' }))
    expect(await screen.findByText('Rich map nsw terrain terrain /api/extus/model?t=1234')).toBeInTheDocument()
    expect(screen.getByText('active candidate model')).toBeInTheDocument()
  })

  it('keeps the WebGL map mounted when returning to 2D', async () => {
    render(<SiteExplorer {...props} />)

    expect(screen.queryByTestId('site-map-3d-shell')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '3D' }))
    expect(await screen.findByText('Rich map nsw terrain terrain /api/extus/model?t=1234')).toBeInTheDocument()
    expect(screen.getByTestId('site-map-3d-shell')).toHaveAttribute('aria-hidden', 'false')

    fireEvent.click(screen.getByRole('button', { name: '2D' }))

    expect(screen.getByTestId('site-map-2d-shell')).toHaveAttribute('aria-hidden', 'false')
    expect(screen.getByTestId('site-map-3d-shell')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByText('Rich map nsw terrain terrain /api/extus/model?t=1234')).toBeInTheDocument()
  })

  it('keeps terrain unavailable until evidence has been cached', () => {
    render(<SiteExplorer {...props} terrainEvidenceId={null} />)

    expect(screen.getByRole('option', { name: 'Terrain relief (cached DEM)' })).toBeDisabled()
    expect(screen.getByRole('option', { name: 'Cached DEM' })).toBeDisabled()
  })

  it('uses cached terrain as the 3D ground independently of the evidence overlay', async () => {
    render(<SiteExplorer {...props} />)

    expect(screen.getByLabelText('Site map layer')).toHaveValue('terrain')
    expect(screen.getByLabelText('Site ground surface')).toHaveValue('terrain')
    fireEvent.click(screen.getByRole('button', { name: '3D' }))
    expect(await screen.findByText('Rich map nsw terrain terrain /api/extus/model?t=1234')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Site ground surface'), { target: { value: 'flat' } })
    expect(screen.getByText('Rich map nsw terrain flat /api/extus/model?t=1234')).toBeInTheDocument()
  })

  it('shares the selected base map between both renderers', () => {
    render(<SiteExplorer {...props} />)

    fireEvent.change(screen.getByLabelText('Site base map'), { target: { value: 'street' } })
    expect(screen.getByText(/Leaflet street terrain/)).toBeInTheDocument()
  })

  it('defaults the candidate to a lightweight plan representation', async () => {
    render(<SiteExplorer {...props} />)

    fireEvent.click(screen.getByRole('button', { name: '3D' }))
    expect(await screen.findByLabelText('Candidate site camera')).toHaveValue('plan')
    expect(screen.getByLabelText('Candidate model representation')).toHaveValue('envelope')
  })
})
