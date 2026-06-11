import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GUEST_WORKSPACE_KEY } from './guestWorkspace'
import { createProjectStorage } from './projectStorage'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  apiFetch: mocks.apiFetch,
}))

describe('projectStorage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('stores guest projects and files locally without calling apiFetch', async () => {
    const storage = createProjectStorage({
      authMode: 'guest',
      serverUrl: '/proxy/api/intus',
      getAccessToken: vi.fn(),
    })

    await storage.createProject('demo')
    await storage.saveCode('demo', 'helper.py', 'print("local")')

    expect(await storage.listProjects()).toContain('demo')
    expect(await storage.loadCode('demo', 'helper.py')).toBe('print("local")')
    expect(localStorage.getItem(GUEST_WORKSPACE_KEY)).toContain('helper.py')
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })

  it('delegates authenticated operations to the Intus API', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ projects: ['demo'] })))
    const getAccessToken = vi.fn()
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken,
    })

    expect(await storage.listProjects()).toEqual(['demo'])

    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/intus/projects', getAccessToken)
  })

  it('throws server error messages when authenticated writes fail', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ success: false, error: 'bad file' }), { status: 400 }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(storage.saveCode('demo', 'bad.py', 'print("x")')).rejects.toThrow('bad file')
  })

  it('includes HTTP status when authenticated writes fail without JSON details', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response('<h1>Server Error</h1>', {
      status: 500,
      statusText: 'Internal Server Error',
      headers: { 'Content-Type': 'text/html' },
    }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(storage.createProject('demo')).rejects.toThrow('Failed to create project demo (500 Internal Server Error)')
  })
})
