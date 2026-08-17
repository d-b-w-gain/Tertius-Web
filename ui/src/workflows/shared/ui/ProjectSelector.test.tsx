import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ACTIVE_PROJECT_CHANGED_EVENT,
  ProjectSelector,
  suggestedProjectName,
} from './ProjectSelector'

const storage = vi.hoisted(() => ({
  listProjects: vi.fn(),
  getActiveProject: vi.fn(),
  getHistory: vi.fn(),
  activateProject: vi.fn(),
  createProject: vi.fn(),
  import3mf: vi.fn(),
}))

vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'authenticated', getAccessToken: vi.fn() }),
}))

vi.mock('../projectStorage', () => ({
  createProjectStorage: () => storage,
}))

describe('ProjectSelector 3MF import', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storage.listProjects.mockResolvedValue(['default'])
    storage.getActiveProject.mockResolvedValue('default')
    storage.getHistory.mockResolvedValue({ is_git: false })
    storage.activateProject.mockResolvedValue(undefined)
    storage.import3mf.mockResolvedValue({ success: true, project: 'falcon9' })
    vi.spyOn(window, 'alert').mockImplementation(() => undefined)
  })

  afterEach(() => cleanup())

  it('imports, activates, refreshes, and broadcasts imported project', async () => {
    const listener = vi.fn()
    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, listener)
    storage.listProjects.mockResolvedValueOnce(['default']).mockResolvedValueOnce(['default', 'falcon9'])
    render(<ProjectSelector />)

    fireEvent.click(await screen.findByRole('button', { name: 'Import 3MF' }))
    const file = new File(['3mf'], 'falcon9.3mf', { type: 'application/vnd.ms-package.3dmanufacturing-3dmodel+xml' })
    fireEvent.change(screen.getByLabelText('3MF file'), { target: { files: [file] } })
    fireEvent.change(screen.getByLabelText('Imported project name'), { target: { value: 'falcon9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import project' }))

    await waitFor(() => expect(storage.import3mf).toHaveBeenCalledWith(file, 'falcon9'))
    await waitFor(() => expect(storage.activateProject).toHaveBeenCalledWith('falcon9'))
    expect(storage.activateProject).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(listener).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, listener)
  })

  it('disables Cancel while import is pending and completes normally', async () => {
    let resolveImport!: (result: { success: boolean; project: string }) => void
    storage.import3mf.mockImplementation(() => new Promise((resolve) => {
      resolveImport = resolve
    }))
    storage.listProjects
      .mockResolvedValueOnce(['default'])
      .mockResolvedValueOnce(['default', 'falcon9'])
    render(<ProjectSelector />)

    fireEvent.click(await screen.findByRole('button', { name: 'Import 3MF' }))
    const file = new File(['3mf'], 'falcon9.3mf')
    fireEvent.change(screen.getByLabelText('3MF file'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Import project' }))

    await waitFor(() => expect(storage.import3mf).toHaveBeenCalledWith(file, 'falcon9'))
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()

    resolveImport({ success: true, project: 'falcon9' })

    await waitFor(() => expect(storage.activateProject).toHaveBeenCalledWith('falcon9'))
    expect(storage.activateProject).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps an imported project selectable when its first activation fails', async () => {
    const listener = vi.fn()
    window.addEventListener(ACTIVE_PROJECT_CHANGED_EVENT, listener)
    let backendActiveProject = 'default'
    let importCompleted = false
    storage.getActiveProject.mockImplementation(async () => backendActiveProject)
    storage.listProjects.mockImplementation(async () => (
      importCompleted ? ['default', 'falcon9'] : ['default']
    ))
    storage.import3mf.mockImplementation(async () => {
      importCompleted = true
      return { success: true, project: 'falcon9' }
    })
    storage.activateProject
      .mockRejectedValueOnce(new Error('activation unavailable'))
      .mockImplementation(async (project: string) => {
        backendActiveProject = project
      })
    render(<ProjectSelector />)

    const selector = await screen.findByRole('combobox')
    await waitFor(() => expect(selector).toHaveValue('default'))
    fireEvent.click(screen.getByRole('button', { name: 'Import 3MF' }))
    const file = new File(['3mf'], 'falcon9.3mf')
    fireEvent.change(screen.getByLabelText('3MF file'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Import project' }))

    await waitFor(() => expect(storage.activateProject).toHaveBeenCalledWith('falcon9'))
    expect(selector).toHaveValue('default')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'falcon9' })).toBeInTheDocument()
    expect(storage.import3mf).toHaveBeenCalledTimes(1)
    expect(listener).not.toHaveBeenCalled()
    expect(window.alert).toHaveBeenCalledWith('activation unavailable')

    fireEvent.change(selector, { target: { value: 'falcon9' } })

    await waitFor(() => expect(selector).toHaveValue('falcon9'))
    expect(storage.activateProject).toHaveBeenCalledTimes(2)
    expect(storage.import3mf).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledTimes(1)
    window.removeEventListener(ACTIVE_PROJECT_CHANGED_EVENT, listener)
  })

  it.each([
    ['Falcon 9 final.3mf', 'Falcon_9_final'],
    ['火箭 — final!!.3MF', 'final'],
    [`${'a'.repeat(90)}.3mf`, 'a'.repeat(80)],
    ['... .3mf', 'imported_3mf'],
  ])('suggests a valid project name for %s', (filename, expected) => {
    expect(suggestedProjectName(filename)).toBe(expected)
  })
})
