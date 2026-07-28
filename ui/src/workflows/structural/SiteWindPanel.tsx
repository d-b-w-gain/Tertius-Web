import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { apiFetch } from '../../api/client'
import type {
  ProjectStructuralCapture,
  StructuralWindActionBasis,
} from './contracts'
import { WindRegionMap } from './WindRegionMap'


const WIND_REGIONS = ['A0', 'A1', 'A2', 'A3', 'A4', 'A5', 'B1', 'B2', 'C', 'D']

const SITE_PARAMETER_NAMES = [
  'site_address',
  'site_latitude',
  'site_longitude',
  'wind_region',
  'wind_region_area',
  'wind_region_source',
  'wind_region_approximate',
  'wind_region_status',
  'wind_standard',
  'wind_table_version',
  'wind_table_status',
  'wind_importance_level',
  'wind_ari_years',
  'wind_terrain_category',
  'wind_reference_height_m',
  'wind_regional_speed_m_s',
  'wind_climate_multiplier',
  'wind_direction_multiplier',
  'wind_terrain_height_multiplier',
  'wind_shielding_multiplier',
  'wind_topographic_multiplier',
  'wind_site_speed_m_s',
  'wind_q_z_kPa',
  'wind_verifier_hash',
] as const

type SiteWindResult = {
  site_address: string
  latitude: number
  longitude: number
  region_area: string
  region_source: string
  region_approximate: boolean
  region_status: 'suggested'
  suggested_region: string | null
  selected_region: string
  region_conflict: boolean
  region_detail: string
  standard: string
  table_version: string
  table_status: 'starter'
  region: string
  terrain_category: string
  importance_level: string
  annual_recurrence_interval_years: number
  reference_height_m: number
  regional_wind_speed_m_s: number
  climate_change_multiplier: number
  direction_multiplier: number
  terrain_height_multiplier: number
  shielding_multiplier: number
  topographic_multiplier: number
  site_wind_speed_m_s: number
  q_z_kPa: number
  verifier_hash: string
  formula: string
  verify_against: string
}

type Props = {
  capture: ProjectStructuralCapture
  serverUrl: string
  intusServerUrl: string
  artusServerUrl: string
  getAccessToken: () => Promise<string>
  onCompiled: () => Promise<void>
}

type Draft = {
  siteAddress: string
  latitude: number
  longitude: number
  region: string
  terrainCategory: string
  importanceLevel: string
  annualProbabilityUls: string
  referenceHeightM: number
  directionMultiplier: number
  shieldingMultiplier: number
  topographicMultiplier: number
  regionVerified: boolean
  tablesVerified: boolean
}

function draftFromBasis(basis: StructuralWindActionBasis | undefined): Draft {
  return {
    siteAddress: basis?.site_address ?? '',
    latitude: basis?.latitude ?? -34.4125046,
    longitude: basis?.longitude ?? 150.8885637,
    region: basis?.region ?? '',
    terrainCategory: basis?.terrain_category ?? '3',
    importanceLevel: basis?.importance_level ?? '2',
    annualProbabilityUls: basis
      ? `1/${basis.annual_recurrence_interval_years}`
      : '1/500',
    referenceHeightM: basis?.reference_height_m ?? 3,
    directionMultiplier: basis?.direction_multiplier ?? 1,
    shieldingMultiplier: basis?.shielding_multiplier ?? 1,
    topographicMultiplier: basis?.topographic_multiplier ?? 1,
    regionVerified: basis?.region_status === 'verified',
    tablesVerified: basis?.table_status === 'verified',
  }
}

const inputClass = (
  'w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 '
  + 'text-[10px] text-slate-200 outline-none focus:border-cyan-500'
)

function Field({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[9px] font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </span>
      {children}
    </label>
  )
}

function errorDetail(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object') {
    const detail = 'detail' in payload ? payload.detail : undefined
    const error = 'error' in payload ? payload.error : undefined
    if (typeof detail === 'string') return detail
    if (typeof error === 'string') return error
  }
  return fallback
}

export function SiteWindPanel({
  capture,
  serverUrl,
  intusServerUrl,
  artusServerUrl,
  getAccessToken,
  onCompiled,
}: Props) {
  const basis = capture.wind_action_bases[0]
  const [draft, setDraft] = useState<Draft>(() => draftFromBasis(basis))
  const [result, setResult] = useState<SiteWindResult | null>(null)
  const [status, setStatus] = useState('')
  const [isBusy, setIsBusy] = useState(false)

  useEffect(() => {
    setDraft(draftFromBasis(capture.wind_action_bases[0]))
    setResult(null)
  }, [capture.design_hash, capture.wind_action_bases])

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
    setResult(null)
  }

  const calculate = async () => {
    setIsBusy(true)
    setStatus('Calculating site wind basis…')
    try {
      const response = await apiFetch(`${serverUrl}/wind/site`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          site_address: draft.siteAddress,
          latitude: draft.latitude,
          longitude: draft.longitude,
          region: draft.region,
          terrain_category: draft.terrainCategory,
          importance_level: draft.importanceLevel,
          annual_probability_uls: draft.annualProbabilityUls,
          reference_height_m: draft.referenceHeightM,
          direction_multiplier: draft.directionMultiplier,
          shielding_multiplier: draft.shieldingMultiplier,
          topographic_multiplier: draft.topographicMultiplier,
        }),
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(errorDetail(payload, `Site wind returned ${response.status}`))
      }
      const next = payload as SiteWindResult
      setResult(next)
      setStatus(
        next.region_conflict
          ? `Conflict: map suggests ${next.suggested_region}; design selects ${next.selected_region}.`
          : `Draft q_z = ${next.q_z_kPa.toFixed(6)} kPa.`,
      )
      return next
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Site wind calculation failed')
      return null
    } finally {
      setIsBusy(false)
    }
  }

  const pickCoordinates = async (latitude: number, longitude: number) => {
    setDraft((current) => ({ ...current, latitude, longitude }))
    setResult(null)
    setStatus('Looking up wind region…')
    try {
      const response = await apiFetch(
        `${serverUrl}/wind/region?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`,
        getAccessToken,
      )
      const payload = await response.json().catch(() => null) as
        | { region?: string | null; area?: string; detail?: string }
        | null
      if (!response.ok) {
        throw new Error(errorDetail(payload, `Region lookup returned ${response.status}`))
      }
      if (payload?.region) {
        setDraft((current) => ({
          ...current,
          latitude,
          longitude,
          region: payload.region || current.region,
          regionVerified: false,
        }))
        setStatus(`Map suggests ${payload.region}${payload.area ? ` — ${payload.area}` : ''}; verify against Figure 3.1(A).`)
      } else {
        setStatus(payload?.detail || 'No wind region found at those coordinates.')
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Wind region lookup failed')
    }
  }

  const geocode = async () => {
    if (!draft.siteAddress.trim()) return
    setIsBusy(true)
    setStatus('Finding site address…')
    try {
      const response = await fetch(
        'https://nominatim.openstreetmap.org/search'
          + `?format=json&limit=1&q=${encodeURIComponent(draft.siteAddress)}`,
        { headers: { Accept: 'application/json' } },
      )
      if (!response.ok) throw new Error(`Address search returned ${response.status}`)
      const payload = await response.json()
      if (!Array.isArray(payload) || !payload[0]) {
        throw new Error('Address was not found')
      }
      await pickCoordinates(Number(payload[0].lat), Number(payload[0].lon))
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Address search failed')
    } finally {
      setIsBusy(false)
    }
  }

  const availableParameterNames = async () => {
    const response = await apiFetch(`${artusServerUrl}/features`, getAccessToken)
    const payload = await response.json().catch(() => null) as
      | { features?: Array<{ name?: string }> }
      | null
    if (!response.ok) {
      throw new Error(errorDetail(payload, 'Could not inspect design.py variables'))
    }
    return new Set(
      (payload?.features ?? [])
        .map((feature) => feature.name)
        .filter((name): name is string => typeof name === 'string'),
    )
  }

  const pollCompile = async (jobId: string) => {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1500))
      const response = await apiFetch(
        `${intusServerUrl}/projects/${encodeURIComponent(capture.project_name)}/compile/jobs/${encodeURIComponent(jobId)}`,
        getAccessToken,
      )
      const payload = await response.json().catch(() => null) as
        | {
          status?: string
          user_message?: string
          error?: string
          artifact_id?: string
        }
        | null
      if (!response.ok) {
        throw new Error(errorDetail(payload, 'Could not refresh compile status'))
      }
      if (payload?.status === 'succeeded') return
      if (payload?.status === 'failed') {
        throw new Error(payload.user_message || payload.error || 'Compile failed')
      }
      setStatus(`Compile ${payload?.status || 'running'}…`)
    }
    throw new Error('Compile is still running; check Intus for its final status')
  }

  const applyToDesign = async () => {
    setIsBusy(true)
    try {
      const calculated = result ?? await calculate()
      if (!calculated) return
      setIsBusy(true)
      setStatus('Checking canonical design.py site parameters…')
      const available = await availableParameterNames()
      const missing = SITE_PARAMETER_NAMES.filter((name) => !available.has(name))
      if (missing.length > 0) {
        throw new Error(
          `design.py is missing the structural site scaffold: ${missing.join(', ')}`,
        )
      }
      const sourceDetail = calculated.region_conflict
        ? `${calculated.region_source}; overlay suggests ${calculated.suggested_region}, selected ${calculated.selected_region}`
        : calculated.region_source
      const updates = {
        site_address: draft.siteAddress,
        site_latitude: draft.latitude,
        site_longitude: draft.longitude,
        wind_region: calculated.selected_region,
        wind_region_area: calculated.region_area,
        wind_region_source: sourceDetail,
        wind_region_approximate: calculated.region_approximate,
        wind_region_status: draft.regionVerified ? 'verified' : 'suggested',
        wind_standard: calculated.standard,
        wind_table_version: calculated.table_version,
        wind_table_status: draft.tablesVerified ? 'verified' : 'starter',
        wind_importance_level: calculated.importance_level,
        wind_ari_years: calculated.annual_recurrence_interval_years,
        wind_terrain_category: calculated.terrain_category,
        wind_reference_height_m: calculated.reference_height_m,
        wind_regional_speed_m_s: calculated.regional_wind_speed_m_s,
        wind_climate_multiplier: calculated.climate_change_multiplier,
        wind_direction_multiplier: calculated.direction_multiplier,
        wind_terrain_height_multiplier: calculated.terrain_height_multiplier,
        wind_shielding_multiplier: calculated.shielding_multiplier,
        wind_topographic_multiplier: calculated.topographic_multiplier,
        wind_site_speed_m_s: calculated.site_wind_speed_m_s,
        wind_q_z_kPa: calculated.q_z_kPa,
        wind_verifier_hash: calculated.verifier_hash,
      }
      const updateResponse = await apiFetch(
        `${artusServerUrl}/update_features`,
        getAccessToken,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ updates }),
        },
      )
      const updatePayload = await updateResponse.json().catch(() => null)
      if (!updateResponse.ok || !updatePayload?.success) {
        throw new Error(errorDetail(updatePayload, 'Could not update design.py'))
      }

      setStatus('Queueing GLB compile from updated design.py…')
      const codeResponse = await apiFetch(
        `${intusServerUrl}/projects/${encodeURIComponent(capture.project_name)}/code?file=design.py`,
        getAccessToken,
      )
      const codePayload = await codeResponse.json().catch(() => null) as
        | { code?: string }
        | null
      if (!codeResponse.ok || !codePayload?.code) {
        throw new Error(errorDetail(codePayload, 'Could not reload updated design.py'))
      }
      const compileResponse = await apiFetch(
        `${intusServerUrl}/projects/${encodeURIComponent(capture.project_name)}/compile`,
        getAccessToken,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: codePayload.code,
            file: 'design.py',
            export_format: 'glb',
            quality: 'high',
          }),
        },
      )
      const compilePayload = await compileResponse.json().catch(() => null) as
        | { job_id?: string; user_message?: string; error?: string }
        | null
      if (!compileResponse.ok || !compilePayload?.job_id) {
        throw new Error(errorDetail(compilePayload, 'Could not queue compile'))
      }
      await pollCompile(compilePayload.job_id)
      setStatus('Compiled. Reloading structural evidence…')
      await onCompiled()
      setStatus('Site wind basis is now authored in design.py and compiled.')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not apply site wind')
    } finally {
      setIsBusy(false)
    }
  }

  const comparison = useMemo(() => {
    if (!basis || !result) return null
    return result.q_z_kPa - basis.q_z_kPa
  }, [basis, result])

  return (
    <section className="rounded border border-cyan-500/30 bg-cyan-950/10 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[9px] font-bold uppercase tracking-[0.16em] text-cyan-300">
            Site wind action input
          </div>
          <div className="mt-1 text-xs font-semibold text-slate-100">
            FBD map → design.py → Actions sheet
          </div>
        </div>
        {basis && (
          <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[9px] text-cyan-200">
            q<sub>z</sub> {basis.q_z_kPa.toFixed(6)} kPa
          </span>
        )}
      </div>

      <div className="mt-3">
        <WindRegionMap
          serverUrl={serverUrl}
          getAccessToken={getAccessToken}
          latitude={draft.latitude}
          longitude={draft.longitude}
          onPick={(latitude, longitude) => void pickCoordinates(latitude, longitude)}
        />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="col-span-2 flex gap-2">
          <div className="flex-1">
            <Field label="Site address">
              <input
                className={inputClass}
                value={draft.siteAddress}
                onChange={(event) => update('siteAddress', event.target.value)}
              />
            </Field>
          </div>
          <button
            type="button"
            disabled={isBusy || !draft.siteAddress.trim()}
            onClick={() => void geocode()}
            className="mt-4 shrink-0 rounded border border-slate-700 bg-slate-900 px-2 text-[9px] text-slate-300 hover:border-cyan-500 disabled:opacity-50"
          >
            Find
          </button>
        </div>
        <Field label="Latitude">
          <input
            type="number"
            step="0.000001"
            className={inputClass}
            value={draft.latitude}
            onChange={(event) => update('latitude', Number(event.target.value))}
          />
        </Field>
        <Field label="Longitude">
          <input
            type="number"
            step="0.000001"
            className={inputClass}
            value={draft.longitude}
            onChange={(event) => update('longitude', Number(event.target.value))}
          />
        </Field>
        <Field label="Wind region">
          <select
            className={inputClass}
            value={draft.region}
            onChange={(event) => update('region', event.target.value)}
          >
            <option value="">Auto from map</option>
            {WIND_REGIONS.map((region) => (
              <option key={region} value={region}>{region}</option>
            ))}
          </select>
        </Field>
        <Field label="Terrain category">
          <select
            className={inputClass}
            value={draft.terrainCategory}
            onChange={(event) => update('terrainCategory', event.target.value)}
          >
            {['1', '2', '2.5', '3', '4'].map((category) => (
              <option key={category} value={category}>{category}</option>
            ))}
          </select>
        </Field>
        <Field label="Importance level">
          <select
            className={inputClass}
            value={draft.importanceLevel}
            onChange={(event) => update('importanceLevel', event.target.value)}
          >
            {['1', '2', '3', '4'].map((level) => (
              <option key={level} value={level}>{level}</option>
            ))}
          </select>
        </Field>
        <Field label="ULS annual probability">
          <input
            className={inputClass}
            value={draft.annualProbabilityUls}
            onChange={(event) => update('annualProbabilityUls', event.target.value)}
          />
        </Field>
        <Field label="Reference height z (m)">
          <input
            type="number"
            step="0.1"
            min="0.1"
            className={inputClass}
            value={draft.referenceHeightM}
            onChange={(event) => update('referenceHeightM', Number(event.target.value))}
          />
        </Field>
        <Field label="Directional multiplier Md">
          <input
            type="number"
            step="0.05"
            min="0.1"
            className={inputClass}
            value={draft.directionMultiplier}
            onChange={(event) => update('directionMultiplier', Number(event.target.value))}
          />
        </Field>
        <Field label="Shielding multiplier Ms">
          <input
            type="number"
            step="0.05"
            min="0.1"
            className={inputClass}
            value={draft.shieldingMultiplier}
            onChange={(event) => update('shieldingMultiplier', Number(event.target.value))}
          />
        </Field>
        <Field label="Topographic multiplier Mt">
          <input
            type="number"
            step="0.05"
            min="0.1"
            className={inputClass}
            value={draft.topographicMultiplier}
            onChange={(event) => update('topographicMultiplier', Number(event.target.value))}
          />
        </Field>
      </div>

      <div className="mt-3 space-y-1 rounded border border-amber-500/30 bg-amber-950/20 p-2 text-[9px] text-amber-100">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={draft.regionVerified}
            onChange={(event) => update('regionVerified', event.target.checked)}
          />
          Region checked against AS/NZS 1170.2 Figure 3.1(A)
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={draft.tablesVerified}
            onChange={(event) => update('tablesVerified', event.target.checked)}
          />
          Starter V<sub>R</sub>/M<sub>z,cat</sub> values checked against the project Standard
        </label>
      </div>

      {result && (
        <div className={`mt-3 rounded border p-2 text-[9px] ${
          result.region_conflict
            ? 'border-red-500/40 bg-red-950/30 text-red-200'
            : 'border-emerald-500/30 bg-emerald-950/20 text-emerald-200'
        }`}>
          <div className="font-semibold">
            Selected {result.selected_region}
            {result.suggested_region ? ` · map ${result.suggested_region}` : ''}
            {' · '}q<sub>z</sub> {result.q_z_kPa.toFixed(6)} kPa
          </div>
          <div className="mt-1 font-mono text-slate-300">
            V<sub>R</sub> {result.regional_wind_speed_m_s.toFixed(3)} m/s ·
            M<sub>z,cat</sub> {result.terrain_height_multiplier.toFixed(4)} ·
            V<sub>sit</sub> {result.site_wind_speed_m_s.toFixed(3)} m/s
          </div>
          {comparison != null && Math.abs(comparison) > 1e-6 && (
            <div className="mt-1">
              Draft differs from compiled design.py by {comparison > 0 ? '+' : ''}
              {comparison.toFixed(6)} kPa.
            </div>
          )}
        </div>
      )}

      {status && (
        <div className="mt-2 text-[9px] text-slate-300">{status}</div>
      )}

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={isBusy}
          onClick={() => void calculate()}
          className="flex-1 rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1.5 text-[9px] font-semibold text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
        >
          Calculate draft
        </button>
        <button
          type="button"
          disabled={isBusy}
          onClick={() => void applyToDesign()}
          className="flex-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1.5 text-[9px] font-semibold text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50"
        >
          Apply &amp; compile design.py
        </button>
      </div>
    </section>
  )
}
