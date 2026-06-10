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

export type ProjectStorage = {
  listProjects: () => Promise<string[]>
  getActiveProject: () => Promise<string>
  createProject: (name: string) => Promise<void>
  activateProject: (name: string) => Promise<void>
  listFiles: (projectName: string) => Promise<string[]>
  loadCode: (projectName: string, filename: string) => Promise<string>
  saveCode: (projectName: string, filename: string, code: string) => Promise<void>
  deleteFile: (projectName: string, filename: string) => Promise<void>
  getStatus: (projectName: string, filename?: string) => Promise<{ mtime?: number }>
  getHistory: (projectName: string) => Promise<ProjectGitStatus>
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
  }
}

function createAuthenticatedStorage(serverUrl: string, getAccessToken: () => Promise<string>): ProjectStorage {
  const requireOk = async (response: Response, message: string) => {
    if (!response.ok) {
      throw new Error(message)
    }
    const text = await response.clone().text()
    if (!text) {
      return
    }
    const data = JSON.parse(text)
    if (data && data.success === false) {
      throw new Error(data.error || message)
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
  }
}

export function createProjectStorage(options: CreateProjectStorageOptions): ProjectStorage {
  if (options.authMode === 'guest') {
    return createGuestStorage()
  }
  return createAuthenticatedStorage(options.serverUrl, options.getAccessToken)
}
