import type { StructuralViewerOverlay } from '../extus/ui/ViewerTab'
import type { StructuralSnapshot } from './contracts'

export type StructuralStageFocus = NonNullable<StructuralViewerOverlay['stageFocus']>

const stageVisualDescription: Record<string, string> = {
  geometry: 'Analytical member axes, shared nodes, supports, and disconnected geometry.',
  actions: 'Applied point, distributed, and nodal actions for the selected load case basis.',
  combinations: 'Factored actions belonging to the selected Tertius-managed combination.',
  analysis: 'Solved reactions and signed member-demand ribbons for the selected combination.',
  stability: 'Amplified displacement shape and the members governing global frame stability.',
  cross_section: 'Member demand and cross-section resistance status for the selected member.',
  member_stability: 'Compression-flange demand, unbraced segments, and member-stability status.',
  bracing: 'Compression-flange restraint segments, physical candidates, required force, and missing evidence. Cyan arrow length reflects calculated demand; its transverse direction is schematic.',
  connections: 'Connection and base force-transfer checks linked to rendered components.',
  serviceability: 'Deflected shape compared with the applicable displacement limits.',
  evidence: 'The physical model items supporting, warning, or blocking the certification decision.',
}

function formatNumber(value: number, digits = 3) {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  })
}

export function buildStructuralStageFocus(
  analysis: StructuralSnapshot | null,
  activeStageId: string,
  activeCombination: StructuralSnapshot['load_combinations'][number] | undefined,
): StructuralStageFocus | undefined {
  if (!analysis) return undefined
  const activeStage = analysis.verification_stages?.find(
    (stage) => stage.id === activeStageId,
  )
  if (!activeStage) return undefined
  const activeCombinationLabel = activeCombination
    ? `${activeCombination.id} · ${activeCombination.label}`
    : undefined

  if (activeStage.id === 'bracing') {
    const checks = analysis.member_restraint_candidate_checks ?? []
    const physicalCandidateIds = new Set(checks.map((check) => check.candidate_id))
    const exactProductCandidateIds = new Set(
      checks
        .filter((check) => check.identity_status === 'pass')
        .map((check) => check.candidate_id),
    )
    const asNzsDemandCandidateIds = new Set(
      checks
        .filter((check) => (
          check.demand_model === 'as_nzs_4600_2005_4_3_2_flange_force'
        ))
        .map((check) => check.candidate_id),
    )
    const verifiedCandidateIds = new Set(
      checks
        .filter((check) => (
          check.status === 'pass'
          && check.stiffness_status === 'verified'
          && check.anchorage_status === 'verified'
        ))
        .map((check) => check.candidate_id),
    )
    const maximumRequiredForceKN = Math.max(
      0,
      ...checks.map((check) => check.required_force_kN ?? 0),
    )

    return {
      id: activeStage.id,
      order: activeStage.order,
      label: activeStage.label,
      status: activeStage.status,
      summary: activeStage.summary,
      visualDescription: stageVisualDescription.bracing!,
      combinationLabel: activeCombinationLabel,
      metrics: [
        { label: 'Physical locations', value: String(physicalCandidateIds.size) },
        { label: 'Exact products', value: String(exactProductCandidateIds.size) },
        { label: 'AS/NZS demand', value: String(asNzsDemandCandidateIds.size) },
        { label: 'Maximum required', value: `${formatNumber(maximumRequiredForceKN)} kN` },
        { label: 'Fully verified', value: String(verifiedCandidateIds.size) },
      ],
      legend: [
        { label: 'Verified restraint', tone: 'verified' },
        { label: 'Exact-product candidate', tone: 'candidate' },
        { label: 'Missing stiffness / anchorage ring', tone: 'missing' },
        { label: 'Required restraint force', tone: 'demand' },
      ],
    }
  }

  return {
    id: activeStage.id,
    order: activeStage.order,
    label: activeStage.label,
    status: activeStage.status,
    summary: activeStage.summary,
    visualDescription: stageVisualDescription[activeStage.id]
      || 'The selected calculation stage is linked to the current analytical model.',
    combinationLabel: activeCombinationLabel,
    metrics: [
      { label: 'Analytical members', value: String(analysis.members.length) },
      { label: 'Stage sheets', value: String(activeStage.sheet_ids.length) },
    ],
    legend: [
      { label: 'Selected-stage analytical context', tone: 'neutral' },
    ],
  }
}

export function buildStage8Overlays(
  analysis: StructuralSnapshot | null,
  activeStageId: string,
  activeCombinationId: string | undefined,
  selectedRestraintTraceId: string,
  stageFocus: StructuralStageFocus | undefined,
): StructuralViewerOverlay[] {
  if (!analysis || activeStageId !== 'bracing' || !stageFocus) return []
  const availableTraces = (analysis.member_restraint_traces ?? [])
    .filter((trace) => (
      trace.combination_id === activeCombinationId
      && trace.status !== 'not_required'
    ))
    .sort((left, right) => (
      Number(right.id === selectedRestraintTraceId)
      - Number(left.id === selectedRestraintTraceId)
      || (right.required_restraint_force_kN ?? 0)
      - (left.required_restraint_force_kN ?? 0)
    ))
  const trace = availableTraces[0]
  if (!trace) {
    return [{
      id: 'stage-focus-bracing',
      label: 'Stage 8 bracing and restraint focus',
      mode: 'moment',
      status: 'not_checked',
      stations: [],
      stageFocus,
    }]
  }

  const candidateChecks = (analysis.member_restraint_candidate_checks ?? [])
    .filter((check) => check.combination_id === trace.combination_id)
  const checksByCandidateId = new Map(
    candidateChecks.map((check) => [check.candidate_id, check]),
  )
  const axis = {
    x: trace.end_position.x - trace.start_position.x,
    y: trace.end_position.y - trace.start_position.y,
    z: trace.end_position.z - trace.start_position.z,
  }
  let direction = { x: -axis.y, y: axis.x, z: 0 }
  let directionLength = Math.hypot(direction.x, direction.y, direction.z)
  if (directionLength <= Number.EPSILON) {
    direction = { x: 0, y: -axis.z, z: axis.y }
    directionLength = Math.hypot(direction.x, direction.y, direction.z)
  }
  if (directionLength <= Number.EPSILON) {
    direction = { x: 1, y: 0, z: 0 }
    directionLength = 1
  }
  const flangeSign = trace.compression_flange === 'negative_local_y' ? -1 : 1
  const unitDirection = {
    x: flangeSign * direction.x / directionLength,
    y: flangeSign * direction.y / directionLength,
    z: flangeSign * direction.z / directionLength,
  }
  const boundaryCandidates = [
    {
      suffix: 'start',
      position: trace.start_position,
      candidateIds: trace.start_restraint_candidate_ids,
    },
    {
      suffix: 'end',
      position: trace.end_position,
      candidateIds: trace.end_restraint_candidate_ids,
    },
  ]
  const restraintMarkers: NonNullable<StructuralViewerOverlay['restraintMarkers']> = []
  for (const boundary of boundaryCandidates) {
    const candidateIds = boundary.candidateIds.length > 0
      ? boundary.candidateIds
      : [`missing-${trace.id}-${boundary.suffix}`]
    for (const candidateId of candidateIds) {
      const check = checksByCandidateId.get(candidateId)
      const status = check?.status === 'pass'
        ? 'verified'
        : check?.status === 'fail'
          ? 'inadequate'
          : check?.identity_status === 'pass'
            ? 'candidate'
            : 'missing'
      const evidenceStatus = check?.status === 'pass'
        && check.stiffness_status === 'verified'
        && check.anchorage_status === 'verified'
        ? 'verified'
        : check?.identity_status === 'fail'
          ? 'mismatch'
          : check?.identity_status === 'pass'
            ? 'missing'
            : 'not_checked'
      const requiredForceKN = check?.required_force_kN
        ?? trace.required_restraint_force_kN
      restraintMarkers.push({
        id: `${trace.id}-${boundary.suffix}-${candidateId}`,
        traceId: trace.id,
        label: `${candidateId} · ${formatNumber(requiredForceKN ?? 0)} kN required`,
        position: boundary.position,
        direction: unitDirection,
        status,
        evidenceStatus,
        requiredForceKN,
        selected: true,
      })
    }
  }

  return [{
    id: `stage-focus-${trace.member_id}`,
    label: `Stage 8 restraint focus · ${trace.id}`,
    mode: 'moment',
    status: trace.status === 'verified'
      ? 'pass'
      : trace.status === 'inadequate' || trace.status === 'missing'
        ? 'fail'
        : 'not_checked',
    stations: [],
    restraintSegments: [{
      id: trace.id,
      label: `${trace.compression_flange.replaceAll('_', ' ')} · ${trace.status}`,
      start: trace.start_position,
      end: trace.end_position,
      compressionFlange: trace.compression_flange,
      status: trace.status,
      selected: true,
    }],
    restraintMarkers,
    stageFocus,
  }]
}
