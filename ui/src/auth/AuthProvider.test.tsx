import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthProvider'

const mocks = vi.hoisted(() => ({
  getUser: vi.fn(),
  signinRedirect: vi.fn(),
  signoutRedirect: vi.fn(),
  signinSilent: vi.fn(),
}))

vi.mock('./keycloak', () => ({
  userManager: {
    getUser: mocks.getUser,
    signinRedirect: mocks.signinRedirect,
    signoutRedirect: mocks.signoutRedirect,
    signinSilent: mocks.signinSilent,
    signinSilentCallback: vi.fn(),
    signinRedirectCallback: vi.fn(),
    events: {
      addUserLoaded: vi.fn(),
      removeUserLoaded: vi.fn(),
      addUserUnloaded: vi.fn(),
      removeUserUnloaded: vi.fn(),
      addAccessTokenExpired: vi.fn(),
      removeAccessTokenExpired: vi.fn(),
    },
  },
}))

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
    delete document.body.dataset.tokenError
  })

  it('renders anonymous sessions as guest mode without starting login', async () => {
    mocks.getUser.mockResolvedValueOnce(null)

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))

    expect(screen.getByTestId('mode')).toHaveTextContent('guest')
    expect(mocks.signinRedirect).not.toHaveBeenCalled()
  })

  it('rejects access token requests when no valid user exists', async () => {
    mocks.getUser.mockResolvedValueOnce(null).mockResolvedValueOnce(null)

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))
    screen.getByText('token').click()

    await waitFor(() => expect(document.body.dataset.tokenError).toBe('Authentication required. Please sign in.'))
    expect(mocks.signinSilent).not.toHaveBeenCalled()
  })
})
