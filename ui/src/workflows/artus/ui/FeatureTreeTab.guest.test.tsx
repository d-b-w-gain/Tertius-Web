import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FeatureTreeTab } from './FeatureTreeTab'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  login: vi.fn(),
}))

vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'guest', getAccessToken: mocks.getAccessToken, login: mocks.login }),
}))

describe('FeatureTreeTab guest mode', () => {
  it('renders a guest notice without authenticated calls', () => {
    render(<FeatureTreeTab serverUrl="/api/artus" />)

    expect(screen.getByText('Log in to inspect and modify features')).toBeInTheDocument()
    expect(mocks.apiFetch).not.toHaveBeenCalled()
    expect(mocks.getAccessToken).not.toHaveBeenCalled()
  })
})
