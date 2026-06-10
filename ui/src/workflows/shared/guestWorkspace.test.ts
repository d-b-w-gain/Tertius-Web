import { beforeEach, describe, expect, it } from 'vitest'
import {
  GUEST_WORKSPACE_KEY,
  createGuestProject,
  deleteGuestFile,
  getGuestCode,
  isValidGuestFilename,
  isValidGuestProjectName,
  loadGuestWorkspace,
  saveGuestCode,
  saveGuestWorkspace,
  setGuestActiveProject,
} from './guestWorkspace'

describe('guestWorkspace', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('normalizes missing, corrupt, and unsupported localStorage data', () => {
    expect(loadGuestWorkspace().activeProject).toBe('default_purlin')

    localStorage.setItem(GUEST_WORKSPACE_KEY, '{bad json')
    expect(loadGuestWorkspace().projects.default_purlin.files['design.py']).toContain('purlin')

    localStorage.setItem(GUEST_WORKSPACE_KEY, JSON.stringify({ version: 99 }))
    expect(loadGuestWorkspace().version).toBe(1)
  })

  it('validates project and Python filenames with backend-compatible patterns', () => {
    expect(isValidGuestProjectName('beam-01_model.v1')).toBe(true)
    expect(isValidGuestProjectName('bad/name')).toBe(false)
    expect(isValidGuestProjectName('')).toBe(false)
    expect(isValidGuestProjectName('a'.repeat(81))).toBe(false)

    expect(isValidGuestFilename('design.py')).toBe(true)
    expect(isValidGuestFilename('nested/design.py')).toBe(false)
    expect(isValidGuestFilename('notes.txt')).toBe(false)
  })

  it('creates projects, saves files, deletes files, and selects active projects', () => {
    let workspace = createGuestProject(loadGuestWorkspace(), 'demo')
    workspace = saveGuestCode(workspace, 'demo', 'helper.py', 'VALUE = 42')
    workspace = setGuestActiveProject(workspace, 'demo')

    expect(workspace.activeProject).toBe('demo')
    expect(workspace.projects.demo.activeFile).toBe('helper.py')
    expect(getGuestCode(workspace, 'demo', 'helper.py')).toBe('VALUE = 42')

    workspace = deleteGuestFile(workspace, 'demo', 'helper.py')
    expect(workspace.projects.demo.files['helper.py']).toBeUndefined()
    expect(workspace.projects.demo.activeFile).toBe('design.py')
  })

  it('persists workspace changes to localStorage', () => {
    const workspace = saveGuestCode(createGuestProject(loadGuestWorkspace(), 'demo'), 'demo', 'design.py', 'print("demo")')

    saveGuestWorkspace(workspace)

    expect(loadGuestWorkspace().projects.demo.files['design.py']).toBe('print("demo")')
  })
})
