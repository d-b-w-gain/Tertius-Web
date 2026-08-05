import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  logout: vi.fn(),
  getAccessToken: vi.fn(),
  user: null as null | {
    user_id: string
    tenant_id: string
    email: string
    workbenches: Array<'site' | 'structural'>
  },
}))

vi.mock('./auth/AuthProvider', () => ({
  hasWorkbenchAccess: (
    user: typeof mocks.user,
    workbench: 'site' | 'structural',
  ) => user?.workbenches.includes(workbench) ?? false,
  useAuth: () => ({
    authMode: mocks.user ? 'authenticated' : 'guest',
    user: mocks.user,
    isLoading: false,
    login: mocks.login,
    logout: mocks.logout,
    getAccessToken: mocks.getAccessToken,
  }),
}))

vi.mock('./workflows/intus/IntusWindow', () => ({ IntusWindow: () => <div>Intus mock</div> }))
vi.mock('./workflows/extus/ExtusWindow', () => ({ ExtusWindow: () => <div>Extus mock</div> }))
vi.mock('./workflows/extus/SharedExtusViewport', () => ({ SharedExtusViewport: () => <div>Shared Extus mock</div> }))
vi.mock('./workflows/artus/ArtusWindow', () => ({ ArtusWindow: () => <div>Artus mock</div> }))
vi.mock('./workflows/timus/TimusWindow', () => ({ TimusWindow: () => <div>Timus mock</div> }))
vi.mock('./workflows/octavus/OctavusWindow', () => ({ OctavusWindow: () => <div>Octavus mock</div> }))
vi.mock('./workflows/structural/StructuralWorkbench', () => ({ StructuralWorkbench: () => <div>Structural mock</div> }))
vi.mock('./workflows/site/SiteWorkbench', () => ({ SiteWorkbench: () => <div>Site mock</div> }))
vi.mock('./workflows/generate/GenerateDesignWindow', () => ({ GenerateDesignWindow: () => <div>Generate mock</div> }))
vi.mock('./workflows/generate/AiUsageGauge', () => ({ AiUsageGauge: () => <div>Usage mock</div> }))

afterEach(() => {
  cleanup()
  mocks.user = null
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
    expect(screen.getByText('Usage mock')).toBeInTheDocument()
    expect(screen.queryByText('Redirecting to login...')).not.toBeInTheDocument()
    expect(mocks.login).not.toHaveBeenCalled()
  })

  it('does not advertise optional engineering workbenches to general users', () => {
    mocks.user = {
      user_id: 'user-1',
      tenant_id: 'tenant-1',
      email: 'cad@example.com',
      workbenches: [],
    }

    render(<App />)

    expect(screen.queryByRole('button', { name: /Site/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Structural/ })).not.toBeInTheDocument()
    expect(screen.queryByText('Site mock')).not.toBeInTheDocument()
    expect(screen.queryByText('Structural mock')).not.toBeInTheDocument()
  })

  it('shows and mounts only the workbenches granted by Keycloak', () => {
    mocks.user = {
      user_id: 'engineer-1',
      tenant_id: 'tenant-1',
      email: 'engineer@example.com',
      workbenches: ['site', 'structural'],
    }

    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: /Site/ }))
    expect(screen.getByText('Site mock')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /Structural/ }))
    expect(screen.getByText('Structural mock')).toBeVisible()
  })
})
