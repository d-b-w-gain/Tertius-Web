import { useEffect, useMemo, useState } from 'react'

import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import { ModelViewerCanvas } from '../extus/ui/ViewerTab'
import { resolveWorkflowServerUrl } from '../shared/apiConfig'
import { GuestWorkflowNotice } from '../shared/ui/GuestWorkflowNotice'
import type { CapabilityState, StructuralSnapshot, Vector3 } from './contracts'

type StructuralWorkbenchProps = {
  isActive?: boolean
}

const capabilityStyle: Record<CapabilityState['status'], string> = {
  online: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  fixture: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  pending: 'border-slate-600 bg-slate-800/60 text-slate-400',
  blocked: 'border-red-500/40 bg-red-500/10 text-red-300',
}

function number(value: number, digits = 3) {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  })
}

function vector(value: Vector3, unit: string) {
  return `X ${number(value.x)} · Y ${number(value.y)} · Z ${number(value.z)} ${unit}`
}

function restraintLabel(snapshot: StructuralSnapshot, nodeId: string) {
  const node = snapshot.nodes.find((candidate) => candidate.id === nodeId)
  if (!node) return 'Missing node'
  const restrained = Object.entries(node.restraints)
    .filter(([, enabled]) => enabled)
    .map(([degree]) => degree.toUpperCase())
  return restrained.length ? restrained.join(', ') : 'Free'
}

export function StructuralWorkbench({ isActive = true }: StructuralWorkbenchProps) {
  const { authMode, getAccessToken, login } = useAuth()
  const [snapshot, setSnapshot] = useState<StructuralSnapshot | null>(null)
  const [selectedVisualNodeId, setSelectedVisualNodeId] = useState('fixture-member-cantilever')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const serverUrl = resolveWorkflowServerUrl('structural', import.meta.env?.VITE_API_URL)

  useEffect(() => {
    if (!isActive || authMode !== 'authenticated') return
    let mounted = true
    setIsLoading(true)
    setError(null)
    apiFetch(`${serverUrl}/fixture/cantilever`, getAccessToken)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Structural fixture returned ${response.status}`)
        return await response.json() as StructuralSnapshot
      })
      .then((payload) => {
        if (!mounted) return
        setSnapshot(payload)
        setSelectedVisualNodeId(payload.members[0]?.visual_node_id || payload.nodes[0]?.visual_node_id || '')
      })
      .catch((loadError: unknown) => {
        if (mounted) setError(loadError instanceof Error ? loadError.message : 'Structural fixture could not be loaded')
      })
      .finally(() => {
        if (mounted) setIsLoading(false)
      })
    return () => {
      mounted = false
    }
  }, [authMode, getAccessToken, isActive, serverUrl])

  const selectedMember = useMemo(
    () => snapshot?.members.find((member) => member.visual_node_id === selectedVisualNodeId) || snapshot?.members[0],
    [selectedVisualNodeId, snapshot],
  )
  const memberResult = snapshot?.member_results.find((result) => result.member_id === selectedMember?.id)
  const memberCheck = snapshot?.member_checks.find((check) => check.member_id === selectedMember?.id)
  const reaction = snapshot?.reactions[0]
  const utilisationPercent = Math.min(100, Math.max(0, (memberCheck?.utilisation || 0) * 100))
  const utilisationColour = (memberCheck?.utilisation || 0) > 1
    ? 'bg-red-500'
    : (memberCheck?.utilisation || 0) >= 0.8
      ? 'bg-amber-400'
      : 'bg-emerald-400'

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
              {snapshot?.title || 'Structural Workbench'}
            </h1>
            <span className="rounded border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold tracking-[0.16em] text-amber-300">
              FIXTURE
            </span>
          </div>
          <p className="mt-0.5 text-xs text-slate-400">
            {snapshot?.subtitle || 'Loading the structural test harness…'}
          </p>
        </div>
        <div className="ml-auto hidden items-center gap-2 lg:flex">
          {snapshot?.capabilities.map((capability) => (
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
        DEMONSTRATION FIXTURE — NOT FOR DESIGN, CERTIFICATION, OR ORDERING
      </div>

      <div className="flex min-h-0 flex-1">
        <aside className="w-[25rem] shrink-0 overflow-y-auto border-r border-slate-800 bg-slate-950">
          {isLoading && <div className="p-5 text-sm text-slate-400">Solving fixture…</div>}
          {error && (
            <div className="m-4 rounded border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
              {error}
            </div>
          )}
          {snapshot && (
            <div className="space-y-5 p-4">
              <section>
                <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                  Traceable entities
                </div>
                <div className="mt-2 space-y-2">
                  {snapshot.members.map((member) => (
                    <button
                      key={member.id}
                      type="button"
                      onClick={() => setSelectedVisualNodeId(member.visual_node_id)}
                      className={`w-full rounded border p-3 text-left transition-colors ${
                        selectedVisualNodeId === member.visual_node_id
                          ? 'border-cyan-500/70 bg-cyan-500/10'
                          : 'border-slate-800 bg-slate-900/70 hover:border-slate-600'
                      }`}
                    >
                      <div className="text-sm font-semibold text-slate-200">{member.label}</div>
                      <div className="mt-1 truncate font-mono text-[10px] text-cyan-400">{member.id}</div>
                    </button>
                  ))}
                  <div className="grid grid-cols-2 gap-2">
                    {snapshot.nodes.map((node) => (
                      <button
                        key={node.id}
                        type="button"
                        onClick={() => setSelectedVisualNodeId(node.visual_node_id)}
                        className={`rounded border p-2 text-left text-xs transition-colors ${
                          selectedVisualNodeId === node.visual_node_id
                            ? 'border-cyan-500/70 bg-cyan-500/10'
                            : 'border-slate-800 bg-slate-900/70 hover:border-slate-600'
                        }`}
                      >
                        <div className="font-semibold text-slate-300">{node.label}</div>
                        <div className="mt-1 text-[10px] text-slate-500">{restraintLabel(snapshot, node.id)}</div>
                      </button>
                    ))}
                  </div>
                </div>
              </section>

              {selectedMember && memberResult && memberCheck && (
                <section className="rounded border border-slate-800 bg-slate-900/60 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                        Member result
                      </div>
                      <div className="mt-1 text-sm font-semibold text-slate-200">{selectedMember.label}</div>
                    </div>
                    <span className={`rounded px-2 py-1 text-xs font-bold ${
                      memberCheck.status === 'pass' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-red-500/15 text-red-300'
                    }`}>
                      {number(memberCheck.utilisation, 2)} util.
                    </span>
                  </div>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-800">
                    <div className={`h-full ${utilisationColour}`} style={{ width: `${utilisationPercent}%` }} />
                  </div>
                  <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                    <div>
                      <dt className="text-slate-500">Moment</dt>
                      <dd className="font-mono text-slate-200">{number(memberResult.max_moment_kNm)} kNm</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Shear</dt>
                      <dd className="font-mono text-slate-200">{number(memberResult.max_shear_kN)} kN</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Tip displacement</dt>
                      <dd className="font-mono text-slate-200">{number(memberResult.max_displacement_mm)} mm</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Capacity</dt>
                      <dd className="font-mono text-slate-200">{number(memberCheck.capacity_kNm)} kNm</dd>
                    </div>
                  </dl>
                  <p className="mt-3 border-t border-slate-800 pt-2 text-[11px] text-amber-300">
                    {memberCheck.basis}
                  </p>
                </section>
              )}

              {reaction && (
                <section className="rounded border border-slate-800 bg-slate-900/60 p-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                      Base reaction
                    </div>
                    <span className="text-[10px] font-semibold text-emerald-300">
                      Equilibrium {snapshot.equilibrium.status}
                    </span>
                  </div>
                  <div className="mt-2 space-y-1 font-mono text-xs text-slate-300">
                    <div>{vector(reaction.force, snapshot.units.force)}</div>
                    <div>{vector(reaction.moment, snapshot.units.moment)}</div>
                  </div>
                </section>
              )}

              <section>
                <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                  Analysis provenance
                </div>
                <dl className="mt-2 space-y-1.5 text-xs">
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Solver</dt>
                    <dd className="text-right font-mono text-slate-300">{snapshot.solver.name} {snapshot.solver.version}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Source</dt>
                    <dd className="text-right text-slate-300">{snapshot.source.label}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-slate-500">Schema</dt>
                    <dd className="font-mono text-slate-300">{snapshot.schema_version}</dd>
                  </div>
                </dl>
              </section>

              <section className="space-y-2 lg:hidden">
                {snapshot.capabilities.map((capability) => (
                  <div key={capability.id} className={`rounded border p-2 text-xs ${capabilityStyle[capability.status]}`}>
                    <div className="font-semibold">{capability.label}</div>
                    <div className="mt-0.5 opacity-80">{capability.detail}</div>
                  </div>
                ))}
              </section>
            </div>
          )}
        </aside>

        <main className="relative min-w-0 flex-1">
          <ModelViewerCanvas
            modelUrl={`${serverUrl}/fixture/cantilever/model`}
            getAccessToken={getAccessToken}
            statusText="Build123D fixture linked to the structural graph"
            projectName="Cantilever fixture"
            isActive={isActive}
            externalSelectedNodeIds={selectedVisualNodeId ? [selectedVisualNodeId] : undefined}
          />
          {snapshot && (
            <button
              type="button"
              onClick={() => setSelectedVisualNodeId(snapshot.loads[0]?.visual_node_id || '')}
              className="absolute bottom-4 right-4 max-w-sm rounded border border-slate-700 bg-slate-950/85 p-3 text-left shadow-xl backdrop-blur transition-colors hover:border-red-400/70"
            >
              <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-400">
                Applied load
              </div>
              <div className="mt-1 text-sm font-semibold text-slate-100">{snapshot.loads[0]?.label}</div>
              <div className="mt-1 font-mono text-xs text-slate-300">
                {snapshot.loads[0] ? vector(snapshot.loads[0].force, snapshot.units.force) : 'No load'}
              </div>
            </button>
          )}
        </main>
      </div>
    </div>
  )
}
