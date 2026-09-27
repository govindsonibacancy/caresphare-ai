import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthProvider'
import { signOut } from '../../auth/auth.service'
import { hasPermission } from '../../auth/permissions'
import { fetchHealth } from '../../lib/api'

const NAV_ITEMS = [
  { to: '/chat', label: 'Chat' },
  { to: '/documents', label: 'Documents' },
  // These two are UX-only gates (see auth/permissions.ts) - hidden when the
  // permission is absent, but the backend enforces its own check
  // independently once those pages call real APIs in a later phase.
  { to: '/audit-logs', label: 'Audit logs', requiresPermission: 'view_hospital_analytics' },
  { to: '/admin', label: 'Admin', requiresPermission: 'manage_users' },
  { to: '/admin/documents', label: 'Manage documents', requiresPermission: 'manage_hospital_documents' },
  { to: '/profile', label: 'Profile' },
]

type BackendStatus = 'checking' | 'online' | 'offline'

export function AppShell() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>('checking')
  const { appUser, permissions } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    fetchHealth()
      .then(() => {
        if (!cancelled) setBackendStatus('online')
      })
      .catch(() => {
        if (!cancelled) setBackendStatus('offline')
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSignOut() {
    await signOut()
    navigate('/login', { replace: true })
  }

  const visibleNavItems = NAV_ITEMS.filter(
    (item) => !item.requiresPermission || hasPermission(permissions, item.requiresPermission),
  )

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title">CareSphere AI</span>
        <div className="app-header-right">
          {appUser && (
            <span className="app-user">
              {appUser.first_name} {appUser.last_name}
            </span>
          )}
          <span className={`backend-status backend-status--${backendStatus}`}>
            backend: {backendStatus}
          </span>
          <button type="button" className="sign-out-button" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </header>
      <div className="app-body">
        <nav className="app-nav">
          {visibleNavItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? 'nav-link nav-link--active' : 'nav-link')}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <main className="app-content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
