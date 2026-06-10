export const GUEST_WORKSPACE_KEY = 'tertius_guest_workspace_v1'
export const GUEST_WORKSPACE_CHANGED_EVENT = 'tertius:guest-workspace-changed'

const DEFAULT_PROJECT_NAME = 'default_purlin'
const DEFAULT_FILE_NAME = 'design.py'

const FALLBACK_TEMPLATE = `# Local guest draft for a simple purlin.
from dataclasses import dataclass

@dataclass
class Purlin:
    span: float = 6.0
    spacing: float = 1.2

purlin = Purlin()
`

export type GuestProject = {
  files: Record<string, string>
  activeFile: string
  updatedAt: string
}

export type GuestWorkspace = {
  version: 1
  activeProject: string
  projects: Record<string, GuestProject>
}

const projectNamePattern = /^[A-Za-z0-9_.-]{1,80}$/
const filenamePattern = /^[A-Za-z0-9_.-]+\.py$/

export function isValidGuestProjectName(name: string) {
  return projectNamePattern.test(name)
}

export function isValidGuestFilename(filename: string) {
  return filenamePattern.test(filename)
}

function nowIso() {
  return new Date().toISOString()
}

function defaultWorkspace(): GuestWorkspace {
  return {
    version: 1,
    activeProject: DEFAULT_PROJECT_NAME,
    projects: {
      [DEFAULT_PROJECT_NAME]: {
        files: {
          [DEFAULT_FILE_NAME]: FALLBACK_TEMPLATE,
        },
        activeFile: DEFAULT_FILE_NAME,
        updatedAt: nowIso(),
      },
    },
  }
}

function normalizeWorkspace(value: unknown): GuestWorkspace {
  if (!value || typeof value !== 'object' || (value as { version?: unknown }).version !== 1) {
    return defaultWorkspace()
  }

  const raw = value as {
    activeProject?: unknown
    projects?: unknown
  }
  if (!raw.projects || typeof raw.projects !== 'object') {
    return defaultWorkspace()
  }

  const projects: GuestWorkspace['projects'] = {}
  for (const [projectName, project] of Object.entries(raw.projects)) {
    if (!isValidGuestProjectName(projectName) || !project || typeof project !== 'object') {
      continue
    }

    const rawProject = project as {
      files?: unknown
      activeFile?: unknown
      updatedAt?: unknown
    }
    const files: Record<string, string> = {}
    if (rawProject.files && typeof rawProject.files === 'object') {
      for (const [filename, code] of Object.entries(rawProject.files)) {
        if (isValidGuestFilename(filename) && typeof code === 'string') {
          files[filename] = code
        }
      }
    }

    if (!(DEFAULT_FILE_NAME in files)) {
      files[DEFAULT_FILE_NAME] = FALLBACK_TEMPLATE
    }

    const activeFile =
      typeof rawProject.activeFile === 'string' && rawProject.activeFile in files
        ? rawProject.activeFile
        : DEFAULT_FILE_NAME

    projects[projectName] = {
      files,
      activeFile,
      updatedAt: typeof rawProject.updatedAt === 'string' ? rawProject.updatedAt : nowIso(),
    }
  }

  if (!projects[DEFAULT_PROJECT_NAME] && Object.keys(projects).length === 0) {
    return defaultWorkspace()
  }

  const activeProject =
    typeof raw.activeProject === 'string' && projects[raw.activeProject]
      ? raw.activeProject
      : Object.keys(projects)[0]

  return {
    version: 1,
    activeProject,
    projects,
  }
}

export function loadGuestWorkspace(): GuestWorkspace {
  const raw = localStorage.getItem(GUEST_WORKSPACE_KEY)
  if (!raw) {
    return defaultWorkspace()
  }

  try {
    return normalizeWorkspace(JSON.parse(raw))
  } catch {
    return defaultWorkspace()
  }
}

export function saveGuestWorkspace(workspace: GuestWorkspace) {
  localStorage.setItem(GUEST_WORKSPACE_KEY, JSON.stringify(normalizeWorkspace(workspace)))
  window.dispatchEvent(new Event(GUEST_WORKSPACE_CHANGED_EVENT))
}

export function clearGuestWorkspace() {
  localStorage.removeItem(GUEST_WORKSPACE_KEY)
}

export function createGuestProject(workspace: GuestWorkspace, projectName: string): GuestWorkspace {
  if (!isValidGuestProjectName(projectName)) {
    throw new Error('Invalid project name')
  }
  if (workspace.projects[projectName]) {
    throw new Error('Project already exists')
  }

  return {
    ...workspace,
    activeProject: projectName,
    projects: {
      ...workspace.projects,
      [projectName]: {
        files: { [DEFAULT_FILE_NAME]: FALLBACK_TEMPLATE },
        activeFile: DEFAULT_FILE_NAME,
        updatedAt: nowIso(),
      },
    },
  }
}

export function setGuestActiveProject(workspace: GuestWorkspace, projectName: string): GuestWorkspace {
  if (!workspace.projects[projectName]) {
    throw new Error('Project not found')
  }
  return {
    ...workspace,
    activeProject: projectName,
  }
}

export function saveGuestCode(
  workspace: GuestWorkspace,
  projectName: string,
  filename: string,
  code: string,
): GuestWorkspace {
  if (!workspace.projects[projectName]) {
    throw new Error('Project not found')
  }
  if (!isValidGuestFilename(filename)) {
    throw new Error('Invalid filename')
  }

  return {
    ...workspace,
    projects: {
      ...workspace.projects,
      [projectName]: {
        ...workspace.projects[projectName],
        files: {
          ...workspace.projects[projectName].files,
          [filename]: code,
        },
        activeFile: filename,
        updatedAt: nowIso(),
      },
    },
  }
}

export function getGuestCode(workspace: GuestWorkspace, projectName: string, filename: string) {
  return workspace.projects[projectName]?.files[filename] ?? ''
}

export function deleteGuestFile(workspace: GuestWorkspace, projectName: string, filename: string): GuestWorkspace {
  const project = workspace.projects[projectName]
  if (!project) {
    throw new Error('Project not found')
  }
  if (filename === DEFAULT_FILE_NAME) {
    throw new Error('design.py cannot be deleted')
  }

  const { [filename]: _removed, ...files } = project.files
  return {
    ...workspace,
    projects: {
      ...workspace.projects,
      [projectName]: {
        ...project,
        files,
        activeFile: project.activeFile === filename ? DEFAULT_FILE_NAME : project.activeFile,
        updatedAt: nowIso(),
      },
    },
  }
}
