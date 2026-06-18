import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GUEST_WORKSPACE_KEY } from '../../shared/guestWorkspace'
import { CompilerTab } from './CompilerTab'

const storage = vi.hoisted(() => ({
  getActiveProject: vi.fn(),
  listFiles: vi.fn(),
  listFileMetadata: vi.fn(),
  loadCode: vi.fn(),
  saveCode: vi.fn(),
  deleteFile: vi.fn(),
  getStatus: vi.fn(),
  getHistory: vi.fn(),
  applyLlmFileEdit: vi.fn(),
}))

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
}))

vi.mock('../../../api/client', () => ({
  apiFetch: mocks.apiFetch,
}))

vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({
    authMode: 'guest',
    getAccessToken: mocks.getAccessToken,
  }),
}))

vi.mock('../../shared/projectStorage', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../shared/projectStorage')>()
  return {
    createProjectStorage: (options: Parameters<typeof actual.createProjectStorage>[0]) => {
      const real = actual.createProjectStorage(options)
      return { ...real, applyLlmFileEdit: storage.applyLlmFileEdit }
    },
  }
})

vi.mock('@monaco-editor/react', () => ({
  default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea aria-label="code editor" value={value} onChange={(event) => onChange(event.currentTarget.value)} />
  ),
}))

describe('CompilerTab guest mode', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    storage.listFileMetadata.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
  })

  it('persists guest editor changes to localStorage without authenticated API calls', async () => {
    render(<CompilerTab serverUrl="/api/intus" />)

    const editor = await screen.findByLabelText('code editor')
    fireEvent.change(editor, { target: { value: 'print("guest")' } })

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem(GUEST_WORKSPACE_KEY) || '{}')
      expect(stored.projects.default_purlin.files['design.py']).toBe('print("guest")')
    })
    expect(mocks.apiFetch).not.toHaveBeenCalled()
    expect(mocks.getAccessToken).not.toHaveBeenCalled()
  })

  it('flushes guest editor changes immediately so login import sees the latest draft', async () => {
    render(<CompilerTab serverUrl="/api/intus" />)

    const editor = await screen.findByLabelText('code editor')
    fireEvent.change(editor, { target: { value: 'print("before login")' } })

    const stored = JSON.parse(localStorage.getItem(GUEST_WORKSPACE_KEY) || '{}')
    expect(stored.projects.default_purlin.files['design.py']).toBe('print("before login")')
  })

  it('lets guests create and select local projects from the compiler tab', async () => {
    render(<CompilerTab serverUrl="/api/intus" />)

    fireEvent.click(await screen.findByRole('button', { name: /new/i }))
    fireEvent.change(screen.getByPlaceholderText('Name...'), { target: { value: 'guest_demo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() => {
      expect(screen.getByText('guest_demo')).toBeInTheDocument()
    })
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })

  it('disables compile and status paths for guests', async () => {
    render(<CompilerTab serverUrl="/api/intus" isActive />)

    const compileButton = await screen.findByRole('button', { name: 'Log in to compile' })

    expect(compileButton).toBeDisabled()
    await waitFor(() => expect(mocks.apiFetch).not.toHaveBeenCalled())
    expect(mocks.getAccessToken).not.toHaveBeenCalled()
  })

  it('does not render the AI prompt control for guests', async () => {
    render(<CompilerTab serverUrl="/api/intus" isActive />)

    await screen.findByLabelText('code editor')
    expect(screen.queryByLabelText('AI prompt')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /AI edit/i })).not.toBeInTheDocument()
  })

  it('never invokes storage.applyLlmFileEdit for guests', async () => {
    storage.applyLlmFileEdit.mockResolvedValue({
      success: true,
      outcome: 'changed',
      message: '',
      model: 'test-model',
      usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
      snapshot: { id: 'snap-1', message: 'edit', content_hash: 'hash' },
      files: [],
    })

    render(<CompilerTab serverUrl="/api/intus" isActive />)

    await screen.findByLabelText('code editor')

    expect(storage.applyLlmFileEdit).not.toHaveBeenCalled()
  })
})
