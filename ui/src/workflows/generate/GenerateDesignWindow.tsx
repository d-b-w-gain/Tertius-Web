import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'
import { resolveWorkflowServerUrl } from '../shared/apiConfig'
import { GUEST_WORKSPACE_CHANGED_EVENT } from '../shared/guestWorkspace'
import {
  createProjectStorage,
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
import { recordAiBudgetUsage } from './AiBudgetGauge'

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
  artifactId?: string
  modelUrl?: string
  compileStatus?: 'queued' | 'running' | 'succeeded' | 'failed'
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

function formatPrice(model: LlmModelOption) {
  return `$${model.input_price_per_million.toFixed(2)} / $${model.output_price_per_million.toFixed(2)}`
}

export function GenerateDesignWindow({ isActive = true }: { isActive?: boolean }) {
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
  const [dailyBudgetUsd, setDailyBudgetUsd] = useState(0)
  const [statusText, setStatusText] = useState('Select a project to generate a design.')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const activeProjectRef = useRef('')
  const compileRequestRef = useRef(0)
  const compileTimerRef = useRef<number | undefined>(undefined)
  const llmEditRequestRef = useRef(0)
  const llmEditTimerRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    activeProjectRef.current = activeProject
  }, [activeProject])

  useEffect(() => () => {
    if (compileTimerRef.current) window.clearTimeout(compileTimerRef.current)
    if (llmEditTimerRef.current) window.clearTimeout(llmEditTimerRef.current)
  }, [])

  const selectedMessage = messages.find(message => message.id === selectedMessageId)
  const selectedModelUrl = selectedMessage?.modelUrl || ''
  const selectedModel = llmModels.find(model => model.id === selectedModelId) || llmModels[0]

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

  const loadActiveProject = useCallback(async (projectName?: string) => {
    if (!shouldRunPollingRequest()) return
    try {
      const nextProject = projectName || await storage.getActiveProject()
      if (!nextProject) {
        setActiveProject('')
        setFileMetadata([])
        setStatusText('No active project selected.')
        return
      }
      if (nextProject !== activeProjectRef.current) {
        setMessages([])
        setSelectedMessageId(null)
      }
      setActiveProject(nextProject)
      const metadata = await refreshMetadata(nextProject)
      setStatusText(`Ready to generate against ${metadata.length} project file(s).`)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load active project.')
    }
  }, [refreshMetadata, storage])

  useEffect(() => {
    if (!isActive) return
    void loadActiveProject()
    const interval = authMode === 'guest'
      ? undefined
      : window.setInterval(() => void loadActiveProject(), getPollingDelay(ACTIVE_PROJECT_POLL_INTERVAL_MS))
    const handleProjectChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ activeProject?: string }>).detail
      if (detail?.activeProject) void loadActiveProject(detail.activeProject)
    }
    const handleGuestChanged = () => void loadActiveProject()

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
        setLlmModels(response.models)
        setDailyBudgetUsd(response.daily_budget_usd)
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

  const modelUrlForArtifact = useCallback((artifactId: string) => (
    `${extusServerUrl}/artifacts/${artifactId}/model?t=${Date.now()}`
  ), [extusServerUrl])

  const pollCompileJob = useCallback((projectName: string, jobId: string, requestId: number, assistantMessageId: string) => {
    const tick = async () => {
      if (compileRequestRef.current !== requestId) return
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
            modelUrl: artifactId ? modelUrlForArtifact(artifactId) : current.modelUrl,
            compileStatus: 'succeeded',
          }))
          if (artifactId) setSelectedMessageId(assistantMessageId)
          setStatusText(artifactId ? `Compiled ${format} artifact ${artifactId}.` : 'Compile succeeded.')
          return
        }

        if (data.status === 'failed') {
          const message = jsonMessage(data, 'Compile failed. Try again.')
          updateAssistantMessage(assistantMessageId, current => ({
            ...current,
            content: `${current.content}\n\nCompile failed: ${message}`,
            compileStatus: 'failed',
          }))
          setStatusText(message)
          return
        }

        updateAssistantMessage(assistantMessageId, current => ({
          ...current,
          compileStatus: data.status === 'queued' ? 'queued' : 'running',
        }))
        compileTimerRef.current = window.setTimeout(tick, COMPILE_STATUS_POLL_MS)
      } catch {
        compileTimerRef.current = window.setTimeout(tick, COMPILE_STATUS_RETRY_MS)
      }
    }

    compileTimerRef.current = window.setTimeout(tick, COMPILE_STATUS_INITIAL_DELAY_MS)
  }, [getAccessToken, intusServerUrl, modelUrlForArtifact, updateAssistantMessage])

  const applyLlmEditResult = useCallback((
    result: LlmFileEditResult,
    projectName: string,
    assistantMessageId: string,
    truncatedMessage?: string,
  ) => {
    recordAiBudgetUsage(result.usage.total_tokens)
    const changedFiles = result.files.filter(file => file.changed !== false)
    const fileSummary = result.files
      .map(file => file.summary)
      .filter(Boolean)
      .join(' ')
    const content = [
      truncatedMessage,
      result.outcome === 'changed'
        ? `Updated ${changedFiles.length || result.files.length} file(s).`
        : result.message || `AI returned ${result.outcome}.`,
      result.model ? `Model: ${result.model}.` : '',
      fileSummary,
    ].filter(Boolean).join(' ')

    updateAssistantMessage(assistantMessageId, current => ({
      ...current,
      content,
      files: result.files.map(file => ({
        filename: file.filename,
        summary: file.summary,
        changed: file.changed,
      })),
      usage: result.usage,
      compileStatus: result.outcome === 'changed' ? 'queued' : undefined,
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
      void queueCompile(projectName, changedFiles.length > 0 ? changedFiles : result.files, assistantMessageId)
    } else {
      setStatusText(result.message || `Generation completed with ${result.outcome}.`)
    }
  }, [queueCompile, updateAssistantMessage, setFileMetadata, setStatusText])

  const pollLlmEditJob = useCallback((projectName: string, jobId: string, requestId: number, assistantMessageId: string) => {
    const tick = async () => {
      if (llmEditRequestRef.current !== requestId) return
      try {
        const response = await storage.getLlmFileEditJob(projectName, jobId)
        if (response.status === 'succeeded') {
          if (!response.result) {
            updateAssistantMessage(assistantMessageId, current => ({
              ...current,
              content: `${current.content}\n\nAI edit returned no result payload.`,
              compileStatus: 'failed',
            }))
            setStatusText('AI edit completed but returned no result payload.')
            return
          }
          applyLlmEditResult(response.result, projectName, assistantMessageId)
          return
        }

        if (response.status === 'failed') {
          const message = jsonMessage(response, 'AI edit failed. Try again.')
          updateAssistantMessage(assistantMessageId, current => ({
            ...current,
            content: `${current.content}\n\nAI edit failed: ${message}`,
            compileStatus: 'failed',
          }))
          setStatusText(message)
          return
        }

        updateAssistantMessage(assistantMessageId, current => ({
          ...current,
          compileStatus: response.status === 'queued' ? 'queued' : 'running',
        }))
        llmEditTimerRef.current = window.setTimeout(tick, LLM_EDIT_STATUS_POLL_MS)
      } catch {
        llmEditTimerRef.current = window.setTimeout(tick, LLM_EDIT_STATUS_RETRY_MS)
      }
    }

    llmEditTimerRef.current = window.setTimeout(tick, LLM_EDIT_STATUS_INITIAL_DELAY_MS)
  }, [applyLlmEditResult, setStatusText, storage, updateAssistantMessage])

  const queueCompile = useCallback(async (
    projectName: string,
    changedFiles: LlmFileEditResult['files'],
    assistantMessageId: string,
  ) => {
    const designChange = changedFiles.find(file => file.filename === 'design.py')
    const code = designChange?.content || await storage.loadCode(projectName, 'design.py')
    if (!code) throw new Error('Compile could not start because design.py could not be loaded.')

    const requestId = compileRequestRef.current + 1
    compileRequestRef.current = requestId
    if (compileTimerRef.current) window.clearTimeout(compileTimerRef.current)

    const response = await apiFetch(`${intusServerUrl}/projects/${projectName}/compile`, getAccessToken, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code,
        export_format: COMPILE_FORMAT,
        quality: COMPILE_QUALITY,
        file: 'design.py',
      }),
    })
    const data = await response.json()
    if (!response.ok || !data.job_id) {
      throw new Error(jsonMessage(data, 'Compile could not start after generation.'))
    }

    updateAssistantMessage(assistantMessageId, current => ({
      ...current,
      content: `${current.content}\n\nCompile queued as ${COMPILE_FORMAT}/${COMPILE_QUALITY}.`,
      compileStatus: data.status === 'queued' ? 'queued' : 'running',
    }))
    setStatusText(`Compile job ${data.job_id} is ${data.status || 'queued'}.`)
    pollCompileJob(projectName, data.job_id, requestId, assistantMessageId)
  }, [getAccessToken, intusServerUrl, pollCompileJob, storage, updateAssistantMessage])

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
      const job = await storage.applyLlmFileEditJob(activeProject, {
        prompt: submittedPrompt,
        files: requestFiles.map(file => ({
          id: file.id,
          filename: file.filename,
          updated_at: file.updated_at,
        })),
        active_file_id: activeFileId,
        model_id: selectedModel?.id,
        metadata: { source: 'generate_design_window' },
      })
      const requestId = llmEditRequestRef.current + 1
      llmEditRequestRef.current = requestId
      if (llmEditTimerRef.current) window.clearTimeout(llmEditTimerRef.current)

      updateAssistantMessage(assistantMessage.id, current => ({
        ...current,
        content: 'AI edit is running...',
        compileStatus: 'running',
      }))
      setStatusText(`AI edit job ${job.job_id} queued.`)
      pollLlmEditJob(activeProject, job.job_id, requestId, assistantMessage.id)
      if (truncatedMessage) {
        updateAssistantMessage(assistantMessage.id, current => ({
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
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100 md:flex-row">
      <section className="flex min-h-0 w-full flex-col border-b border-slate-800 bg-slate-900/40 md:w-1/2 md:border-b-0 md:border-r">
        <div className="border-b border-slate-800 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-slate-100">Generate Design</h2>
            </div>
            <button
              type="button"
              onClick={() => void loadActiveProject()}
              className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              Refresh
            </button>
          </div>
          <div className="mt-4">
            <ProjectSelector />
          </div>
        </div>

        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <span className="text-sm text-slate-300">{statusText}</span>
          <span className="rounded border border-slate-800 bg-slate-900 px-2 py-1 font-mono text-[10px] text-slate-500">
            {dailyBudgetUsd ? `$${dailyBudgetUsd.toFixed(2)}/day` : `${COMPILE_FORMAT}/${COMPILE_QUALITY}`}
          </span>
        </div>

        {llmModels.length > 0 && (
          <div className="flex gap-2 overflow-x-auto border-b border-slate-800 px-4 py-3">
            {llmModels.map(model => (
              <button
                key={model.id}
                type="button"
                onClick={() => setSelectedModelId(model.id)}
                className={`shrink-0 rounded border px-3 py-2 text-left text-xs transition-colors ${
                  selectedModelId === model.id
                    ? 'border-cyan-600 bg-cyan-950/50 text-cyan-100'
                    : 'border-slate-800 bg-slate-900 text-slate-300 hover:bg-slate-800'
                }`}
                title={`${model.model} ${formatPrice(model)} per 1M tokens`}
              >
                <span className="block whitespace-nowrap font-semibold">{model.label}</span>
                <span className="block whitespace-nowrap font-mono text-[10px] text-slate-500">
                  {formatPrice(model)}
                </span>
              </button>
            ))}
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
                  {message.usage && (
                    <div className="mt-2 font-mono text-[10px] text-slate-500">
                      {message.usage.total_tokens} tokens
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
      </section>

      <section className="flex min-h-0 w-full flex-col md:w-1/2">
        <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {selectedModelUrl ? 'Historical Model' : 'Latest Model'}
            </div>
            <div className="truncate text-xs text-slate-400">
              {selectedMessage?.content || activeProject || 'No active project'}
            </div>
          </div>
          {selectedModelUrl && (
            <a
              href={selectedModelUrl}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
            >
              Open
            </a>
          )}
        </div>
        <div className="min-h-0 flex-1">
          {selectedModelUrl ? (
            <ModelViewerCanvas
              modelUrl={selectedModelUrl}
              getAccessToken={getAccessToken}
              statusText="Selected historical model"
              projectName={activeProject}
              isActive={isActive}
            />
          ) : (
            <LatestModelViewer serverUrl={extusServerUrl} isActive={isActive} />
          )}
        </div>
      </section>
    </div>
  )
}
