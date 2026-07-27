import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import { LatestModelViewer } from '../extus/ui/ViewerTab'
import { resolveWorkflowServerUrl } from '../shared/apiConfig'
import { ACTIVE_PROJECT_CHANGED_EVENT } from '../shared/ui/ProjectSelector'
import { GuestWorkflowNotice } from '../shared/ui/GuestWorkflowNotice'
import type {
  CapabilityState,
  DesignComponent,
  ProjectStructuralCapture,
  StructuralSnapshot,
  Vector3,
} from './contracts'

type StructuralWorkbenchProps = {
  isActive?: boolean
}

const capabilityStyle: Record<CapabilityState['status'], string> = {
  online: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  fixture: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  pending: 'border-slate-600 bg-slate-800/60 text-slate-400',
  blocked: 'border-red-500/40 bg-red-500/10 text-red-300',
}

const componentStyle: Record<DesignComponent['kind'], string> = {
  ground: 'border-emerald-500/50 bg-emerald-500/10',
  member: 'border-cyan-500/50 bg-cyan-500/10',
  surface: 'border-indigo-500/50 bg-indigo-500/10',
  connector: 'border-amber-500/40 bg-amber-500/10',
  support: 'border-violet-500/40 bg-violet-500/10',
}

function number(value: number, digits = 3) {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  })
}

function vector(value: Vector3) {
  return `X ${number(value.x)} · Y ${number(value.y)} · Z ${number(value.z)}`
}

export function StructuralWorkbench({ isActive = true }: StructuralWorkbenchProps) {
  const { authMode, getAccessToken, login } = useAuth()
  const [capture, setCapture] = useState<ProjectStructuralCapture | null>(null)
  const [analysis, setAnalysis] = useState<StructuralSnapshot | null>(null)
  const [selectedVisualNodeId, setSelectedVisualNodeId] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const captureRequestId = useRef(0)
  const serverUrl = resolveWorkflowServerUrl('structural', import.meta.env?.VITE_API_URL)
  const extusServerUrl = resolveWorkflowServerUrl('extus', import.meta.env?.VITE_API_URL)

  const loadCapture = useCallback(async () => {
    if (!isActive || authMode !== 'authenticated') return
    const requestId = ++captureRequestId.current
    setIsLoading(true)
    setError(null)
    setAnalysisError(null)
    try {
      const [captureResponse, analysisResponse] = await Promise.all([
        apiFetch(`${serverUrl}/active/capture`, getAccessToken),
        apiFetch(`${serverUrl}/active/analysis`, getAccessToken),
      ])
      const payload = await captureResponse.json().catch(() => null) as
        | ProjectStructuralCapture
        | { detail?: string }
        | null
      if (!captureResponse.ok) {
        const detail = payload && 'detail' in payload ? payload.detail : undefined
        throw new Error(detail || `Structural capture returned ${captureResponse.status}`)
      }
      const analysisPayload = await analysisResponse.json().catch(() => null) as
        | StructuralSnapshot
        | { detail?: string }
        | null
      const nextCapture = payload as ProjectStructuralCapture
      if (requestId !== captureRequestId.current) return
      setCapture(nextCapture)
      setSelectedVisualNodeId('')
      if (analysisResponse.ok) {
        setAnalysis(analysisPayload as StructuralSnapshot)
      } else {
        setAnalysis(null)
        setAnalysisError(
          analysisPayload && 'detail' in analysisPayload
            ? analysisPayload.detail || `Structural analysis returned ${analysisResponse.status}`
            : `Structural analysis returned ${analysisResponse.status}`,
        )
      }
    } catch (loadError) {
      if (requestId !== captureRequestId.current) return
      setCapture(null)
      setAnalysis(null)
      setError(
        loadError instanceof Error
          ? loadError.message
          : 'The active project structural declaration could not be loaded',
      )
    } finally {
      if (requestId === captureRequestId.current) {
        setIsLoading(false)
      }
    }
  }, [authMode, getAccessToken, isActive, serverUrl])

  useEffect(() => {
    void loadCapture()
    const handleActiveProjectChange = () => {
      void loadCapture()
    }
    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChange)
    return () => {
      window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChange)
      captureRequestId.current += 1
    }
  }, [loadCapture])

  const componentsById = useMemo(
    () => new Map(capture?.components.map((component) => [component.id, component]) || []),
    [capture],
  )
  const firstLoad = capture?.loads[0]
  const firstPath = capture?.load_paths.find((path) => path.load_id === firstLoad?.id)
  const resultantForce = firstLoad ? firstLoad.pressure_kPa * firstLoad.area_m2 : 0
  const firstMemberResult = analysis?.member_results[0]
  const firstMember = analysis?.members[0]
  const firstReaction = analysis?.reactions[0]
  const firstCheck = analysis?.member_checks[0]
  const structuralOverlay = useMemo(() => {
    const diagram = analysis?.member_diagrams[0]
    if (!diagram) return undefined
    return {
      id: diagram.member_id,
      label: `${firstMember?.label || diagram.member_id} signed bending moment`,
      stations: diagram.stations.map((station) => ({
        position: station.position,
        moment_kNm: station.moment_kNm,
      })),
      maxOffsetMm: 260,
    }
  }, [analysis, firstMember?.label])
  const capabilities = analysis?.capabilities || capture?.capabilities || []

  if (authMode === 'guest') {
    return (
      <GuestWorkflowNotice
        title="Log in to use the Structural Workbench"
        message="Structural source, analysis results, and model artifacts are kept inside your authenticated project."
        onLogin={login}
      />
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100">
      <header className="flex shrink-0 items-center gap-4 border-b border-slate-800 bg-slate-900/80 px-5 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="truncate text-base font-semibold text-slate-100">
              {capture?.title || 'Structural Workbench'}
            </h1>
            {capture && (
              <span className="rounded border border-cyan-500/50 bg-cyan-500/10 px-2 py-0.5 text-[10px] font-bold tracking-[0.16em] text-cyan-300">
                {capture.project_name}
              </span>
            )}
            {capture?.authoring_mode === 'generated' && (
              <span className="rounded border border-emerald-500/50 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold tracking-[0.12em] text-emerald-300">
                HANDLE-AUTHORED
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-400">
            {analysis
              ? 'Active-project geometry with PyNite member demand and signed diagrams'
              : 'Active-project geometry with statically parsed structural connectivity'}
          </p>
        </div>
        <div className="ml-auto hidden items-center gap-2 lg:flex">
          {capabilities.map((capability) => (
            <span
              key={capability.id}
              title={capability.detail}
              className={`rounded border px-2 py-1 text-[10px] font-semibold ${capabilityStyle[capability.status]}`}
            >
              {capability.label}
            </span>
          ))}
        </div>
      </header>

      <div className="shrink-0 border-b border-amber-500/30 bg-amber-950/40 px-5 py-2 text-xs font-semibold text-amber-200">
        {analysis
          ? 'ELASTIC MEMBER DEMAND ONLINE — CAPACITY, CONNECTIONS, ANCHORS, AND CONCRETE ARE NOT CHECKED'
          : 'LOAD PATH CAPTURE ONLY — CAPACITY, CONNECTIONS, ANCHORS, AND CONCRETE ARE NOT CHECKED'}
      </div>

      <div className="flex min-h-0 flex-1">
        <aside className="w-[27rem] shrink-0 overflow-y-auto border-r border-slate-800 bg-slate-950">
          {isLoading && <div className="p-5 text-sm text-slate-400">Parsing active design…</div>}
          {error && (
            <div className="m-4 rounded border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
              <div className="font-semibold">Structural declaration unavailable</div>
              <div className="mt-1 text-xs">{error}</div>
            </div>
          )}
          {analysisError && capture && (
            <div className="m-4 rounded border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-200">
              <div className="font-semibold">Member analysis unavailable</div>
              <div className="mt-1 text-xs">{analysisError}</div>
            </div>
          )}
          {capture && (
            <div className="space-y-5 p-4">
              <section>
                <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                  Declared components
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {capture.components.map((component) => (
                    <button
                      key={component.id}
                      type="button"
                      onClick={() => setSelectedVisualNodeId(component.visual_node_id)}
                      className={`rounded border p-2 text-left transition-colors ${componentStyle[component.kind]} ${
                        selectedVisualNodeId === component.visual_node_id
                          ? 'ring-1 ring-cyan-300'
                          : 'hover:border-slate-400'
                      }`}
                    >
                      <div className="text-xs font-semibold text-slate-200">{component.label}</div>
                      <div className="mt-1 flex items-center justify-between gap-2 text-[9px] uppercase tracking-wide text-slate-400">
                        <span>{component.kind}</span>
                        {component.grounded && <span className="text-emerald-300">Grounded</span>}
                      </div>
                    </button>
                  ))}
                </div>
              </section>

              <section>
                <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                  Declared connections
                </div>
                <div className="mt-2 space-y-2">
                  {capture.connections.map((connection) => {
                    const from = componentsById.get(connection.from_component_id)
                    const to = componentsById.get(connection.to_component_id)
                    return (
                      <button
                        key={connection.id}
                        type="button"
                        onClick={() => setSelectedVisualNodeId(to?.visual_node_id || '')}
                        className="w-full rounded border border-slate-800 bg-slate-900/70 p-3 text-left hover:border-slate-600"
                      >
                        <div className="text-xs font-semibold text-slate-200">{connection.label}</div>
                        <div className="mt-1 text-[10px] text-cyan-300">
                          {from?.label || connection.from_component_id} → {to?.label || connection.to_component_id}
                        </div>
                        <div className="mt-1 text-[9px] uppercase tracking-wide text-slate-500">
                          Via {connection.connector_component_ids.map(
                            (id) => componentsById.get(id)?.label || id,
                          ).join(', ') || 'direct declaration'} · {connection.transfers.join(', ')}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </section>

              {firstLoad && (
                <section className="rounded border border-indigo-500/40 bg-indigo-500/10 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-indigo-300">
                      Applied {firstLoad.case} load
                    </div>
                    <span className="rounded bg-slate-950/60 px-2 py-1 font-mono text-[10px] text-slate-300">
                      {number(resultantForce)} kN
                    </span>
                  </div>
                  <div className="mt-2 text-sm font-semibold text-slate-100">{firstLoad.label}</div>
                  <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <dt className="text-slate-500">Pressure</dt>
                      <dd className="font-mono">{number(firstLoad.pressure_kPa)} kPa</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Loaded area</dt>
                      <dd className="font-mono">{number(firstLoad.area_m2)} m²</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-slate-500">Direction</dt>
                      <dd className="font-mono">{vector(firstLoad.direction)}</dd>
                    </div>
                  </dl>
                  <p className="mt-3 border-t border-indigo-500/20 pt-2 text-[10px] text-slate-400">
                    {firstLoad.provenance}
                  </p>
                </section>
              )}

              {firstPath && (
                <section className={`rounded border p-3 ${
                  firstPath.status === 'complete'
                    ? 'border-emerald-500/40 bg-emerald-500/10'
                    : 'border-red-500/40 bg-red-500/10'
                }`}>
                  <div className="flex items-center justify-between">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-400">
                      Parsed load path
                    </div>
                    <span className={`text-[10px] font-bold uppercase ${
                      firstPath.status === 'complete' ? 'text-emerald-300' : 'text-red-300'
                    }`}>
                      {firstPath.status === 'complete' ? 'Reaches ground' : 'Blocked'}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1 text-[10px] text-slate-200">
                    {firstPath.component_ids.map((componentId, index) => (
                      <span key={componentId} className="contents">
                        {index > 0 && <span className="text-slate-500">→</span>}
                        <button
                          type="button"
                          onClick={() => setSelectedVisualNodeId(
                            componentsById.get(componentId)?.visual_node_id || '',
                          )}
                          className="rounded border border-slate-700 bg-slate-950/60 px-2 py-1 hover:border-cyan-400"
                        >
                          {componentsById.get(componentId)?.label || componentId}
                        </button>
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 text-[10px] text-slate-400">{firstPath.detail}</p>
                </section>
              )}

              {analysis && firstMemberResult && firstMember && firstReaction && (
                <section className="rounded border border-cyan-500/40 bg-cyan-500/10 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-300">
                        PyNite elastic demand
                      </div>
                      <div className="mt-1 text-sm font-semibold text-slate-100">
                        {firstMember.label}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setSelectedVisualNodeId(firstMember.visual_node_id)}
                      className="rounded border border-cyan-500/40 bg-slate-950/50 px-2 py-1 text-[10px] font-semibold text-cyan-200 hover:border-cyan-300"
                    >
                      Focus member
                    </button>
                  </div>
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <dt className="text-slate-500">Max moment</dt>
                      <dd className="font-mono text-amber-200">
                        {number(firstMemberResult.max_moment_kNm, 4)} kN·m
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Max shear</dt>
                      <dd className="font-mono">
                        {number(firstMemberResult.max_shear_kN, 4)} kN
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Max displacement</dt>
                      <dd className="font-mono">
                        {number(firstMemberResult.max_displacement_mm, 3)} mm
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Load points</dt>
                      <dd className="font-mono">{analysis.member_loads.length} screws</dd>
                    </div>
                  </dl>
                  <div className="mt-3 border-t border-cyan-500/20 pt-3">
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="font-bold uppercase tracking-[0.15em] text-slate-400">
                        Fixed-base reaction
                      </span>
                      <span className={
                        analysis.equilibrium.status === 'pass'
                          ? 'text-emerald-300'
                          : 'text-red-300'
                      }>
                        Equilibrium {analysis.equilibrium.status}
                      </span>
                    </div>
                    <div className="mt-2 font-mono text-[10px] text-slate-300">
                      Force {vector(firstReaction.force)} kN
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-slate-300">
                      Moment {vector(firstReaction.moment)} kN·m
                    </div>
                  </div>
                  <p className="mt-3 border-t border-cyan-500/20 pt-2 text-[10px] text-slate-400">
                    Signed ribbon: blue is low demand and amber is peak demand. Its width is
                    scaled for visibility; values above are the solver results.
                  </p>
                </section>
              )}

              <section className="rounded border border-amber-500/30 bg-amber-950/30 p-3 text-xs text-amber-200">
                <div className="font-semibold">Capacity status: NOT CHECKED</div>
                <p className="mt-1 text-[10px] text-amber-200/75">
                  {firstCheck?.basis || (
                    'This proves declared connectivity only. It does not yet solve the C100 member '
                    + 'or check screws, bolts, bracket, anchors, or concrete.'
                  )}
                </p>
              </section>
            </div>
          )}
        </aside>

        <main className="relative min-w-0 flex-1">
          <LatestModelViewer
            serverUrl={extusServerUrl}
            isActive={isActive}
            statusTextOverride={
              analysis
                ? 'Active-project model with PyNite signed moment ribbon'
                : 'Active-project model linked to parsed structural declarations'
            }
            externalSelectedNodeIds={selectedVisualNodeId ? [selectedVisualNodeId] : undefined}
            structuralOverlay={structuralOverlay}
          />
        </main>
      </div>
    </div>
  )
}
