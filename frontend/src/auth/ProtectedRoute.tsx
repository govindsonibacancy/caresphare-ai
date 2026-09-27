import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthProvider'

/**
 * UX-only gate: redirects an unauthenticated browser away from protected
 * pages. It is not the security boundary - the backend independently
 * requires a valid, verified bearer token on every protected API call (see
 * backend/app/auth/dependencies.py). A user who bypasses this route still
 * can't get data the backend wouldn't otherwise return.
 */
export function ProtectedRoute() {
  const { loading, session } = useAuth()
  const location = useLocation()

  if (loading) {
    return <div className="page">Loading...</div>
  }
  if (!session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <Outlet />
}
