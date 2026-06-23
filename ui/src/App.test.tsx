import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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
vi.mock('./workflows/octavus/OctavusWindow', () => ({ OctavusWindow: () => <div>Octavus mock</div> }))
vi.mock('./workflows/generate/GenerateDesignWindow', () => ({ GenerateDesignWindow: () => <div>Generate mock</div> }))
vi.mock('./workflows/generate/AiBudgetGauge', () => ({ AiBudgetGauge: () => <div>Budget mock</div> }))

afterEach(() => {
  cleanup()
})

describe('App guest mode', () => {
  it('starts with the sidebar collapsed on desktop', () => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1024,
    })

    render(<App />)

    const sidebar = screen.getByText('Artus mock').closest('.absolute')
    expect(sidebar).not.toBeNull()
    expect(sidebar?.className).toContain('md:w-0')
  })

  it('renders the app shell for anonymous users instead of redirecting to login', () => {
    render(<App />)

    expect(screen.getByText('Tertius')).toBeInTheDocument()
    expect(screen.getByText('Guest')).toBeInTheDocument()
    expect(screen.getByText('Generate mock')).toBeInTheDocument()
    expect(screen.getByText('Budget mock')).toBeInTheDocument()
    expect(screen.queryByText('Redirecting to login...')).not.toBeInTheDocument()
    expect(mocks.login).not.toHaveBeenCalled()
  })
})
