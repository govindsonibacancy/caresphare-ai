import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { requestPasswordReset } from '../../../auth/auth.service'

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await requestPasswordReset(email)
    } finally {
      setSubmitting(false)
      // Always show the same confirmation whether or not the email is
      // registered - Supabase itself does not reveal that either.
      setSubmitted(true)
    }
  }

  if (submitted) {
    return (
      <section className="auth-page">
        <h1>Check your email</h1>
        <p>If an account exists for that email, we've sent a password reset link.</p>
        <Link to="/login">Return to login</Link>
      </section>
    )
  }

  return (
    <section className="auth-page">
      <h1>Reset your password</h1>
      <form className="auth-form" onSubmit={handleSubmit}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <button type="submit" disabled={submitting}>
          {submitting ? 'Sending...' : 'Send reset link'}
        </button>
      </form>
      <p>
        <Link to="/login">Return to login</Link>
      </p>
    </section>
  )
}
