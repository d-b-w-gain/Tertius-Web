import { useCallback, useEffect, useRef, useState } from 'react'

import { apiFetch } from '../../api/client'
import type {
  CardinalMultiplierValues,
  GisDirectionalWindMultiplierEvidence,
  SiteDefinition,
} from './contracts'


const DIRECTIONS: Array<keyof CardinalMultiplierValues> = [
  'n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw',
]

type ComponentName = 'M_z_cat' | 'M_s' | 'M_t'

type Props = {
  serverUrl: string
  getAccessToken: () => Promise<string>
  latitude: number
  longitude: number
  referenceHeightM: number
  terrainEvidenceId: string | null
  structure: SiteDefinition['structure']
  windRegion: string
  adoptedEvidence: SiteDefinition['wind']['multiplier_evidence'] | null
  onEvidence: (
    components: ComponentName[],
    evidence: GisDirectionalWindMultiplierEvidence,
  ) => void
}

function responseDetail(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = payload.detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

export function WindMultiplierEvidencePanel({
  serverUrl,
  getAccessToken,
  latitude,
  longitude,
  referenceHeightM,
  terrainEvidenceId,
  structure,
  windRegion,
  adoptedEvidence,
  onEvidence,
}: Props) {
  const [evidence, setEvidence] = useState<GisDirectionalWindMultiplierEvidence | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const requestedCoordinate = useRef('')

  const fetchEvidence = useCallback(async () => {
    if (!(-44.5 <= latitude && latitude <= -9 && 112 <= longitude && longitude <= 154)) return
    setIsBusy(true)
    setError(null)
    try {
      const placementLatitude = structure.placement_latitude ?? latitude
      const placementLongitude = structure.placement_longitude ?? longitude
      const query = new URLSearchParams({
        latitude: String(latitude),
        longitude: String(longitude),
      })
      if (terrainEvidenceId) {
        query.set('placement_latitude', String(placementLatitude))
        query.set('placement_longitude', String(placementLongitude))
        query.set('reference_height_m', String(referenceHeightM))
        query.set('footprint_length_m', String(structure.footprint_length_m))
        query.set('footprint_width_m', String(structure.footprint_width_m))
        query.set('front_bearing_degrees', String(structure.front_bearing_degrees))
        query.set('wind_region', windRegion)
      }
      const response = await apiFetch(
        terrainEvidenceId
          ? `${serverUrl}/gis/evidence/${terrainEvidenceId}/local-wind?${query}`
          : `${serverUrl}/gis/wind-multipliers/site?${query}`,
        getAccessToken,
      )
      const payload = await response.json().catch(() => null) as
        | GisDirectionalWindMultiplierEvidence
        | { detail?: string }
        | null
      if (!response.ok) {
        throw new Error(responseDetail(payload, `Multiplier lookup returned ${response.status}`))
      }
      if (
        !payload
        || typeof payload !== 'object'
        || !('terrain_height_multipliers' in payload)
        || !('shielding_multipliers' in payload)
        || !('topographic_multipliers' in payload)
      ) {
        throw new Error('Multiplier lookup returned an invalid evidence contract')
      }
      const resolvedEvidence = payload as GisDirectionalWindMultiplierEvidence
      setEvidence(resolvedEvidence)
      const eligibleComponents: ComponentName[] = ['M_s', 'M_t']
      if (
        resolvedEvidence.method_status === 'automated_local_analysis'
        || Math.abs(referenceHeightM - resolvedEvidence.terrain_reference_height_m) < 1e-6
      ) {
        eligibleComponents.unshift('M_z_cat')
      }
      onEvidence(eligibleComponents, resolvedEvidence)
    } catch (failure) {
      setEvidence(null)
      setError(failure instanceof Error ? failure.message : 'Multiplier evidence lookup failed')
    } finally {
      setIsBusy(false)
    }
  }, [
    getAccessToken, latitude, longitude, onEvidence, referenceHeightM, serverUrl,
    structure, terrainEvidenceId, windRegion,
  ])

  useEffect(() => {
    const coordinate = [
      latitude.toFixed(6), longitude.toFixed(6), referenceHeightM.toFixed(3),
      terrainEvidenceId ?? 'no-dem',
      (structure.placement_latitude ?? latitude).toFixed(6),
      (structure.placement_longitude ?? longitude).toFixed(6),
      structure.footprint_length_m.toFixed(3), structure.footprint_width_m.toFixed(3),
      structure.front_bearing_degrees.toFixed(3), windRegion,
    ].join(',')
    if (requestedCoordinate.current === coordinate) return
    requestedCoordinate.current = coordinate
    const timer = window.setTimeout(() => void fetchEvidence(), 400)
    return () => window.clearTimeout(timer)
  }, [
    fetchEvidence, latitude, longitude, referenceHeightM, structure,
    terrainEvidenceId, windRegion,
  ])

  const terrainHeightMatches = evidence
    ? Math.abs(referenceHeightM - evidence.terrain_reference_height_m) < 1e-6
    : false
  const rows = evidence ? [
    { key: 'M_z_cat' as const, label: 'Terrain / height', symbol: 'Mz,cat', values: evidence.terrain_height_multipliers },
    { key: 'M_s' as const, label: 'Shielding', symbol: 'Ms', values: evidence.shielding_multipliers },
    { key: 'M_t' as const, label: 'Topographic', symbol: 'Mt', values: evidence.topographic_multipliers },
  ] : []
  const selectedComponents = adoptedEvidence?.adopted_components ?? []

  return (
    <section className="rounded border border-violet-500/40 bg-violet-950/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-slate-100">Directional multiplier evidence</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">
            The GIS pod combines the pinned DEM, candidate placement and open building geometry
            into eight reproducible Mz,cat, Ms and Mt sectors. The January 2016 Geoscience
            Australia shielding grid is the directional Ms baseline. Conservative present-day
            building evidence can improve it, while incomplete reconstruction cannot make it
            worse. Md remains sourced from the selected wind-region table.
          </p>
        </div>
        <button type="button" disabled={isBusy} onClick={() => void fetchEvidence()}
          className="rounded border border-violet-500/50 px-3 py-2 text-xs font-semibold text-violet-200 disabled:opacity-50">
          {isBusy ? 'Analysing…' : terrainEvidenceId ? 'Refresh local analysis' : 'Refresh baseline'}
        </button>
      </div>

      {error && <p role="status" className="mt-3 text-xs text-red-300">{error}</p>}
      {!evidence && !error && (
        <p className="mt-3 text-xs text-slate-500">{isBusy ? 'Querying the GIS cache…' : 'Waiting for an Australian site coordinate.'}</p>
      )}

          {evidence && (
        <>
          <div className="mt-4 overflow-x-auto rounded border border-slate-800">
            <table className="w-full min-w-[42rem] text-xs">
              <thead className="bg-slate-950/80 text-[10px] uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left">Multiplier</th>
                  {DIRECTIONS.map((direction) => (
                    <th key={direction} className="px-2 py-2 text-center">{direction.toUpperCase()}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                    <tr key={row.key} className="border-t border-slate-800">
                      <td className="px-3 py-2 text-slate-300">{row.label} <span className="font-mono text-cyan-300">{row.symbol}</span></td>
                      {DIRECTIONS.map((direction) => (
                        <td key={direction} className="px-2 py-2 text-center font-mono text-cyan-200">
                          {row.values[direction].toFixed(3)}
                        </td>
                      ))}
                    </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!terrainHeightMatches && evidence.method_status !== 'automated_local_analysis' && (
            <p className="mt-3 rounded border border-amber-500/30 bg-amber-950/20 p-3 text-xs text-amber-200">
              GA Mz,cat is a {evidence.terrain_reference_height_m.toFixed(0)} m comparison value, while this candidate uses {referenceHeightM.toFixed(2)} m.
              It is visible for comparison but cannot be adopted as the candidate-height multiplier.
            </p>
          )}
          <div className="mt-3 rounded border border-amber-500/30 bg-amber-950/20 p-3 text-[11px] leading-5 text-amber-100">
            <b>Hazard evidence — review required.</b> {evidence.review_note}
            <div className="mt-1 text-slate-400">
              {evidence.dataset_version} · {evidence.tile_id ? `tile ${evidence.tile_id} · ` : ''}
              {evidence.licence} · evidence {evidence.evidence_id}
            </div>
          </div>

          {evidence.directions && (
            <details className="mt-3 rounded border border-slate-800 bg-slate-950/40 p-3">
              <summary className="cursor-pointer text-xs font-semibold text-cyan-200">
                Why each directional multiplier was selected
              </summary>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                {DIRECTIONS.map((direction) => {
                  const assessment = evidence.directions?.[direction]
                  if (!assessment) return null
                  return (
                    <article key={direction} className="rounded border border-slate-800 p-3 text-[11px] leading-5 text-slate-400">
                      <h3 className="font-semibold text-slate-200">{direction.toUpperCase()} · {assessment.bearing_degrees}°</h3>
                      <p><b className="text-cyan-300">Mz {assessment.terrain_height_multiplier.toFixed(3)}:</b> {assessment.terrain_reason}</p>
                      <p className="mt-1"><b className="text-cyan-300">Ms {assessment.shielding_multiplier.toFixed(3)}:</b> {assessment.shielding_reason}</p>
                      {assessment.ga_shielding_multiplier_2016 != null && (
                        <p className="mt-1 text-slate-500">
                          Adopted source: {assessment.shielding_basis === 'local_improvement' ? 'current local improvement' : 'GA January 2016 baseline'}
                          {' · '}GA baseline Ms {assessment.ga_shielding_multiplier_2016.toFixed(3)}
                          {assessment.local_shielding_multiplier != null
                            ? ` · local Table 4.2 Ms ${assessment.local_shielding_multiplier.toFixed(3)}`
                            : ' · no conclusive local Table 4.2 value'}
                        </p>
                      )}
                      <p className="mt-1"><b className="text-cyan-300">Mt {assessment.topographic_multiplier.toFixed(3)}:</b> {assessment.topographic_reason}</p>
                      {assessment.topographic_cross_section_bearing_degrees != null && (
                        <p className="mt-1 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-slate-500">
                          Section {assessment.topographic_cross_section_bearing_degrees.toFixed(1)} deg
                          {' · '}{assessment.topographic_feature_type?.replaceAll('_', ' ') ?? 'no qualifying feature'}
                          {' · '}{assessment.topographic_candidate_count ?? 0} candidates
                          {' · '}DEM {assessment.topographic_search_complete ? 'complete' : 'partial'}
                          {assessment.topographic_slope != null ? ` · H/(2Lu) ${assessment.topographic_slope.toFixed(3)}` : ''}
                          {assessment.topographic_l2_m != null ? ` · L2 ${assessment.topographic_l2_m.toFixed(0)} m` : ''}
                          {assessment.topographic_mh != null ? ` · Mh ${assessment.topographic_mh.toFixed(3)}` : ''}
                        </p>
                      )}
                    </article>
                  )
                })}
              </div>
            </details>
          )}

          <div className="mt-3 rounded border border-cyan-500/40 bg-cyan-950/20 p-3 text-xs text-cyan-100">
            <b>Automatic best-available basis active.</b> The working calculation uses the pinned evidence values for{' '}
            {selectedComponents.length > 0 ? selectedComponents.join(', ') : 'the eligible multipliers'}.
            {evidence.method_status === 'automated_local_analysis'
              ? ' Candidate-height Mz,cat comes from the pinned local terrain analysis.'
              : ' Shed-height Mz,cat continues to come from the project terrain/height table.'}
            {' '}Final licensed-standard verification is tracked separately from the working calculation.
          </div>
        </>
      )}
    </section>
  )
}
