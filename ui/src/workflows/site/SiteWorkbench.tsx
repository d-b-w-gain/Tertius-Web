import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import { resolveWorkflowServerUrl } from '../shared/apiConfig'
import { ACTIVE_PROJECT_CHANGED_EVENT } from '../shared/ui/ProjectSelector'
import { GuestWorkflowNotice } from '../shared/ui/GuestWorkflowNotice'
import { GisEvidencePanel } from './GisEvidencePanel'
import { DirectionalMultiplierEditor } from './DirectionalMultiplierEditor'
import { SiteExplorer } from './SiteExplorer'
import { StandardTableEvidencePanel } from './StandardTableEvidencePanel'
import { StructureWindRose } from './StructureWindRose'
import { WindMultiplierEvidencePanel } from './WindMultiplierEvidencePanel'
import type {
  CandidateModelSiteDimensions,
  GisGeocodeCandidate,
  GisBuildingEvidence,
  GisEvidenceManifest,
  GisDirectionalWindMultiplierEvidence,
  GisSiteBoundaryEvidence,
  SiteCalculation,
  SiteDefinition,
  SiteWorkbenchResponse,
  WindStandardEvidence,
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

function FeatureSection({ children }: { children: ReactNode }) {
  return <div>{children}</div>
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
  const extusServerUrl = resolveWorkflowServerUrl('extus', import.meta.env?.VITE_API_URL)
  const [projectName, setProjectName] = useState('')
  const [exists, setExists] = useState(false)
  const [draft, setDraft] = useState<SiteDefinition | null>(null)
  const [calculation, setCalculation] = useState<SiteCalculation | null>(null)
  const [source, setSource] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [geocodeCandidates, setGeocodeCandidates] = useState<GisGeocodeCandidate[]>([])
  const [terrainEvidence, setTerrainEvidence] = useState<GisEvidenceManifest | null>(null)
  const [siteBoundary, setSiteBoundary] = useState<GisSiteBoundaryEvidence | null>(null)
  const [buildingEvidence, setBuildingEvidence] = useState<GisBuildingEvidence | null>(null)
  const [directionalEvidence, setDirectionalEvidence] = useState<GisDirectionalWindMultiplierEvidence | null>(null)
  const [candidateDimensions, setCandidateDimensions] = useState<CandidateModelSiteDimensions | null>(null)
  const [standardEvidence, setStandardEvidence] = useState<WindStandardEvidence | null>(null)
  const [isReportBusy, setIsReportBusy] = useState(false)
  const requestId = useRef(0)
  const terrainFetchKey = useRef('')
  const boundaryFetchKey = useRef('')
  const buildingFetchKey = useRef('')
  const candidateDimensionsKey = useRef('')
  const hasLoaded = useRef(false)
  const draftRef = useRef<SiteDefinition | null>(null)
  const editVersion = useRef(0)
  const savedVersion = useRef(0)
  const saveInFlight = useRef(false)
  const saveQueued = useRef(false)
  const saveLatestRef = useRef<() => Promise<void>>(async () => undefined)
  const standardsSection = useRef<HTMLElement>(null)
  const siteLatitude = draft?.location.latitude
  const siteLongitude = draft?.location.longitude
  const terrainReference = draft?.terrain_evidence
  const buildingLatitude = draft?.structure.placement_latitude ?? siteLatitude
  const buildingLongitude = draft?.structure.placement_longitude ?? siteLongitude
  const buildingRadius = draft
    ? Math.max(50, 20 * draft.wind.reference_height_m)
    : undefined

  const edit = useCallback((next: SiteDefinition) => {
    draftRef.current = next
    editVersion.current += 1
    setDraft(next)
    setIsDirty(true)
    setSaveError(null)
    setCalculation(null)
    setStatus('Site changes waiting to autosave…')
  }, [])

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
      hasLoaded.current = true
      setProjectName(next.project_name)
      setExists(next.exists)
      draftRef.current = next.site_dict
      editVersion.current = 0
      savedVersion.current = 0
      saveQueued.current = false
      setSaveError(null)
      setDraft(next.site_dict)
      setCalculation(next.calculation)
      setStandardEvidence(next.calculation.standard_table_evidence ?? null)
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
    if (!isActive || authMode !== 'authenticated') return
    const latitude = siteLatitude
    const longitude = siteLongitude
    if (typeof latitude !== 'number' || typeof longitude !== 'number'
      || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return
    const key = `${latitude.toFixed(6)},${longitude.toFixed(6)}`
    if (terrainFetchKey.current === key) return

    let cancelled = false
    const timer = window.setTimeout(() => {
      terrainFetchKey.current = key
      const acquire = async () => {
        const reference = terrainReference
        const matchesSavedReference = Boolean(
          reference
          && Math.abs(reference.site_latitude - latitude) <= 1e-6
          && Math.abs(reference.site_longitude - longitude) <= 1e-6,
        )
        if (reference && matchesSavedReference) {
          const restored = await apiFetch(
            `${serverUrl}/gis/evidence/${reference.evidence_id}`,
            getAccessToken,
          )
          if (restored.ok) {
            return {
              manifest: await restored.json() as GisEvidenceManifest,
              restored: true,
            }
          }
        }
        const query = new URLSearchParams({
          latitude: String(latitude),
          longitude: String(longitude),
          radius_m: '2000',
        })
        const response = await apiFetch(`${serverUrl}/gis/terrain/site?${query}`, getAccessToken, {
          method: 'POST',
        })
        const payload = await response.json().catch(() => null) as
          | GisEvidenceManifest
          | { detail?: string }
          | null
        if (!response.ok) {
          throw new Error(errorDetail(payload, `Terrain acquisition returned ${response.status}`))
        }
        return { manifest: payload as GisEvidenceManifest, restored: false }
      }
      setStatus(terrainReference
        ? 'Restoring the adopted terrain evidence from the GIS cache...'
        : 'Loading the best cached terrain for this site...')
      void acquire().then(({ manifest, restored }) => {
        if (cancelled) return
        setTerrainEvidence(manifest)
        if (!restored) {
          const reference = {
            evidence_id: manifest.evidence_id,
            site_latitude: latitude,
            site_longitude: longitude,
            radius_m: 2000,
          }
          const currentDraft = draftRef.current
          if (currentDraft) edit({ ...currentDraft, terrain_evidence: reference })
        }
        setStatus(
          restored
            ? `${manifest.source.provider} terrain restored from the adopted cache record; no terrain was downloaded.`
            : `${manifest.source.provider} terrain loaded once and attached to this site; future reloads reuse this evidence ID.`,
        )
      }).catch((terrainError) => {
        if (cancelled) return
        terrainFetchKey.current = ''
        setError(terrainError instanceof Error ? terrainError.message : 'Terrain acquisition failed')
      })
    }, 600)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [
    authMode,
    getAccessToken,
    isActive,
    serverUrl,
    siteLatitude,
    siteLongitude,
    terrainReference,
    edit,
  ])

  useEffect(() => {
    if (!isActive || authMode !== 'authenticated'
      || typeof buildingLatitude !== 'number'
      || typeof buildingLongitude !== 'number'
      || typeof buildingRadius !== 'number') return
    const key = `${buildingLatitude.toFixed(6)},${buildingLongitude.toFixed(6)},${buildingRadius.toFixed(1)}`
    if (buildingFetchKey.current === key) return
    buildingFetchKey.current = key
    const query = new URLSearchParams({
      latitude: String(buildingLatitude),
      longitude: String(buildingLongitude),
      radius_m: String(buildingRadius),
    })
    let cancelled = false
    void apiFetch(`${serverUrl}/gis/buildings/site?${query}`, getAccessToken)
      .then(async (response) => {
        const payload = await response.json().catch(() => null) as GisBuildingEvidence | { detail?: string } | null
        if (!response.ok) throw new Error(errorDetail(payload, `Building evidence returned ${response.status}`))
        return payload as GisBuildingEvidence
      })
      .then((evidence) => { if (!cancelled) setBuildingEvidence(evidence) })
      .catch(() => {
        if (cancelled) return
        buildingFetchKey.current = ''
        setBuildingEvidence(null)
    })
    return () => { cancelled = true }
  }, [
    authMode,
    buildingLatitude,
    buildingLongitude,
    buildingRadius,
    getAccessToken,
    isActive,
    serverUrl,
  ])

  useEffect(() => {
    if (!isActive || authMode !== 'authenticated') return
    const latitude = siteLatitude
    const longitude = siteLongitude
    if (typeof latitude !== 'number' || typeof longitude !== 'number'
      || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return
    const key = `${latitude.toFixed(6)},${longitude.toFixed(6)}`
    if (boundaryFetchKey.current === key) return
    boundaryFetchKey.current = key
    const query = new URLSearchParams({
      latitude: String(latitude),
      longitude: String(longitude),
    })
    let cancelled = false
    void apiFetch(`${serverUrl}/gis/cadastre/site?${query}`, getAccessToken)
      .then(async (response) => {
        const payload = await response.json().catch(() => null) as
          | GisSiteBoundaryEvidence
          | { detail?: string }
          | null
        if (!response.ok) {
          throw new Error(errorDetail(payload, `Property boundary returned ${response.status}`))
        }
        if (
          !payload
          || !('schema_version' in payload)
          || payload.schema_version !== 'tertius.gis.site-boundary.v1'
          || !('feature' in payload)
        ) {
          throw new Error('Property boundary returned an unexpected response')
        }
        return payload as GisSiteBoundaryEvidence
      })
      .then((boundary) => {
        if (!cancelled) setSiteBoundary(boundary)
      })
      .catch(() => {
        if (cancelled) return
        boundaryFetchKey.current = ''
        setSiteBoundary(null)
      })
    return () => { cancelled = true }
  }, [
    authMode,
    getAccessToken,
    isActive,
    serverUrl,
    siteLatitude,
    siteLongitude,
  ])

  useEffect(() => {
    if (authMode !== 'authenticated') {
      hasLoaded.current = false
    } else if (isActive && !hasLoaded.current) {
      void load()
    }
    const handleProjectChange = () => {
      hasLoaded.current = false
      terrainFetchKey.current = ''
      boundaryFetchKey.current = ''
      buildingFetchKey.current = ''
      candidateDimensionsKey.current = ''
      setTerrainEvidence(null)
      setSiteBoundary(null)
      setBuildingEvidence(null)
      setDirectionalEvidence(null)
      setCandidateDimensions(null)
      if (isActive) void load()
    }
    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleProjectChange)
    return () => {
      window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleProjectChange)
      requestId.current += 1
    }
  }, [authMode, isActive, load])

  const applyCandidateModelDimensions = useCallback((dimensions: CandidateModelSiteDimensions) => {
    setCandidateDimensions((current) => (
      current?.model_artifact_id === dimensions.model_artifact_id ? current : dimensions
    ))
    if (!draft || candidateDimensionsKey.current === dimensions.model_artifact_id) return
    candidateDimensionsKey.current = dimensions.model_artifact_id
    const differs = (
      Math.abs(draft.structure.footprint_length_m - dimensions.footprint_length_m) > 1e-3
      || Math.abs(draft.structure.footprint_width_m - dimensions.footprint_width_m) > 1e-3
      || Math.abs(draft.wind.reference_height_m - dimensions.reference_height_m) > 1e-3
    )
    if (!differs) return
    edit({
      ...draft,
      structure: {
        ...draft.structure,
        footprint_length_m: dimensions.footprint_length_m,
        footprint_width_m: dimensions.footprint_width_m,
      },
      wind: {
        ...draft.wind,
        reference_height_m: dimensions.reference_height_m,
        cardinal_terrain_height_multipliers: null,
        cardinal_shielding_multipliers: null,
        cardinal_topographic_multipliers: null,
        multiplier_evidence: null,
      },
    })
    buildingFetchKey.current = ''
    setDirectionalEvidence(null)
    setStatus(
      `Synced the active candidate model: ${dimensions.footprint_length_m.toFixed(2)} × `
      + `${dimensions.footprint_width_m.toFixed(2)} m, wind reference height `
      + `${dimensions.reference_height_m.toFixed(2)} m. Rebuilding directional evidence.`,
    )
  }, [draft, edit])

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

  const updateWindRegion = (region: string) => {
    if (!draft || region === draft.wind.region) return
    edit({
      ...draft,
      wind: {
        ...draft.wind,
        region,
        cardinal_direction_multipliers: null,
        climate_change_multiplier: null,
        table_status: 'starter',
      },
    })
  }

  const updateStructure = (structure: SiteDefinition['structure']) => {
    if (!draft) return
    edit({ ...draft, structure })
  }

  const updateActionEnvelope = <
    K extends keyof SiteDefinition['wind']['action_envelope'],
  >(
    key: K,
    value: SiteDefinition['wind']['action_envelope'][K],
  ) => {
    if (!draft) return
    edit({
      ...draft,
      wind: {
        ...draft.wind,
        action_envelope: {
          ...draft.wind.action_envelope,
          [key]: value,
        },
      },
    })
  }

  const saveLatest = useCallback(async () => {
    if (saveInFlight.current) {
      saveQueued.current = true
      return
    }
    const candidate = draftRef.current
    const version = editVersion.current
    const activeRequest = requestId.current
    if (!candidate || version <= savedVersion.current) return

    saveInFlight.current = true
    saveQueued.current = false
    setIsSaving(true)
    setSaveError(null)
    setStatus('Autosaving site inputs and recalculating the wind basis…')
    let succeeded = false
    try {
      const response = await apiFetch(`${serverUrl}/active`, getAccessToken, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(candidate),
      })
      const payload = await response.json().catch(() => null) as
        | SiteWorkbenchResponse
        | { detail?: string }
        | null
      if (!response.ok) {
        throw new Error(errorDetail(payload, `Site save returned ${response.status}`))
      }
      if (activeRequest !== requestId.current) return
      const next = payload as SiteWorkbenchResponse
      succeeded = true
      setError(null)
      savedVersion.current = Math.max(savedVersion.current, version)
      setExists(true)
      setSource(next.source)
      if (editVersion.current === version) {
        draftRef.current = next.site_dict
        setDraft(next.site_dict)
        setCalculation(next.calculation)
        setStandardEvidence(next.calculation.standard_table_evidence ?? null)
        setIsDirty(false)
        setStatus(`All site changes saved · revision ${next.calculation.revision}.`)
      } else {
        saveQueued.current = true
        setStatus('A newer site change is waiting to autosave…')
      }
      window.dispatchEvent(new CustomEvent(SITE_BASIS_CHANGED_EVENT, {
        detail: {
          projectName: next.project_name,
          revision: next.calculation.revision,
        },
      }))
    } catch (saveError) {
      if (activeRequest !== requestId.current) return
      const message = saveError instanceof Error ? saveError.message : 'Could not save site definition'
      setSaveError(message)
      setError(`${message}. Your changes are still present; use Retry save.`)
    } finally {
      saveInFlight.current = false
      if (activeRequest !== requestId.current) {
        setIsSaving(false)
      } else {
        const shouldContinue = succeeded && (
          saveQueued.current || editVersion.current > savedVersion.current
        )
        saveQueued.current = false
        if (shouldContinue) {
          window.setTimeout(() => void saveLatestRef.current(), 0)
        } else {
          setIsSaving(false)
        }
      }
    }
  }, [getAccessToken, serverUrl])

  useEffect(() => {
    saveLatestRef.current = saveLatest
  }, [saveLatest])

  useEffect(() => {
    if (!isDirty || !draft) return
    const timer = window.setTimeout(() => void saveLatest(), 500)
    return () => window.clearTimeout(timer)
  }, [draft, isDirty, saveLatest])

  const downloadSiteReport = useCallback(async () => {
    if (!draft) return
    setIsReportBusy(true)
    setError(null)
    setStatus('Building the site wind evidence report...')
    try {
      const response = await apiFetch(`${serverUrl}/report/site-wind.pdf`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      })
      if (!response.ok) throw new Error(`Site report returned ${response.status}`)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'tertius-site-wind-basis.pdf'
      anchor.click()
      URL.revokeObjectURL(url)
      setStatus(
        'Downloaded the PDF evidence report with cached building heights, satellite '
        + 'placement and parcel, terrain heat map, four primary x-z plots from eight sampled directions, calculation ledger '
        + 'and supplied 2021 standard extracts.',
      )
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : 'Could not download site report')
    } finally {
      setIsReportBusy(false)
    }
  }, [draft, getAccessToken, serverUrl])

  const applyStandardTableValues = useCallback((evidence: WindStandardEvidence) => {
    if (!draft || evidence.region !== draft.wind.region) return
    edit({
      ...draft,
      wind: {
        ...draft.wind,
        cardinal_direction_multipliers: evidence.direction_multipliers,
        climate_change_multiplier: evidence.climate_change_multiplier,
        table_status: 'starter',
      },
    })
    setStatus(
      `Applied Table 3.2(A) Md and Table 3.3 Mc for ${evidence.region}; `
      + 'licensed-standard verification is still required.',
    )
  }, [draft, edit])

  const applyMultiplierEvidence = useCallback((
    components: Array<'M_z_cat' | 'M_s' | 'M_t'>,
    evidence: GisDirectionalWindMultiplierEvidence,
  ) => {
    if (!draft) return
    setDirectionalEvidence(evidence)
    const currentEvidence = draft.wind.multiplier_evidence
    if (
      currentEvidence?.evidence_id === evidence.evidence_id
      && components.length === currentEvidence.adopted_components.length
      && components.every((component) => currentEvidence.adopted_components.includes(component))
    ) return
    const componentField = {
      M_z_cat: 'cardinal_terrain_height_multipliers',
      M_s: 'cardinal_shielding_multipliers',
      M_t: 'cardinal_topographic_multipliers',
    } as const
    const componentValues = {
      M_z_cat: evidence.terrain_height_multipliers,
      M_s: evidence.shielding_multipliers,
      M_t: evidence.topographic_multipliers,
    }
    const nextWind = { ...draft.wind }
    for (const component of components) {
      nextWind[componentField[component]] = componentValues[component]
    }
    edit({
      ...draft,
      wind: {
        ...nextWind,
        multiplier_evidence: {
          evidence_id: evidence.evidence_id,
          provider: evidence.provider,
          dataset: evidence.dataset,
          dataset_version: evidence.dataset_version,
          source_uri: evidence.source_uri,
          site_latitude: evidence.latitude,
          site_longitude: evidence.longitude,
          terrain_reference_height_m: evidence.terrain_reference_height_m,
          method_status: evidence.method_status,
          terrain_evidence_id: evidence.terrain_evidence_id ?? null,
          placement_latitude: evidence.placement_latitude ?? null,
          placement_longitude: evidence.placement_longitude ?? null,
          footprint_length_m: evidence.footprint_length_m ?? null,
          footprint_width_m: evidence.footprint_width_m ?? null,
          front_bearing_degrees: evidence.front_bearing_degrees ?? null,
          adopted_components: components,
          review_status: 'suggested',
          review_reason: 'Automatically applied as best-available preliminary evidence; licensed-standard verification remains a separate certification step.',
        },
      },
    })
    setStatus(`Automatically applied pinned GIS values for ${components.join(', ')} to the working calculation.`)
  }, [draft, edit])

  const pickAddressCoordinates = useCallback(async (latitude: number, longitude: number, address?: string) => {
    if (!draft) return
    const coordinateDraft = {
      ...draft,
      location: { ...draft.location, latitude, longitude, address: address || draft.location.address },
      structure: draft.structure.placement_latitude == null
        ? {
          ...draft.structure,
          placement_latitude: latitude,
          placement_longitude: longitude,
        }
        : draft.structure,
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

  const pickStructureCoordinates = useCallback((latitude: number, longitude: number) => {
    const currentDraft = draftRef.current
    if (!currentDraft) return
    const next = {
      ...currentDraft,
      structure: {
        ...currentDraft.structure,
        placement_latitude: latitude,
        placement_longitude: longitude,
      },
    }
    setDirectionalEvidence(null)
    edit(next)
    setStatus('Shed placement changed; autosave will retain the full site definition.')
  }, [edit])

  const geocode = useCallback(async () => {
    if (!draft?.location.address.trim()) return
    setIsBusy(true)
    setError(null)
    setGeocodeCandidates([])
    setStatus('Finding the site address…')
    try {
      const query = new URLSearchParams({ query: draft.location.address, limit: '5' })
      const response = await apiFetch(
        `${serverUrl}/gis/geocode?${query}`,
        getAccessToken,
      )
      if (!response.ok) throw new Error(`Address search returned ${response.status}`)
      const payload = await response.json() as GisGeocodeCandidate[]
      if (!Array.isArray(payload) || !payload[0]) {
        throw new Error('No G-NAF address point was found; check the street number, suburb and postcode')
      }
      if (payload.length === 1) {
        await pickAddressCoordinates(payload[0].latitude, payload[0].longitude, payload[0].address)
        setStatus(`G-NAF address point selected: ${payload[0].address}`)
      } else {
        setGeocodeCandidates(payload)
        setStatus('Select the matching G-NAF address point.')
      }
    } catch (geocodeError) {
      setError(geocodeError instanceof Error ? geocodeError.message : 'Address search failed')
    } finally {
      setIsBusy(false)
    }
  }, [draft, getAccessToken, pickAddressCoordinates, serverUrl])

  const missing = useMemo(() => {
    if (!draft) return []
    const values: { id: string, label: string }[] = []
    if (!draft.location.address.trim()) values.push({ id: 'site-address', label: 'enter the site address' })
    if (draft.structure.orientation_status !== 'verified') {
      values.push({ id: 'structure-orientation', label: 'verify the structure bearing against site north' })
    }
    if (draft.wind.cardinal_direction_multipliers === null) {
      values.push({ id: 'cardinal-multipliers', label: 'enter the eight cardinal direction multipliers' })
    }
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

  const workingBasisReady = calculation?.working_basis_ready ?? calculation?.site_ready ?? false
  const certificationReady = calculation?.certification_ready ?? calculation?.site_ready ?? false

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
            <div
              aria-live="polite"
              className={`rounded border px-3 py-2 text-xs font-semibold ${
                saveError
                  ? 'border-red-500/60 bg-red-950/30 text-red-200'
                  : isSaving || isDirty
                    ? 'border-amber-500/50 bg-amber-950/20 text-amber-200'
                    : 'border-emerald-500/40 bg-emerald-950/20 text-emerald-200'
              }`}
            >
              {saveError
                ? 'Autosave needs attention'
                : isSaving
                  ? 'Saving changes…'
                  : isDirty
                    ? 'Autosave pending…'
                    : exists
                      ? 'All changes saved'
                      : 'Edit a setting to create the Site file'}
            </div>
            {saveError && (
              <button
                type="button"
                onClick={() => void saveLatest()}
                className="rounded border border-red-500/60 bg-red-950/30 px-3 py-2 text-xs font-bold text-red-100 hover:bg-red-950/60"
              >
                Retry save
              </button>
            )}
            <button
              type="button"
              onClick={() => void downloadSiteReport()}
              disabled={isBusy || isReportBusy}
              title="Includes satellite placement, cached terrain, calculations and supplied 2021 standard extracts"
              className="rounded border border-cyan-500/60 bg-cyan-950/30 px-3 py-2 text-xs font-bold text-cyan-100 hover:bg-cyan-950/60 disabled:opacity-50"
            >
              {isReportBusy ? 'Building PDF...' : 'Download PDF evidence report'}
            </button>
          </div>
        </div>
        {(status || error) && (
          <div className={`mt-2 text-xs ${error ? 'text-red-300' : 'text-cyan-200'}`}>
            {error || status}
          </div>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto xl:flex-row xl:overflow-hidden">
        <main className="min-w-0 flex-1 space-y-3 overflow-y-auto p-4">
          <SiteExplorer
            projectName={projectName}
            serverUrl={serverUrl}
            extusServerUrl={extusServerUrl}
            getAccessToken={getAccessToken}
            latitude={draft.structure.placement_latitude ?? draft.location.latitude}
            longitude={draft.structure.placement_longitude ?? draft.location.longitude}
            footprintLengthM={draft.structure.footprint_length_m}
            footprintWidthM={draft.structure.footprint_width_m}
            frontBearingDegrees={draft.structure.front_bearing_degrees}
            referenceHeightM={draft.wind.reference_height_m}
            cardinalMultipliers={draft.wind.cardinal_direction_multipliers}
            terrainEvidenceId={terrainEvidence?.evidence_id || draft.terrain_evidence?.evidence_id || null}
            terrainEvidenceBounds={terrainEvidence?.asset?.crs === 'EPSG:4326'
              ? terrainEvidence.asset.bounds
              : null}
            siteBoundary={siteBoundary}
            buildingEvidence={buildingEvidence}
            directionalEvidence={directionalEvidence}
            onCandidateDimensions={applyCandidateModelDimensions}
            onPick={pickStructureCoordinates}
          />
          <FeatureSection>
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
          </FeatureSection>

          <FeatureSection>
          <section className="rounded border border-slate-800 bg-slate-900/50 p-4">
            <h2 className="font-semibold text-slate-100">Location &amp; regional wind basis</h2>
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
                  {geocodeCandidates.length > 0 && (
                    <div className="mt-2 space-y-1 rounded border border-cyan-500/30 bg-cyan-950/20 p-2">
                      {geocodeCandidates.map((candidate) => (
                        <button key={candidate.address_pid} type="button"
                          className="block w-full rounded border border-slate-700 px-2 py-1.5 text-left text-xs hover:border-cyan-400"
                          onClick={() => {
                            setGeocodeCandidates([])
                            void pickAddressCoordinates(candidate.latitude, candidate.longitude, candidate.address)
                          }}>
                          <span className="text-slate-200">{candidate.address}</span>
                          <span className="ml-2 font-mono text-[10px] text-cyan-400">G-NAF address point</span>
                        </button>
                      ))}
                    </div>
                  )}
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
                  onChange={(event) => updateWindRegion(event.target.value)}>
                  {WIND_REGIONS.map((region) => <option key={region} value={region}>{region}</option>)}
                </select>
              </Field>
              <Field label="Terrain category">
                <select className={inputClass} value={draft.wind.terrain_category}
                  onChange={(event) => updateWind('terrain_category', event.target.value as SiteDefinition['wind']['terrain_category'])}>
                  {['1', '2', '2.5', '3', '4'].map((category) => <option key={category}>{category}</option>)}
                </select>
              </Field>
              <Field label="Reference height z (m)" hint={candidateDimensions
                ? `Active model: ${candidateDimensions.reference_height_basis}; overall height ${candidateDimensions.overall_height_m.toFixed(2)} m.`
                : 'Uses the active candidate geometry when available; otherwise remains an authored input.'}>
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
          </FeatureSection>

          <FeatureSection>
          <GisEvidencePanel
            serverUrl={serverUrl}
            getAccessToken={getAccessToken}
            latitude={draft.location.latitude}
            longitude={draft.location.longitude}
            initialEvidence={terrainEvidence}
            onEvidenceChange={setTerrainEvidence}
          />
          </FeatureSection>

          <div id="multiplier-evidence">
          <FeatureSection>
          <WindMultiplierEvidencePanel
            serverUrl={serverUrl}
            getAccessToken={getAccessToken}
            latitude={draft.location.latitude}
            longitude={draft.location.longitude}
            referenceHeightM={draft.wind.reference_height_m}
            terrainEvidenceId={terrainEvidence?.evidence_id ?? null}
            structure={draft.structure}
            windRegion={draft.wind.region}
            adoptedEvidence={draft.wind.multiplier_evidence ?? null}
            onEvidence={applyMultiplierEvidence}
          />
          </FeatureSection>
          </div>

          <div id="cardinal-multipliers">
          <FeatureSection>
          <StructureWindRose
            structure={draft.structure}
            multipliers={draft.wind.cardinal_direction_multipliers}
            fallbackMultiplier={draft.wind.direction_multiplier}
            calculation={calculation}
            onStructureChange={updateStructure}
            onMultipliersChange={(cardinalDirectionMultipliers) => updateWind(
              'cardinal_direction_multipliers', cardinalDirectionMultipliers,
            )}
          />
          </FeatureSection>
          </div>

          <FeatureSection>
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
              <Field label="Fallback direction Md" hint="Used for every direction until cardinal inputs are enabled.">
                <input type="number" min="0.01" step="0.01" className={inputClass} value={draft.wind.direction_multiplier}
                  disabled={draft.wind.cardinal_direction_multipliers !== null}
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
            <div className="mt-4 space-y-3">
              <DirectionalMultiplierEditor
                label="Terrain / height by direction"
                symbol="Mz,cat,β"
                values={draft.wind.cardinal_terrain_height_multipliers ?? null}
                fallback={calculation?.terrain_height_multiplier ?? 1}
                fallbackLabel={`Category ${draft.wind.terrain_category} table lookup at ${draft.wind.reference_height_m.toFixed(2)} m is used in every direction.`}
                onChange={(values) => updateWind('cardinal_terrain_height_multipliers', values)}
              />
              <DirectionalMultiplierEditor
                label="Shielding by direction"
                symbol="Ms,β"
                values={draft.wind.cardinal_shielding_multipliers ?? null}
                fallback={draft.wind.shielding_multiplier}
                fallbackLabel="The single conservative shielding value is used in every direction."
                onChange={(values) => updateWind('cardinal_shielding_multipliers', values)}
              />
              <DirectionalMultiplierEditor
                label="Topography by direction"
                symbol="Mt,β"
                values={draft.wind.cardinal_topographic_multipliers ?? null}
                fallback={draft.wind.topographic_multiplier}
                fallbackLabel="The single conservative topographic value is used in every direction."
                onChange={(values) => updateWind('cardinal_topographic_multipliers', values)}
              />
            </div>
          </section>
          </FeatureSection>

          <FeatureSection>
          <StandardTableEvidencePanel
            serverUrl={serverUrl}
            getAccessToken={getAccessToken}
            site={draft}
            evidence={standardEvidence}
            onApply={applyStandardTableValues}
          />
          </FeatureSection>

          <FeatureSection>
          <section className="rounded border border-cyan-500/40 bg-cyan-950/10 p-4">
            <h2 className="font-semibold text-slate-100">Working wind action envelope</h2>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              Define the credible operating basis once. Structural will solve the authored
              service cases and automatically display the governing one; this does not mark
              surface-zone coefficients or opening capacities as verified.
            </p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <Field label="Building envelope">
                <select className={inputClass} value={draft.wind.action_envelope.enclosure}
                  onChange={(event) => updateActionEnvelope(
                    'enclosure',
                    event.target.value as SiteDefinition['wind']['action_envelope']['enclosure'],
                  )}>
                  <option value="enclosed">Enclosed building</option>
                  <option value="open_sided">Permanently open-sided structure</option>
                </select>
              </Field>
              <Field label="Doors and windows">
                <select className={inputClass}
                  value={draft.wind.action_envelope.openings_operating_state}
                  onChange={(event) => updateActionEnvelope(
                    'openings_operating_state',
                    event.target.value as SiteDefinition['wind']['action_envelope']['openings_operating_state'],
                  )}>
                  <option value="normally_closed">Normally closed</option>
                  <option value="normally_open">Normally open</option>
                </select>
              </Field>
              <Field label="Opening pressure capacity">
                <select className={inputClass}
                  value={draft.wind.action_envelope.opening_capacity_status}
                  onChange={(event) => updateActionEnvelope(
                    'opening_capacity_status',
                    event.target.value as SiteDefinition['wind']['action_envelope']['opening_capacity_status'],
                  )}>
                  <option value="unverified">Unverified — retain working conservative status</option>
                  <option value="verified">Verified against calculated pressure</option>
                </select>
              </Field>
              <Field label="Case selection">
                <select className={inputClass}
                  value={draft.wind.action_envelope.coefficient_selection_policy}
                  onChange={(event) => updateActionEnvelope(
                    'coefficient_selection_policy',
                    event.target.value as SiteDefinition['wind']['action_envelope']['coefficient_selection_policy'],
                  )}>
                  <option value="worst_available_credible">
                    Auto-select worst available credible service case
                  </option>
                  <option value="verified_only">
                    Verified coefficients only
                  </option>
                </select>
              </Field>
            </div>
            <div className="mt-3 rounded border border-cyan-500/20 bg-slate-950/60 p-3 text-xs leading-5 text-slate-400">
              Current working basis: <b className="text-cyan-200">
                {draft.wind.action_envelope.enclosure === 'enclosed'
                  ? 'enclosed building'
                  : 'open-sided structure'}
              </b>, doors/windows <b className="text-cyan-200">
                {draft.wind.action_envelope.openings_operating_state.replace('_', ' ')}
              </b>, opening capacity <b className={
                draft.wind.action_envelope.opening_capacity_status === 'verified'
                  ? 'text-emerald-300'
                  : 'text-amber-300'
              }>
                {draft.wind.action_envelope.opening_capacity_status}
              </b>.
            </div>
          </section>
          </FeatureSection>

          <FeatureSection>
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
          </FeatureSection>
        </main>

        <aside className="w-full flex-none space-y-4 overflow-y-auto border-t border-slate-800 p-4 xl:w-[430px] xl:border-l xl:border-t-0">
          <section className={`rounded border p-4 ${
            certificationReady
              ? 'border-emerald-500/50 bg-emerald-950/20'
              : workingBasisReady
                ? 'border-cyan-500/50 bg-cyan-950/20'
                : 'border-amber-500/50 bg-amber-950/20'
          }`}>
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-semibold text-slate-100">Derived action basis</h2>
              <span className={`rounded px-2 py-1 text-[10px] font-bold uppercase ${
                certificationReady
                  ? 'bg-emerald-500/20 text-emerald-300'
                  : workingBasisReady
                    ? 'bg-cyan-500/20 text-cyan-200'
                    : 'bg-amber-500/20 text-amber-300'
              }`}>
                {certificationReady ? 'certification ready' : workingBasisReady ? 'preliminary basis active' : 'site data incomplete'}
              </span>
            </div>
            {calculation ? (
              <div className="mt-4 grid grid-cols-2 gap-3">
                {[
                  ['Regional speed VR', `${calculation.regional_wind_speed_m_s.toFixed(2)} m/s`],
                  ['Terrain multiplier', calculation.terrain_height_multiplier.toFixed(3)],
                  ['Site speed Vsit', `${calculation.site_wind_speed_m_s.toFixed(3)} m/s`],
                  ['Dynamic pressure qz', `${calculation.q_z_kPa.toFixed(6)} kPa`],
                  ['Calculation dataset', calculation.table_version],
                  ['Governing cardinal', calculation.governing_cardinal_direction],
                  ['Front bearing', `${calculation.structure.front_bearing_degrees.toFixed(0)}° true`],
                  ['ULS return period', `${calculation.annual_recurrence_interval_years} years`],
                  ['Envelope', calculation.action_envelope.enclosure.replace('_', ' ')],
                  ['Case selection', calculation.action_envelope.coefficient_selection_policy.replaceAll('_', ' ')],
                  ['Verifier', calculation.verifier_hash],
                ].map(([label, value]) => (
                  <div key={label} className="rounded border border-slate-800 bg-slate-950/70 p-3">
                    <div className="text-[9px] font-bold uppercase tracking-wide text-slate-500">{label}</div>
                    <div className="mt-1 font-mono text-sm text-cyan-200">{value}</div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-xs text-slate-400">
                Complete the site inputs above; the derived wind basis updates automatically after each saved change.
              </p>
            )}
            {missing.length > 0 && (
              <div className="mt-3 rounded border border-amber-500/30 bg-amber-950/30 p-3 text-xs text-amber-200">
                <div className="font-bold">Checks before certification</div>
                <p className="mt-1 text-amber-100/70">
                  These do not stop the automatic working calculation or its GIS benefits.
                </p>
                <ul className="mt-2 space-y-1">
                  {missing.map((item) => (
                    <li key={item.id}>
                      <button type="button" className="min-h-9 w-full rounded border border-amber-500/30 px-2 py-1.5 text-left underline decoration-amber-400/50 hover:border-amber-300 hover:text-white"
                        onClick={() => {
                          const target = item.id === 'action-standards'
                            ? standardsSection.current
                            : document.getElementById(item.id)
                          const drawer = target?.closest('details')
                          if (drawer) drawer.open = true
                          window.requestAnimationFrame(() => target?.scrollIntoView({
                            behavior: 'smooth',
                            block: 'center',
                          }))
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
