import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  logout: vi.fn(),
  getAccessToken: vi.fn(),
}))

vi.mock('./auth/AuthProvider', () => ({
  useAuth: () => ({
    authMode: 'guest',
    user: null,
    isLoading: false,
    login: mocks.login,
    logout: mocks.logout,
    getAccessToken: mocks.getAccessToken,
  }),
}))

vi.mock('./workflows/intus/IntusWindow', () => ({ IntusWindow: () => <div>Intus mock</div> }))
vi.mock('./workflows/extus/ExtusWindow', () => ({ ExtusWindow: () => <div>Extus mock</div> }))
vi.mock('./workflows/artus/ArtusWindow', () => ({ ArtusWindow: () => <div>Artus mock</div> }))
vi.mock('./workflows/timus/TimusWindow', () => ({ TimusWindow: () => <div>Timus mock</div> }))

describe('App guest mode', () => {
  it('renders the app shell for anonymous users instead of redirecting to login', () => {
    render(<App />)

    expect(screen.getByText('Tertius')).toBeInTheDocument()
    expect(screen.getByText('Guest')).toBeInTheDocument()
    expect(screen.queryByText('Redirecting to login...')).not.toBeInTheDocument()
    expect(mocks.login).not.toHaveBeenCalled()
  })
})
