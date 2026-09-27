import { useAuth } from '../../../auth/AuthProvider'

export function ProfilePage() {
  const { appUser, error } = useAuth()

  if (error) {
    return (
      <section className="page">
        <h1>Profile</h1>
        <p className="auth-error">{error}</p>
      </section>
    )
  }

  if (!appUser) {
    return (
      <section className="page">
        <h1>Profile</h1>
        <p>Loading...</p>
      </section>
    )
  }

  return (
    <section className="page">
      <h1>Profile</h1>
      <dl className="profile-details">
        <dt>Name</dt>
        <dd>
          {appUser.first_name} {appUser.last_name}
        </dd>
        <dt>Email</dt>
        <dd>{appUser.email}</dd>
        <dt>Role</dt>
        <dd>{appUser.role}</dd>
        <dt>Account status</dt>
        <dd>{appUser.is_active ? 'Active' : 'Inactive'}</dd>
      </dl>
    </section>
  )
}
