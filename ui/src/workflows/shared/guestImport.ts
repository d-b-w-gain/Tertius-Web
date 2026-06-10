import { apiFetch } from '../../api/client'
import { clearGuestWorkspace, loadGuestWorkspace, type GuestWorkspace } from './guestWorkspace'
import { resolveWorkflowServerUrl } from './apiConfig'

type ImportGuestWorkspaceOptions = {
  getAccessToken: () => Promise<string>
  timestamp?: () => string
}

export type GuestImportResult = {
  importedProjects: Record<string, string>
  activeProject: string
  activeFile: string
}

function defaultTimestamp() {
  const date = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}`
}

function hasGuestData(workspace: GuestWorkspace) {
  return Object.keys(workspace.projects).length > 0
}

function collisionSafeName(name: string, existing: Set<string>, timestamp: string) {
  if (!existing.has(name)) {
    existing.add(name)
    return name
  }
  const baseCandidate = `${name}-guest-${timestamp}`
  let candidate = baseCandidate
  let counter = 2
  while (existing.has(candidate)) {
    candidate = `${baseCandidate}-${counter}`
    counter += 1
  }
  existing.add(candidate)
  return candidate
}

async function requireOk(response: Response, message: string) {
  if (!response.ok) {
    throw new Error(message)
  }
  try {
    const data = await response.clone().json()
    if (data && data.success === false) {
      throw new Error(message)
    }
  } catch (error) {
    if (error instanceof Error && error.message === message) {
      throw error
    }
  }
}

export async function importGuestWorkspace(options: ImportGuestWorkspaceOptions): Promise<GuestImportResult> {
  const workspace = loadGuestWorkspace()
  if (!hasGuestData(workspace)) {
    return { importedProjects: {}, activeProject: '', activeFile: '' }
  }

  const intusBase = resolveWorkflowServerUrl('intus', import.meta.env?.VITE_API_URL)
  const projectsRes = await apiFetch(`${intusBase}/projects`, options.getAccessToken)
  await requireOk(projectsRes, 'Failed to list projects')
  const projectsData = await projectsRes.json()
  const existing = new Set<string>(projectsData.projects || [])
  const timestamp = (options.timestamp || defaultTimestamp)()
  const importedProjects: Record<string, string> = {}

  for (const [guestName, project] of Object.entries(workspace.projects)) {
    const targetName = collisionSafeName(guestName, existing, timestamp)
    importedProjects[guestName] = targetName

    await requireOk(
      await apiFetch(`${intusBase}/projects/${targetName}/new`, options.getAccessToken, { method: 'POST' }),
      `Failed to create ${targetName}`,
    )

    for (const [file, code] of Object.entries(project.files)) {
      await requireOk(
        await apiFetch(`${intusBase}/projects/${targetName}/save`, options.getAccessToken, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file, code }),
        }),
        `Failed to save ${file}`,
      )
    }
  }

  const activeProject = importedProjects[workspace.activeProject] || Object.values(importedProjects)[0] || ''
  const activeFile = workspace.projects[workspace.activeProject]?.activeFile || 'design.py'

  if (activeProject) {
    await requireOk(
      await apiFetch(`${intusBase}/projects/${activeProject}/activate`, options.getAccessToken, { method: 'POST' }),
      `Failed to activate ${activeProject}`,
    )
  }

  clearGuestWorkspace()
  return { importedProjects, activeProject, activeFile }
}
