import { type FormEvent, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { signUp } from '../../../auth/auth.service'
import { type InvitationPreview, acceptInvitation, previewInvitation } from '../../../lib/api'

const MIN_PASSWORD_LENGTH = 8

const STATUS_MESSAGES: Record<string, string> = {
  ACCEPTED: 'This invitation has already been accepted. Please sign in instead.',
  EXPIRED: 'This invitation has expired. Ask your hospital administrator to send a new one.',
  REVOKED: 'This invitation has been revoked.',
}

function titleCase(role: string): string {
  return role.charAt(0) + role.slice(1).toLowerCase()
}

export function AcceptInvitationPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState<InvitationPreview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  useEffect(() => {
    if (!token) {
      setLoadError('Missing invitation token.')
      setLoading(false)
      return
    }
    previewInvitation(token)
      .then(setPreview)
      .catch((err) => setLoadError(err instanceof Error ? err.message : 'Invitation not found.'))
      .finally(() => setLoading(false))
  }, [token])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    if (password !== confirmPassword) {
      setFormError('Passwords do not match.')
      return
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setFormError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`)
      return
    }
    setSubmitting(true)
    try {
      const { user } = await signUp({
        email,
        password,
        firstName: preview?.first_name ?? '',
        lastName: preview?.last_name ?? '',
      })
      if (!user) {
        throw new Error('Could not create your account. Please try again.')
      }
      // Role/hospital/department all come from the invitation record on the
      // backend - this call sends nothing but the id of the identity we
      // just created.
      await acceptInvitation(token, user.id)
      navigate('/login', { replace: true })
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Could not complete your account setup.')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <section className="auth-page">
        <p>Loading invitation...</p>
      </section>
    )
  }

  if (loadError || !preview) {
    return (
      <section className="auth-page">
        <h1>Invitation not found</h1>
        <p className="auth-error">{loadError ?? 'This invitation link is invalid.'}</p>
      </section>
    )
  }

  if (preview.status !== 'PENDING') {
    return (
      <section className="auth-page">
        <h1>Invitation {preview.status.toLowerCase()}</h1>
        <p>{STATUS_MESSAGES[preview.status] ?? 'This invitation can no longer be used.'}</p>
      </section>
    )
  }

  return (
    <section className="auth-page">
      <h1>Join CareSphere</h1>
      <p className="auth-subtitle">
        {preview.first_name} {preview.last_name} - invited as {titleCase(preview.role)}
        {preview.department ? ` in ${preview.department}` : ''}. Sent to {preview.email}.
      </p>
      <form className="auth-form" onSubmit={handleSubmit}>
        <label>
          Confirm your email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={MIN_PASSWORD_LENGTH}
            required
          />
        </label>
        <label>
          Confirm password
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
          />
        </label>
        {formError && <p className="auth-error">{formError}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Creating account...' : 'Create account'}
        </button>
      </form>
    </section>
  )
}
