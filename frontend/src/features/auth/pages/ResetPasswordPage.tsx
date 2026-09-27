import { type FormEvent, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { updatePassword } from '../../../auth/auth.service'
import { supabase } from '../../../lib/supabase'

const MIN_PASSWORD_LENGTH = 8

export function ResetPasswordPage() {
  const navigate = useNavigate()
  const [ready, setReady] = useState(false)
  const [hasRecoverySession, setHasRecoverySession] = useState(false)
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  useEffect(() => {
    // The recovery link Supabase emails carries a token in the URL that the
    // client exchanges for a session automatically - we just need to wait
    // for that to land before trusting we're really in a recovery flow.
    supabase.auth.getSession().then(({ data }) => {
      setHasRecoverySession(Boolean(data.session))
      setReady(true)
    })
  }, [])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`)
      return
    }
    setSubmitting(true)
    try {
      await updatePassword(password)
      setSuccess(true)
      setTimeout(() => navigate('/login', { replace: true }), 2000)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not reset password. Please request a new link.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (!ready) {
    return (
      <section className="auth-page">
        <p>Loading...</p>
      </section>
    )
  }

  if (!hasRecoverySession) {
    return (
      <section className="auth-page">
        <h1>Reset link invalid</h1>
        <p>This password reset link is invalid or has expired. Please request a new one.</p>
      </section>
    )
  }

  if (success) {
    return (
      <section className="auth-page">
        <h1>Password updated</h1>
        <p>Redirecting you to sign in...</p>
      </section>
    )
  }

  return (
    <section className="auth-page">
      <h1>Choose a new password</h1>
      <form className="auth-form" onSubmit={handleSubmit}>
        <label>
          New password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={MIN_PASSWORD_LENGTH}
            required
          />
        </label>
        <label>
          Confirm new password
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
          />
        </label>
        {error && <p className="auth-error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Updating...' : 'Update password'}
        </button>
      </form>
    </section>
  )
}
