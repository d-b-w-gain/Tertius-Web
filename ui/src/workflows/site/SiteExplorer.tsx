import { useEffect, useState } from 'react'

import { apiFetch } from '../../api/client'
import { WindRegionMap } from '../structural/WindRegionMap'
import { RichSiteMap } from './RichSiteMap'


export type SiteBaseMapMode = 'street' | 'satellite' | 'none'
export type SiteGroundMode = 'flat' | 'terrain'

type Props = {
  serverUrl: string
  extusServerUrl: string
  getAccessToken: () => Promise<string>
  latitude: number
  longitude: number
  footprintLengthM: number
  footprintWidthM: number
  frontBearingDegrees: number
  referenceHeightM: number
  cardinalMultipliers: Record<string, number> | null
  terrainEvidenceId: string | null
  terrainEvidenceBounds: [number, number, number, number] | null
  onPick: (latitude: number, longitude: number) => void
}

export function SiteExplorer(props: Props) {
  const [viewMode, setViewMode] = useState<'2d' | '3d'>('2d')
  const [hasOpened3d, setHasOpened3d] = useState(false)
  const [overlayMode, setOverlayMode] = useState<'wind' | 'terrain' | 'none'>('wind')
  const [baseMapMode, setBaseMapMode] = useState<SiteBaseMapMode>('street')
  const [groundMode, setGroundMode] = useState<SiteGroundMode>('flat')
  const [richCapable, setRichCapable] = useState(true)
  const [candidateModelUrl, setCandidateModelUrl] = useState<string | null>(null)
  const [candidateModelState, setCandidateModelState] = useState<'idle' | 'loading' | 'ready' | 'missing'>('idle')

  useEffect(() => {
    setGroundMode(props.terrainEvidenceId ? 'terrain' : 'flat')
  }, [props.terrainEvidenceId])

  useEffect(() => {
    if (typeof WebGL2RenderingContext === 'undefined') {
      setRichCapable(false)
      return
    }
    const canvas = document.createElement('canvas')
    setRichCapable(Boolean(canvas.getContext('webgl2')))
  }, [])

  useEffect(() => {
    if (viewMode !== '3d') return
    let cancelled = false
    const checkCandidate = async () => {
      setCandidateModelState((current) => current === 'ready' ? current : 'loading')
      try {
        const response = await apiFetch(`${props.extusServerUrl}/status`, props.getAccessToken)
        if (!response.ok) {
          if (!cancelled) {
            setCandidateModelUrl(null)
            setCandidateModelState('missing')
          }
          return
        }
        const payload = await response.json() as { mtime?: number }
        if (!payload.mtime) throw new Error('Model status did not include an artifact timestamp')
        if (!cancelled) {
          setCandidateModelUrl(`${props.extusServerUrl}/model?t=${encodeURIComponent(payload.mtime)}`)
          setCandidateModelState('ready')
        }
      } catch {
        if (!cancelled) {
          setCandidateModelUrl(null)
          setCandidateModelState('missing')
        }
      }
    }
    void checkCandidate()
    const interval = window.setInterval(() => void checkCandidate(), 30_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [props.extusServerUrl, props.getAccessToken, viewMode])

  return (
    <section className="rounded border border-cyan-500/40 bg-slate-900/60 p-3" data-testid="site-explorer">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-semibold text-slate-100">Site Explorer</h2>
            <span className="rounded bg-cyan-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-cyan-300">
              {viewMode === '2d' ? 'fast 2D' : 'WebGL 3D'}
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Structure placement, cardinal wind sectors and authoritative GIS evidence in one view.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
            Base
            <select aria-label="Site base map" className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs font-normal text-slate-200"
              value={baseMapMode} onChange={(event) => setBaseMapMode(event.target.value as SiteBaseMapMode)}>
              <option value="street">Street</option>
              <option value="satellite">Satellite</option>
              <option value="none">No base</option>
            </select>
          </label>
          <label className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
            Evidence
            <select aria-label="Site map layer" className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs font-normal text-slate-200"
              value={overlayMode} onChange={(event) => setOverlayMode(event.target.value as typeof overlayMode)}>
              <option value="wind">Wind regions</option>
              <option value="terrain" disabled={!props.terrainEvidenceId}>Terrain relief</option>
              <option value="none">Base map only</option>
            </select>
          </label>
          <label className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
            Ground
            <select aria-label="Site ground surface" className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs font-normal text-slate-200"
              value={groundMode} onChange={(event) => setGroundMode(event.target.value as SiteGroundMode)}>
              <option value="flat">Flat</option>
              <option value="terrain" disabled={!props.terrainEvidenceId}>Cached DEM</option>
            </select>
          </label>
          <div className="flex rounded border border-slate-700 bg-slate-950 p-0.5">
            <button type="button" onClick={() => setViewMode('2d')}
              className={`rounded px-2 py-1 text-xs ${viewMode === '2d' ? 'bg-cyan-600 text-white' : 'text-slate-400'}`}>
              2D
            </button>
            <button type="button" disabled={!richCapable} onClick={() => {
              setHasOpened3d(true)
              setViewMode('3d')
            }}
              title={richCapable ? 'Show pitched 3D terrain' : 'WebGL 2 is unavailable on this machine'}
              className={`rounded px-2 py-1 text-xs disabled:opacity-40 ${viewMode === '3d' ? 'bg-cyan-600 text-white' : 'text-slate-400'}`}>
              3D
            </button>
          </div>
          {viewMode === '3d' && (
            <span className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-400">
              {candidateModelState === 'ready'
                ? 'active candidate model'
                : candidateModelState === 'loading'
                  ? 'finding candidate model…'
                  : 'footprint fallback'}
            </span>
          )}
        </div>
      </div>
      <div className="relative h-[clamp(28rem,58vh,52rem)]">
        <div
          aria-hidden={viewMode !== '2d'}
          data-testid="site-map-2d-shell"
          className={`absolute inset-0 ${viewMode === '2d' ? 'visible' : 'invisible pointer-events-none'}`}
        >
          <WindRegionMap
            {...props}
            overlayMode={overlayMode}
            baseMapMode={baseMapMode}
            className="h-full"
          />
        </div>
        {hasOpened3d && (
          <div
            aria-hidden={viewMode !== '3d'}
            data-testid="site-map-3d-shell"
            className={`absolute inset-0 ${viewMode === '3d' ? 'visible' : 'invisible pointer-events-none'}`}
          >
            <RichSiteMap
              {...props}
              overlayMode={overlayMode}
              baseMapMode={baseMapMode}
              terrainMode={groundMode}
              candidateModelUrl={candidateModelUrl}
            />
          </div>
        )}
      </div>
    </section>
  )
}
