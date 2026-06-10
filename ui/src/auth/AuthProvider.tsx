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
import type { User } from 'oidc-client-ts'
import { userManager } from './keycloak'

export interface AuthState {
  user: User | null
  token: string | null
  authMode: 'guest' | 'authenticated'
  isLoading: boolean
  getAccessToken: () => Promise<string>
  login: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

const hasSigninCallbackParams = () => {
  const params = new URLSearchParams(window.location.search)
  return params.has('code') || params.has('error')
}

const clearSigninCallbackParams = () => {
  window.history.replaceState({}, document.title, `${window.location.pathname}${window.location.hash}`)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const silentSigninPromiseRef = useRef<Promise<User | null> | null>(null)

  useEffect(() => {
    let isMounted = true

    const loadUser = async () => {
      try {
        // Handle silent renew callback in iframe
        if (window.self !== window.top) {
          try {
             await userManager.signinSilentCallback()
          } catch (e) {
             console.error("Silent callback error:", e)
          }
          return
        }

        if (hasSigninCallbackParams()) {
          try {
            const callbackUser = await userManager.signinRedirectCallback()
            if (isMounted) {
              setUser(callbackUser)
            }
          } catch (e) {
            console.warn("Signin callback error (likely StrictMode double-fire):", e)
            const storedUser = await userManager.getUser()
            if (storedUser && !storedUser.expired && isMounted) {
               setUser(storedUser)
            }
          } finally {
            clearSigninCallbackParams()
          }
          return
        }

        const storedUser = await userManager.getUser()
        if (isMounted) {
          setUser(storedUser && !storedUser.expired ? storedUser : null)
        }
      } finally {
        if (isMounted && window.self === window.top) {
          setIsLoading(false)
        }
      }
    }

    const onUserLoaded = (loadedUser: User) => setUser(loadedUser)
    const onUserUnloaded = () => setUser(null)

    userManager.events.addUserLoaded(onUserLoaded)
    userManager.events.addUserUnloaded(onUserUnloaded)
    userManager.events.addAccessTokenExpired(onUserUnloaded)
    void loadUser()

    return () => {
      isMounted = false
      userManager.events.removeUserLoaded(onUserLoaded)
      userManager.events.removeUserUnloaded(onUserUnloaded)
      userManager.events.removeAccessTokenExpired(onUserUnloaded)
    }
  }, [])

  const login = useCallback(() => userManager.signinRedirect(), [])
  const logout = useCallback(() => userManager.signoutRedirect(), [])

  const getAccessToken = useCallback(async () => {
    const current = await userManager.getUser()
    if (current && !current.expired) {
      return current.access_token
    }

    if (user) {
      if (silentSigninPromiseRef.current) {
        const renewed = await silentSigninPromiseRef.current
        if (renewed) return renewed.access_token
        throw new Error('Concurrent silent sign in failed')
      }

      try {
        silentSigninPromiseRef.current = userManager.signinSilent()
        const renewed = await silentSigninPromiseRef.current
        if (!renewed) {
          throw new Error('Silent sign-in did not return a user')
        }
        setUser(renewed)
        return renewed.access_token
      } catch (e) {
        console.error('Silent token refresh failed:', e)
        throw new Error('Authentication expired. Please sign in again.', { cause: e })
      } finally {
        silentSigninPromiseRef.current = null
      }
    }

    throw new Error('Authentication required. Please sign in.')
  }, [user])

  const value = useMemo<AuthState>(
    () => ({
      user,
      token: user?.access_token ?? null,
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
