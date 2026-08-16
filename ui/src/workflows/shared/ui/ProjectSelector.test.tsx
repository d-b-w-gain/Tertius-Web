import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ACTIVE_PROJECT_CHANGED_EVENT, ProjectSelector } from './ProjectSelector'

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
})
