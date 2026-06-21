import { beforeEach, describe, expect, it, vi } from 'vitest'

async function loadClient() {
  vi.resetModules()
  return import('./client')
}

describe('apiFetch', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
    sessionStorage.clear()
    Object.defineProperty(document, 'cookie', {
      writable: true,
      value: '',
    })
  })

  it('backs off read polling after transient server failures', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('', { status: 502, statusText: 'Bad Gateway' }))
    vi.stubGlobal('fetch', fetchMock)
    const { apiFetch } = await loadClient()
    const getAccessToken = vi.fn().mockResolvedValue('token')

    const first = await apiFetch('/api/intus/project_name', getAccessToken)
    const second = await apiFetch('/api/intus/project_name', getAccessToken)

    expect(first.status).toBe(502)
    expect(second.status).toBe(503)
    expect(await second.json()).toMatchObject({ error: 'Backend is recovering; polling paused briefly.' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getAccessToken).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledWith('/api/intus/project_name', expect.objectContaining({
      credentials: 'same-origin',
    }))
  })

  it('does not suppress mutating requests during read polling backoff', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 502, statusText: 'Bad Gateway' }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: true })))
    vi.stubGlobal('fetch', fetchMock)
    const { apiFetch } = await loadClient()
    const getAccessToken = vi.fn().mockResolvedValue('token')
    document.cookie = 'tertius_csrf=csrf-token'

    await apiFetch('/api/intus/project_name', getAccessToken)
    const postResponse = await apiFetch('/api/intus/projects/shed/activate', getAccessToken, { method: 'POST' })

    expect(postResponse.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(getAccessToken).not.toHaveBeenCalled()
    const postInit = fetchMock.mock.calls[1]![1] as RequestInit
    expect(postInit.credentials).toBe('same-origin')
    expect(new Headers(postInit.headers).get('X-CSRF-Token')).toBe('csrf-token')
  })

  it('deduplicates concurrent readonly requests for the same endpoint', async () => {
    let resolveFetch: (response: Response) => void = () => {}
    const fetchMock = vi.fn().mockReturnValueOnce(new Promise<Response>((resolve) => {
      resolveFetch = resolve
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { apiFetch } = await loadClient()
    const getAccessToken = vi.fn().mockResolvedValue('token')

    const first = apiFetch('/api/intus/project_name', getAccessToken)
    const second = apiFetch('/api/intus/project_name', getAccessToken)
    resolveFetch(new Response(JSON.stringify({ project_name: 'default_purlin' })))

    await expect(first).resolves.toBeInstanceOf(Response)
    await expect(second).resolves.toBeInstanceOf(Response)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getAccessToken).not.toHaveBeenCalled()
  })

  it('does not force login for a workflow 401 when the cookie session is still valid', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Missing authentication' }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ authenticated: true })))
    vi.stubGlobal('fetch', fetchMock)
    const { apiFetch } = await loadClient()
    const getAccessToken = vi.fn().mockResolvedValue('token')

    const response = await apiFetch('/api/intus/project_name', getAccessToken)

    expect(response.status).toBe(401)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/me', { credentials: 'same-origin' })
    expect(sessionStorage.getItem('tertius:stale-token-redirecting')).toBeNull()
  })

  it('keeps the current page when session revalidation is temporarily unavailable', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Authentication expired' }), { status: 401 }))
      .mockResolvedValueOnce(new Response('', { status: 503 }))
    vi.stubGlobal('fetch', fetchMock)
    const { apiFetch } = await loadClient()
    const getAccessToken = vi.fn().mockResolvedValue('token')

    const response = await apiFetch('/api/intus/project_name', getAccessToken)

    expect(response.status).toBe(401)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(sessionStorage.getItem('tertius:stale-token-redirecting')).toBeNull()
  })
})
