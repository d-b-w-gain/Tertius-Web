import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ViewerTab } from './ViewerTab'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  login: vi.fn(),
}))

vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'guest', getAccessToken: mocks.getAccessToken, login: mocks.login }),
}))

describe('ViewerTab guest mode', () => {
  it('renders a guest notice without authenticated calls', () => {
    render(<ViewerTab serverUrl="/api/extus" isActive />)

    expect(screen.getByText('Log in to view compiled models')).toBeInTheDocument()
    expect(mocks.apiFetch).not.toHaveBeenCalled()
    expect(mocks.getAccessToken).not.toHaveBeenCalled()
  })
})
