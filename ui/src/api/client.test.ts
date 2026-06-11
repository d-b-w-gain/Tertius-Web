import { beforeEach, describe, expect, it, vi } from 'vitest'

async function loadClient() {
  vi.resetModules()
  return import('./client')
}

describe('apiFetch', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
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
    expect(getAccessToken).toHaveBeenCalledTimes(1)
  })

  it('does not suppress mutating requests during read polling backoff', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 502, statusText: 'Bad Gateway' }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: true })))
    vi.stubGlobal('fetch', fetchMock)
    const { apiFetch } = await loadClient()
    const getAccessToken = vi.fn().mockResolvedValue('token')

    await apiFetch('/api/intus/project_name', getAccessToken)
    const postResponse = await apiFetch('/api/intus/projects/shed/activate', getAccessToken, { method: 'POST' })

    expect(postResponse.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(getAccessToken).toHaveBeenCalledTimes(2)
  })
})
