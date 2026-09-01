import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import {
  LatestModelViewer,
  type StructuralViewerOverlay,
} from '../extus/ui/ViewerTab'
import { resolveWorkflowServerUrl } from '../shared/apiConfig'
import { ACTIVE_PROJECT_CHANGED_EVENT } from '../shared/ui/ProjectSelector'
import { GuestWorkflowNotice } from '../shared/ui/GuestWorkflowNotice'
import type {
  ActiveStructuralWorkbenchResponse,
  CapabilityState,
  CertificationIssue,
  DesignComponent,
  ProjectStructuralCapture,
  StructuralAnalysisCacheInfo,
  StructuralSnapshot,
  Vector3,
  VerificationStatus,
} from './contracts'
import { SITE_BASIS_CHANGED_EVENT } from '../site/SiteWorkbench'
import { StructuralWindBasisPanel } from './StructuralWindBasisPanel'
import { buildStage8Overlays, buildStructuralStageFocus } from './stageFocus'

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

const verificationStyle: Record<VerificationStatus, string> = {
  pass: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300',
  fail: 'border-red-500/60 bg-red-500/15 text-red-300',
  warning: 'border-amber-500/50 bg-amber-500/10 text-amber-300',
  not_checked: 'border-slate-600 bg-slate-800/70 text-slate-300',
  unsupported: 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-300',
  blocked: 'border-red-500/40 bg-red-950/40 text-red-300',
}

const certificationIssueStyle: Record<CertificationIssue['kind'], string> = {
  design_failure: 'border-red-500/50 bg-red-950/30 text-red-200',
  evidence_gap: 'border-fuchsia-500/40 bg-fuchsia-950/20 text-fuchsia-200',
  provisional_input: 'border-amber-500/40 bg-amber-950/20 text-amber-200',
  dependent_blocker: 'border-orange-500/40 bg-orange-950/20 text-orange-200',
  engineering_warning: 'border-yellow-500/40 bg-yellow-950/20 text-yellow-200',
}

const certificationIssueLabel: Record<CertificationIssue['kind'], string> = {
  design_failure: 'calculated failure',
  evidence_gap: 'evidence gap',
  provisional_input: 'Tertius provisional',
  dependent_blocker: 'downstream blocker',
  engineering_warning: 'engineering review',
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

const stabilityStatusRank = {
  fail: 4,
  unsupported: 3,
  not_checked: 2,
  pass: 1,
} as const

const recoverableStructuralGatewayStatuses = new Set([502, 503, 504, 524])

async function fetchStructuralResponse(
  url: string,
  getAccessToken: () => Promise<string>,
) {
  const response = await apiFetch(url, getAccessToken)
  if (!recoverableStructuralGatewayStatuses.has(response.status)) return response

  // A fresh structural solve can outlive the public gateway request while the
  // API still completes and stores its content-addressed result. Retry once so
  // the UI picks up that saved result instead of leaving a 524 error on screen.
  return apiFetch(url, getAccessToken)
}

function analysisCacheFromHeaders(response: Response): StructuralAnalysisCacheInfo | null {
  const status = response.headers.get('X-Tertius-Structural-Cache')?.toLowerCase()
  const keyDigest = response.headers.get('X-Tertius-Structural-Cache-Key')
  const engineVersion = response.headers.get('X-Tertius-Structural-Engine')
  const calculatedAt = response.headers.get('X-Tertius-Structural-Calculated-At')
  const duration = Number(
    response.headers.get('X-Tertius-Structural-Calculation-Seconds'),
  )
  if (
    (status !== 'hit' && status !== 'calculated')
    || !keyDigest
    || !engineVersion
    || !calculatedAt
    || !Number.isFinite(duration)
  ) {
    return null
  }
  return {
    status,
    key_digest: keyDigest,
    engine_version: engineVersion,
    calculated_at: calculatedAt,
    calculation_duration_seconds: duration,
  }
}

export function StructuralWorkbench({ isActive = true }: StructuralWorkbenchProps) {
  const { authMode, getAccessToken, login } = useAuth()
  const [capture, setCapture] = useState<ProjectStructuralCapture | null>(null)
  const [analysis, setAnalysis] = useState<StructuralSnapshot | null>(null)
  const [selectedVisualNodeId, setSelectedVisualNodeId] = useState('')
  const [selectedMemberId, setSelectedMemberId] = useState('')
  const [selectedCombinationId, setSelectedCombinationId] = useState('')
  const [selectedSheetId, setSelectedSheetId] = useState('')
  const [activeStageId, setActiveStageId] = useState('')
  const [selectedRestraintTraceId, setSelectedRestraintTraceId] = useState('')
  const [diagramMode, setDiagramMode] = useState<'moment' | 'displacement'>('moment')
  const [momentComponent, setMomentComponent] = useState<'resultant' | 'major' | 'minor'>(
    'resultant',
  )
  const [memberEvidenceStage, setMemberEvidenceStage] = useState<
    'cross_section' | 'member_stability'
  >('member_stability')
  const [isLoading, setIsLoading] = useState(false)
  const [analysisLoadPhase, setAnalysisLoadPhase] = useState<
    'idle' | 'checking' | 'calculating'
  >('idle')
  const [analysisCache, setAnalysisCache] = useState<StructuralAnalysisCacheInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const captureRequestId = useRef(0)
  const serverUrl = resolveWorkflowServerUrl('structural', import.meta.env?.VITE_API_URL)
  const extusServerUrl = resolveWorkflowServerUrl('extus', import.meta.env?.VITE_API_URL)

  const loadCapture = useCallback(async () => {
    if (!isActive || authMode !== 'authenticated') return
    const requestId = ++captureRequestId.current
    setIsLoading(true)
    setAnalysisLoadPhase('checking')
    setAnalysisCache(null)
    setError(null)
    setAnalysisError(null)
    const calculationTimer = window.setTimeout(() => {
      if (requestId === captureRequestId.current) {
        setAnalysisLoadPhase('calculating')
      }
    }, 750)
    try {
      const response = await fetchStructuralResponse(
        `${serverUrl}/active/workbench`,
        getAccessToken,
      )
      const payload = await response.json().catch(() => null) as
        | ActiveStructuralWorkbenchResponse
        | { detail?: string }
        | null
      if (!response.ok) {
        const detail = payload && 'detail' in payload ? payload.detail : undefined
        throw new Error(detail || `Structural workbench returned ${response.status}`)
      }
      const workbench = payload as ActiveStructuralWorkbenchResponse
      if (requestId !== captureRequestId.current) return
      setCapture(workbench.capture)
      setSelectedVisualNodeId('')
      setSelectedRestraintTraceId('')
      setAnalysisCache(workbench.cache)
      if (workbench.analysis) {
        const nextAnalysis = workbench.analysis
        setAnalysis(nextAnalysis)
        setSelectedCombinationId(nextAnalysis.solver.combination_id)
        setSelectedMemberId(nextAnalysis.members[0]?.id || '')
        setSelectedSheetId(nextAnalysis.calculation_sheets?.[0]?.id || '')
        setActiveStageId(nextAnalysis.calculation_sheets?.[0]?.stage_id || '')
        setMemberEvidenceStage('member_stability')
      } else {
        setAnalysis(null)
        setAnalysisError(workbench.analysis_error || 'Structural analysis is unavailable')
      }
    } catch (loadError) {
      if (requestId !== captureRequestId.current) return
      setCapture(null)
      setAnalysis(null)
      setAnalysisCache(null)
      setError(
        loadError instanceof Error
          ? loadError.message
          : 'The active project structural declaration could not be loaded',
      )
    } finally {
      window.clearTimeout(calculationTimer)
      if (requestId === captureRequestId.current) {
        setIsLoading(false)
        setAnalysisLoadPhase('idle')
      }
    }
  }, [authMode, getAccessToken, isActive, serverUrl])

  useEffect(() => {
    void loadCapture()
    const handleActiveProjectChange = () => {
      void loadCapture()
    }
    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChange)
    window.addEventListener(SITE_BASIS_CHANGED_EVENT, handleActiveProjectChange)
    return () => {
      window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleActiveProjectChange)
      window.removeEventListener(SITE_BASIS_CHANGED_EVENT, handleActiveProjectChange)
      captureRequestId.current += 1
    }
  }, [loadCapture])

  const selectCombination = useCallback(async (combinationId: string) => {
    setSelectedCombinationId(combinationId)
    setAnalysisError(null)
    setAnalysisLoadPhase('checking')
    const calculationTimer = window.setTimeout(() => {
      setAnalysisLoadPhase('calculating')
    }, 750)
    try {
      const response = await fetchStructuralResponse(
        `${serverUrl}/active/analysis?combination_id=${encodeURIComponent(combinationId)}`,
        getAccessToken,
      )
      const payload = await response.json().catch(() => null) as
        | StructuralSnapshot
        | { detail?: string }
        | null
      if (!response.ok) {
        const detail = payload && 'detail' in payload ? payload.detail : undefined
        throw new Error(detail || `Structural analysis returned ${response.status}`)
      }
      const nextAnalysis = payload as StructuralSnapshot
      setAnalysis(nextAnalysis)
      setAnalysisCache(analysisCacheFromHeaders(response))
      setSelectedRestraintTraceId('')
      setSelectedSheetId((current) => (
        nextAnalysis.calculation_sheets?.some((sheet) => sheet.id === current)
          ? current
          : nextAnalysis.calculation_sheets?.[0]?.id || ''
      ))
      setSelectedMemberId((current) => (
        nextAnalysis.members.some((member) => member.id === current)
          ? current
          : nextAnalysis.members[0]?.id || ''
      ))
    } catch (loadError) {
      setAnalysisError(
        loadError instanceof Error
          ? loadError.message
          : 'The selected load combination could not be solved',
      )
    } finally {
      window.clearTimeout(calculationTimer)
      setAnalysisLoadPhase('idle')
    }
  }, [getAccessToken, serverUrl])

  const componentsById = useMemo(
    () => new Map(capture?.components.map((component) => [component.id, component]) || []),
    [capture],
  )
  const connectionChecksById = useMemo(
    () => new Map(
      (analysis?.connection_checks ?? []).map((check) => [check.connection_id, check]),
    ),
    [analysis],
  )
  const firstLoad = capture?.loads[0]
  const firstPath = capture?.load_paths.find((path) => path.load_id === firstLoad?.id)
  const resultantForce = firstLoad ? firstLoad.pressure_kPa * firstLoad.area_m2 : 0
  const selectedMember = analysis?.members.find(
    (member) => member.id === selectedMemberId,
  ) || analysis?.members[0]
  const selectedMemberResult = analysis?.member_results.find(
    (result) => result.member_id === selectedMember?.id,
  )
  const selectedSourceConnection = capture?.connections.find(
    (connection) => connection.id === selectedMember?.source_connection_id,
  )
  const selectedSection = analysis?.sections.find(
    (section) => section.id === selectedMember?.section_id,
  )
  const catalogueProperties = selectedSection?.catalog?.properties
  const firstReaction = analysis?.reactions[0]
  const selectedCheck = analysis?.member_checks.find(
    (check) => check.member_id === selectedMember?.id,
  )
  const selectedTensionCheck = analysis?.tension_member_checks?.find(
    (check) => check.member_id === selectedMember?.id,
  )
  const selectedGlobalBracingTrace = analysis?.bracing_load_path_traces?.find(
    (trace) => trace.member_id === selectedMember?.id,
  )
  const availableCrossSectionCheck = analysis?.cross_section_checks?.find(
    (check) => check.member_id === selectedMember?.id,
  )
  const availableMemberStabilityCheck = analysis?.member_stability_checks
    ?.filter((check) => check.member_id === selectedMember?.id)
    .sort((left, right) => (
      stabilityStatusRank[right.status] - stabilityStatusRank[left.status]
      || (right.governing_utilisation ?? -1) - (left.governing_utilisation ?? -1)
    ))[0]
  const activeMemberEvidenceStage = memberEvidenceStage === 'cross_section'
    && availableCrossSectionCheck
    ? 'cross_section'
    : memberEvidenceStage === 'member_stability' && availableMemberStabilityCheck
      ? 'member_stability'
      : availableMemberStabilityCheck
        ? 'member_stability'
        : 'cross_section'
  const selectedCrossSectionCheck = activeMemberEvidenceStage === 'cross_section'
    ? availableCrossSectionCheck
    : undefined
  const selectedMemberStabilityCheck = activeMemberEvidenceStage === 'member_stability'
    ? availableMemberStabilityCheck
    : undefined
  const selectedDisplayCheckStatus = selectedMemberStabilityCheck?.status
    || selectedCrossSectionCheck?.status
    || selectedCheck?.status
  const selectedDisplayUtilisation = selectedMemberStabilityCheck?.governing_utilisation
    ?? selectedCrossSectionCheck?.governing_utilisation
    ?? selectedCheck?.utilisation
  const crossSectionStage = analysis?.verification_stages?.find(
    (stage) => stage.id === 'cross_section',
  )
  const memberStabilityStage = analysis?.verification_stages?.find(
    (stage) => stage.id === 'member_stability',
  )
  const selectedServiceability = analysis?.serviceability_checks.find(
    (check) => (
      check.member_id === selectedMember?.id
      || check.analytical_member_ids?.includes(selectedMember?.id || '')
    ),
  )
  const activeCombination = analysis?.load_combinations.find(
    (combination) => combination.id === selectedCombinationId,
  ) || analysis?.load_combinations[0]
  const unavailableCombinations = analysis?.unavailable_load_combinations ?? []
  const selectedCalculationSheet = analysis?.calculation_sheets?.find(
    (sheet) => sheet.id === selectedSheetId,
  ) || analysis?.calculation_sheets?.[0]
  const selectedRestraintTrace = analysis?.member_restraint_traces?.find(
    (trace) => trace.id === selectedRestraintTraceId,
  ) || (activeStageId === 'bracing'
    ? analysis?.member_restraint_traces
      ?.filter((trace) => (
        trace.combination_id === selectedCombinationId
        && trace.status !== 'not_required'
      ))
      .sort((left, right) => (
        (right.required_restraint_force_kN ?? 0)
        - (left.required_restraint_force_kN ?? 0)
      ))[0]
    : undefined)
  const selectedBoundaryCandidateIds = new Set([
    ...(selectedRestraintTrace?.start_restraint_candidate_ids ?? []),
    ...(selectedRestraintTrace?.end_restraint_candidate_ids ?? []),
  ])
  const selectedRestraintChecks = (analysis?.member_restraint_candidate_checks ?? [])
    .filter((check) => (
      selectedRestraintTrace?.combination_id === check.combination_id
      && selectedBoundaryCandidateIds.has(check.candidate_id)
    ))
  const selectedRestraintVisualNodeIds = (() => {
    if (!capture) {
      return selectedVisualNodeId ? [selectedVisualNodeId] : undefined
    }
    const componentVisualNodes = new Map(
      capture.components.map((component) => [component.id, component.visual_node_id]),
    )
    return Array.from(new Set([
      selectedVisualNodeId,
      ...(selectedCrossSectionCheck?.off_axis_source_component_ids ?? [])
        .map((componentId) => componentVisualNodes.get(componentId) ?? ''),
      ...(selectedCrossSectionCheck?.off_axis_collector_component_ids ?? [])
        .map((componentId) => componentVisualNodes.get(componentId) ?? ''),
      ...(selectedRestraintTrace
        ? selectedRestraintChecks.flatMap((check) => check.anchorage_component_ids)
        : [])
        .map((componentId) => componentVisualNodes.get(componentId) ?? ''),
      ...(selectedGlobalBracingTrace?.component_ids ?? [])
        .map((componentId) => componentVisualNodes.get(componentId) ?? ''),
    ].filter(Boolean)))
  })()
  const selectRestraintTrace = useCallback((traceId: string) => {
    const currentAnalysis = analysis
    const trace = currentAnalysis?.member_restraint_traces?.find(
      (candidate) => candidate.id === traceId,
    )
    if (!trace || !currentAnalysis) return
    setSelectedRestraintTraceId(trace.id)
    setActiveStageId('bracing')
    setSelectedMemberId(trace.member_id)
    setSelectedVisualNodeId(
      currentAnalysis.members.find(
        (member) => member.id === trace.member_id,
      )?.visual_node_id || '',
    )
    setSelectedSheetId(
      currentAnalysis.calculation_sheets?.find(
        (sheet) => sheet.stage_id === 'bracing',
      )?.id
      || currentAnalysis.calculation_sheets?.find(
        (sheet) => sheet.stage_id === 'member_stability',
      )?.id
      || '',
    )
    setDiagramMode('moment')
  }, [analysis])
  const analysisOverlays = useMemo(() => {
    if (!analysis || !activeCombination) return undefined
    const nodes = new Map(analysis.nodes.map((node) => [node.id, node]))
    return analysis.member_diagrams
      .filter((diagram) => !analysis.members.find(
        (candidate) => candidate.id === diagram.member_id,
      )?.tension_only)
      .map((diagram, diagramIndex) => {
      const member = analysis.members.find(
        (candidate) => candidate.id === diagram.member_id,
      )
      const start = member ? nodes.get(member.start_node_id)?.position : undefined
      const end = member ? nodes.get(member.end_node_id)?.position : undefined
      const memberLength = start && end
        ? Math.hypot(end.x - start.x, end.y - start.y, end.z - start.z)
        : 0
      const positionAt = (distanceM: number) => {
        const ratio = memberLength > 0 ? distanceM / memberLength : 0
        return {
          x: (start?.x ?? 0) + ((end?.x ?? 0) - (start?.x ?? 0)) * ratio,
          y: (start?.y ?? 0) + ((end?.y ?? 0) - (start?.y ?? 0)) * ratio,
          z: (start?.z ?? 0) + ((end?.z ?? 0) - (start?.z ?? 0)) * ratio,
        }
      }
      const pointArrows = analysis.member_loads
        .filter((load) => load.member_id === diagram.member_id)
        .flatMap((load) => {
          const factor = activeCombination.factors[load.case_id] ?? 0
          if (factor === 0) return []
          return [{
            id: load.id,
            label: load.label,
            position: positionAt(load.distance_m),
            force_kN: {
              x: load.force.x * factor,
              y: load.force.y * factor,
              z: load.force.z * factor,
            },
          }]
        })
      const nodalArrows = diagramIndex === 0
        ? analysis.loads.flatMap((load) => {
          const factor = activeCombination.factors[load.case_id] ?? 0
          const node = nodes.get(load.node_id)
          if (factor === 0 || !node) return []
          return [{
            id: load.id,
            label: load.label,
            position: node.position,
            force_kN: {
              x: load.force.x * factor,
              y: load.force.y * factor,
              z: load.force.z * factor,
            },
          }]
        })
        : []
      const lineArrows = analysis.member_distributed_loads
        .filter((load) => load.member_id === diagram.member_id)
        .flatMap((load) => {
          const factor = activeCombination.factors[load.case_id] ?? 0
          if (factor === 0) return []
          return [{
            id: load.id,
            label: load.label,
            position: positionAt(
              (load.start_distance_m + load.end_distance_m) / 2,
            ),
            force_kN: {
              x: (load.start_force_kN_m.x + load.end_force_kN_m.x) / 2 * factor,
              y: (load.start_force_kN_m.y + load.end_force_kN_m.y) / 2 * factor,
              z: (load.start_force_kN_m.z + load.end_force_kN_m.z) / 2 * factor,
            },
          }]
        })
      const check = diagramMode === 'moment'
        ? analysis.member_checks.find(
          (candidate) => candidate.member_id === diagram.member_id,
        )
        : analysis.serviceability_checks.find(
          (candidate) => (
            candidate.member_id === diagram.member_id
            || candidate.analytical_member_ids?.includes(diagram.member_id)
          ),
        )
      const restraintSegments = (analysis.member_restraint_traces ?? [])
        .filter((trace) => (
          trace.member_id === diagram.member_id
          && trace.combination_id === activeCombination.id
        ))
        .map((trace) => ({
          id: trace.id,
          label: `${trace.compression_flange.replaceAll('_', ' ')} · ${trace.status}`,
          start: trace.start_position,
          end: trace.end_position,
          compressionFlange: trace.compression_flange,
          status: trace.status,
        }))
      return {
        id: diagram.member_id,
        label: diagramMode === 'displacement'
          ? `${member?.label || diagram.member_id} amplified displacement`
          : `${member?.label || diagram.member_id} signed bending moment`,
        mode: diagramMode,
        status: check?.status ?? 'not_checked',
        utilisation: check?.utilisation,
        diagramColor: diagramMode === 'moment'
          ? momentComponent === 'major'
            ? 0x22d3ee
            : momentComponent === 'minor'
              ? 0xf472b6
              : undefined
          : undefined,
        stations: diagram.stations.map((station) => ({
          position: station.position,
          moment_kNm: momentComponent === 'major'
            ? station.major_moment_kNm
            : momentComponent === 'minor'
              ? station.minor_moment_kNm
              : station.moment_kNm,
          displacement_mm: station.displacement_mm,
        })),
        loadArrows: [...nodalArrows, ...pointArrows, ...lineArrows],
        nodes: diagramIndex === 0
          ? analysis.nodes.map((node) => ({
            id: node.id,
            label: node.label,
            position: node.position,
            restrained: Object.values(node.restraints).some(Boolean),
          }))
          : undefined,
        reactions: diagramIndex === 0
          ? analysis.reactions.flatMap((reaction) => {
            const reactionNode = nodes.get(reaction.node_id)
            if (!reactionNode) return []
            return [{
              id: `${reaction.node_id}-${reaction.combination_id}`,
              label: `${reactionNode.label} reaction`,
              position: reactionNode.position,
              force_kN: reaction.force,
              moment_kNm: reaction.moment,
            }]
          })
          : undefined,
        restraintSegments: diagramMode === 'moment' ? restraintSegments : undefined,
        maxOffsetMm: 260,
      }
    })
  }, [activeCombination, analysis, diagramMode, momentComponent])
  const activeStage = analysis?.verification_stages?.find(
    (stage) => stage.id === activeStageId,
  )
  const stageFocus = useMemo(() => (
    buildStructuralStageFocus(analysis, activeStageId, activeCombination)
  ), [activeCombination, activeStageId, analysis])
  const stage8Overlays = useMemo(() => (
    buildStage8Overlays(
      analysis,
      activeStageId,
      activeCombination?.id,
      selectedRestraintTraceId,
      stageFocus,
    )
  ), [
    activeCombination?.id,
    activeStageId,
    analysis,
    selectedRestraintTraceId,
    stageFocus,
  ])
  const structuralOverlays = useMemo<StructuralViewerOverlay[] | undefined>(() => {
    if (!analysis) return undefined
    if (activeStageId === 'bracing') return stage8Overlays
    if (!stageFocus) return analysisOverlays
    const baseOverlays = analysisOverlays ?? []
    if (baseOverlays.length === 0) {
      return [{
        id: `stage-focus-${stageFocus.id}`,
        label: `Stage ${stageFocus.order} ${stageFocus.label}`,
        mode: diagramMode,
        status: 'not_checked',
        stations: [],
        stageFocus,
      }]
    }
    return baseOverlays.map((overlay, index) => (
      index === 0 ? { ...overlay, stageFocus } : overlay
    ))
  }, [
    activeStageId,
    analysis,
    analysisOverlays,
    diagramMode,
    stage8Overlays,
    stageFocus,
  ])
  const capabilities = analysis?.capabilities || capture?.capabilities || []
  const windActionBases = analysis?.wind_action_bases || capture?.wind_action_bases || []
  const selectVerificationStage = (stageId: string) => {
    if (!analysis) return
    setActiveStageId(stageId)
    const stage = analysis.verification_stages?.find((candidate) => candidate.id === stageId)
    const sheet = analysis.calculation_sheets?.find(
      (candidate) => stage?.sheet_ids.includes(candidate.id),
    )
    if (!sheet) return
    setSelectedSheetId(sheet.id)
    if (stageId === 'cross_section' || stageId === 'member_stability') {
      setMemberEvidenceStage(stageId)
    }
    if (stageId === 'stability' && analysis.stability) {
      setDiagramMode('displacement')
      if (selectedCombinationId !== analysis.stability.combination_id) {
        void selectCombination(analysis.stability.combination_id)
      }
    }
    if (stageId === 'member_stability') {
      setDiagramMode('moment')
      const governingCombination = analysis.member_stability_checks?.find(
        (check) => check.governing_combination_id,
      )?.governing_combination_id
      if (governingCombination && selectedCombinationId !== governingCombination) {
        void selectCombination(governingCombination)
      }
    }
    if (stageId === 'bracing') {
      const trace = analysis.member_restraint_traces?.find((candidate) => (
        candidate.combination_id === selectedCombinationId
        && candidate.status !== 'not_required'
      )) || analysis.member_restraint_traces?.find(
        (candidate) => candidate.status !== 'not_required',
      )
      if (trace) {
        selectRestraintTrace(trace.id)
        setSelectedSheetId(sheet.id)
        return
      }
    }
    const memberId = sheet.related_member_ids[0]
    const member = analysis.members.find((candidate) => candidate.id === memberId)
    if (member) {
      setSelectedMemberId(member.id)
      setSelectedVisualNodeId(member.visual_node_id)
    }
  }
  const selectMemberEvidenceStage = (
    stageId: 'cross_section' | 'member_stability',
  ) => {
    setActiveStageId(stageId)
    setMemberEvidenceStage(stageId)
    const sheet = analysis?.calculation_sheets?.find(
      (candidate) => candidate.stage_id === stageId,
    )
    if (sheet) setSelectedSheetId(sheet.id)
  }
  const downloadCalculationSheets = () => {
    if (!analysis) return
    const payload = {
      source: analysis.source,
      design_basis: analysis.design_basis,
      wind_action_bases: analysis.wind_action_bases,
      active_combination: analysis.solver.combination_id,
      stability: analysis.stability ?? null,
      connection_checks: analysis.connection_checks ?? [],
      member_restraint_traces: analysis.member_restraint_traces ?? [],
      member_restraint_candidate_checks: analysis.member_restraint_candidate_checks ?? [],
      certification_readiness: analysis.certification_readiness,
      verification_stages: analysis.verification_stages ?? [],
      calculation_sheets: analysis.calculation_sheets ?? [],
    }
    const url = URL.createObjectURL(new Blob(
      [JSON.stringify(payload, null, 2)],
      { type: 'application/json' },
    ))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${analysis.source.design_id || 'tertius'}-australian-structural-evidence.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

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
            {capture?.analysis_configuration_revision && (
              <span
                className="rounded border border-violet-500/50 bg-violet-500/10 px-2 py-0.5 font-mono text-[10px] font-bold tracking-[0.08em] text-violet-300"
                title={capture.analysis_configuration_digest || undefined}
              >
                CONFIG R{capture.analysis_configuration_revision}
              </span>
            )}
            {analysis?.action_standard_pack && (
              <span
                className="rounded border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 font-mono text-[10px] font-bold tracking-[0.08em] text-amber-300"
                title={`${analysis.action_standard_pack.standard_reference} — ${analysis.action_standard_pack.basis}`}
              >
                ACTIONS PACK {analysis.action_standard_pack.pack_version}
              </span>
            )}
            {analysisLoadPhase !== 'idle' && (
              <span className="rounded border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold tracking-[0.12em] text-amber-200">
                {analysisLoadPhase === 'checking'
                  ? 'CHECKING SAVED ANALYSIS'
                  : 'CALCULATING & SAVING ANALYSIS'}
              </span>
            )}
            {analysisLoadPhase === 'idle' && analysisCache && (
              <span
                className={`rounded border px-2 py-0.5 text-[10px] font-bold tracking-[0.12em] ${
                  analysisCache.status === 'hit'
                    ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
                    : 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300'
                }`}
                title={`Structural engine ${analysisCache.engine_version}; cache ${analysisCache.key_digest}; original calculation ${number(analysisCache.calculation_duration_seconds, 1)} seconds`}
              >
                {analysisCache.status === 'hit'
                  ? 'SAVED ANALYSIS'
                  : `CALCULATED & SAVED ${number(analysisCache.calculation_duration_seconds, 1)}s`}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-400">
            {analysis
              ? 'Active-project geometry with PyNite member demand and signed diagrams'
              : 'Active-project compiled mechanical topology; analysis context required'}
          </p>
        </div>
        {analysis && (
          <div className="ml-auto flex items-center gap-2">
            <label className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">
              Combination
              <select
                aria-label="Load combination"
                value={selectedCombinationId}
                onChange={(event) => void selectCombination(event.target.value)}
                disabled={analysisLoadPhase !== 'idle'}
                className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs normal-case tracking-normal text-slate-200"
              >
                {analysis.load_combinations.map((combination) => (
                  <option key={combination.id} value={combination.id}>
                    {combination.id} · {combination.label}
                  </option>
                ))}
                {unavailableCombinations.length > 0 && (
                  <optgroup label="Unavailable — missing inputs">
                    {unavailableCombinations.map((combination) => (
                      <option
                        key={`unavailable-${combination.family}-${combination.id}`}
                        value={`unavailable:${combination.family}:${combination.id}`}
                        disabled
                      >
                        {combination.id} · unavailable — {combination.reason}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            </label>
            {unavailableCombinations.length > 0 && (
              <details className="relative">
                <summary className="cursor-pointer list-none rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[10px] font-semibold text-amber-200 hover:bg-amber-500/15">
                  {unavailableCombinations.length} unavailable
                </summary>
                <div className="absolute right-0 z-50 mt-2 max-h-[70vh] w-96 overflow-y-auto rounded border border-amber-500/40 bg-slate-950 p-3 text-xs shadow-2xl shadow-black/50">
                  <p className="font-semibold text-amber-200">
                    Combinations waiting for required actions
                  </p>
                  <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
                    These formulas are owned by Tertius, but remain disabled until
                    their inputs can be generated from the project model.
                  </p>
                  <div className="mt-3 space-y-3">
                    {unavailableCombinations.map((combination) => (
                      <div
                        key={`unavailable-detail-${combination.family}-${combination.id}`}
                        className="border-t border-slate-800 pt-2 first:border-0 first:pt-0"
                      >
                        <p className="font-mono font-semibold text-slate-200">
                          {combination.id}
                        </p>
                        <p className="text-[11px] text-slate-400">
                          {combination.label}
                        </p>
                        <p className="mt-1 text-[11px] leading-relaxed text-amber-100/80">
                          {combination.reason}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </details>
            )}
            {analysis.solver.combination_selection === 'governing_working_envelope' && (
              <span className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-[10px] font-semibold text-cyan-200">
                Governing working envelope
              </span>
            )}
            <div className="flex rounded border border-slate-700 bg-slate-950 p-0.5">
              {(['moment', 'displacement'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setDiagramMode(mode)}
                  className={`rounded px-2 py-1 text-[10px] font-semibold capitalize ${
                    diagramMode === mode
                      ? 'bg-cyan-500/20 text-cyan-200'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>
            {diagramMode === 'moment' && (
              <div className="flex rounded border border-slate-700 bg-slate-950 p-0.5">
                {(['resultant', 'major', 'minor'] as const).map((component) => (
                  <button
                    key={component}
                    type="button"
                    onClick={() => setMomentComponent(component)}
                    title={component === 'major'
                      ? 'PyNite local Mz: bending about the catalogue major axis'
                      : component === 'minor'
                        ? 'PyNite local My: weak-axis bending requiring separate resistance evidence'
                        : 'Vector sum of major- and minor-axis moments'}
                    className={`rounded px-2 py-1 text-[10px] font-semibold capitalize ${
                      momentComponent === component
                        ? component === 'minor'
                          ? 'bg-pink-500/20 text-pink-200'
                          : 'bg-cyan-500/20 text-cyan-200'
                        : 'text-slate-500 hover:text-slate-300'
                    }`}
                  >
                    {component}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="hidden items-center gap-2 xl:flex">
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

      <div className={`shrink-0 border-b px-5 py-2 text-xs font-semibold ${
        analysis?.certification_readiness?.ready_for_certificate
          ? 'border-emerald-500/30 bg-emerald-950/40 text-emerald-200'
          : 'border-amber-500/30 bg-amber-950/40 text-amber-200'
      }`}>
        {analysis?.certification_readiness
          ? analysis.certification_readiness.ready_for_certificate
            ? 'AUSTRALIAN TECHNICAL GATES PASS — CONTROLLED CERTIFICATE DRAFT REQUIRES ENGINEER REVIEW AND SIGNATURE'
            : `AUSTRALIAN VERIFICATION ACTIVE — ${analysis.certification_readiness.blocking_gate_ids.length} CERTIFICATION GATE(S) OPEN; ${analysis.certification_readiness.ready_for_engineering_review ? 'DRAFT ENGINEERING REVIEW EVIDENCE IS AVAILABLE' : 'ANALYSIS IS INCOMPLETE'}`
          : 'LOAD PATH CAPTURE ONLY — CAPACITY, CONNECTIONS, ANCHORS, AND CONCRETE ARE NOT CHECKED'}
      </div>

      <StructuralWindBasisPanel bases={windActionBases} />

      <div className="flex min-h-0 flex-1">
        <aside className="w-[27rem] shrink-0 overflow-y-auto border-r border-slate-800 bg-slate-950">
          {isLoading && (
            <div className="p-5 text-sm text-slate-400">
              <div>
                {analysisLoadPhase === 'calculating'
                  ? 'Calculating structural analysis…'
                  : 'Loading saved structural analysis…'}
              </div>
              {analysisLoadPhase === 'calculating' && (
                <div className="mt-2 text-xs leading-relaxed text-slate-500">
                  This design, site basis, or Tertius structural engine changed. The
                  result will be stored and reused when the workbench is reopened.
                </div>
              )}
            </div>
          )}
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
              {analysis?.design_basis && (
                <section className="rounded border border-cyan-500/40 bg-cyan-950/20 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-300">
                        Primary Australian compliance framework
                      </div>
                      <div className="mt-1 text-sm font-semibold text-slate-100">
                        {analysis.design_basis.framework_label}
                      </div>
                    </div>
                    <span className="rounded border border-cyan-500/40 bg-slate-950/60 px-2 py-1 font-mono text-[9px] text-cyan-200">
                      {analysis.design_basis.framework_id}
                    </span>
                  </div>
                  <p className="mt-2 text-[10px] text-slate-400">
                    {analysis.design_basis.framework_reference} ·{' '}
                    {analysis.design_basis.jurisdiction} ·{' '}
                    {analysis.design_basis.analysis_method}
                  </p>
                  {analysis.design_basis.supplemental_methods.length > 0 && (
                    <div className="mt-2 rounded border border-slate-700 bg-slate-950/60 px-2 py-1.5 text-[9px] text-slate-400">
                      Supplemental method: {analysis.design_basis.supplemental_methods
                        .map((method) => `${method.id} — ${method.role}`)
                        .join('; ')}
                    </div>
                  )}
                </section>
              )}

              {analysis?.certification_readiness && (
                <section className={`rounded border p-3 ${
                  analysis.certification_readiness.ready_for_certificate
                    ? verificationStyle.pass
                    : verificationStyle.blocked
                }`}>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-80">
                        Australian certification readiness
                      </div>
                      <div className="mt-1 text-xs font-semibold text-slate-100">
                        {analysis.certification_readiness.draft_document_label}
                      </div>
                    </div>
                    <span className="font-mono text-[9px] uppercase">
                      {analysis.certification_readiness.document_status.replaceAll('_', ' ')}
                    </span>
                  </div>
                  <p className="mt-2 text-[10px] leading-relaxed text-slate-300">
                    {analysis.certification_readiness.conclusion}
                  </p>
                  <div className={`mt-3 rounded border p-2 ${
                    analysis.certification_readiness.model_coverage.status === 'complete'
                      ? verificationStyle.pass
                      : verificationStyle.fail
                  }`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[9px] font-semibold">PyNite model coverage</span>
                      <span className="font-mono text-[8px] uppercase">
                        {analysis.certification_readiness.model_coverage.solved_member_count}/
                        {analysis.certification_readiness.model_coverage.compiled_member_count} members
                      </span>
                    </div>
                    <p className="mt-1 text-[9px] leading-relaxed opacity-80">
                      {analysis.certification_readiness.model_coverage.summary}
                    </p>
                  </div>
                  {analysis.certification_readiness.issues.length > 0 && (
                    <div className="mt-3 space-y-2">
                      <div className="text-[9px] font-bold uppercase tracking-[0.16em] text-slate-400">
                        What is actually blocking certification
                      </div>
                      {analysis.certification_readiness.issues.map((issue) => (
                        <button
                          key={issue.id}
                          type="button"
                          onClick={() => selectVerificationStage(issue.stage_id)}
                          className={`w-full rounded border p-2 text-left ${certificationIssueStyle[issue.kind]}`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <span className="text-[9px] font-semibold leading-snug">
                              {issue.title}
                            </span>
                            <span className="shrink-0 font-mono text-[8px] uppercase">
                              {issue.count} · {issue.owner}
                            </span>
                          </div>
                          <div className="mt-1 text-[8px] font-bold uppercase tracking-wide opacity-70">
                            {certificationIssueLabel[issue.kind]} · Stage {issue.stage_id.replaceAll('_', ' ')}
                          </div>
                          <p className="mt-1 text-[9px] leading-relaxed opacity-85">
                            {issue.detail}
                          </p>
                          <p className="mt-1 text-[9px] leading-relaxed text-slate-300">
                            Next: {issue.next_action}
                          </p>
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {analysis.certification_readiness.gates.map((gate) => (
                      <div
                        key={gate.id}
                        title={gate.summary}
                        className={`rounded border p-2 ${verificationStyle[gate.status]}`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[9px] font-semibold">{gate.order}. {gate.label}</span>
                          <span className="font-mono text-[8px] uppercase">
                            {gate.status.replace('_', ' ')}
                          </span>
                        </div>
                        <div className="mt-1 text-[8px] opacity-70">{gate.primary_reference}</div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {analysis && (analysis.verification_stages?.length ?? 0) > 0 && (
                <section>
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                      Australian verification detail
                    </div>
                    <button
                      type="button"
                      onClick={downloadCalculationSheets}
                      className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[9px] font-semibold text-slate-300 hover:border-cyan-500 hover:text-cyan-200"
                    >
                      Export engineering-review JSON
                    </button>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    {analysis.verification_stages?.map((stage) => (
                      <button
                        key={stage.id}
                        type="button"
                        title={stage.summary}
                        onClick={() => selectVerificationStage(stage.id)}
                        className={`rounded border p-2 text-left ${verificationStyle[stage.status]} ${
                          activeStageId === stage.id
                            ? 'ring-1 ring-cyan-300'
                            : ''
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[10px] font-semibold">
                            {stage.order}. {stage.label}
                          </span>
                          <span className="font-mono text-[8px] uppercase">
                            {stage.status.replace('_', ' ')}
                          </span>
                        </div>
                        <div className="mt-1 text-[8px] opacity-70">{stage.primary_reference}</div>
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {selectedCalculationSheet && (
                <section className={`rounded border p-3 ${
                  verificationStyle[selectedCalculationSheet.status]
                }`}>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[9px] font-bold uppercase tracking-[0.16em] opacity-75">
                        Calculation sheet
                      </div>
                      <div className="mt-1 text-xs font-semibold text-slate-100">
                        {selectedCalculationSheet.title}
                      </div>
                    </div>
                    <span className="font-mono text-[9px] uppercase">
                      {selectedCalculationSheet.status.replace('_', ' ')}
                    </span>
                  </div>
                  <p className="mt-2 text-[10px] text-slate-300">
                    {selectedCalculationSheet.purpose}
                  </p>
                  <div className="mt-2 rounded bg-slate-950/60 px-2 py-1 font-mono text-[9px] text-slate-400">
                    {selectedCalculationSheet.primary_reference}
                  </div>
                  {selectedCalculationSheet.supplemental_references.length > 0 && (
                    <div className="mt-1 rounded bg-slate-950/40 px-2 py-1 text-[9px] text-slate-500">
                      Supplemental: {selectedCalculationSheet.supplemental_references.join('; ')}
                    </div>
                  )}
                  {selectedCalculationSheet.inputs.length > 0 && (
                    <div className="mt-3">
                      <div className="text-[9px] font-bold uppercase tracking-wide opacity-70">
                        Inputs
                      </div>
                      <div className="mt-1 space-y-1">
                        {selectedCalculationSheet.inputs.map((input) => (
                          <div key={`${input.symbol}-${input.label}`} className="flex justify-between gap-3 text-[9px]">
                            <span title={input.source}>{input.label}</span>
                            <span className="shrink-0 font-mono">
                              {String(input.value)}{input.unit ? ` ${input.unit}` : ''}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {selectedCalculationSheet.equations.length > 0 && (
                    <div className="mt-3">
                      <div className="text-[9px] font-bold uppercase tracking-wide opacity-70">
                        Trace
                      </div>
                      <div className="mt-1 space-y-2">
                        {selectedCalculationSheet.equations.map((equation) => (
                          <div key={`${equation.label}-${equation.substitution}`} className="rounded bg-slate-950/50 p-2 text-[9px]">
                            <div className="font-semibold text-slate-200">{equation.label}</div>
                            <div className="mt-1 font-mono text-slate-400">
                              {equation.expression} = {equation.substitution}
                            </div>
                            <div className="mt-1 font-mono text-cyan-200">
                              = {String(equation.result)}{equation.unit ? ` ${equation.unit}` : ''}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {selectedCalculationSheet.outputs.length > 0 && (
                    <div className="mt-3">
                      <div className="text-[9px] font-bold uppercase tracking-wide opacity-70">
                        Outputs
                      </div>
                      <div className="mt-1 space-y-1">
                        {selectedCalculationSheet.outputs.map((output) => (
                          <div key={`${output.symbol}-${output.label}`} className="flex justify-between gap-3 text-[9px]">
                            <span title={output.source}>{output.label}</span>
                            <span className="shrink-0 font-mono">
                              {String(output.value)}{output.unit ? ` ${output.unit}` : ''}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {selectedCalculationSheet.assumptions.length > 0 && (
                    <div className="mt-3 border-t border-current/20 pt-2">
                      <div className="text-[9px] font-bold uppercase tracking-wide opacity-70">
                        Missing / assumptions
                      </div>
                      {selectedCalculationSheet.assumptions.map((assumption) => (
                        <p key={assumption} className="mt-1 text-[9px] opacity-80">
                          {assumption}
                        </p>
                      ))}
                    </div>
                  )}
                </section>
              )}

              {selectedRestraintTrace && (
                <section className={`rounded border p-3 ${
                  selectedRestraintTrace.status === 'verified'
                    ? 'border-emerald-500/50 bg-emerald-950/30'
                    : selectedRestraintTrace.status === 'candidate'
                      ? 'border-amber-500/50 bg-amber-950/30'
                      : 'border-red-500/50 bg-red-950/30'
                }`}>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[9px] font-bold uppercase tracking-[0.16em] text-cyan-300">
                        Selected 3D restraint trace
                      </div>
                      <div className="mt-1 text-xs font-semibold text-slate-100">
                        {selectedRestraintTrace.member_id} ·{' '}
                        {number(selectedRestraintTrace.segment_start_m, 3)}–
                        {number(selectedRestraintTrace.segment_end_m, 3)} m
                      </div>
                    </div>
                    <span className="font-mono text-[9px] font-bold uppercase text-slate-200">
                      {selectedRestraintTrace.status.replace('_', ' ')}
                    </span>
                  </div>
                  <dl className="mt-3 grid grid-cols-2 gap-2 text-[9px]">
                    <div>
                      <dt className="text-slate-500">Combination</dt>
                      <dd className="font-mono text-slate-200">
                        {selectedRestraintTrace.combination_id}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Compression flange</dt>
                      <dd className="font-mono text-slate-200">
                        {selectedRestraintTrace.compression_flange.replaceAll('_', ' ')}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Required brace force</dt>
                      <dd className="font-mono text-slate-200">
                        {selectedRestraintTrace.required_restraint_force_kN === null
                          ? 'not quantified'
                          : `${number(selectedRestraintTrace.required_restraint_force_kN, 4)} kN`}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Available resistance</dt>
                      <dd className="font-mono text-slate-200">
                        {selectedRestraintTrace.available_restraint_force_kN === null
                          ? 'not verified'
                          : `${number(selectedRestraintTrace.available_restraint_force_kN, 4)} kN`}
                      </dd>
                    </div>
                  </dl>
                  <div className="mt-3 space-y-2">
                    {selectedRestraintChecks.length === 0 ? (
                      <p className="text-[9px] text-red-200">
                        No effective physical candidate exists at both required boundaries.
                      </p>
                    ) : selectedRestraintChecks.map((check) => (
                      <div
                        key={check.id}
                        className="rounded border border-slate-700 bg-slate-950/60 p-2 text-[9px]"
                      >
                        <div className="flex justify-between gap-2">
                          <span className="font-semibold text-slate-200">{check.candidate_id}</span>
                          <span className="font-mono uppercase text-slate-300">
                            {selectedRestraintTrace.effective_restraint_candidate_ids
                              .includes(check.candidate_id)
                              ? check.status
                              : 'located — not credited'}
                          </span>
                        </div>
                        <div className="mt-1 font-mono text-cyan-200">
                          P* {check.required_force_kN === null
                            ? '—'
                            : `${number(check.required_force_kN, 4)} kN`}
                          {' / '}φR {check.available_force_kN === null
                            ? '—'
                            : `${number(check.available_force_kN, 4)} kN`}
                          {' · '}stiffness {check.stiffness_status}
                        </div>
                        <div className="mt-1 text-[8px] uppercase tracking-wide text-slate-500">
                          Demand {check.demand_model === 'as_nzs_4600_2005_4_3_2_flange_force'
                            ? 'AS/NZS 4600:2005 clauses 4.3.2.2-4.3.2.3 · 2.5% critical flange force'
                            : check.demand_model.replaceAll('_', ' ')}
                        </div>
                        <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-[8px] uppercase">
                          <span className={check.identity_status === 'pass'
                            ? 'text-emerald-300'
                            : check.identity_status === 'fail'
                              ? 'text-red-300'
                              : 'text-slate-500'}>
                            Identity {check.identity_status.replace('_', ' ')}
                          </span>
                          <span className={check.anchorage_status === 'verified'
                            ? 'text-emerald-300'
                            : 'text-amber-300'}>
                            Anchorage {check.anchorage_status}
                          </span>
                          <span className={check.available_force_kN !== null
                            && check.available_moment_kNm !== null
                            ? 'text-emerald-300'
                            : 'text-amber-300'}>
                            Resistance {check.available_force_kN !== null
                              && check.available_moment_kNm !== null
                              ? 'defined'
                              : 'unverified'}
                          </span>
                          <span className={check.stiffness_status === 'verified'
                            ? 'text-emerald-300'
                            : 'text-amber-300'}>
                            Stiffness {check.stiffness_status}
                          </span>
                        </div>
                        {check.evidence_pack_id && (
                          <p className="mt-2 break-all font-mono text-[8px] text-violet-300">
                            {check.evidence_pack_id}
                            {check.evidence_pack_version ? ` v${check.evidence_pack_version}` : ''}
                          </p>
                        )}
                        {check.identity_mismatches.length > 0 && (
                          <ul className="mt-1 space-y-1 text-red-200">
                            {check.identity_mismatches.map((mismatch) => (
                              <li key={mismatch}>Identity mismatch: {mismatch}</li>
                            ))}
                          </ul>
                        )}
                        <p className="mt-1 text-amber-100">{check.anchorage_basis}</p>
                        {(check.anchorage_blockers?.length ?? 0) > 0 && (
                          <ul className="mt-1 space-y-1 text-[9px] text-amber-200">
                            {check.anchorage_blockers?.map((blocker) => (
                              <li key={blocker}>• {blocker}</li>
                            ))}
                          </ul>
                        )}
                        {check.anchorage_component_ids.length > 0 && (
                          <p className="mt-1 break-all font-mono text-[8px] text-slate-500">
                            Path {check.anchorage_component_ids.join(' → ')}
                            {check.anchorage_grounded_component_id
                              ? ` → grounded ${check.anchorage_grounded_component_id}`
                              : ' → no grounded endpoint'}
                          </p>
                        )}
                        <p className="mt-1 text-slate-400">{check.mechanism}</p>
                        <p className="mt-1 text-slate-500">{check.provenance}</p>
                        {check.evidence_references.length > 0 && (
                          <details className="mt-2 text-slate-500">
                            <summary className="cursor-pointer text-[8px] uppercase tracking-wide">
                              Evidence references
                            </summary>
                            <ul className="mt-1 space-y-1">
                              {check.evidence_references.map((reference) => (
                                <li key={reference}>{reference}</li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </div>
                    ))}
                  </div>
                  <p className="mt-2 border-t border-slate-700 pt-2 text-[9px] text-slate-400">
                    {selectedRestraintTrace.basis}
                  </p>
                </section>
              )}

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
                    const check = connectionChecksById.get(connection.id)
                    return (
                      <button
                        key={connection.id}
                        type="button"
                        onClick={() => setSelectedVisualNodeId(to?.visual_node_id || '')}
                        className="w-full rounded border border-slate-800 bg-slate-900/70 p-3 text-left hover:border-slate-600"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="text-xs font-semibold text-slate-200">{connection.label}</div>
                          {check && (
                            <span className={`rounded border px-1.5 py-0.5 font-mono text-[8px] uppercase ${
                              verificationStyle[check.status]
                            }`}>
                              {check.status.replace('_', ' ')}
                            </span>
                          )}
                        </div>
                        <div className="mt-1 text-[10px] text-cyan-300">
                          {from?.label || connection.from_component_id} → {to?.label || connection.to_component_id}
                        </div>
                        <div className="mt-1 text-[9px] uppercase tracking-wide text-slate-500">
                          Via {connection.connector_component_ids.map(
                            (id) => componentsById.get(id)?.label || id,
                          ).join(', ') || 'direct declaration'} · {connection.transfers.join(', ')}
                        </div>
                        {check && (
                          <div className="mt-2 border-t border-slate-800 pt-2">
                            <div className="grid grid-cols-3 gap-2 font-mono text-[9px] text-slate-300">
                              <span>N* {number(check.axial_demand_kN, 3)} kN</span>
                              <span>V* {number(check.shear_demand_kN, 3)} kN</span>
                              <span>M* {number(check.moment_demand_kNm, 3)} kN·m</span>
                            </div>
                            {(check.design_axial_capacity_kN !== null
                              || check.design_shear_capacity_kN !== null
                              || check.design_moment_capacity_kNm !== null) && (
                              <div className="mt-1 grid grid-cols-3 gap-2 font-mono text-[9px] text-emerald-300">
                                <span>ϕNn {check.design_axial_capacity_kN === null
                                  ? '—'
                                  : `${number(check.design_axial_capacity_kN, 3)} kN`}</span>
                                <span>ϕVn {check.design_shear_capacity_kN === null
                                  ? '—'
                                  : `${number(check.design_shear_capacity_kN, 3)} kN`}</span>
                                <span>ϕMn {check.design_moment_capacity_kNm === null
                                  ? '—'
                                  : `${number(check.design_moment_capacity_kNm, 3)} kN·m`}</span>
                              </div>
                            )}
                            <div className="mt-1 flex items-center justify-between gap-2 text-[9px] text-slate-500">
                              <span>{check.pack_id} v{check.pack_version}</span>
                              <span className={
                                check.identity_status === 'pass'
                                  ? 'text-emerald-400'
                                  : check.identity_status === 'fail'
                                    ? 'text-red-400'
                                    : 'text-amber-300'
                              }>
                                Identity {check.identity_status.replace('_', ' ')}
                              </span>
                            </div>
                            <p className="mt-1 text-[9px] leading-relaxed text-slate-500">
                              {check.governing_utilisation !== null
                                ? `Governing utilisation ${number(check.governing_utilisation, 3)}.`
                                : `Resistance unavailable—demand is visible but this joint cannot pass. ${
                                  check.assumptions[1] || check.assumptions[0] || ''
                                }`}
                            </p>
                            {check.governing_combination_id && (
                              <p className="mt-1 font-mono text-[8px] text-cyan-300">
                                Governing {check.governing_combination_id}
                                {check.governing_member_id
                                  ? ` · ${check.governing_member_id}`
                                  : ''}
                              </p>
                            )}
                            {check.anchor_group && (
                              <div className={`mt-2 rounded border p-2 ${
                                check.anchor_group.status === 'pass'
                                  ? 'border-emerald-500/30 bg-emerald-500/5'
                                  : check.anchor_group.status === 'fail'
                                    ? 'border-red-500/40 bg-red-500/10'
                                    : 'border-amber-500/30 bg-amber-500/5'
                              }`}>
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-mono text-[9px] font-bold text-slate-200">
                                    {check.anchor_group.anchor_count}× {check.anchor_group.anchor_part_number}
                                  </span>
                                  <span className={`font-mono text-[8px] uppercase ${
                                    check.anchor_group.status === 'pass'
                                      ? 'text-emerald-300'
                                      : check.anchor_group.status === 'fail'
                                        ? 'text-red-300'
                                        : 'text-amber-300'
                                  }`}>
                                    Anchor {check.anchor_group.status}
                                  </span>
                                </div>
                                <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[8px] text-slate-400">
                                  <span>
                                    Uplift {number(check.anchor_group.tension_demand_kN, 3)} / {
                                      check.anchor_group.tension_capacity_kN === null
                                        ? '—'
                                        : number(check.anchor_group.tension_capacity_kN, 3)
                                    } kN
                                  </span>
                                  <span>
                                    Shear {number(check.anchor_group.shear_demand_kN, 3)} / {
                                      check.anchor_group.shear_capacity_kN === null
                                        ? '—'
                                        : number(check.anchor_group.shear_capacity_kN, 3)
                                    } kN
                                  </span>
                                  <span>
                                    Embed {check.anchor_group.installed_effective_embedment_mm === null
                                      ? '—'
                                      : number(check.anchor_group.installed_effective_embedment_mm, 1)} mm · {
                                      check.anchor_group.embedment_status.replace('_', ' ')
                                    }
                                  </span>
                                  <span>
                                    Edge {check.anchor_group.minimum_edge_distance_mm === null
                                      ? '—'
                                      : number(check.anchor_group.minimum_edge_distance_mm, 1)} mm · {
                                      check.anchor_group.edge_distance_status.replace('_', ' ')
                                    }
                                  </span>
                                  <span>
                                    Spacing {check.anchor_group.minimum_spacing_mm === null
                                      ? '—'
                                      : number(check.anchor_group.minimum_spacing_mm, 1)} mm · {
                                      check.anchor_group.spacing_status.replace('_', ' ')
                                    }
                                  </span>
                                  <span>
                                    Interaction {check.anchor_group.interaction_utilisation === null
                                      ? '—'
                                      : number(check.anchor_group.interaction_utilisation, 3)}
                                  </span>
                                </div>
                                <p className="mt-1 text-[8px] text-slate-500">
                                  Substrate {check.anchor_group.substrate_type || 'not declared'} · {
                                    check.anchor_group.substrate_status
                                  } · effective group count {
                                    number(check.anchor_group.effective_anchor_count, 1)
                                  }
                                </p>
                                {check.anchor_group.blockers.length > 0 && (
                                  <ul className="mt-1 space-y-1 text-[8px] text-amber-200">
                                    {check.anchor_group.blockers.map((blocker) => (
                                      <li key={blocker}>• {blocker}</li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            )}
                            {check.bolted_sheet_interface && (
                              <div className={`mt-2 rounded border p-2 ${
                                check.bolted_sheet_interface.status === 'pass'
                                  ? 'border-emerald-500/30 bg-emerald-500/5'
                                  : check.bolted_sheet_interface.status === 'fail'
                                    ? 'border-red-500/40 bg-red-500/10'
                                    : 'border-amber-500/30 bg-amber-500/5'
                              }`}>
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-mono text-[9px] font-bold text-slate-200">
                                    {check.bolted_sheet_interface.connected_sheet_part_number || 'Cee web'} / {
                                      check.bolted_sheet_interface.bolt_count
                                    }x {check.bolted_sheet_interface.bolt_part_number}
                                  </span>
                                  <span className={`font-mono text-[8px] uppercase ${
                                    check.bolted_sheet_interface.status === 'pass'
                                      ? 'text-emerald-300'
                                      : check.bolted_sheet_interface.status === 'fail'
                                        ? 'text-red-300'
                                        : 'text-amber-300'
                                  }`}>
                                    Web interface {check.bolted_sheet_interface.status}
                                  </span>
                                </div>
                                <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[8px] text-slate-400">
                                  <span>
                                    Resultant {number(
                                      check.bolted_sheet_interface.resultant_shear_demand_kN,
                                      3,
                                    )} kN
                                  </span>
                                  <span>
                                    Governing capacity {
                                      check.bolted_sheet_interface.governing_capacity_kN === null
                                        ? '—'
                                        : number(
                                          check.bolted_sheet_interface.governing_capacity_kN,
                                          3,
                                        )
                                    } kN
                                  </span>
                                  <span>
                                    Bolt shear {
                                      check.bolted_sheet_interface.design_bolt_shear_capacity_kN === null
                                        ? '—'
                                        : number(
                                          check.bolted_sheet_interface.design_bolt_shear_capacity_kN,
                                          3,
                                        )
                                    } kN · {check.bolted_sheet_interface.bolt_shear_status}
                                  </span>
                                  <span>
                                    Sheet bearing {
                                      check.bolted_sheet_interface.design_sheet_bearing_capacity_kN === null
                                        ? '—'
                                        : number(
                                          check.bolted_sheet_interface.design_sheet_bearing_capacity_kN,
                                          3,
                                        )
                                    } kN · {check.bolted_sheet_interface.sheet_bearing_status}
                                  </span>
                                  <span>
                                    Sheet tearout {
                                      check.bolted_sheet_interface.design_sheet_tearout_capacity_kN === null
                                        ? '—'
                                        : number(
                                          check.bolted_sheet_interface.design_sheet_tearout_capacity_kN,
                                          3,
                                        )
                                    } kN · {check.bolted_sheet_interface.sheet_tearout_status}
                                  </span>
                                  <span>
                                    Utilisation {
                                      check.bolted_sheet_interface.governing_utilisation === null
                                        ? '—'
                                        : number(
                                          check.bolted_sheet_interface.governing_utilisation,
                                          3,
                                        )
                                    }
                                  </span>
                                </div>
                                <p className="mt-1 text-[8px] text-slate-500">
                                  Hole {check.bolted_sheet_interface.hole_diameter_mm === null
                                    ? '—'
                                    : number(check.bolted_sheet_interface.hole_diameter_mm, 1)} mm · {
                                    check.bolted_sheet_interface.hole_status
                                  } · spacing {check.bolted_sheet_interface.spacing_status} · edge {
                                    check.bolted_sheet_interface.edge_distance_status
                                  }
                                </p>
                                <p className={`mt-1 text-[8px] ${
                                  check.bolted_sheet_interface.fixture_capacity_status === 'verified'
                                    ? 'text-emerald-300'
                                    : 'text-amber-200'
                                }`}>
                                  Fixture {
                                    check.bolted_sheet_interface.fixture_part_number || 'not resolved'
                                  } · plate {check.bolted_sheet_interface.fixture_capacity_status.replace('_', ' ')}
                                </p>
                                {check.bolted_sheet_interface.blockers.length > 0 && (
                                  <ul className="mt-1 space-y-1 text-[8px] text-amber-200">
                                    {check.bolted_sheet_interface.blockers.map((blocker) => (
                                      <li key={blocker}>• {blocker}</li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            )}
                          </div>
                        )}
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
                    {firstLoad.net_pressure_coefficient !== null && (
                      <div>
                        <dt className="text-slate-500">Net coefficient Cnet</dt>
                        <dd className="font-mono">
                          {number(firstLoad.net_pressure_coefficient)}
                        </dd>
                      </div>
                    )}
                    {firstLoad.coefficient_status && (
                      <div>
                        <dt className="text-slate-500">Coefficient basis</dt>
                        <dd className={
                          firstLoad.coefficient_status === 'verified'
                            ? 'text-emerald-300'
                            : firstLoad.coefficient_status === 'working_conservative'
                              ? 'text-cyan-200'
                              : 'text-amber-300'
                        }>
                          {firstLoad.coefficient_status.replace('_', ' ')}
                        </dd>
                      </div>
                    )}
                    {firstLoad.external_pressure_coefficient !== null
                      && firstLoad.external_pressure_coefficient !== undefined && (
                      <div>
                        <dt className="text-slate-500">External Cp,e</dt>
                        <dd className="font-mono">
                          {number(firstLoad.external_pressure_coefficient)}
                        </dd>
                      </div>
                    )}
                    {firstLoad.internal_pressure_coefficient !== null
                      && firstLoad.internal_pressure_coefficient !== undefined && (
                      <div>
                        <dt className="text-slate-500">Internal Cp,i</dt>
                        <dd className="font-mono">
                          {number(firstLoad.internal_pressure_coefficient)}
                        </dd>
                      </div>
                    )}
                    {firstLoad.area_reduction_factor !== null
                      && firstLoad.area_reduction_factor !== undefined && (
                      <div>
                        <dt className="text-slate-500">Area factor Ka</dt>
                        <dd className="font-mono">
                          {number(firstLoad.area_reduction_factor)}
                        </dd>
                      </div>
                    )}
                    <div className="col-span-2">
                      <dt className="text-slate-500">Direction</dt>
                      <dd className="font-mono">{vector(firstLoad.direction)}</dd>
                    </div>
                    {firstLoad.surface_action_pack_id && (
                      <div className="col-span-2">
                        <dt className="text-slate-500">Tertius surface-action pack</dt>
                        <dd className="break-all font-mono text-[10px] text-cyan-200">
                          {firstLoad.surface_action_pack_id}
                        </dd>
                      </div>
                    )}
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

              {analysis && (
                <section className="rounded border border-violet-500/40 bg-violet-500/10 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-violet-300">
                      Gravity and service actions
                    </div>
                    <span className="font-mono text-[10px] text-violet-200">
                      {analysis.solver.combination_id}
                    </span>
                  </div>
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <dt className="text-slate-500">Catalogue member mass</dt>
                      <dd className="font-mono">
                        {number(analysis.load_summary.member_mass_kg, 2)} kg
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Member self-weight</dt>
                      <dd className="font-mono">
                        {number(analysis.load_summary.self_weight_kN, 3)} kN
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Other permanent actions</dt>
                      <dd className="font-mono">
                        {number(analysis.load_summary.additional_dead_load_kN, 3)} kN
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Imposed actions</dt>
                      <dd className="font-mono">
                        {number(analysis.load_summary.imposed_load_kN, 3)} kN
                      </dd>
                    </div>
                  </dl>
                  <div className="mt-3 border-t border-violet-500/20 pt-2 text-[10px] text-slate-400">
                    {analysis.members.length} members · {analysis.nodes.length} shared nodes ·{' '}
                    {analysis.member_distributed_loads.length} distributed loads
                  </div>
                </section>
              )}

              {analysis?.stability && (
                <section className="rounded border border-fuchsia-500/40 bg-fuchsia-950/20 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-fuchsia-300">
                      First / second order
                    </div>
                    <span className="rounded bg-slate-950/60 px-2 py-1 font-mono text-[9px] text-fuchsia-200">
                      {analysis.stability.converged ? 'P-DELTA CONVERGED' : 'NOT CONVERGED'}
                    </span>
                  </div>
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <dt className="text-slate-500">Moment amplification ηM</dt>
                      <dd className="font-mono text-slate-100">
                        {number(analysis.stability.governing_moment_amplification, 4)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Displacement amplification ηδ</dt>
                      <dd className="font-mono text-slate-100">
                        {number(analysis.stability.governing_displacement_amplification, 4)}
                      </dd>
                    </div>
                  </dl>
                  <button
                    type="button"
                    onClick={() => selectVerificationStage('stability')}
                    className="mt-3 w-full rounded border border-fuchsia-500/30 bg-slate-950/50 px-2 py-1.5 text-[10px] font-semibold text-fuchsia-200 hover:border-fuchsia-300"
                  >
                    Show stability combination + displaced shape
                  </button>
                </section>
              )}

              {analysis && activeCombination && (
                <section className="rounded border border-sky-500/30 bg-sky-950/20 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-sky-300">
                      Authored action cases
                    </div>
                    <span className="text-[9px] text-sky-200">
                      arrows show active directions
                    </span>
                  </div>
                  <div className="mt-3 space-y-1">
                    {analysis.load_cases.map((loadCase) => {
                      const factor = activeCombination.factors[loadCase.id] ?? 0
                      return (
                        <div
                          key={loadCase.id}
                          className="flex items-center justify-between rounded border border-slate-800 bg-slate-950/50 px-2 py-1.5"
                        >
                          <div>
                            <div className="text-[10px] font-semibold text-slate-200">
                              {loadCase.label}
                            </div>
                            <div className="font-mono text-[9px] text-slate-500">
                              {loadCase.id}
                            </div>
                          </div>
                          <span className={`rounded px-2 py-0.5 font-mono text-[9px] ${
                            factor === 0
                              ? 'bg-slate-800 text-slate-500'
                              : 'bg-sky-500/15 text-sky-200'
                          }`}>
                            × {number(factor, factor % 1 === 0 ? 0 : 2)}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </section>
              )}

              {analysis && analysis.members.length > 1 && (
                <section>
                  <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">
                    Analytical members
                  </div>
                  <div className="mt-2 space-y-1">
                    {analysis.members.map((member) => {
                      const result = analysis.member_results.find(
                        (candidate) => candidate.member_id === member.id,
                      )
                      const check = diagramMode === 'moment'
                        ? (member.tension_only
                          ? analysis.tension_member_checks?.find(
                            (candidate) => candidate.member_id === member.id,
                          )
                          : analysis.member_checks.find(
                            (candidate) => candidate.member_id === member.id,
                          ))
                        : analysis.serviceability_checks.find(
                          (candidate) => (
                            candidate.member_id === member.id
                            || candidate.analytical_member_ids?.includes(member.id)
                          ),
                        )
                      return (
                        <button
                          key={member.id}
                          type="button"
                          onClick={() => {
                            setSelectedMemberId(member.id)
                            setSelectedVisualNodeId(member.visual_node_id)
                          }}
                          className={`flex w-full items-center justify-between rounded border px-3 py-2 text-left ${
                            selectedMember?.id === member.id
                              ? 'border-cyan-400 bg-cyan-500/10'
                              : 'border-slate-800 bg-slate-900/60 hover:border-slate-600'
                          }`}
                        >
                          <span className="text-xs font-semibold text-slate-200">
                            {member.label}
                            {member.tension_only && (
                              <span className="ml-2 rounded bg-violet-500/15 px-1.5 py-0.5 font-mono text-[8px] text-violet-300">
                                TENSION-ONLY
                              </span>
                            )}
                            {member.analytical_role === 'rigid_zone' && (
                              <span className="ml-2 rounded bg-cyan-500/15 px-1.5 py-0.5 font-mono text-[8px] text-cyan-300">
                                JOINT RIGID ZONE
                              </span>
                            )}
                          </span>
                          <span className={`rounded px-2 py-0.5 font-mono text-[9px] ${
                            check?.status === 'pass'
                              ? 'bg-emerald-500/15 text-emerald-300'
                              : check?.status === 'fail'
                                ? 'bg-red-500/15 text-red-300'
                                : 'bg-slate-800 text-slate-400'
                          }`}>
                            {check?.status === 'not_checked' || !check
                              ? 'NOT CHECKED'
                              : `${check.status.toUpperCase()} · ${number(
                                (('utilisation' in check
                                  ? check.utilisation
                                  : check.governing_utilisation) ?? 0) * 100,
                                1,
                              )}%`}
                            {result && diagramMode === 'displacement'
                              ? ` · ${number(result.max_displacement_mm, 2)} mm`
                              : ''}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </section>
              )}

              {analysis && selectedMemberResult && selectedMember && firstReaction && (
                <section className="rounded border border-cyan-500/40 bg-cyan-500/10 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-300">
                        PyNite elastic demand
                      </div>
                      <div className="mt-1 text-sm font-semibold text-slate-100">
                        {selectedMember.label}
                        {selectedMember.tension_only && (
                          <span className="ml-2 rounded bg-violet-500/15 px-1.5 py-0.5 font-mono text-[9px] text-violet-300">
                            TENSION-ONLY
                          </span>
                        )}
                        {selectedMember.analytical_role === 'rigid_zone' && (
                          <span className="ml-2 rounded bg-cyan-500/15 px-1.5 py-0.5 font-mono text-[9px] text-cyan-300">
                            JOINT RIGID ZONE
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setSelectedVisualNodeId(selectedMember.visual_node_id)}
                      className="rounded border border-cyan-500/40 bg-slate-950/50 px-2 py-1 text-[10px] font-semibold text-cyan-200 hover:border-cyan-300"
                    >
                      Focus member
                    </button>
                  </div>
                  <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <dt className="text-slate-500">Max moment</dt>
                      <dd className={`font-mono ${
                        selectedCheck?.status === 'pass'
                          ? 'text-emerald-300'
                          : selectedCheck?.status === 'fail'
                            ? 'text-red-300'
                            : 'text-slate-300'
                      }`}>
                        {number(selectedMemberResult.max_moment_kNm, 4)} kN·m
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Max shear</dt>
                      <dd className="font-mono">
                        {number(selectedMemberResult.max_shear_kN, 4)} kN
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Max axial</dt>
                      <dd className="font-mono">
                        {number(selectedMemberResult.max_axial_kN, 4)} kN
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Max displacement</dt>
                      <dd className="font-mono">
                        {number(selectedMemberResult.max_displacement_mm, 3)} mm
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Member loads</dt>
                      <dd className="font-mono">
                        {analysis.member_loads.filter(
                          (load) => load.member_id === selectedMember.id,
                        ).length} point ·{' '}
                        {analysis.member_distributed_loads.filter(
                          (load) => load.member_id === selectedMember.id,
                        ).length} line
                      </dd>
                    </div>
                  </dl>
                  {selectedMember.analytical_role === 'rigid_zone' && selectedSourceConnection?.joint_model && (
                    <div className="mt-3 rounded border border-cyan-500/30 bg-slate-950/50 p-3 text-xs">
                      <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-cyan-300">
                        Geometry-linked connection arm
                      </div>
                      <div className="mt-1 font-semibold text-slate-200">
                        {selectedSourceConnection.label}
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-slate-400">
                        <span>Model</span>
                        <span className="font-mono text-slate-200">
                          {selectedSourceConnection.joint_model.analysis_model}
                        </span>
                        <span>Evidence</span>
                        <span className="font-mono text-amber-300">
                          {selectedSourceConnection.joint_model.stiffness_status.toUpperCase()}
                        </span>
                      </div>
                      <p className="mt-2 text-[10px] leading-relaxed text-slate-400">
                        {selectedSourceConnection.joint_model.stiffness_basis}
                      </p>
                    </div>
                  )}
                  {selectedMember.tension_only && selectedTensionCheck && (
                    <div className="mt-3 rounded border border-violet-500/30 bg-slate-950/50 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-violet-300">
                          Stage 8 strap check
                        </span>
                        <span className={`rounded px-2 py-0.5 font-mono text-[9px] ${
                          selectedTensionCheck.status === 'pass'
                            ? 'bg-emerald-500/15 text-emerald-300'
                            : selectedTensionCheck.status === 'fail'
                              ? 'bg-red-500/15 text-red-300'
                              : 'bg-amber-500/15 text-amber-300'
                        }`}>
                          {selectedTensionCheck.status.toUpperCase()}
                        </span>
                      </div>
                      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <dt className="text-slate-500">Governing ULS tension</dt>
                          <dd className="font-mono text-slate-200">
                            {number(selectedTensionCheck.tension_demand_kN, 4)} kN
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Combination</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.governing_combination_id || 'No tension'}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Strap design capacity</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.tension_capacity_kN == null
                              ? 'Unverified'
                              : `${number(selectedTensionCheck.tension_capacity_kN, 3)} kN`}
                          </dd>
                          <dd className="mt-0.5 font-mono text-[9px] uppercase text-violet-300">
                            {selectedTensionCheck.member_capacity_status.replace('_', ' ')}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">End connection capacity</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.end_connection_capacity_kN == null
                              ? 'Unverified'
                              : `${number(selectedTensionCheck.end_connection_capacity_kN, 3)} kN`}
                          </dd>
                          <dd className="mt-0.5 font-mono text-[9px] uppercase text-violet-300">
                            {selectedTensionCheck.connection_capacity_status.replace('_', ' ')}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Gross / net area</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.gross_area_mm2 == null
                              || selectedTensionCheck.net_area_mm2 == null
                              ? 'Unspecified'
                              : `${number(selectedTensionCheck.gross_area_mm2, 2)} / ${number(
                                  selectedTensionCheck.net_area_mm2,
                                  2,
                                )} mm²`}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Gross yield / net fracture</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.gross_yield_capacity_kN == null
                              || selectedTensionCheck.net_fracture_capacity_kN == null
                              ? 'Unverified'
                              : `${number(selectedTensionCheck.gross_yield_capacity_kN, 3)} / ${number(
                                  selectedTensionCheck.net_fracture_capacity_kN,
                                  3,
                                )} kN`}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Bearing / tear-out capacity</dt>
                          <dd className="font-mono text-slate-200">
                            {[
                              selectedTensionCheck.end_bearing_capacity_kN,
                              selectedTensionCheck.end_tearout_capacity_kN,
                            ].map((value) => (
                              value == null ? '—' : number(value, 3)
                            )).join(' / ')} kN
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Installed fastener product</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.end_fastener_part_numbers.join(', ') || 'Unidentified'}
                          </dd>
                          <dd className="mt-0.5 font-mono text-[9px] uppercase text-violet-300">
                            {(selectedTensionCheck.fastener_evidence_status || 'unverified').replace('_', ' ')} evidence
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Tested shear / required 1.25 Vb</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.fastener_tested_single_shear_strength_kN == null
                              || selectedTensionCheck.fastener_required_single_shear_strength_kN == null
                              ? 'Not qualified'
                              : `${number(
                                  selectedTensionCheck.fastener_tested_single_shear_strength_kN,
                                  3,
                                )} / ${number(
                                  selectedTensionCheck.fastener_required_single_shear_strength_kN,
                                  3,
                                )} kN`}
                          </dd>
                          <dd className={`mt-0.5 font-mono text-[9px] uppercase ${
                            selectedTensionCheck.fastener_shear_qualification_status === 'pass'
                              ? 'text-emerald-300'
                              : selectedTensionCheck.fastener_shear_qualification_status === 'fail'
                                ? 'text-red-300'
                                : 'text-amber-300'
                          }`}>
                            {selectedTensionCheck.fastener_shear_qualification_status.replace('_', ' ')}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Spacing / edge distance</dt>
                          <dd className="font-mono uppercase text-slate-200">
                            {selectedTensionCheck.spacing_status.replace('_', ' ')} /{' '}
                            {selectedTensionCheck.edge_distance_status.replace('_', ' ')}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Required / end fastener</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.required_force_per_end_fastener_kN == null
                              ? 'Unspecified'
                              : `${number(selectedTensionCheck.required_force_per_end_fastener_kN, 4)} kN`}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Rendered end fasteners</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.rendered_end_connection_count === 0
                              ? 'No physical ends found'
                              : `${selectedTensionCheck.rendered_end_fastener_counts.join(' / ')} across ${
                                  selectedTensionCheck.rendered_end_connection_count
                                } ends`}
                          </dd>
                        </div>
                        <div className="col-span-2">
                          <dt className="text-slate-500">Fastener evidence</dt>
                          <dd className="text-[10px] leading-relaxed text-slate-300">
                            {selectedTensionCheck.fastener_evidence_url ? (
                              <a
                                className="text-cyan-300 underline decoration-cyan-500/50 underline-offset-2"
                                href={selectedTensionCheck.fastener_evidence_url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {selectedTensionCheck.fastener_evidence_source || 'Manufacturer source'}
                                {selectedTensionCheck.fastener_evidence_revision
                                  ? ` · ${selectedTensionCheck.fastener_evidence_revision}`
                                  : ''}
                              </a>
                            ) : (
                              selectedTensionCheck.fastener_evidence_source || 'No source linked'
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Governing utilisation</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedTensionCheck.governing_utilisation == null
                              ? 'Unverified'
                              : `${number(selectedTensionCheck.governing_utilisation * 100, 1)}%`}
                          </dd>
                        </div>
                      </dl>
                      <div className="mt-2 border-t border-slate-800 pt-2 text-[10px] leading-relaxed text-slate-400">
                        <p>
                          {selectedTensionCheck.standard_reference || selectedTensionCheck.basis}
                        </p>
                        {selectedTensionCheck.assumptions.length > 0 && (
                          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-amber-200/80">
                            {selectedTensionCheck.assumptions.map((assumption) => (
                              <li key={assumption}>{assumption}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </div>
                  )}
                  {selectedMember.tension_only && selectedGlobalBracingTrace && (
                    <div className="mt-3 rounded border border-cyan-500/30 bg-slate-950/50 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-cyan-300">
                          Selected global bracing trace
                        </span>
                        <span className={`rounded px-2 py-0.5 font-mono text-[9px] ${
                          selectedGlobalBracingTrace.status === 'pass'
                            ? 'bg-emerald-500/15 text-emerald-300'
                            : selectedGlobalBracingTrace.status === 'fail'
                              || selectedGlobalBracingTrace.status === 'blocked'
                              ? 'bg-red-500/15 text-red-300'
                              : 'bg-amber-500/15 text-amber-300'
                        }`}>
                          {selectedGlobalBracingTrace.status.toUpperCase()}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-1 text-[9px] text-slate-200">
                        {selectedGlobalBracingTrace.component_ids.map((componentId, index) => (
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
                      <p className="mt-2 text-[10px] leading-relaxed text-slate-400">
                        {selectedGlobalBracingTrace.basis}
                      </p>
                      <dl className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
                        <div>
                          <dt className="text-slate-500">Grounded components</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedGlobalBracingTrace.grounded_component_ids.join(', ') || 'None'}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Governing ULS</dt>
                          <dd className="font-mono text-slate-200">
                            {selectedGlobalBracingTrace.governing_combination_id || 'No tension'}
                          </dd>
                        </div>
                      </dl>
                      {selectedGlobalBracingTrace.blockers.length > 0 && (
                        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[10px] leading-relaxed text-amber-200/80">
                          {selectedGlobalBracingTrace.blockers.map((blocker) => (
                            <li key={blocker}>{blocker}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  {(availableCrossSectionCheck || availableMemberStabilityCheck) && (
                    <div
                      role="group"
                      aria-label="Selected member verification stage"
                      className="mt-3 grid grid-cols-2 gap-2 rounded border border-slate-700 bg-slate-950/50 p-1.5"
                    >
                      <button
                        type="button"
                        disabled={!availableCrossSectionCheck}
                        onClick={() => selectMemberEvidenceStage('cross_section')}
                        className={`rounded border px-2 py-2 text-left transition ${
                          activeMemberEvidenceStage === 'cross_section'
                            ? 'border-cyan-400 bg-cyan-500/15 text-cyan-100'
                            : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-600'
                        } disabled:cursor-not-allowed disabled:opacity-40`}
                      >
                        <span className="block text-[9px] font-bold uppercase tracking-[0.14em]">
                          6. Cross-section
                        </span>
                        <span className="mt-1 block font-mono text-[9px] uppercase">
                          {availableCrossSectionCheck?.status.replace('_', ' ') || 'not available'}
                          {availableCrossSectionCheck?.governing_utilisation !== null
                            && availableCrossSectionCheck?.governing_utilisation !== undefined
                            ? ` · ${number(
                                availableCrossSectionCheck.governing_utilisation * 100,
                                1,
                              )}%`
                            : ''}
                        </span>
                      </button>
                      <button
                        type="button"
                        disabled={!availableMemberStabilityCheck}
                        onClick={() => selectMemberEvidenceStage('member_stability')}
                        className={`rounded border px-2 py-2 text-left transition ${
                          activeMemberEvidenceStage === 'member_stability'
                            ? 'border-violet-400 bg-violet-500/15 text-violet-100'
                            : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-600'
                        } disabled:cursor-not-allowed disabled:opacity-40`}
                      >
                        <span className="block text-[9px] font-bold uppercase tracking-[0.14em]">
                          7. Member stability
                        </span>
                        <span className="mt-1 block font-mono text-[9px] uppercase">
                          {availableMemberStabilityCheck?.status.replace('_', ' ') || 'not available'}
                          {availableMemberStabilityCheck?.governing_utilisation !== null
                            && availableMemberStabilityCheck?.governing_utilisation !== undefined
                            ? ` · ${number(
                                availableMemberStabilityCheck.governing_utilisation * 100,
                                1,
                              )}%`
                            : ''}
                        </span>
                      </button>
                    </div>
                  )}
                  {(selectedCheck || selectedCrossSectionCheck || selectedMemberStabilityCheck) && (
                    <div className={`mt-3 rounded border p-3 ${
                      selectedDisplayCheckStatus === 'pass'
                        ? 'border-emerald-500/40 bg-emerald-950/30'
                        : selectedDisplayCheckStatus === 'fail'
                          ? 'border-red-500/50 bg-red-950/30'
                          : selectedDisplayCheckStatus === 'unsupported'
                            ? 'border-amber-500/40 bg-amber-950/30'
                          : 'border-slate-700 bg-slate-950/40'
                    }`}>
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="font-bold uppercase tracking-[0.15em] text-slate-400">
                          {selectedMemberStabilityCheck
                            ? 'AS/NZS 4600 Stage 7 member stability'
                            : selectedCrossSectionCheck
                              ? 'AS/NZS 4600 Stage 6 cross-section'
                            : 'Effective-section yield reference'}
                        </span>
                        <span className={`font-bold uppercase ${
                          selectedDisplayCheckStatus === 'pass'
                            ? 'text-emerald-300'
                            : selectedDisplayCheckStatus === 'fail'
                              ? 'text-red-300'
                              : selectedDisplayCheckStatus === 'unsupported'
                                ? 'text-amber-300'
                              : 'text-slate-400'
                        }`}>
                          {(selectedDisplayCheckStatus || 'not_checked')
                            .replace('_', ' ')}
                        </span>
                      </div>
                      <div className="mt-2 font-mono text-xs text-slate-200">
                        {selectedMemberStabilityCheck
                          ? (
                            <>
                              {selectedMemberStabilityCheck.axial_kN === null
                                ? '—'
                                : number(selectedMemberStabilityCheck.axial_kN, 3)}
                              {' / '}
                              {selectedMemberStabilityCheck
                                .design_member_compression_capacity_kN === null
                                ? 'no member resistance'
                                : `${number(
                                    selectedMemberStabilityCheck
                                      .design_member_compression_capacity_kN,
                                    3,
                                  )} kN axial screen`}
                            </>
                          )
                          : selectedCrossSectionCheck
                            ? (
                              <>
                                {selectedCrossSectionCheck.governing_utilisation === null
                                  ? '—'
                                  : `${number(
                                      selectedCrossSectionCheck.governing_utilisation * 100,
                                      1,
                                    )}% governing section envelope`}
                              </>
                            )
                            : selectedCheck
                              ? (
                            <>
                              {number(selectedCheck.demand_kNm, 4)} kN·m /{' '}
                              {selectedCheck.capacity_kNm === null
                                ? 'no reference'
                                : `${number(selectedCheck.capacity_kNm, 4)} kN·m`}
                            </>
                              )
                              : '—'}
                      </div>
                      {selectedDisplayUtilisation !== null
                        && selectedDisplayUtilisation !== undefined && (
                        <div className="mt-1 font-mono text-[10px] text-slate-400">
                          {number(selectedDisplayUtilisation * 100, 1)}%{' '}
                          {selectedMemberStabilityCheck
                            ? 'governing member utilisation'
                            : selectedCrossSectionCheck
                              ? 'governing cross-section utilisation'
                            : 'reference utilisation'}
                        </div>
                      )}
                      {selectedCrossSectionCheck && !selectedMemberStabilityCheck && (
                        <dl className="mt-2 grid grid-cols-2 gap-2 border-t border-slate-700/70 pt-2 text-[9px]">
                          <div>
                            <dt className="text-slate-500">ULS envelope</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.governing_combination_id || '—'}
                              {' @ '}
                              {selectedCrossSectionCheck.governing_station_m === null
                                ? '—'
                                : `${number(selectedCrossSectionCheck.governing_station_m, 3)} m`}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Axial / resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.axial_kN === null
                                ? '—'
                                : number(selectedCrossSectionCheck.axial_kN, 3)}
                              {' / '}
                              {selectedCrossSectionCheck.design_compression_capacity_kN === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck.design_compression_capacity_kN,
                                    3,
                                  )} kN
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Web shear / resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.web_shear_kN === null
                                ? '—'
                                : number(selectedCrossSectionCheck.web_shear_kN, 3)}
                              {' / '}
                              {selectedCrossSectionCheck.design_web_shear_capacity_kN === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck.design_web_shear_capacity_kN,
                                    3,
                                  )} kN
                            </dd>
                          </div>
                          <div>
                            <dt className="text-cyan-400">Major-axis Mz / resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.major_moment_kNm === null
                                ? '—'
                                : number(selectedCrossSectionCheck.major_moment_kNm, 4)}
                              {' / '}
                              {selectedCrossSectionCheck.design_major_bending_capacity_kNm === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck.design_major_bending_capacity_kNm,
                                    4,
                                  )} kN·m
                            </dd>
                          </div>
                          <div>
                            <dt className="text-pink-400">Minor-axis My / resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.minor_moment_kNm === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck.minor_moment_kNm,
                                    4,
                                  )}
                              {' / '}
                              {selectedCrossSectionCheck.design_minor_bending_capacity_kNm === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck.design_minor_bending_capacity_kNm,
                                    4,
                                  )} kN·m
                            </dd>
                          </div>
                          <div>
                            <dt className="text-pink-400">Off-axis shear Fz / resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.off_axis_shear_kN === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck.off_axis_shear_kN,
                                    4,
                                  )}
                              {' / '}
                              {selectedCrossSectionCheck.design_off_axis_shear_capacity_kN === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck.design_off_axis_shear_capacity_kN,
                                    4,
                                  )} kN
                            </dd>
                          </div>
                          <div>
                            <dt className="text-pink-400">Torque / St-Venant resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.torsion_kNm === null
                                ? '—'
                                : number(selectedCrossSectionCheck.torsion_kNm, 4)}
                              {' / '}
                              {selectedCrossSectionCheck
                                .design_st_venant_torsion_capacity_kNm === null
                                ? '—'
                                : number(
                                    selectedCrossSectionCheck
                                      .design_st_venant_torsion_capacity_kNm,
                                    4,
                                  )} kN·m
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Web regime</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedCrossSectionCheck.shear_regime?.replace('_', ' ') || '—'}
                            </dd>
                          </div>
                          <div className="col-span-2 grid grid-cols-2 gap-2 rounded border border-slate-700/70 p-2">
                            <div>
                              <dt className="text-slate-500">Biaxial N + M utilisation</dt>
                              <dd className="font-mono text-slate-300">
                                {selectedCrossSectionCheck
                                  .biaxial_axial_bending_utilisation === null
                                  ? '—'
                                  : `${number(
                                      selectedCrossSectionCheck
                                        .biaxial_axial_bending_utilisation * 100,
                                      1,
                                    )}%`}
                              </dd>
                            </div>
                            <div>
                              <dt className="text-slate-500">Torsion utilisation</dt>
                              <dd className="font-mono text-slate-300">
                                {selectedCrossSectionCheck.torsion_utilisation === null
                                  ? '—'
                                  : `${number(
                                      selectedCrossSectionCheck.torsion_utilisation * 100,
                                      1,
                                    )}%`}
                              </dd>
                            </div>
                          </div>
                          <div className="col-span-2 rounded border border-amber-500/30 bg-amber-950/20 p-2">
                            <div className="flex items-center justify-between gap-2">
                              <dt className="font-semibold text-amber-300">
                                Off-axis load path
                              </dt>
                              <dd className="font-bold uppercase text-amber-300">
                                {selectedCrossSectionCheck.off_axis_load_path_status
                                  .replace('_', ' ')}
                              </dd>
                            </div>
                            <div className="mt-1 font-mono text-slate-300">
                              Required support reaction:{' '}
                              {selectedCrossSectionCheck.off_axis_required_reaction_kN === null
                                ? '—'
                                : `${number(
                                    selectedCrossSectionCheck.off_axis_required_reaction_kN,
                                    4,
                                  )} kN`}
                            </div>
                            <div className="mt-1 text-slate-400">
                              Action source:{' '}
                              {selectedCrossSectionCheck.off_axis_source_component_ids.join(' → ')
                                || 'no wind-normal surface connection declared'}
                            </div>
                            <div className="mt-1 text-slate-400">
                              Collector to ground:{' '}
                              {selectedCrossSectionCheck.off_axis_collector_component_ids.join(' → ')
                                || 'not declared'}
                            </div>
                            <div className="mt-1 text-slate-500">
                              {selectedCrossSectionCheck.off_axis_load_path_basis
                                || 'No authored collector path basis.'}
                            </div>
                          </div>
                        </dl>
                      )}
                      {selectedMemberStabilityCheck && (
                        <dl className="mt-2 grid grid-cols-2 gap-2 border-t border-slate-700/70 pt-2 text-[9px]">
                          <div>
                            <dt className="text-slate-500">Unbraced segment</dt>
                            <dd className="font-mono text-slate-300">
                              {number(selectedMemberStabilityCheck.unbraced_length_m, 3)} m
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Axial / member resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.axial_kN === null
                                ? '—'
                                : number(selectedMemberStabilityCheck.axial_kN, 3)}
                              {' / '}
                              {selectedMemberStabilityCheck
                                .design_member_compression_capacity_kN === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .design_member_compression_capacity_kN,
                                    3,
                                  )} kN
                            </dd>
                          </div>
                          <div>
                            <dt className="text-cyan-400">Major Mz / member resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.major_moment_kNm === null
                                ? '—'
                                : number(selectedMemberStabilityCheck.major_moment_kNm, 3)}
                              {' / '}
                              {selectedMemberStabilityCheck.design_major_bending_capacity_kNm === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .design_major_bending_capacity_kNm,
                                    3,
                                  )} kN·m
                            </dd>
                          </div>
                          <div>
                            <dt className="text-pink-400">Minor My / member resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.minor_moment_kNm === null
                                ? '—'
                                : number(selectedMemberStabilityCheck.minor_moment_kNm, 3)}
                              {' / '}
                              {selectedMemberStabilityCheck.design_minor_bending_capacity_kNm === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .design_minor_bending_capacity_kNm,
                                    3,
                                  )} kN·m
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Shear Fy / Fz</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.web_shear_kN === null
                                ? '—'
                                : number(selectedMemberStabilityCheck.web_shear_kN, 3)}
                              {' / '}
                              {selectedMemberStabilityCheck.off_axis_shear_kN === null
                                ? '—'
                                : number(selectedMemberStabilityCheck.off_axis_shear_kN, 3)} kN
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Torque / resistance</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.torsion_kNm === null
                                ? '—'
                                : number(selectedMemberStabilityCheck.torsion_kNm, 4)}
                              {' / '}
                              {selectedMemberStabilityCheck
                                .design_st_venant_torsion_capacity_kNm === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .design_st_venant_torsion_capacity_kNm,
                                    4,
                                  )} kN·m
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Governing modes N / Mz / My</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.governing_compression_mode
                                ?.replaceAll('_', ' ') || '—'}
                              {' / '}
                              {selectedMemberStabilityCheck.governing_bending_mode
                                ?.replaceAll('_', ' ') || '—'}
                              {' / '}
                              {selectedMemberStabilityCheck.governing_minor_bending_mode
                                ?.replaceAll('_', ' ') || '—'}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Interaction / torsion</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck
                                .biaxial_member_interaction_utilisation === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .biaxial_member_interaction_utilisation * 100,
                                    1,
                                  )}%
                              {' / '}
                              {selectedMemberStabilityCheck.torsion_utilisation === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck.torsion_utilisation * 100,
                                    1,
                                  )}%
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Axial amplification Mz / My</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck
                                .major_axis_amplification_factor === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .major_axis_amplification_factor,
                                    3,
                                  )}
                              {' / '}
                              {selectedMemberStabilityCheck
                                .minor_axis_amplification_factor === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .minor_axis_amplification_factor,
                                    3,
                                  )}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Flexural-torsional Fe</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck
                                .elastic_flexural_torsional_buckling_stress_MPa === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .elastic_flexural_torsional_buckling_stress_MPa,
                                    2,
                                  )} MPa
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Distortional fod (N / M)</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck
                                .elastic_distortional_compression_stress_MPa === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .elastic_distortional_compression_stress_MPa,
                                    2,
                                  )}
                              {' / '}
                              {selectedMemberStabilityCheck
                                .elastic_distortional_bending_stress_MPa === null
                                ? '—'
                                : number(
                                    selectedMemberStabilityCheck
                                      .elastic_distortional_bending_stress_MPa,
                                    2,
                                  )} MPa
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Candidate restraint (not credited)</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.lateral_bending_restraint
                                .replaceAll('_', ' ')}
                              {' · '}
                              {selectedMemberStabilityCheck.restraint_status}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Signed-moment compression flange</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.compression_flange
                                .replaceAll('_', ' ')}
                            </dd>
                          </div>
                          <div className="col-span-2">
                            <dt className="text-slate-500">Effective physical candidates</dt>
                            <dd className="break-all font-mono text-slate-300">
                              {selectedMemberStabilityCheck.restraint_candidate_ids.length
                                ? selectedMemberStabilityCheck.restraint_candidate_ids.join(', ')
                                : 'none at both required boundaries'}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Distortional buckling</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.distortional_buckling_status}
                            </dd>
                          </div>
                          <div className="col-span-2">
                            <dt className="text-slate-500">Calculation basis</dt>
                            <dd className="font-mono text-slate-300">
                              {selectedMemberStabilityCheck.standard_reference || '—'}
                            </dd>
                            <dd className="break-all font-mono text-[8px] text-slate-500">
                              {selectedMemberStabilityCheck.standard_source_sha256 || 'source hash missing'}
                            </dd>
                          </div>
                        </dl>
                      )}
                      <p className="mt-2 text-[9px] text-slate-500">
                        {selectedMemberStabilityCheck?.basis
                          || selectedCrossSectionCheck?.basis
                          || selectedCheck?.basis
                          || 'No calculation basis is available for this member.'}
                      </p>
                    </div>
                  )}
                  {selectedServiceability && (
                    <div className={`mt-3 rounded border p-3 ${
                      selectedServiceability.status === 'pass'
                        ? 'border-emerald-500/30 bg-emerald-950/30'
                        : selectedServiceability.status === 'fail'
                          ? 'border-red-500/40 bg-red-950/30'
                          : 'border-slate-700 bg-slate-950/40'
                    }`}>
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="font-bold uppercase tracking-[0.15em] text-slate-400">
                          Deflection criterion
                        </span>
                        <span className="font-bold uppercase">
                          {selectedServiceability.status.replace('_', ' ')}
                        </span>
                      </div>
                      <div className="mt-2 font-mono text-xs text-slate-200">
                        {number(selectedServiceability.displacement_mm, 3)} mm /{' '}
                        {selectedServiceability.limit_mm === null
                          ? 'no authored limit'
                          : `${number(selectedServiceability.limit_mm, 3)} mm`}
                      </div>
                      <p className="mt-2 text-[9px] text-slate-500">
                        {selectedServiceability.basis}
                      </p>
                    </div>
                  )}
                  {selectedSection?.catalog && (
                    <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-950/30 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-[10px] font-bold uppercase tracking-[0.15em] text-emerald-300">
                            Validated catalogue section
                          </div>
                          <div className="mt-1 text-xs font-semibold text-slate-100">
                            {selectedSection.catalog.section_key}
                          </div>
                        </div>
                        <span className="rounded bg-slate-950/60 px-2 py-1 font-mono text-[9px] text-emerald-200">
                          {selectedSection.catalog.catalog_id} v{selectedSection.catalog.catalog_version}
                        </span>
                      </div>
                      <dl className="mt-3 grid grid-cols-3 gap-2 text-[10px]">
                        <div>
                          <dt className="text-slate-500">Area</dt>
                          <dd className="font-mono">
                            {typeof (catalogueProperties?.A_mm2 ?? catalogueProperties?.A) === 'number'
                              ? `${number(
                                  (catalogueProperties?.A_mm2 ?? catalogueProperties?.A) as number,
                                  0,
                                )} mm²`
                              : '—'}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Yield</dt>
                          <dd className="font-mono">
                            {typeof (catalogueProperties?.fy_MPa ?? catalogueProperties?.fy) === 'number'
                              ? `${number(
                                  (catalogueProperties?.fy_MPa ?? catalogueProperties?.fy) as number,
                                  0,
                                )} MPa`
                              : '—'}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Zxe</dt>
                          <dd className="font-mono">
                            {typeof (catalogueProperties?.Zxe_mm3 ?? catalogueProperties?.Zxe) === 'number'
                              ? `${number(
                                  (catalogueProperties?.Zxe_mm3 ?? catalogueProperties?.Zxe) as number,
                                  0,
                                )} mm³`
                              : '—'}
                          </dd>
                        </div>
                      </dl>
                      <div
                        className="mt-2 truncate font-mono text-[9px] text-slate-500"
                        title={selectedSection.catalog.record_sha256}
                      >
                        Record {selectedSection.catalog.record_sha256.slice(0, 12)}
                      </div>
                    </div>
                  )}
                  <div className="mt-3 border-t border-cyan-500/20 pt-3">
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="font-bold uppercase tracking-[0.15em] text-slate-400">
                        First support reaction
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
                    {diagramMode === 'displacement'
                      ? 'Displacement is amplified for visibility; the reported millimetres are unscaled.'
                      : 'Use Resultant, Major, and Minor above to audit the analytical axes against the CAD. Major-axis ribbons are cyan; minor-axis ribbons are pink and require separate resistance evidence. Member centreline colour remains the pass/fail status. Cyan arrows are loads; pink arrows are reactions.'}
                  </p>
                </section>
              )}

              <section className={`rounded border p-3 text-xs ${
                crossSectionStage?.status === 'pass'
                  ? 'border-emerald-500/30 bg-emerald-950/30 text-emerald-200'
                  : crossSectionStage?.status === 'fail'
                    ? 'border-red-500/40 bg-red-950/30 text-red-200'
                    : 'border-amber-500/30 bg-amber-950/30 text-amber-200'
              }`}>
                <div className="font-semibold">
                  Cross-section status: {(crossSectionStage?.status || 'not checked')
                    .replace('_', ' ')
                    .toUpperCase()}
                </div>
                <p className="mt-1 text-[10px] opacity-75">
                  {crossSectionStage?.summary ||
                    'Select a versioned Australian capacity pack in Structural workbench configuration.'}
                  {' '}This colour is Stage 6 cross-section resistance only. Member buckling,
                  restraint, bracing, connections, bases, and the final order decision remain
                  separate verification stages.
                </p>
              </section>
              <section className={`rounded border p-3 text-xs ${
                memberStabilityStage?.status === 'pass'
                  ? 'border-emerald-500/30 bg-emerald-950/30 text-emerald-200'
                  : memberStabilityStage?.status === 'fail'
                    ? 'border-red-500/40 bg-red-950/30 text-red-200'
                    : 'border-amber-500/30 bg-amber-950/30 text-amber-200'
              }`}>
                <div className="font-semibold">
                  Member-stability status: {(memberStabilityStage?.status || 'not checked')
                    .replace('_', ' ')
                    .toUpperCase()}
                </div>
                <p className="mt-1 text-[10px] opacity-75">
                  {memberStabilityStage?.summary ||
                    'Author restraint-defined segments in Structural workbench configuration.'}
                  {' '}Green/red member colours only represent Stage 7 when the
                  governing compression-flange/twist restraint and distortional
                  buckling resistance are verified.
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
                ? activeStage
                  ? `Stage ${activeStage.order} ${activeStage.label} · ${activeStage.status.replace('_', ' ')}`
                  : `Active-project model with PyNite ${diagramMode} overlay`
                : 'Active-project model linked to parsed structural declarations'
            }
            externalSelectedNodeIds={selectedRestraintVisualNodeIds}
            structuralOverlays={structuralOverlays}
            onStructuralRestraintSelect={selectRestraintTrace}
          />
        </main>
      </div>
    </div>
  )
}
