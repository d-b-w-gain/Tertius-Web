import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

export interface AuthState {
  user: AuthUser | null
  token: string | null
  authMode: 'guest' | 'authenticated'
  isLoading: boolean
  getAccessToken: () => Promise<string>
  login: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

interface AuthUser {
  user_id: string
  tenant_id: string
  email: string | null
}

const currentReturnTo = () => `${window.location.pathname}${window.location.search}${window.location.hash}`

async function fetchCurrentUser(): Promise<AuthUser | null> {
  const response = await fetch('/api/auth/me', { credentials: 'same-origin' })
  if (response.status === 401) return null
  if (!response.ok) throw new Error(`Authentication check failed with status ${response.status}`)
  return response.json()
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const refreshPromiseRef = useRef<Promise<AuthUser | null> | null>(null)

  useEffect(() => {
    let isMounted = true

    const loadUser = async () => {
      try {
        if (isMounted) {
          setUser(await fetchCurrentUser())
        }
      } catch (e) {
        console.warn('Authentication check failed:', e)
        if (isMounted) setUser(null)
      } finally {
        if (isMounted) {
          setIsLoading(false)
        }
      }
    }

    void loadUser()

    return () => {
      isMounted = false
    }
  }, [])

  const login = useCallback(async () => {
    window.location.assign(`/api/auth/login?return_to=${encodeURIComponent(currentReturnTo())}`)
  }, [])

  const logout = useCallback(async () => {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': document.cookie.match(/(?:^|; )tertius_csrf=([^;]+)/)?.[1] ?? '' },
    })
    setUser(null)
  }, [])

  const getAccessToken = useCallback(async () => {
    if (user) return ''

    if (refreshPromiseRef.current) {
      const refreshed = await refreshPromiseRef.current
      if (refreshed) return ''
      throw new Error('Authentication required. Please sign in.')
    }

    refreshPromiseRef.current = fetchCurrentUser()
    try {
      const refreshed = await refreshPromiseRef.current
      setUser(refreshed)
      if (refreshed) return ''
      throw new Error('Authentication required. Please sign in.')
    } finally {
      refreshPromiseRef.current = null
    }
  }, [user])

  const value = useMemo<AuthState>(
    () => ({
      user,
      token: null,
      authMode: user ? 'authenticated' : 'guest',
      isLoading,
      getAccessToken,
      login,
      logout,
    }),
    [getAccessToken, isLoading, login, logout, user],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used inside AuthProvider')
  }
  return ctx
}
