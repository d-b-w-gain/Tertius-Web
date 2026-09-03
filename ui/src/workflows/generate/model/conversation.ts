import type {
  LlmEditProgressEvent,
  LlmEditProgressSnapshot,
  LlmFileEditResult,
  ProjectFileMetadata,
} from '../../shared/projectStorage'

export type EditableFilePointer = ProjectFileMetadata & {
  id: string
  updated_at: string
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  files?: Array<{ filename: string; summary?: string; changed?: boolean }>
  usage?: LlmFileEditResult['usage']
  model?: string
  artifactId?: string
  modelUrl?: string
  compileStatus?: 'queued' | 'running' | 'succeeded' | 'failed'
  jobId?: string
  repairJobId?: string
  compileJobId?: string
  repairAttempted?: boolean
  repairForCompileJobId?: string
  progress?: LlmEditProgressSnapshot
  progressActive?: boolean
  progressDisclosure?: boolean
  renderKey?: string
}

export type CompileJobStatus = {
  status?: string
  job_id?: string
  artifact_id?: string
  format?: string
  export_format?: string
  user_message?: string
  short?: string
  error?: string
  error_code?: string
  retryable?: boolean
}

function hasEditableFilePointer(file: ProjectFileMetadata): file is EditableFilePointer {
  return Boolean(file.id && file.updated_at)
}

export function orderEditableFiles(metadata: ProjectFileMetadata[]) {
  const designFile = metadata.find(file => file.filename === 'design.py')
  const remainingFiles = metadata.filter(file => file.filename !== 'design.py')
  return [
    ...(designFile ? [designFile] : []),
    ...remainingFiles,
  ].filter(hasEditableFilePointer)
}

export function isNonTerminalStatus(status?: string) {
  return status === 'queued' || status === 'running'
}

export function buildCompileRepairPrompt(originalPrompt: string, data: CompileJobStatus) {
  const failure = [
    data.error_code ? `Error code: ${data.error_code}` : '',
    data.user_message ? `User message: ${data.user_message}` : '',
    data.error ? `Traceback:\n${data.error}` : '',
  ].filter(Boolean).join('\n\n')
  return [
    'The previous generated design failed to compile in the Tertius build123d sandbox.',
    'Fix the Python source so it compiles successfully. Preserve the original design intent.',
    'Do not use APIs shown as missing in the traceback. Return the full corrected file content.',
    '',
    `Original user request:\n${originalPrompt}`,
    '',
    failure,
  ].join('\n')
}

function executionStartedAt(snapshot: LlmEditProgressSnapshot) {
  const timestamp = Date.parse(snapshot.execution_started_at)
  return Number.isFinite(timestamp) ? timestamp : 0
}

export function mergeProgressSnapshot(
  current: LlmEditProgressSnapshot | undefined,
  incoming: LlmEditProgressSnapshot,
): LlmEditProgressSnapshot {
  if (!current) return incoming
  if (current.execution_id !== incoming.execution_id) {
    return executionStartedAt(incoming) > executionStartedAt(current) ? incoming : current
  }
  if (
    incoming.last_batch_sequence < current.last_batch_sequence
    || incoming.last_sequence < current.last_sequence
  ) {
    return current
  }

  const truncationBoundaries = [
    current.truncated_before_sequence,
    incoming.truncated_before_sequence,
  ].filter((sequence): sequence is number => sequence !== null)
  const truncatedBeforeSequence = truncationBoundaries.length > 0
    ? Math.max(...truncationBoundaries)
    : null
  const eventsBySequence = new Map<number, LlmEditProgressEvent>()
  for (const event of current.events) eventsBySequence.set(event.sequence, event)
  for (const event of incoming.events) eventsBySequence.set(event.sequence, event)
  const events = [...eventsBySequence.values()]
    .filter(event => (
      event.sequence <= incoming.last_sequence
      && (truncatedBeforeSequence === null || event.sequence > truncatedBeforeSequence)
    ))
    .sort((left, right) => left.sequence - right.sequence)

  return {
    ...incoming,
    truncated_before_sequence: truncatedBeforeSequence,
    events,
  }
}
