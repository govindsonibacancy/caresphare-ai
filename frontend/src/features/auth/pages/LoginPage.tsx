import { type FormEvent, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { signIn } from '../../../auth/auth.service'

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const state = location.state as { from?: string } | null
  const redirectTo = state?.from ?? '/chat'

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await signIn(email, password)
      navigate(redirectTo, { replace: true })
    } catch (err) {
      setError(describeLoginError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="auth-page">
      <h1>Sign in</h1>
      <form className="auth-form" onSubmit={handleSubmit}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="auth-error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
      <p>
        <Link to="/forgot-password">Forgot your password?</Link>
      </p>
      <p>
        Need a patient account? <Link to="/register">Register</Link>
      </p>
    </section>
  )
}

function describeLoginError(err: unknown): string {
  const message = err instanceof Error ? err.message.toLowerCase() : ''
  if (message.includes('email not confirmed')) {
    return 'Please verify your email address before signing in.'
  }
  if (message.includes('invalid login credentials')) {
    return 'Incorrect email or password.'
  }
  return 'Unable to sign in right now. Please try again.'
}
