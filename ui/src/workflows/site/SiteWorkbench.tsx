import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import { resolveWorkflowServerUrl } from '../shared/apiConfig'
import { ACTIVE_PROJECT_CHANGED_EVENT } from '../shared/ui/ProjectSelector'
import { GuestWorkflowNotice } from '../shared/ui/GuestWorkflowNotice'
import { WindRegionMap } from '../structural/WindRegionMap'
import type {
  SiteCalculation,
  SiteDefinition,
  SiteWorkbenchResponse,
} from './contracts'


export const SITE_BASIS_CHANGED_EVENT = 'tertius:site-basis-changed'

const WIND_REGIONS = ['A0', 'A1', 'A2', 'A3', 'A4', 'A5', 'B1', 'B2', 'C', 'D']
const BUILDING_CLASSIFICATIONS = [
  ['1a', 'Class 1a — house or attached dwelling'],
  ['1b', 'Class 1b — small boarding/guest house or holiday accommodation'],
  ['2', 'Class 2 — apartment building'],
  ['3', 'Class 3 — residential building other than Class 1 or 2'],
  ['4', 'Class 4 — dwelling in a Class 5–9 building'],
  ['5', 'Class 5 — office'],
  ['6', 'Class 6 — shop, restaurant or public-facing service'],
  ['7a', 'Class 7a — car park'],
  ['7b', 'Class 7b — storage or wholesale building'],
  ['8', 'Class 8 — laboratory, factory or process building'],
  ['9a', 'Class 9a — health-care building'],
  ['9b', 'Class 9b — assembly building'],
  ['9c', 'Class 9c — residential care building'],
  ['10a', 'Class 10a — non-habitable garage, carport or shed'],
  ['10b', 'Class 10b — fence, mast, retaining wall, pool or similar structure'],
  ['10c', 'Class 10c — private bushfire shelter'],
] as const
const STANDARD_OPTIONS = {
  combinations: ['AS/NZS 1170.0:2002'],
  permanent_and_imposed: ['AS/NZS 1170.1:2002'],
  wind: ['AS/NZS 1170.2:2021'],
} as const
const inputClass = (
  'w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 '
  + 'text-sm text-slate-100 outline-none focus:border-cyan-500'
)

function Field({ label, hint, children }: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="block text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
        {label}
      </span>
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  )
}

function errorDetail(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = payload.detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

function numberValue(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

type SiteWorkbenchProps = {
  isActive?: boolean
}

export function SiteWorkbench({ isActive = true }: SiteWorkbenchProps) {
  const { authMode, getAccessToken, login } = useAuth()
  const serverUrl = resolveWorkflowServerUrl('site', import.meta.env?.VITE_API_URL)
  const [projectName, setProjectName] = useState('')
  const [exists, setExists] = useState(false)
  const [draft, setDraft] = useState<SiteDefinition | null>(null)
  const [calculation, setCalculation] = useState<SiteCalculation | null>(null)
  const [source, setSource] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const requestId = useRef(0)
  const standardsSection = useRef<HTMLElement>(null)

  const load = useCallback(async () => {
    if (!isActive || authMode !== 'authenticated') return
    const currentRequest = ++requestId.current
    setIsBusy(true)
    setError(null)
    try {
      const response = await apiFetch(`${serverUrl}/active`, getAccessToken)
      const payload = await response.json().catch(() => null) as
        | SiteWorkbenchResponse
        | { detail?: string }
        | null
      if (!response.ok) {
        throw new Error(errorDetail(payload, `Site definition returned ${response.status}`))
      }
      if (currentRequest !== requestId.current) return
      const next = payload as SiteWorkbenchResponse
      setProjectName(next.project_name)
      setExists(next.exists)
      setDraft(next.site_dict)
      setCalculation(next.calculation)
      setSource(next.source)
      setIsDirty(false)
      setStatus(
        next.exists
          ? `Loaded ${next.filename} revision ${next.calculation.revision}.`
          : 'No tertius_site.py yet — this is the starter definition.',
      )
    } catch (loadError) {
      if (currentRequest !== requestId.current) return
      setError(loadError instanceof Error ? loadError.message : 'Could not load site definition')
    } finally {
      if (currentRequest === requestId.current) setIsBusy(false)
    }
  }, [authMode, getAccessToken, isActive, serverUrl])

  useEffect(() => {
    void load()
    const handleProjectChange = () => void load()
    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleProjectChange)
    return () => {
      window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleProjectChange)
      requestId.current += 1
    }
  }, [load])

  const edit = useCallback((next: SiteDefinition) => {
    setDraft(next)
    setIsDirty(true)
    setCalculation(null)
    setStatus('Unsaved site inputs.')
  }, [])

  const updateProjectBasis = <K extends keyof SiteDefinition['project_basis']>(
    key: K,
    value: SiteDefinition['project_basis'][K],
  ) => {
    if (!draft) return
    edit({ ...draft, project_basis: { ...draft.project_basis, [key]: value } })
  }

  const updateStandards = <K extends keyof SiteDefinition['project_basis']['standards']>(
    key: K,
    value: SiteDefinition['project_basis']['standards'][K],
  ) => {
    if (!draft) return
    edit({
      ...draft,
      project_basis: {
        ...draft.project_basis,
        standards: { ...draft.project_basis.standards, [key]: value },
      },
    })
  }

  const updateLocation = <K extends keyof SiteDefinition['location']>(
    key: K,
    value: SiteDefinition['location'][K],
  ) => {
    if (!draft) return
    edit({ ...draft, location: { ...draft.location, [key]: value } })
  }

  const updateWind = <K extends keyof SiteDefinition['wind']>(
    key: K,
    value: SiteDefinition['wind'][K],
  ) => {
    if (!draft) return
    edit({ ...draft, wind: { ...draft.wind, [key]: value } })
  }

  const calculate = useCallback(async () => {
    if (!draft) return null
    setIsBusy(true)
    setError(null)
    setStatus('Calculating derived wind basis…')
    try {
      const response = await apiFetch(`${serverUrl}/calculate`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      })
      const payload = await response.json().catch(() => null) as
        | SiteCalculation
        | { detail?: string }
        | null
      if (!response.ok) {
        throw new Error(errorDetail(payload, `Site calculation returned ${response.status}`))
      }
      const next = payload as SiteCalculation
      setCalculation(next)
      setStatus(`Derived qz = ${next.q_z_kPa.toFixed(6)} kPa. Not saved yet.`)
      return next
    } catch (calculationError) {
      setError(calculationError instanceof Error ? calculationError.message : 'Site calculation failed')
      return null
    } finally {
      setIsBusy(false)
    }
  }, [draft, getAccessToken, serverUrl])

  const save = useCallback(async () => {
    if (!draft) return
    setIsBusy(true)
    setError(null)
    setStatus('Saving canonical tertius_site.py…')
    try {
      const response = await apiFetch(`${serverUrl}/active`, getAccessToken, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      })
      const payload = await response.json().catch(() => null) as
        | SiteWorkbenchResponse
        | { detail?: string }
        | null
      if (!response.ok) {
        throw new Error(errorDetail(payload, `Site save returned ${response.status}`))
      }
      const next = payload as SiteWorkbenchResponse
      setExists(true)
      setDraft(next.site_dict)
      setCalculation(next.calculation)
      setSource(next.source)
      setIsDirty(false)
      setStatus(
        `Saved ${next.filename} revision ${next.calculation.revision}; `
        + 'structural Actions will recompute without a CAD rebuild.',
      )
      window.dispatchEvent(new CustomEvent(SITE_BASIS_CHANGED_EVENT, {
        detail: {
          projectName: next.project_name,
          revision: next.calculation.revision,
        },
      }))
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Could not save site definition')
    } finally {
      setIsBusy(false)
    }
  }, [draft, getAccessToken, serverUrl])

  const pickCoordinates = useCallback(async (latitude: number, longitude: number) => {
    if (!draft) return
    const coordinateDraft = {
      ...draft,
      location: { ...draft.location, latitude, longitude },
    }
    edit(coordinateDraft)
    setStatus('Looking up the wind-region overlay…')
    try {
      const response = await apiFetch(
        `${serverUrl}/wind/region?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`,
        getAccessToken,
      )
      const payload = await response.json().catch(() => null) as
        | {
          region?: string | null
          area?: string | null
          source?: string
          approximate?: boolean
          detail?: string
        }
        | null
      if (!response.ok) {
        throw new Error(errorDetail(payload, `Wind-region lookup returned ${response.status}`))
      }
      if (!payload?.region) {
        setStatus(payload?.detail || 'No wind region was found at these coordinates.')
        return
      }
      edit({
        ...coordinateDraft,
        wind: {
          ...coordinateDraft.wind,
          region: payload.region,
          region_area: payload.area || coordinateDraft.wind.region_area,
          region_source: payload.source || coordinateDraft.wind.region_source,
          region_approximate: payload.approximate ?? true,
          region_status: 'suggested',
        },
      })
      setStatus(`Map suggests ${payload.region}; verify it against the nominated standard figure.`)
    } catch (lookupError) {
      setError(lookupError instanceof Error ? lookupError.message : 'Wind-region lookup failed')
    }
  }, [draft, edit, getAccessToken, serverUrl])

  const geocode = useCallback(async () => {
    if (!draft?.location.address.trim()) return
    setIsBusy(true)
    setError(null)
    setStatus('Finding the site address…')
    try {
      const response = await fetch(
        'https://nominatim.openstreetmap.org/search'
          + `?format=json&limit=1&q=${encodeURIComponent(draft.location.address)}`,
        { headers: { Accept: 'application/json' } },
      )
      if (!response.ok) throw new Error(`Address search returned ${response.status}`)
      const payload = await response.json()
      if (!Array.isArray(payload) || !payload[0]) throw new Error('Address was not found')
      await pickCoordinates(Number(payload[0].lat), Number(payload[0].lon))
    } catch (geocodeError) {
      setError(geocodeError instanceof Error ? geocodeError.message : 'Address search failed')
    } finally {
      setIsBusy(false)
    }
  }, [draft, pickCoordinates])

  const missing = useMemo(() => {
    if (!draft) return []
    const values: { id: string, label: string }[] = []
    if (!draft.location.address.trim()) values.push({ id: 'site-address', label: 'enter the site address' })
    if (draft.wind.region_status !== 'verified') values.push({ id: 'wind-region', label: 'verify the wind region' })
    if (draft.wind.table_status !== 'verified') values.push({ id: 'wind-table', label: 'verify the wind tables' })
    if (!draft.project_basis.standards.confirmed) {
      values.push({ id: 'action-standards', label: 'confirm the three selected action-standard editions' })
    }
    return values
  }, [draft])

  const importanceRecommendation = useMemo(() => {
    if (!draft) return null
    const classification = draft.project_basis.building_classification
    const use = draft.project_basis.building_use.toLowerCase()
    if (classification === '10a' || classification === '10b') {
      const isolatedMinor = /\b(isolated|minor|farm)\b/.test(use)
      return {
        level: isolatedMinor ? '1' : '2',
        reason: isolatedMinor
          ? 'NCC indicates Level 1 for isolated minor Class 10a/10b structures with low consequence of failure.'
          : 'NCC indicates Level 2 for Class 10a/10b structures associated with a Class 1 property; use and consequence still require confirmation.',
      }
    }
    if (['9a', '9b', '9c'].includes(classification)) {
      return {
        level: null,
        reason: 'Class 9 occupancy and capacity can require Level 3 or 4. Determine the consequence of failure before selecting a level.',
      }
    }
    return {
      level: '2',
      reason: 'Level 2 is the normal working basis unless the structure meets the low-hazard Level 1 criteria or the higher-consequence Level 3/4 criteria.',
    }
  }, [draft])

  if (authMode !== 'authenticated') {
    return (
      <GuestWorkflowNotice
        title="Site workbench requires an account"
        message="Log in to save a project-owned tertius_site.py and use it in Structural Actions."
        onLogin={login}
      />
    )
  }

  if (!draft) {
    return (
      <div className="flex h-full items-center justify-center bg-slate-950 text-sm text-slate-400">
        {error || 'Loading site definition…'}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-200">
      <header className="border-b border-slate-800 bg-slate-900/80 px-5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold text-slate-100">Site &amp; Design Basis Workbench</h1>
              <span className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-0.5 font-mono text-[10px] text-cyan-300">
                {projectName || 'active project'}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-400">
              One editable site dictionary; derived actions remain calculated evidence.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void calculate()}
              disabled={isBusy}
              className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-xs font-semibold hover:border-cyan-500 disabled:opacity-50"
            >
              Recalculate
            </button>
            <button
              type="button"
              onClick={() => void save()}
              disabled={isBusy || (!isDirty && exists)}
              className="rounded bg-cyan-600 px-3 py-2 text-xs font-bold text-white hover:bg-cyan-500 disabled:opacity-50"
            >
              {exists ? 'Save tertius_site.py' : 'Create tertius_site.py'}
            </button>
          </div>
        </div>
        {(status || error) && (
          <div className={`mt-2 text-xs ${error ? 'text-red-300' : 'text-cyan-200'}`}>
            {error || status}
          </div>
        )}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-[minmax(34rem,1.2fr)_minmax(30rem,1fr)]">
        <main className="space-y-4 border-r border-slate-800 p-4">
          <section className="rounded border border-slate-800 bg-slate-900/50 p-4">
            <div className="mb-3">
              <h2 className="font-semibold text-slate-100">Project design basis</h2>
              <p className="mt-1 text-xs text-slate-500">
                Importance Level follows the building use and consequence of failure, not its map position.
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Building use">
                <input className={inputClass} value={draft.project_basis.building_use}
                  onChange={(event) => updateProjectBasis('building_use', event.target.value)} />
              </Field>
              <Field label="Building classification">
                <select className={inputClass} value={draft.project_basis.building_classification}
                  onChange={(event) => updateProjectBasis('building_classification', event.target.value)}>
                  {BUILDING_CLASSIFICATIONS.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
              </Field>
              <Field label="Importance level">
                <select className={inputClass} value={draft.project_basis.importance_level}
                  onChange={(event) => updateProjectBasis('importance_level', event.target.value as '1' | '2' | '3' | '4')}>
                  {['1', '2', '3', '4'].map((level) => <option key={level} value={level}>Level {level}</option>)}
                </select>
              </Field>
              <Field label="Design life (years)">
                <input type="number" min="1" className={inputClass} value={draft.project_basis.design_life_years}
                  onChange={(event) => updateProjectBasis('design_life_years', numberValue(event.target.value))} />
              </Field>
              <div className="md:col-span-2">
                <Field label="Jurisdiction">
                  <input className={inputClass} value={draft.project_basis.jurisdiction}
                    onChange={(event) => updateProjectBasis('jurisdiction', event.target.value)} />
                </Field>
              </div>
              {importanceRecommendation && (
                <div className="md:col-span-2 rounded border border-cyan-500/30 bg-cyan-950/20 p-3 text-xs">
                  <div className="font-semibold text-cyan-200">
                    {importanceRecommendation.level
                      ? `NCC working recommendation: Importance Level ${importanceRecommendation.level}`
                      : 'NCC classification requires an Importance Level review'}
                  </div>
                  <p className="mt-1 leading-5 text-slate-400">{importanceRecommendation.reason}</p>
                  {importanceRecommendation.level
                    && importanceRecommendation.level !== draft.project_basis.importance_level && (
                    <button type="button"
                      className="mt-2 rounded border border-cyan-500/50 px-2 py-1 font-semibold text-cyan-200 hover:bg-cyan-950"
                      onClick={() => updateProjectBasis('importance_level', importanceRecommendation.level as '1' | '2')}>
                      Use Level {importanceRecommendation.level}
                    </button>
                  )}
                </div>
              )}
            </div>
          </section>

          <section className="rounded border border-slate-800 bg-slate-900/50 p-4">
            <h2 className="font-semibold text-slate-100">Location &amp; regional wind basis</h2>
            <div className="mt-3">
              <WindRegionMap
                serverUrl={serverUrl}
                getAccessToken={getAccessToken}
                latitude={draft.location.latitude}
                longitude={draft.location.longitude}
                onPick={(latitude, longitude) => void pickCoordinates(latitude, longitude)}
              />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div className="md:col-span-2">
                <Field label="Site address">
                  <div className="flex gap-2">
                    <input id="site-address" className={inputClass} value={draft.location.address}
                      onChange={(event) => updateLocation('address', event.target.value)} />
                    <button type="button" disabled={isBusy || !draft.location.address.trim()}
                      onClick={() => void geocode()}
                      className="rounded border border-slate-700 px-3 text-xs hover:border-cyan-500 disabled:opacity-50">
                      Find
                    </button>
                  </div>
                </Field>
              </div>
              <Field label="Latitude">
                <input type="number" step="0.000001" className={inputClass} value={draft.location.latitude}
                  onChange={(event) => updateLocation('latitude', numberValue(event.target.value))} />
              </Field>
              <Field label="Longitude">
                <input type="number" step="0.000001" className={inputClass} value={draft.location.longitude}
                  onChange={(event) => updateLocation('longitude', numberValue(event.target.value))} />
              </Field>
              <Field label="Wind region">
                <select className={inputClass} value={draft.wind.region}
                  onChange={(event) => updateWind('region', event.target.value)}>
                  {WIND_REGIONS.map((region) => <option key={region} value={region}>{region}</option>)}
                </select>
              </Field>
              <Field label="Terrain category">
                <select className={inputClass} value={draft.wind.terrain_category}
                  onChange={(event) => updateWind('terrain_category', event.target.value as SiteDefinition['wind']['terrain_category'])}>
                  {['1', '2', '2.5', '3', '4'].map((category) => <option key={category}>{category}</option>)}
                </select>
              </Field>
              <Field label="Reference height z (m)" hint="A design input until geometry supplies it automatically.">
                <input type="number" min="0.1" step="0.1" className={inputClass} value={draft.wind.reference_height_m}
                  onChange={(event) => updateWind('reference_height_m', numberValue(event.target.value))} />
              </Field>
              <Field label="ULS annual probability" hint="Leave blank to derive from Importance Level.">
                <input className={inputClass} placeholder="e.g. 1/500" value={draft.wind.annual_probability_uls}
                  onChange={(event) => updateWind('annual_probability_uls', event.target.value)} />
              </Field>
            </div>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <label id="wind-region" className="flex items-start gap-2 rounded border border-slate-700 bg-slate-950/60 p-3 text-xs">
                <input type="checkbox" className="mt-0.5" checked={draft.wind.region_status === 'verified'}
                  onChange={(event) => updateWind('region_status', event.target.checked ? 'verified' : 'suggested')} />
                <span><b>Region checked</b><br /><span className="text-slate-500">Verified against the nominated wind-region figure.</span></span>
              </label>
              <label id="wind-table" className="flex items-start gap-2 rounded border border-slate-700 bg-slate-950/60 p-3 text-xs">
                <input type="checkbox" className="mt-0.5" checked={draft.wind.table_status === 'verified'}
                  onChange={(event) => updateWind('table_status', event.target.checked ? 'verified' : 'starter')} />
                <span><b>Wind tables checked</b><br /><span className="text-slate-500">Starter values checked against the project edition.</span></span>
              </label>
            </div>
          </section>

          <section className="rounded border border-slate-800 bg-slate-900/50 p-4">
            <h2 className="font-semibold text-slate-100">Exposure multipliers</h2>
            <p className="mt-1 text-xs text-slate-500">
              Nearby trees are not credited as beneficial shielding. Record only defensible project values.
            </p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <Field label="Climate Mc" hint="Leave blank for the region-table value.">
                <input type="number" min="0.01" step="0.01" className={inputClass}
                  value={draft.wind.climate_change_multiplier ?? ''}
                  placeholder="Automatic"
                  onChange={(event) => updateWind(
                    'climate_change_multiplier',
                    event.target.value.trim() ? numberValue(event.target.value) : null,
                  )} />
              </Field>
              <Field label="Direction Md">
                <input type="number" min="0.01" step="0.01" className={inputClass} value={draft.wind.direction_multiplier}
                  onChange={(event) => updateWind('direction_multiplier', numberValue(event.target.value))} />
              </Field>
              <Field label="Shielding Ms">
                <input type="number" min="0.01" step="0.01" className={inputClass} value={draft.wind.shielding_multiplier}
                  onChange={(event) => updateWind('shielding_multiplier', numberValue(event.target.value))} />
              </Field>
              <Field label="Topographic Mt">
                <input type="number" min="0.01" step="0.01" className={inputClass} value={draft.wind.topographic_multiplier}
                  onChange={(event) => updateWind('topographic_multiplier', numberValue(event.target.value))} />
              </Field>
            </div>
          </section>

          <section id="action-standards" ref={standardsSection}
            className={`rounded border bg-slate-900/50 p-4 ${
              draft.project_basis.standards.confirmed ? 'border-slate-800' : 'border-amber-500/70'
            }`}>
            <h2 className="font-semibold text-slate-100">Action standards</h2>
            <p className="mt-1 text-xs text-slate-500">
              These are the editions referenced by NCC 2022. Selecting an edition and confirming
              that it applies are separate, auditable decisions.
            </p>
            <div className="mt-3 grid gap-3">
              <Field label="Combinations edition">
                <select className={inputClass} value={draft.project_basis.standards.combinations}
                  onChange={(event) => updateStandards('combinations', event.target.value)}>
                  {STANDARD_OPTIONS.combinations.map((reference) => (
                    <option key={reference} value={reference}>{reference} — NCC 2022 referenced edition</option>
                  ))}
                </select>
              </Field>
              <Field label="Permanent and imposed actions edition">
                <select className={inputClass} value={draft.project_basis.standards.permanent_and_imposed}
                  onChange={(event) => updateStandards('permanent_and_imposed', event.target.value)}>
                  {STANDARD_OPTIONS.permanent_and_imposed.map((reference) => (
                    <option key={reference} value={reference}>{reference} — NCC 2022 referenced edition</option>
                  ))}
                </select>
              </Field>
              <Field label="Wind actions edition">
                <select className={inputClass} value={draft.project_basis.standards.wind}
                  onChange={(event) => updateStandards('wind', event.target.value)}>
                  {STANDARD_OPTIONS.wind.map((reference) => (
                    <option key={reference} value={reference}>{reference} — NCC 2022 referenced edition</option>
                  ))}
                </select>
              </Field>
              <label className={`flex items-start gap-2 rounded border p-3 text-xs ${
                draft.project_basis.standards.confirmed
                  ? 'border-emerald-500/40 bg-emerald-950/20'
                  : 'border-amber-400 bg-amber-950/40'
              }`}>
                <input type="checkbox" className="mt-0.5" checked={draft.project_basis.standards.confirmed}
                  onChange={(event) => updateStandards('confirmed', event.target.checked)} />
                <span>
                  <b>{draft.project_basis.standards.confirmed
                    ? 'Project editions confirmed'
                    : 'Missing: confirm these editions for this project'}</b>
                  <br />
                  <span className={draft.project_basis.standards.confirmed ? 'text-slate-500' : 'text-amber-200/80'}>
                    Tick after checking that all three selected references apply to this project.
                    Do not edit or delete part of a standard name.
                  </span>
                </span>
              </label>
            </div>
          </section>
        </main>

        <aside className="space-y-4 p-4">
          <section className={`rounded border p-4 ${calculation?.site_ready ? 'border-emerald-500/50 bg-emerald-950/20' : 'border-amber-500/50 bg-amber-950/20'}`}>
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-semibold text-slate-100">Derived action basis</h2>
              <span className={`rounded px-2 py-1 text-[10px] font-bold uppercase ${calculation?.site_ready ? 'bg-emerald-500/20 text-emerald-300' : 'bg-amber-500/20 text-amber-300'}`}>
                {calculation?.site_ready ? 'ready' : 'inputs incomplete'}
              </span>
            </div>
            {calculation ? (
              <div className="mt-4 grid grid-cols-2 gap-3">
                {[
                  ['Regional speed VR', `${calculation.regional_wind_speed_m_s.toFixed(2)} m/s`],
                  ['Terrain multiplier', calculation.terrain_height_multiplier.toFixed(3)],
                  ['Site speed Vsit', `${calculation.site_wind_speed_m_s.toFixed(3)} m/s`],
                  ['Dynamic pressure qz', `${calculation.q_z_kPa.toFixed(6)} kPa`],
                  ['ULS return period', `${calculation.annual_recurrence_interval_years} years`],
                  ['Verifier', calculation.verifier_hash],
                ].map(([label, value]) => (
                  <div key={label} className="rounded border border-slate-800 bg-slate-950/70 p-3">
                    <div className="text-[9px] font-bold uppercase tracking-wide text-slate-500">{label}</div>
                    <div className="mt-1 font-mono text-sm text-cyan-200">{value}</div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-xs text-slate-400">Recalculate to preview the derived wind basis.</p>
            )}
            {missing.length > 0 && (
              <div className="mt-3 rounded border border-amber-500/30 bg-amber-950/30 p-3 text-xs text-amber-200">
                <div className="font-bold">Incomplete fields</div>
                <ul className="mt-2 space-y-1">
                  {missing.map((item) => (
                    <li key={item.id}>
                      <button type="button" className="text-left underline decoration-amber-400/50 hover:text-white"
                        onClick={() => {
                          if (item.id === 'action-standards') {
                            standardsSection.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                          } else {
                            document.getElementById(item.id)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                          }
                        }}>
                        {item.label}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <section className="rounded border border-cyan-500/30 bg-cyan-950/10 p-4">
            <h2 className="font-semibold text-slate-100">Single source of truth</h2>
            <div className="mt-3 rounded bg-slate-950 p-3 font-mono text-xs text-cyan-200">
              from tertius_site import site_dict
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-400">
              This workbench writes only the inputs in <b>tertius_site.py</b>. Wind speed,
              qz, pressures, load combinations, and member demand remain repeatable calculated
              outputs. Saving this file invalidates no Build123D geometry.
            </p>
          </section>

          <details className="rounded border border-slate-800 bg-slate-900/50 p-4">
            <summary className="cursor-pointer text-sm font-semibold text-slate-200">
              Preview tertius_site.py
            </summary>
            <pre className="mt-3 max-h-[30rem] overflow-auto rounded bg-slate-950 p-3 text-[10px] leading-4 text-slate-300">
              {source || 'Save to generate the canonical source preview.'}
            </pre>
          </details>
        </aside>
      </div>
    </div>
  )
}
