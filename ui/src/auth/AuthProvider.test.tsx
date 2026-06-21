import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthProvider'

function AuthProbe() {
  const auth = useAuth()

  return (
    <div>
      <span data-testid="mode">{auth.authMode}</span>
      <span data-testid="loading">{String(auth.isLoading)}</span>
      <button
        type="button"
        onClick={() => {
          void auth.getAccessToken().catch((error: unknown) => {
            const message = error instanceof Error ? error.message : String(error)
            document.body.dataset.tokenError = message
          })
        }}
      >
        token
      </button>
    </div>
  )
}

describe('AuthProvider', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.useRealTimers()
    delete document.body.dataset.tokenError
  })

  it('renders anonymous sessions as guest mode without starting login', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))

    expect(screen.getByTestId('mode')).toHaveTextContent('guest')
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', { credentials: 'same-origin' })
  })

  it('rejects access token requests when no valid user exists', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(new Response('', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))
    screen.getByText('token').click()

    await waitFor(() => expect(document.body.dataset.tokenError).toBe('Authentication required. Please sign in.'))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('retries transient auth checks before falling back to guest mode', async () => {
    vi.useFakeTimers()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        user_id: 'user-1',
        tenant_id: 'tenant-1',
        email: 'alice@example.com',
      })))
    vi.stubGlobal('fetch', fetchMock)

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    expect(screen.getByTestId('loading')).toHaveTextContent('true')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })

    expect(screen.getByTestId('loading')).toHaveTextContent('false')
    expect(screen.getByTestId('mode')).toHaveTextContent('authenticated')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
