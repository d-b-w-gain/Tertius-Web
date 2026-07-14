import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import { resolveWorkflowServerUrl } from '../shared/apiConfig'
import { GUEST_WORKSPACE_CHANGED_EVENT } from '../shared/guestWorkspace'
import {
  createProjectStorage,
  type LlmEditConversationEntry,
  type LlmFileEditResult,
  type LlmModelOption,
  type ProjectFileMetadata,
} from '../shared/projectStorage'
import {
  ACTIVE_PROJECT_CHANGED_EVENT,
  ProjectSelector,
} from '../shared/ui/ProjectSelector'
import { GuestWorkflowNotice } from '../shared/ui/GuestWorkflowNotice'
import { ACTIVE_PROJECT_POLL_INTERVAL_MS, getPollingDelay, shouldRunPollingRequest } from '../shared/polling'
import { LatestModelViewer, ModelViewerCanvas } from '../extus/ui/ViewerTab'
import { recordAiUsage } from './AiUsageGauge'
import { runWithInteractionSpan } from '../../telemetry'

const AI_EDIT_FILE_LIMIT = 20
const COMPILE_FORMAT = 'glb'
const COMPILE_QUALITY = 'sketch'
const COMPILE_STATUS_INITIAL_DELAY_MS = 1_000
const COMPILE_STATUS_POLL_MS = 2_000
const COMPILE_STATUS_RETRY_MS = 3_000
const LLM_EDIT_STATUS_INITIAL_DELAY_MS = 1_000
const LLM_EDIT_STATUS_POLL_MS = 1_500
const LLM_EDIT_STATUS_RETRY_MS = 2_000

type EditableFilePointer = ProjectFileMetadata & {
  id: string
  updated_at: string
}

type ChatMessage = {
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
}

export type GenerateViewportState = {
  title: string
  subtitle: string
  projectName: string
  modelUrl: string
  statusText?: string
  openUrl?: string
}

type GenerateDesignWindowProps = {
  isActive?: boolean
  renderViewport?: boolean
  onViewportStateChange?: (state: GenerateViewportState) => void
}

type CompileJobStatus = {
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

function orderEditableFiles(metadata: ProjectFileMetadata[]) {
  const designFile = metadata.find(file => file.filename === 'design.py')
  const remainingFiles = metadata.filter(file => file.filename !== 'design.py')
  return [
    ...(designFile ? [designFile] : []),
    ...remainingFiles,
  ].filter(hasEditableFilePointer)
}

function jsonMessage(data: unknown, fallback: string) {
  if (data && typeof data === 'object') {
    const maybe = data as Record<string, unknown>
    for (const key of ['user_message', 'short', 'error', 'detail']) {
      const value = maybe[key]
      if (typeof value === 'string' && value.trim()) return value
    }
  }
  return fallback
}

function messageId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function promptMessageId(jobId: string) {
  return `prompt:${jobId}`
}

function assistantMessageId(jobId: string) {
  return `job:${jobId}`
}

function isNonTerminalStatus(status?: string) {
  return status === 'queued' || status === 'running'
}

function isRepairableCompileFailure(data: CompileJobStatus) {
  const detail = `${data.error_code || ''}\n${data.error || ''}\n${data.user_message || ''}`.toLowerCase()
  return data.retryable !== false && (
    data.error_code === 'sandbox_error' ||
    detail.includes('traceback') ||
    detail.includes('attributeerror') ||
    detail.includes('nameerror') ||
    detail.includes('typeerror')
  )
}

function buildCompileRepairPrompt(originalPrompt: string, data: CompileJobStatus) {
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

function isCompileRepairEntry(entry: LlmEditConversationEntry) {
  return entry.metadata?.source === 'generate_design_compile_repair'
}

export function GenerateDesignWindow({
  isActive = true,
  renderViewport = true,
  onViewportStateChange,
}: GenerateDesignWindowProps) {
  const { authMode, getAccessToken, login } = useAuth()
  const intusServerUrl = resolveWorkflowServerUrl('intus', import.meta.env?.VITE_API_URL)
  const extusServerUrl = resolveWorkflowServerUrl('extus', import.meta.env?.VITE_API_URL)
  const storage = useMemo(
    () => createProjectStorage({ authMode, serverUrl: intusServerUrl, getAccessToken }),
    [authMode, getAccessToken, intusServerUrl],
  )

  const [activeProject, setActiveProject] = useState('')
  const [fileMetadata, setFileMetadata] = useState<ProjectFileMetadata[]>([])
  const [prompt, setPrompt] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null)
  const [llmModels, setLlmModels] = useState<LlmModelOption[]>([])
  const [selectedModelId, setSelectedModelId] = useState('')
  const [statusText, setStatusText] = useState('Select a project to generate a design.')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isConversationOpen, setIsConversationOpen] = useState(false)

  const activeProjectRef = useRef('')
  const messagesRef = useRef<ChatMessage[]>([])
  const startLlmEditPollingRef = useRef<(projectName: string, jobId: string, assistantId: string) => void>(() => {})
  const compileRequestRef = useRef(new Map<string, number>())
  const compileTimerRef = useRef(new Map<string, number>())
  const llmEditRequestRef = useRef(new Map<string, number>())
  const llmEditTimerRef = useRef(new Map<string, number>())

  useEffect(() => {
    activeProjectRef.current = activeProject
  }, [activeProject])

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  const clearCompileTimer = useCallback((jobId: string) => {
    const timer = compileTimerRef.current.get(jobId)
    if (timer) window.clearTimeout(timer)
    compileTimerRef.current.delete(jobId)
  }, [])

  const clearLlmEditTimer = useCallback((jobId: string) => {
    const timer = llmEditTimerRef.current.get(jobId)
    if (timer) window.clearTimeout(timer)
    llmEditTimerRef.current.delete(jobId)
  }, [])

  const clearAllJobPolling = useCallback(() => {
    for (const timer of compileTimerRef.current.values()) window.clearTimeout(timer)
    for (const timer of llmEditTimerRef.current.values()) window.clearTimeout(timer)
    compileTimerRef.current.clear()
    llmEditTimerRef.current.clear()
    compileRequestRef.current.clear()
    llmEditRequestRef.current.clear()
  }, [])

  useEffect(() => () => {
    clearAllJobPolling()
  }, [clearAllJobPolling])

  const selectedMessage = messages.find(message => message.id === selectedMessageId)
  const selectedJobAssistant = selectedMessage?.jobId
    ? messages.find(message => (
      message.role === 'assistant'
      && message.jobId === selectedMessage.jobId
      && message.modelUrl
    ))
    : undefined
  const selectedModelUrl = selectedMessage?.modelUrl || selectedJobAssistant?.modelUrl || ''
  const selectedModel = llmModels.find(model => model.id === selectedModelId) || llmModels[0]
  const selectedCompileStatus = (
    selectedMessage?.compileJobId ? selectedMessage.compileStatus : undefined
  ) || (
    selectedJobAssistant?.compileJobId ? selectedJobAssistant.compileStatus : undefined
  )
  const modelViewerStatusText = isNonTerminalStatus(selectedCompileStatus)
    ? 'Compiling updated model...'
    : undefined
  const viewportState = useMemo<GenerateViewportState>(() => ({
    title: selectedModelUrl ? 'Historical Model' : 'Latest Model',
    subtitle: selectedMessage?.content || activeProject || 'No active project',
    projectName: activeProject,
    modelUrl: selectedModelUrl,
    statusText: modelViewerStatusText,
    openUrl: selectedModelUrl || undefined,
  }), [activeProject, modelViewerStatusText, selectedMessage?.content, selectedModelUrl])

  useEffect(() => {
    onViewportStateChange?.(viewportState)
  }, [onViewportStateChange, viewportState])

  const updateAssistantMessage = useCallback((messageIdToUpdate: string, updater: (message: ChatMessage) => ChatMessage) => {
    setMessages(prev => prev.map(message => (
      message.id === messageIdToUpdate ? updater(message) : message
    )))
  }, [])

  const refreshMetadata = useCallback(async (projectName: string) => {
    const metadata = await storage.listFileMetadata(projectName)
    setFileMetadata(metadata)
    return metadata
  }, [storage])

  const modelUrlForArtifact = useCallback((artifactId: string, projectName?: string) => {
    const query = new URLSearchParams({ t: String(Date.now()) })
    if (projectName) query.set('project', projectName)
    return `${extusServerUrl}/artifacts/${artifactId}/model?${query.toString()}`
  }, [extusServerUrl])

  const conversationEntriesToMessages = useCallback((
    entries: LlmEditConversationEntry[],
    metadata: ProjectFileMetadata[],
  ) => {
    const totalEditableFiles = orderEditableFiles(metadata).length
    return entries.flatMap((entry): ChatMessage[] => {
      const createdAt = entry.created_at ? Date.parse(entry.created_at) : Date.now()
      const stablePromptId = promptMessageId(entry.job_id)
      const stableAssistantId = assistantMessageId(entry.job_id)
      const compile = entry.compile || undefined
      const artifactId = compile?.artifact_id
      const requestedCount = entry.requested_file_count || 0
      const requestedFileNotice = requestedCount > 0 && totalEditableFiles > requestedCount
        ? `Included ${requestedCount} of ${totalEditableFiles} editable files.`
        : ''
      const generatedContent = entry.content?.trim()
      const nonTerminalContent = entry.status === 'queued'
        ? 'AI edit is queued...'
        : 'AI edit is running...'
      const assistantContent = [
        requestedFileNotice,
        generatedContent || (isNonTerminalStatus(entry.status) ? nonTerminalContent : 'AI edit completed.'),
      ].filter(Boolean).join('\n')
      const compileStatus = compile?.status
        || (isNonTerminalStatus(entry.status) ? entry.status : entry.status === 'failed' ? 'failed' : undefined)

      return [
        {
          id: stablePromptId,
          role: 'user',
          content: entry.prompt || '',
          createdAt: Number.isFinite(createdAt) ? createdAt : Date.now(),
          jobId: entry.job_id,
        },
        {
          id: stableAssistantId,
          role: 'assistant',
          content: assistantContent,
          createdAt: Number.isFinite(createdAt) ? createdAt + 1 : Date.now(),
          files: entry.files,
          usage: entry.usage,
          model: entry.model,
          artifactId,
          modelUrl: artifactId ? modelUrlForArtifact(artifactId, activeProjectRef.current) : undefined,
          compileStatus,
          jobId: entry.job_id,
          repairJobId: isCompileRepairEntry(entry) ? entry.job_id : undefined,
          compileJobId: compile?.job_id,
          repairAttempted: isCompileRepairEntry(entry),
        },
      ]
    })
  }, [modelUrlForArtifact])

  const resumeHydratedConversationRef = useRef((
    _projectName: string,
    _entries: LlmEditConversationEntry[],
  ) => {})

  const loadActiveProject = useCallback(async (
    projectName?: string,
    options: { hydrateConversation?: boolean } = {},
  ) => {
    if (!shouldRunPollingRequest()) return
    try {
      const nextProject = projectName || await storage.getActiveProject()
      if (!nextProject) {
        clearAllJobPolling()
        activeProjectRef.current = ''
        setActiveProject('')
        setFileMetadata([])
        setMessages([])
        setSelectedMessageId(null)
        setStatusText('No active project selected.')
        return
      }
      const projectChanged = nextProject !== activeProjectRef.current
      const shouldHydrateConversation = options.hydrateConversation || projectChanged
      if (projectChanged || shouldHydrateConversation) clearAllJobPolling()
      activeProjectRef.current = nextProject
      setActiveProject(nextProject)
      const metadata = await refreshMetadata(nextProject)
      if (shouldHydrateConversation) {
        const entries = await storage.listLlmEditConversation(nextProject)
        const hydratedMessages = conversationEntriesToMessages(entries, metadata)
        setMessages(hydratedMessages)
        const lastAssistantMessage = [...hydratedMessages].reverse().find(message => message.role === 'assistant')
        setSelectedMessageId(current => (
          current && hydratedMessages.some(message => message.id === current)
            ? current
            : lastAssistantMessage?.id || null
        ))
        resumeHydratedConversationRef.current(nextProject, entries)
      }
      setStatusText(`Ready to generate against ${metadata.length} project file(s).`)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load active project.')
    }
  }, [clearAllJobPolling, conversationEntriesToMessages, refreshMetadata, storage])

  useEffect(() => {
    if (!isActive) return
    void loadActiveProject(undefined, { hydrateConversation: true })
    const interval = authMode === 'guest'
      ? undefined
      : window.setInterval(() => void loadActiveProject(undefined, { hydrateConversation: false }), getPollingDelay(ACTIVE_PROJECT_POLL_INTERVAL_MS))
    const handleProjectChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ activeProject?: string }>).detail
      if (detail?.activeProject) void loadActiveProject(detail.activeProject, { hydrateConversation: true })
    }
    const handleGuestChanged = () => void loadActiveProject(undefined, { hydrateConversation: true })

    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleProjectChanged)
    if (authMode === 'guest') window.addEventListener(GUEST_WORKSPACE_CHANGED_EVENT, handleGuestChanged)
    return () => {
      if (interval) window.clearInterval(interval)
      window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, handleProjectChanged)
      if (authMode === 'guest') window.removeEventListener(GUEST_WORKSPACE_CHANGED_EVENT, handleGuestChanged)
    }
  }, [authMode, isActive, loadActiveProject])

  useEffect(() => {
    if (!isActive || authMode === 'guest') return
    let cancelled = false

    const loadModels = async () => {
      try {
        const response = await storage.listLlmModels()
        if (cancelled) return
        if (response.models.length === 0) {
          throw new Error('No AI model is configured.')
        }
        setLlmModels(response.models)
        setSelectedModelId(current => {
          if (current && response.models.some(model => model.id === current)) return current
          return response.default_model_id || response.models[0]?.id || ''
        })
      } catch (modelError) {
        if (!cancelled) setError(modelError instanceof Error ? modelError.message : 'Failed to load LLM models.')
      }
    }

    void loadModels()
    return () => {
      cancelled = true
    }
  }, [authMode, isActive, storage])

  const buildLlmEditRequest = useCallback(async () => {
    if (!activeProject) {
      throw new Error('Select a project before generating a design.')
    }
    const latestMetadata = await refreshMetadata(activeProject)
    const orderedFiles = orderEditableFiles(latestMetadata)
    if (orderedFiles.length === 0) {
      throw new Error('AI generation requires authenticated file metadata. Reload the project and try again.')
    }
    const requestFiles = orderedFiles.slice(0, AI_EDIT_FILE_LIMIT)
    return {
      requestFiles,
      activeFileId: requestFiles.find(file => file.filename === 'design.py')?.id || requestFiles[0]?.id,
      truncatedMessage: orderedFiles.length > AI_EDIT_FILE_LIMIT
        ? `Included ${AI_EDIT_FILE_LIMIT} of ${orderedFiles.length} editable files.`
        : '',
    }
  }, [activeProject, refreshMetadata])

  const pollCompileJob = useCallback((projectName: string, jobId: string, requestId: number, assistantMessageId: string) => {
    const tick = async () => {
      if (compileRequestRef.current.get(jobId) !== requestId || activeProjectRef.current !== projectName) return
      try {
        const response = await apiFetch(`${intusServerUrl}/projects/${projectName}/compile/jobs/${jobId}`, getAccessToken)
        const data = await response.json() as CompileJobStatus
        if (!response.ok) {
          const message = jsonMessage(data, 'Compile job status could not be loaded.')
          updateAssistantMessage(assistantMessageId, current => ({
            ...current,
            content: `${current.content}\n\nCompile failed: ${message}`,
            compileStatus: 'failed',
          }))
          clearCompileTimer(jobId)
          compileRequestRef.current.delete(jobId)
          setStatusText(message)
          return
        }

        if (data.status === 'succeeded') {
          const artifactId = data.artifact_id
          const format = data.format || data.export_format || COMPILE_FORMAT
          updateAssistantMessage(assistantMessageId, current => ({
            ...current,
            content: artifactId
              ? `${current.content}\n\nCompiled ${format} artifact ${artifactId}.`
              : `${current.content}\n\nCompile succeeded, but no artifact id was returned.`,
            artifactId,
            modelUrl: artifactId ? modelUrlForArtifact(artifactId, projectName) : current.modelUrl,
            compileStatus: 'succeeded',
          }))
          clearCompileTimer(jobId)
          compileRequestRef.current.delete(jobId)
          if (artifactId) setSelectedMessageId(assistantMessageId)
          setStatusText(artifactId ? `Compiled ${format} artifact ${artifactId}.` : 'Compile succeeded.')
          return
        }

        if (data.status === 'failed') {
          const message = jsonMessage(data, 'Compile failed. Try again.')
          if (isRepairableCompileFailure(data)) {
            const currentMessage = messagesRef.current.find(candidate => candidate.id === assistantMessageId)
            if (currentMessage && !currentMessage.repairAttempted) {
              try {
                const originalPrompt = messagesRef.current.find(candidate => (
                  candidate.id === promptMessageId(currentMessage.jobId || '')
                ))?.content || prompt || 'Generate a design.'
                const { requestFiles, activeFileId } = await buildLlmEditRequest()
                const repairJob = await storage.applyLlmFileEditJob(projectName, {
                  prompt: buildCompileRepairPrompt(originalPrompt, data),
                  files: requestFiles.map(file => ({
                    id: file.id,
                    filename: file.filename,
                    updated_at: file.updated_at,
                  })),
                  active_file_id: activeFileId,
                  model_id: selectedModel?.id,
                  metadata: { source: 'generate_design_compile_repair' },
                })
                updateAssistantMessage(assistantMessageId, current => ({
                  ...current,
                  content: `${current.content}\n\nCompile failed; attempting one automatic repair.`,
                  compileStatus: 'running',
                  repairAttempted: true,
                  repairForCompileJobId: jobId,
                  repairJobId: repairJob.job_id,
                }))
                clearCompileTimer(jobId)
                compileRequestRef.current.delete(jobId)
                setStatusText('Compile failed; automatic repair is running.')
                startLlmEditPollingRef.current(projectName, repairJob.job_id, assistantMessageId)
                return
              } catch (repairError) {
                const repairMessage = repairError instanceof Error ? repairError.message : 'Automatic repair could not start.'
                updateAssistantMessage(assistantMessageId, current => ({
                  ...current,
                  content: `${current.content}\n\nCompile failed: ${message}\nAutomatic repair could not start: ${repairMessage}`,
                  compileStatus: 'failed',
                  repairAttempted: true,
                  repairForCompileJobId: jobId,
                }))
                clearCompileTimer(jobId)
                compileRequestRef.current.delete(jobId)
                setStatusText(repairMessage)
                return
              }
            }
          }
          updateAssistantMessage(assistantMessageId, current => ({
            ...current,
            content: `${current.content}\n\nCompile failed: ${message}`,
            compileStatus: 'failed',
          }))
          clearCompileTimer(jobId)
          compileRequestRef.current.delete(jobId)
          setStatusText(message)
          return
        }

        updateAssistantMessage(assistantMessageId, current => ({
          ...current,
          compileStatus: data.status === 'queued' ? 'queued' : 'running',
          compileJobId: jobId,
        }))
        compileTimerRef.current.set(jobId, window.setTimeout(tick, COMPILE_STATUS_POLL_MS))
      } catch {
        compileTimerRef.current.set(jobId, window.setTimeout(tick, COMPILE_STATUS_RETRY_MS))
      }
    }

    clearCompileTimer(jobId)
    compileTimerRef.current.set(jobId, window.setTimeout(tick, COMPILE_STATUS_INITIAL_DELAY_MS))
  }, [buildLlmEditRequest, clearCompileTimer, getAccessToken, intusServerUrl, modelUrlForArtifact, prompt, selectedModel?.id, storage, updateAssistantMessage])

  const startCompilePolling = useCallback((projectName: string, jobId: string, assistantMessageId: string) => {
    const requestId = (compileRequestRef.current.get(jobId) || 0) + 1
    compileRequestRef.current.set(jobId, requestId)
    pollCompileJob(projectName, jobId, requestId, assistantMessageId)
  }, [pollCompileJob])

  const queueCompile = useCallback(async (
    projectName: string,
    changedFiles: LlmFileEditResult['files'],
    assistantMessageId: string,
    originatingLlmEditJobId?: string,
  ) => {
    const designChange = changedFiles.find(file => file.filename === 'design.py')
    const code = designChange?.content || await storage.loadCode(projectName, 'design.py')
    if (!code) throw new Error('Compile could not start because design.py could not be loaded.')

    const response = await runWithInteractionSpan('compile_submit', {
      workflow: 'generate',
      export_format: COMPILE_FORMAT,
      quality: COMPILE_QUALITY,
      source: 'generate_design_window',
    }, () => apiFetch(`${intusServerUrl}/projects/${projectName}/compile`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code,
          export_format: COMPILE_FORMAT,
          quality: COMPILE_QUALITY,
          file: 'design.py',
          originating_llm_edit_job_id: originatingLlmEditJobId,
        }),
      }))
    const data = await response.json()
    if (!response.ok || !data.job_id) {
      throw new Error(jsonMessage(data, 'Compile could not start after generation.'))
    }

    updateAssistantMessage(assistantMessageId, current => ({
      ...current,
      content: `${current.content}\n\nCompile queued as ${COMPILE_FORMAT}/${COMPILE_QUALITY}.`,
      compileStatus: data.status === 'queued' ? 'queued' : 'running',
      jobId: current.repairAttempted ? current.jobId : originatingLlmEditJobId || current.jobId,
      repairJobId: current.repairAttempted ? originatingLlmEditJobId || current.repairJobId : current.repairJobId,
      compileJobId: data.job_id,
    }))
    setStatusText(`Compile job ${data.job_id} is ${data.status || 'queued'}.`)
    startCompilePolling(projectName, data.job_id, assistantMessageId)
  }, [getAccessToken, intusServerUrl, startCompilePolling, storage, updateAssistantMessage])

  const applyLlmEditResult = useCallback((
    result: LlmFileEditResult,
    projectName: string,
    assistantMessageId: string,
    originatingLlmEditJobId?: string,
    truncatedMessage?: string,
  ) => {
    recordAiUsage(result.usage.total_tokens)
    const changedFiles = result.files.filter(file => file.changed !== false)
    const fileSummary = result.files
      .map(file => file.summary)
      .filter(Boolean)
      .join(' ')
    const resultContentParts = [
      result.outcome === 'changed'
        ? `Updated ${changedFiles.length || result.files.length} file(s).`
        : result.message || `AI returned ${result.outcome}.`,
      result.model ? `Model: ${result.model}.` : '',
      fileSummary,
    ].filter(Boolean)

    updateAssistantMessage(assistantMessageId, current => ({
      ...current,
      content: [
        truncatedMessage || current.content.match(/^Included \d+ of \d+ editable files\./m)?.[0] || '',
        ...resultContentParts,
      ].filter(Boolean).join(' '),
      files: result.files.map(file => ({
        filename: file.filename,
        summary: file.summary,
        changed: file.changed,
      })),
      usage: result.usage,
      model: result.model,
      compileStatus: result.outcome === 'changed' ? 'queued' : undefined,
      jobId: current.repairAttempted ? current.jobId : originatingLlmEditJobId || current.jobId,
      repairJobId: current.repairAttempted ? originatingLlmEditJobId || current.repairJobId : current.repairJobId,
    }))

    const nextMetadata = result.files.map(file => ({
      id: file.id,
      filename: file.filename,
      updated_at: file.updated_at,
    }))
    setFileMetadata(prev => (
      prev
        .map(existing => nextMetadata.find(file => file.id === existing.id) || existing)
        .concat(nextMetadata.filter(file => !prev.some(existing => existing.id === file.id))
      )
    ))

    if (result.outcome === 'changed') {
      setStatusText('AI edit applied. Queueing Intus compile.')
      void queueCompile(
        projectName,
        changedFiles.length > 0 ? changedFiles : result.files,
        assistantMessageId,
        originatingLlmEditJobId,
      )
    } else {
      setStatusText(result.message || `Generation completed with ${result.outcome}.`)
    }
  }, [queueCompile, updateAssistantMessage, setFileMetadata, setStatusText])

  const pollLlmEditJob = useCallback((projectName: string, jobId: string, requestId: number, assistantMessageId: string) => {
    const tick = async () => {
      if (llmEditRequestRef.current.get(jobId) !== requestId || activeProjectRef.current !== projectName) return
      try {
        const response = await storage.getLlmFileEditJob(projectName, jobId)
        if (response.status === 'succeeded') {
          if (!response.result) {
            updateAssistantMessage(assistantMessageId, current => ({
              ...current,
                content: `${current.content}\n\nAI edit returned no result payload.`,
                compileStatus: 'failed',
                jobId: current.repairAttempted ? current.jobId : jobId,
                repairJobId: current.repairAttempted ? jobId : current.repairJobId,
              }))
            clearLlmEditTimer(jobId)
            llmEditRequestRef.current.delete(jobId)
            setStatusText('AI edit completed but returned no result payload.')
            return
          }
          clearLlmEditTimer(jobId)
          llmEditRequestRef.current.delete(jobId)
          applyLlmEditResult(response.result, projectName, assistantMessageId, jobId)
          return
        }

        if (response.status === 'failed') {
          const message = jsonMessage(response, 'AI edit failed. Try again.')
          updateAssistantMessage(assistantMessageId, current => ({
            ...current,
            content: `${current.content}\n\nAI edit failed: ${message}`,
            compileStatus: 'failed',
            jobId: current.repairAttempted ? current.jobId : jobId,
            repairJobId: current.repairAttempted ? jobId : current.repairJobId,
          }))
          clearLlmEditTimer(jobId)
          llmEditRequestRef.current.delete(jobId)
          setStatusText(message)
          return
        }

        updateAssistantMessage(assistantMessageId, current => ({
          ...current,
          compileStatus: response.status === 'queued' ? 'queued' : 'running',
          jobId: current.repairAttempted ? current.jobId : jobId,
          repairJobId: current.repairAttempted ? jobId : current.repairJobId,
        }))
        llmEditTimerRef.current.set(jobId, window.setTimeout(tick, LLM_EDIT_STATUS_POLL_MS))
      } catch {
        llmEditTimerRef.current.set(jobId, window.setTimeout(tick, LLM_EDIT_STATUS_RETRY_MS))
      }
    }

    clearLlmEditTimer(jobId)
    llmEditTimerRef.current.set(jobId, window.setTimeout(tick, LLM_EDIT_STATUS_INITIAL_DELAY_MS))
  }, [applyLlmEditResult, clearLlmEditTimer, setStatusText, storage, updateAssistantMessage])

  const startLlmEditPolling = useCallback((projectName: string, jobId: string, assistantId: string) => {
    const requestId = (llmEditRequestRef.current.get(jobId) || 0) + 1
    llmEditRequestRef.current.set(jobId, requestId)
    pollLlmEditJob(projectName, jobId, requestId, assistantId)
  }, [pollLlmEditJob])

  useEffect(() => {
    startLlmEditPollingRef.current = startLlmEditPolling
  }, [startLlmEditPolling])

  useEffect(() => {
    resumeHydratedConversationRef.current = (projectName: string, entries: LlmEditConversationEntry[]) => {
      for (const entry of entries) {
        const stableAssistantId = assistantMessageId(entry.job_id)
        if (isNonTerminalStatus(entry.status)) {
          startLlmEditPolling(projectName, entry.job_id, stableAssistantId)
        }
        if (entry.compile?.job_id && isNonTerminalStatus(entry.compile.status)) {
          startCompilePolling(projectName, entry.compile.job_id, stableAssistantId)
        }
      }
    }
  }, [startCompilePolling, startLlmEditPolling])

  const submitPrompt = async (event: FormEvent) => {
    event.preventDefault()
    if (!prompt.trim() || isSubmitting) return
    const submittedPrompt = prompt.trim()
    const userMessage: ChatMessage = {
      id: messageId('user'),
      role: 'user',
      content: submittedPrompt,
      createdAt: Date.now(),
    }
    const assistantMessage: ChatMessage = {
      id: messageId('assistant'),
      role: 'assistant',
      content: 'Generating design edit...',
      createdAt: Date.now(),
      compileStatus: 'queued',
    }

    setMessages(prev => [...prev, userMessage, assistantMessage])
    setSelectedMessageId(assistantMessage.id)
    setPrompt('')
    setError(null)
    setIsSubmitting(true)
    setStatusText('Submitting prompt to AI file edit.')

    try {
      const { requestFiles, activeFileId, truncatedMessage } = await buildLlmEditRequest()
      const job = await runWithInteractionSpan('llm_file_edit_submit', {
        workflow: 'generate',
        source: 'generate_design_window',
        model_id: selectedModel?.id || '',
      }, () => storage.applyLlmFileEditJob(activeProject, {
          prompt: submittedPrompt,
          files: requestFiles.map(file => ({
            id: file.id,
            filename: file.filename,
            updated_at: file.updated_at,
          })),
          active_file_id: activeFileId,
          model_id: selectedModel?.id,
          metadata: { source: 'generate_design_window' },
        }))
      const stablePromptId = promptMessageId(job.job_id)
      const stableAssistantId = assistantMessageId(job.job_id)
      setMessages(prev => prev.map(message => {
        if (message.id === userMessage.id) {
          return { ...message, id: stablePromptId, jobId: job.job_id }
        }
        if (message.id === assistantMessage.id) {
          return {
            ...message,
            id: stableAssistantId,
            jobId: job.job_id,
          }
        }
        return message
      }))
      setSelectedMessageId(current => current === assistantMessage.id ? stableAssistantId : current)

      updateAssistantMessage(stableAssistantId, current => ({
        ...current,
        content: 'AI edit is running...',
        compileStatus: 'running',
        jobId: job.job_id,
      }))
      setStatusText(`AI edit job ${job.job_id} queued.`)
      startLlmEditPolling(activeProject, job.job_id, stableAssistantId)
      if (truncatedMessage) {
        updateAssistantMessage(stableAssistantId, current => ({
          ...current,
          content: `${current.content}\n${truncatedMessage}`,
        }))
      }
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : 'Generate Design failed.'
      setError(message)
      updateAssistantMessage(assistantMessage.id, current => ({
        ...current,
        content: `Error: ${message}`,
        compileStatus: 'failed',
      }))
      setStatusText(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  if (authMode === 'guest') {
    return (
      <GuestWorkflowNotice
        title="Log in to generate designs"
        message="Generate Design uses authenticated project metadata, LLM file edits, and compiled model artifacts."
        onLogin={login}
      />
    )
  }

  return (
    <div className={renderViewport
      ? 'relative flex h-full min-h-0 overflow-hidden bg-slate-950 text-slate-100'
      : 'pointer-events-none relative h-full min-h-0 overflow-hidden text-slate-100'
    }>
      <section className={renderViewport
        ? 'flex min-h-0 w-full flex-col'
        : 'pointer-events-auto absolute left-4 top-24 z-20 max-w-[min(36rem,calc(100%-2rem))] rounded border border-slate-800 bg-slate-950/90 shadow-xl shadow-slate-950/50 backdrop-blur'
      }>
        <div className={renderViewport
          ? 'flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/95 px-4 py-3'
          : 'flex items-center justify-between gap-3 px-4 py-3'
        }>
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {viewportState.title}
            </div>
            <div className="truncate text-xs text-slate-400">
              {viewportState.subtitle}
            </div>
          </div>
          {viewportState.openUrl && (
            <a
              href={viewportState.openUrl}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
            >
              Open
            </a>
          )}
        </div>
        {renderViewport && (
          <div className="min-h-0 flex-1">
            {selectedModelUrl ? (
              <ModelViewerCanvas
                key={selectedModelUrl}
                modelUrl={selectedModelUrl}
                getAccessToken={getAccessToken}
                statusText={modelViewerStatusText || 'Selected historical model'}
                projectName={activeProject}
                isActive={isActive}
              />
            ) : (
              <LatestModelViewer
                serverUrl={extusServerUrl}
                isActive={isActive}
                statusTextOverride={modelViewerStatusText}
              />
            )}
          </div>
        )}
      </section>

      {!isConversationOpen && (
        <div className="pointer-events-none absolute right-4 top-16 z-20">
          <button
            type="button"
            aria-expanded="false"
            onClick={() => setIsConversationOpen(true)}
            className="pointer-events-auto rounded border border-slate-700 bg-slate-900/95 px-3 py-2 text-xs font-semibold text-slate-100 shadow-xl shadow-slate-950/40 transition-colors hover:bg-slate-800"
          >
            Open Generate Design conversation
          </button>
        </div>
      )}

      {isConversationOpen && (
        <aside
          role="complementary"
          aria-label="Generate Design conversation"
          className="pointer-events-auto absolute inset-x-3 bottom-3 top-16 z-20 flex min-h-0 flex-col rounded border border-slate-700 bg-slate-950/95 shadow-2xl shadow-slate-950/60 backdrop-blur md:left-auto md:right-4 md:w-[28rem]"
        >
          <div className="border-b border-slate-800 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-100">Generate Design</h2>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => void loadActiveProject(undefined, { hydrateConversation: true })}
                  className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
                >
                  Refresh
                </button>
                <button
                  type="button"
                  aria-expanded="true"
                  onClick={() => setIsConversationOpen(false)}
                  className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
                >
                  Close Generate Design conversation
                </button>
              </div>
            </div>
            <div className="mt-4">
              <ProjectSelector />
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
            <span className="min-w-0 text-sm text-slate-300">{statusText}</span>
            <span className="shrink-0 rounded border border-slate-800 bg-slate-900 px-2 py-1 font-mono text-[10px] text-slate-500">
              {COMPILE_FORMAT}/{COMPILE_QUALITY}
            </span>
          </div>

          {selectedModel && (
            <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3 text-xs">
              <span className="font-semibold text-slate-200">{selectedModel.label}</span>
              <span className="font-mono text-slate-500">{selectedModel.model}</span>
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-auto p-4">
            {messages.length === 0 ? (
              <div className="rounded border border-slate-800 bg-slate-900/40 p-4 text-sm text-slate-500">
                Generated design messages will appear here.
              </div>
            ) : (
              <div className="space-y-3">
                {messages.map(message => (
                  <button
                    key={message.id}
                    type="button"
                    onClick={() => setSelectedMessageId(message.id)}
                    className={`block w-full rounded border p-3 text-left transition-colors ${
                      selectedMessageId === message.id
                        ? 'border-cyan-700 bg-cyan-950/30'
                        : 'border-slate-800 bg-slate-900/50 hover:bg-slate-900'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className={message.role === 'assistant' ? 'text-xs font-semibold text-cyan-300' : 'text-xs font-semibold text-slate-300'}>
                        {message.role === 'assistant' ? 'Assistant' : 'Prompt'}
                      </span>
                      {message.compileStatus && (
                        <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400">{message.compileStatus}</span>
                      )}
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">{message.content}</p>
                    {(message.model || message.usage) && (
                      <div className="mt-2 font-mono text-[10px] text-slate-500">
                        {[message.model, message.usage ? `${message.usage.total_tokens} tokens` : ''].filter(Boolean).join(' / ')}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <form onSubmit={submitPrompt} className="border-t border-slate-800 p-4">
            <textarea
              value={prompt}
              onChange={event => setPrompt(event.currentTarget.value)}
              placeholder="Describe the CAD design or modification..."
              className="h-28 w-full resize-none rounded border border-slate-700 bg-slate-950 p-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-cyan-500"
            />
            {error && <div className="rounded border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-200">{error}</div>}
            <button
              type="submit"
              disabled={isSubmitting || !prompt.trim() || !activeProject || fileMetadata.length === 0 || !selectedModel}
              className="mt-3 w-full rounded bg-cyan-600 px-4 py-3 text-base font-semibold text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSubmitting ? 'Generating...' : 'Generate Design'}
            </button>
          </form>
        </aside>
      )}
    </div>
  )
}
