import { useCallback, useEffect, useState } from 'react'

import { apiFetch } from '../../api/client'
import type { GisCacheHealth, GisEvidenceManifest, GisPointResult } from './contracts'


type GisEvidencePanelProps = {
  serverUrl: string
  getAccessToken: () => Promise<string>
  latitude: number
  longitude: number
  onEvidenceChange?: (manifest: GisEvidenceManifest) => void
}

function responseDetail(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = payload.detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / 1024 ** 2).toFixed(1)} MiB`
}

export function GisEvidencePanel({
  serverUrl,
  getAccessToken,
  latitude,
  longitude,
  onEvidenceChange,
}: GisEvidencePanelProps) {
  const [health, setHealth] = useState<GisCacheHealth | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dataset, setDataset] = useState('Site terrain test')
  const [provider, setProvider] = useState('manual-upload')
  const [datasetVersion, setDatasetVersion] = useState('manual-test')
  const [licence, setLicence] = useState('User supplied — verify before design use')
  const [attribution, setAttribution] = useState('Uploaded through Tertius Site workbench')
  const [manifest, setManifest] = useState<GisEvidenceManifest | null>(null)
  const [point, setPoint] = useState<GisPointResult | null>(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const [radiusM, setRadiusM] = useState(2000)
  const [previewUrl, setPreviewUrl] = useState('')

  const checkHealth = useCallback(async () => {
    setHealthError(null)
    try {
      const response = await apiFetch(`${serverUrl}/gis/health`, getAccessToken)
      const payload = await response.json().catch(() => null) as GisCacheHealth | { detail?: string } | null
      if (!response.ok) {
        throw new Error(responseDetail(payload, `GIS cache returned ${response.status}`))
      }
      setHealth(payload as GisCacheHealth)
    } catch (healthFailure) {
      setHealth(null)
      setHealthError(
        healthFailure instanceof Error ? healthFailure.message : 'GIS cache is unavailable',
      )
    }
  }, [getAccessToken, serverUrl])

  useEffect(() => {
    void checkHealth()
  }, [checkHealth])

  const queryPoint = useCallback(async (evidenceId: string) => {
    const query = new URLSearchParams({
      latitude: String(latitude),
      longitude: String(longitude),
    })
    const response = await apiFetch(
      `${serverUrl}/gis/evidence/${evidenceId}/point?${query}`,
      getAccessToken,
    )
    const payload = await response.json().catch(() => null) as GisPointResult | { detail?: string } | null
    if (!response.ok) {
      throw new Error(responseDetail(payload, `Elevation query returned ${response.status}`))
    }
    setPoint(payload as GisPointResult)
  }, [getAccessToken, latitude, longitude, serverUrl])

  const upload = useCallback(async () => {
    if (!file) return
    setIsBusy(true)
    setError(null)
    setPoint(null)
    setStatus('Validating and caching the terrain raster…')
    try {
      const body = new FormData()
      body.set('raster', file)
      body.set('provider', provider)
      body.set('dataset', dataset)
      body.set('dataset_version', datasetVersion)
      body.set('licence', licence)
      body.set('attribution', attribution)
      const response = await apiFetch(`${serverUrl}/gis/evidence`, getAccessToken, {
        method: 'POST',
        body,
      })
      const payload = await response.json().catch(() => null) as
        | GisEvidenceManifest
        | { detail?: string }
        | null
      if (!response.ok) {
        throw new Error(responseDetail(payload, `GIS upload returned ${response.status}`))
      }
      const nextManifest = payload as GisEvidenceManifest
      setManifest(nextManifest)
      onEvidenceChange?.(nextManifest)
      setStatus('Evidence cached. Reading the current site coordinate…')
      await queryPoint(nextManifest.evidence_id)
      setStatus('Test evidence is ready. No design input has been changed.')
    } catch (uploadFailure) {
      setError(uploadFailure instanceof Error ? uploadFailure.message : 'GIS evidence upload failed')
      setStatus('')
    } finally {
      setIsBusy(false)
    }
  }, [attribution, dataset, datasetVersion, file, getAccessToken, licence, onEvidenceChange, provider, queryPoint, serverUrl])

  const fetchOfficialTerrain = useCallback(async () => {
    setIsBusy(true)
    setError(null)
    setPoint(null)
    setStatus('Selecting the best official terrain source for this site…')
    try {
      const query = new URLSearchParams({
        latitude: String(latitude),
        longitude: String(longitude),
        radius_m: String(radiusM),
      })
      const response = await apiFetch(`${serverUrl}/gis/terrain/site?${query}`, getAccessToken, {
        method: 'POST',
      })
      const payload = await response.json().catch(() => null) as
        | GisEvidenceManifest
        | { detail?: string }
        | null
      if (!response.ok) {
        throw new Error(responseDetail(payload, `Terrain acquisition returned ${response.status}`))
      }
      const nextManifest = payload as GisEvidenceManifest
      setManifest(nextManifest)
      onEvidenceChange?.(nextManifest)
      setStatus('Terrain cached. Reading elevation at the site point…')
      await queryPoint(nextManifest.evidence_id)
      setStatus(`${nextManifest.source.provider} terrain evidence is ready. Design inputs remain unchanged pending review.`)
    } catch (fetchFailure) {
      setError(fetchFailure instanceof Error ? fetchFailure.message : 'Terrain acquisition failed')
      setStatus('')
    } finally {
      setIsBusy(false)
    }
  }, [getAccessToken, latitude, longitude, onEvidenceChange, queryPoint, radiusM, serverUrl])

  const elevation = point?.values[0]

  useEffect(() => {
    if (!manifest) {
      setPreviewUrl('')
      return
    }
    let cancelled = false
    let objectUrl = ''
    void apiFetch(
      `${serverUrl}/gis/evidence/${manifest.evidence_id}/preview.png`,
      getAccessToken,
    ).then((response) => {
      if (!response.ok) throw new Error(`Preview returned ${response.status}`)
      return response.blob()
    }).then((blob) => {
      if (cancelled) return
      objectUrl = URL.createObjectURL(blob)
      setPreviewUrl(objectUrl)
    }).catch(() => {
      if (!cancelled) setPreviewUrl('')
    })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [getAccessToken, manifest, serverUrl])

  return (
    <section className="rounded border border-sky-500/40 bg-sky-950/10 p-4" data-testid="gis-evidence-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-semibold text-slate-100">GIS terrain evidence</h2>
            <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-300">
              experimental
            </span>
          </div>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
            Fetch the best official site-sized DEM available: NSW 5 m terrain locally, with Geoscience Australia 30 m terrain as the national fallback.
            Results are evidence only and never change terrain category or wind multipliers automatically.
          </p>
        </div>
        <button type="button" onClick={() => void checkHealth()}
          className="rounded border border-slate-700 px-2 py-1 text-[11px] hover:border-sky-400">
          Check service
        </button>
      </div>

      <div className={`mt-3 rounded border p-3 text-xs ${
        health
          ? 'border-emerald-500/30 bg-emerald-950/20 text-emerald-200'
          : 'border-amber-500/30 bg-amber-950/20 text-amber-200'
      }`}>
        {health
          ? `GIS cache ready · ${formatBytes(health.free_bytes)} free`
          : `GIS cache unavailable${healthError ? ` · ${healthError}` : ' · checking…'}`}
      </div>

      <div className="mt-3 rounded border border-cyan-500/30 bg-cyan-950/20 p-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="block text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
              Site radius
            </span>
            <select className="mt-1 rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
              value={radiusM} onChange={(event) => setRadiusM(Number(event.target.value))}>
              <option value={1000}>1 km</option>
              <option value={2000}>2 km</option>
              <option value={5000}>5 km</option>
              <option value={10000}>10 km</option>
            </select>
          </label>
          <button type="button" disabled={isBusy || !health}
            onClick={() => void fetchOfficialTerrain()}
            className="rounded bg-cyan-600 px-3 py-2 text-xs font-bold text-white hover:bg-cyan-500 disabled:opacity-50">
            {isBusy ? 'Processing terrain…' : 'Fetch best terrain for this site'}
          </button>
        </div>
        <p className="mt-2 text-[10px] leading-4 text-slate-500">
          NSW sites use the 5 m bare-earth DEM. The 2 m contour product is a vertical contour interval derived from that DEM, so it is not used as the 3D height surface.
        </p>
      </div>

      <details className="mt-3 rounded border border-slate-800 bg-slate-950/50 p-3">
        <summary className="cursor-pointer text-xs font-semibold text-slate-300">Upload higher-resolution terrain</summary>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="block md:col-span-2">
          <span className="block text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
            Elevation GeoTIFF
          </span>
          <input type="file" accept=".tif,.tiff,image/tiff" className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 p-2 text-xs"
            onChange={(event) => {
              const selected = event.target.files?.[0] || null
              setFile(selected)
              if (selected && dataset === 'Site terrain test') setDataset(selected.name)
            }} />
        </label>
        <label className="block">
          <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">Dataset</span>
          <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={dataset} onChange={(event) => setDataset(event.target.value)} />
        </label>
        <label className="block">
          <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">Provider</span>
          <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={provider} onChange={(event) => setProvider(event.target.value)} />
        </label>
      </div>

      <details className="mt-3 rounded border border-slate-800 bg-slate-950/50 p-3">
        <summary className="cursor-pointer text-xs font-semibold text-slate-300">Test provenance</summary>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="text-xs text-slate-400">Dataset version
            <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
              value={datasetVersion} onChange={(event) => setDatasetVersion(event.target.value)} />
          </label>
          <label className="text-xs text-slate-400">Licence
            <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
              value={licence} onChange={(event) => setLicence(event.target.value)} />
          </label>
          <label className="text-xs text-slate-400 md:col-span-2">Attribution
            <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
              value={attribution} onChange={(event) => setAttribution(event.target.value)} />
          </label>
        </div>
      </details>

      <button type="button" disabled={isBusy || !health || !file || !dataset.trim()}
        onClick={() => void upload()}
        className="mt-3 rounded bg-sky-600 px-3 py-2 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-50">
        {isBusy ? 'Processing terrain…' : 'Cache and inspect terrain'}
      </button>
      </details>

      {(status || error) && (
        <p className={`mt-3 text-xs ${error ? 'text-red-300' : 'text-sky-200'}`} role="status">
          {error || status}
        </p>
      )}

      {manifest && (
        <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(14rem,0.8fr)]">
          <div className="rounded border border-slate-800 bg-slate-950/70 p-3 text-xs">
            <div className="font-semibold text-slate-200">Cached evidence</div>
            <dl className="mt-2 grid grid-cols-[7rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-slate-400">
              <dt>Evidence ID</dt><dd className="break-all font-mono text-sky-200">{manifest.evidence_id}</dd>
              <dt>Source</dt><dd>{manifest.source.provider} · {manifest.source.dataset}</dd>
              <dt>Raster</dt><dd>{manifest.asset.width} × {manifest.asset.height} · {manifest.asset.dtype}</dd>
              <dt>CRS</dt><dd>{manifest.asset.crs}</dd>
              <dt>Resolution</dt><dd>{manifest.asset.resolution.join(' × ')} {manifest.asset.crs === 'EPSG:4326' ? 'degrees' : 'm'}</dd>
              <dt>Licence</dt><dd>{manifest.source.licence}</dd>
              <dt>Size</dt><dd>{formatBytes(manifest.asset.size_bytes)}</dd>
              <dt>Site elevation</dt><dd className="font-mono text-cyan-200">
                {typeof elevation === 'number' ? `${elevation.toFixed(3)} raster units` : 'No data at site point'}
              </dd>
            </dl>
          </div>
          <figure className="overflow-hidden rounded border border-slate-800 bg-slate-950/70 p-2">
            {previewUrl ? (
              <img src={previewUrl} alt="Cached terrain raster preview" className="h-44 w-full object-contain [image-rendering:pixelated]" />
            ) : (
              <div className="flex h-44 items-center justify-center text-[10px] text-slate-600">Rendering preview…</div>
            )}
            <figcaption className="mt-2 text-[10px] text-slate-500">
              Diagnostic rendering of the cached analysis raster.
            </figcaption>
          </figure>
        </div>
      )}
    </section>
  )
}
