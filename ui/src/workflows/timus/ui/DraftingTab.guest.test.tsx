import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DraftingTab } from './DraftingTab'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  login: vi.fn(),
}))

vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'guest', getAccessToken: mocks.getAccessToken, login: mocks.login }),
}))

describe('DraftingTab guest mode', () => {
  it('renders a guest notice without authenticated calls', () => {
    render(<DraftingTab serverUrl="/api/timus" isActive />)

    expect(screen.getByText('Log in to generate drawings')).toBeInTheDocument()
    expect(mocks.apiFetch).not.toHaveBeenCalled()
    expect(mocks.getAccessToken).not.toHaveBeenCalled()
  })
})
