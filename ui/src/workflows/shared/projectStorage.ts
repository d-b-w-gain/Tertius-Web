import { apiFetch } from '../../api/client'
import {
  createGuestProject,
  deleteGuestFile,
  getGuestCode,
  loadGuestWorkspace,
  saveGuestCode,
  saveGuestWorkspace,
  setGuestActiveProject,
  type GuestWorkspace,
} from './guestWorkspace'

export type ProjectGitStatus = {
  is_git: boolean
  commit?: string
  history?: string[]
  label?: string
}

export type ProjectFileMetadata = {
  id: string
  filename: string
  updated_at?: string
}

export type LlmFileEditResult = {
  success: true
  outcome: 'changed' | 'no_change' | 'cannot_complete'
  message: string
  provider?: string
  model: string
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  cost_usd?: number
  snapshot: { id: string; message: string; content_hash: string } | null
  files: Array<{
    id: string
    filename: string
    content: string
    updated_at?: string
    changed: boolean
    summary?: string
  }>
}

export type LlmModelOption = {
  id: string
  label: string
  model: string
  api: string
  endpoint: string
  input_price_per_million: number
  output_price_per_million: number
  cached_read_price_per_million: number | null
  cached_write_price_per_million: number | null
  enabled: boolean
}

export type LlmModelsResponse = {
  default_model_id: string
  daily_budget_usd: number
  models: LlmModelOption[]
}

export type ProjectStorage = {
  listProjects: () => Promise<string[]>
  getActiveProject: () => Promise<string>
  createProject: (name: string) => Promise<void>
  activateProject: (name: string) => Promise<void>
  listFiles: (projectName: string) => Promise<string[]>
  listFileMetadata: (projectName: string) => Promise<ProjectFileMetadata[]>
  loadCode: (projectName: string, filename: string) => Promise<string>
  saveCode: (projectName: string, filename: string, code: string) => Promise<void>
  deleteFile: (projectName: string, filename: string) => Promise<void>
  getStatus: (projectName: string, filename?: string) => Promise<{ mtime?: number }>
  getHistory: (projectName: string) => Promise<ProjectGitStatus>
  applyLlmFileEdit: (
    projectName: string,
    request: {
      prompt: string
      files: Array<{ id: string; filename: string; updated_at: string }>
      active_file_id?: string
      model_id?: string
      metadata?: Record<string, string>
    },
  ) => Promise<LlmFileEditResult>
  listLlmModels: () => Promise<LlmModelsResponse>
}

type CreateProjectStorageOptions = {
  authMode: 'guest' | 'authenticated'
  serverUrl: string
  getAccessToken: () => Promise<string>
}

function updateGuestWorkspace(updater: (workspace: GuestWorkspace) => GuestWorkspace) {
  const next = updater(loadGuestWorkspace())
  saveGuestWorkspace(next)
  return next
}

function createGuestStorage(): ProjectStorage {
  return {
    async listProjects() {
      return Object.keys(loadGuestWorkspace().projects)
    },
    async getActiveProject() {
      return loadGuestWorkspace().activeProject
    },
    async createProject(name) {
      updateGuestWorkspace((workspace) => createGuestProject(workspace, name))
    },
    async activateProject(name) {
      updateGuestWorkspace((workspace) => setGuestActiveProject(workspace, name))
    },
    async listFiles(projectName) {
      return Object.keys(loadGuestWorkspace().projects[projectName]?.files ?? {})
    },
    async listFileMetadata(projectName) {
      return Object.keys(loadGuestWorkspace().projects[projectName]?.files ?? {}).map((filename) => ({
        id: '',
        filename,
      }))
    },
    async loadCode(projectName, filename) {
      return getGuestCode(loadGuestWorkspace(), projectName, filename)
    },
    async saveCode(projectName, filename, code) {
      updateGuestWorkspace((workspace) => saveGuestCode(workspace, projectName, filename, code))
    },
    async deleteFile(projectName, filename) {
      updateGuestWorkspace((workspace) => deleteGuestFile(workspace, projectName, filename))
    },
    async getStatus() {
      return {}
    },
    async getHistory() {
      return { is_git: false, label: 'Local draft' }
    },
    async applyLlmFileEdit() {
      throw new Error('Log in to use AI file edits')
    },
    async listLlmModels() {
      return { default_model_id: '', daily_budget_usd: 0, models: [] }
    },
  }
}

function createAuthenticatedStorage(serverUrl: string, getAccessToken: () => Promise<string>): ProjectStorage {
  const responseJson = async (response: Response) => {
    try {
      return await response.clone().json()
    } catch {
      return null
    }
  }

  const responseErrorMessage = async (response: Response, fallback: string) => {
    const data: unknown = await responseJson(response)
    if (data && typeof data === 'object') {
      const error = 'error' in data ? data.error : undefined
      const detail = 'detail' in data ? data.detail : undefined
      if (typeof error === 'string' && error.trim()) return error
      if (typeof detail === 'string' && detail.trim()) return detail
    }

    if (response.status === 524) {
      return `${fallback}: the request timed out at the edge. The edit may still be running; refresh the project before retrying.`
    }

    if (!response.ok) {
      return `${fallback} (${response.status} ${response.statusText || 'HTTP error'})`
    }
    return fallback
  }

  const requireOk = async (response: Response, message: string) => {
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, message))
    }
    const data: unknown = await responseJson(response)
    if (data && typeof data === 'object' && 'success' in data && data.success === false) {
      throw new Error(await responseErrorMessage(response, message))
    }
  }

  return {
    async listProjects() {
      const res = await apiFetch(`${serverUrl}/projects`, getAccessToken)
      if (!res.ok) return []
      const data = await res.json()
      return data.projects || []
    },
    async getActiveProject() {
      const res = await apiFetch(`${serverUrl}/project_name`, getAccessToken)
      if (!res.ok) return ''
      const data = await res.json()
      return data.project_name || ''
    },
    async createProject(name) {
      await requireOk(
        await apiFetch(`${serverUrl}/projects/${name}/new`, getAccessToken, { method: 'POST' }),
        `Failed to create project ${name}`,
      )
    },
    async activateProject(name) {
      await requireOk(
        await apiFetch(`${serverUrl}/projects/${name}/activate`, getAccessToken, { method: 'POST' }),
        `Failed to activate project ${name}`,
      )
    },
    async listFiles(projectName) {
      const res = await apiFetch(`${serverUrl}/projects/${projectName}/files`, getAccessToken)
      if (!res.ok) return ['design.py']
      const data = await res.json()
      return data.files || ['design.py']
    },
    async listFileMetadata(projectName) {
      const res = await apiFetch(`${serverUrl}/projects/${projectName}/files`, getAccessToken)
      if (!res.ok) return [{ id: '', filename: 'design.py' }]
      const data = await res.json()
      if (Array.isArray(data.file_metadata)) {
        return data.file_metadata as ProjectFileMetadata[]
      }
      const fallbackNames: string[] = Array.isArray(data.files) ? data.files : ['design.py']
      return fallbackNames.map((filename) => ({ id: '', filename }))
    },
    async loadCode(projectName, filename) {
      const res = await apiFetch(`${serverUrl}/projects/${projectName}/code?file=${filename}`, getAccessToken)
      if (!res.ok) return ''
      const data = await res.json()
      return data.code || ''
    },
    async saveCode(projectName, filename, code) {
      await requireOk(
        await apiFetch(`${serverUrl}/projects/${projectName}/save`, getAccessToken, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, file: filename }),
        }),
        `Failed to save ${filename}`,
      )
    },
    async deleteFile(projectName, filename) {
      await requireOk(
        await apiFetch(`${serverUrl}/projects/${projectName}/file?file=${filename}`, getAccessToken, {
          method: 'DELETE',
        }),
        `Failed to delete ${filename}`,
      )
    },
    async getStatus(projectName, filename = 'design.py') {
      const res = await apiFetch(`${serverUrl}/projects/${projectName}/status?file=${filename}`, getAccessToken)
      if (!res.ok) return {}
      return res.json()
    },
    async getHistory(projectName) {
      const res = await apiFetch(`${serverUrl}/projects/${projectName}/git_status`, getAccessToken)
      if (!res.ok) return { is_git: false }
      return res.json()
    },
    async applyLlmFileEdit(projectName, request) {
      const res = await apiFetch(`${serverUrl}/projects/${projectName}/files/llm-edit`, getAccessToken, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      })
      await requireOk(res, 'LLM file edit failed')
      return (await res.json()) as LlmFileEditResult
    },
    async listLlmModels() {
      const res = await apiFetch(`${serverUrl}/llm-usage/models`, getAccessToken)
      await requireOk(res, 'Failed to load LLM models')
      return (await res.json()) as LlmModelsResponse
    },
  }
}

export function createProjectStorage(options: CreateProjectStorageOptions): ProjectStorage {
  if (options.authMode === 'guest') {
    return createGuestStorage()
  }
  return createAuthenticatedStorage(options.serverUrl, options.getAccessToken)
}
