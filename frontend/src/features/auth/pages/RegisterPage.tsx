import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { signUp } from '../../../auth/auth.service'
import { registerPatient } from '../../../lib/api'

const MIN_PASSWORD_LENGTH = 8

export function RegisterPage() {
  const navigate = useNavigate()
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      const { user } = await signUp({ email, password, firstName, lastName })
      if (!user) {
        throw new Error('Registration did not return an account. Please try again.')
      }
      await registerPatient({
        auth_user_id: user.id,
        email,
        first_name: firstName,
        last_name: lastName,
      })
      navigate('/verify-email', { state: { email } })
    } catch (err) {
      setError(describeRegistrationError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="auth-page">
      <h1>Create your patient account</h1>
      <p className="auth-subtitle">
        Accounts created here are patient accounts. Hospital staff accounts
        are set up by your hospital administrator.
      </p>
      <form className="auth-form" onSubmit={handleSubmit}>
        <label>
          First name
          <input value={firstName} onChange={(e) => setFirstName(e.target.value)} required />
        </label>
        <label>
          Last name
          <input value={lastName} onChange={(e) => setLastName(e.target.value)} required />
        </label>
        <label>
          Email
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
        {error && <p className="auth-error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Creating account...' : 'Create account'}
        </button>
      </form>
      <p>
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </section>
  )
}

function describeRegistrationError(err: unknown): string {
  const message = err instanceof Error ? err.message : ''
  const lower = message.toLowerCase()
  if (lower.includes('failed to fetch') || lower.includes('networkerror') || lower.includes('load failed')) {
    return 'Unable to reach the authentication service. Please check your connection and try again.'
  }
  if (lower.includes('already registered') || lower.includes('already exists')) {
    return 'An account with this email already exists.'
  }
  return message || 'Registration failed. Please try again.'
}
