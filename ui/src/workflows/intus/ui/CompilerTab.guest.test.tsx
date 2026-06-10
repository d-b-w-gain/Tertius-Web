import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GUEST_WORKSPACE_KEY } from '../../shared/guestWorkspace'
import { CompilerTab } from './CompilerTab'

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

vi.mock('@monaco-editor/react', () => ({
  default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea aria-label="code editor" value={value} onChange={(event) => onChange(event.currentTarget.value)} />
  ),
}))

describe('CompilerTab guest mode', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
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

  it('disables compile and status paths for guests', async () => {
    render(<CompilerTab serverUrl="/api/intus" isActive />)

    const compileButton = await screen.findByRole('button', { name: 'Log in to compile' })

    expect(compileButton).toBeDisabled()
    await waitFor(() => expect(mocks.apiFetch).not.toHaveBeenCalled())
    expect(mocks.getAccessToken).not.toHaveBeenCalled()
  })
})
