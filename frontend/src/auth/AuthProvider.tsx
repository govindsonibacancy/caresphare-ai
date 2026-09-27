import type { Session } from '@supabase/supabase-js'
import { type ReactNode, createContext, useCallback, useContext, useEffect, useState } from 'react'
import { type CurrentUser, fetchAuthorizationContext, fetchCurrentUser } from '../lib/api'
import { supabase } from '../lib/supabase'

interface AuthContextValue {
  loading: boolean
  session: Session | null
  appUser: CurrentUser | null
  /** UX-only - see auth/permissions.ts. null while loading or unavailable. */
  permissions: string[] | null
  error: string | null
  refreshAppUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true)
  const [session, setSession] = useState<Session | null>(null)
  const [appUser, setAppUser] = useState<CurrentUser | null>(null)
  const [permissions, setPermissions] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadAppUser = useCallback(async () => {
    try {
      const user = await fetchCurrentUser()
      setAppUser(user)
      setError(null)
    } catch (err) {
      setAppUser(null)
      setError(err instanceof Error ? err.message : 'Failed to load account')
      setPermissions(null)
      return
    }
    // Best-effort: a failure here shouldn't block the rest of the app, it
    // just means UX-only permission checks fall back to "hide it".
    try {
      const context = await fetchAuthorizationContext()
      setPermissions(context.permissions)
    } catch {
      setPermissions(null)
    }
  }, [])

  useEffect(() => {
    let active = true

    supabase.auth.getSession().then(async ({ data }) => {
      if (!active) return
      setSession(data.session)
      if (data.session) {
        await loadAppUser()
      }
      if (active) setLoading(false)
    })

    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
      if (nextSession) {
        void loadAppUser()
      } else {
        setAppUser(null)
        setPermissions(null)
        setError(null)
      }
    })

    return () => {
      active = false
      listener.subscription.unsubscribe()
    }
  }, [loadAppUser])

  const value: AuthContextValue = { loading, session, appUser, permissions, error, refreshAppUser: loadAppUser }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}
