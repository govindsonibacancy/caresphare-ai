import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { resendVerificationEmail } from '../../../auth/auth.service'

type ResendStatus = 'idle' | 'sending' | 'sent' | 'error'

export function VerifyEmailPage() {
  const location = useLocation()
  const email = (location.state as { email?: string } | null)?.email ?? null
  const [status, setStatus] = useState<ResendStatus>('idle')

  async function handleResend() {
    if (!email) return
    setStatus('sending')
    try {
      await resendVerificationEmail(email)
      setStatus('sent')
    } catch {
      setStatus('error')
    }
  }

  return (
    <section className="auth-page">
      <h1>Check your email</h1>
      <p>
        Your account has been created. Please check your email and verify
        your account before signing in.
      </p>
      {email && (
        <button type="button" onClick={handleResend} disabled={status === 'sending'}>
          {status === 'sending' ? 'Resending...' : 'Resend verification email'}
        </button>
      )}
      {status === 'sent' && <p>Verification email resent.</p>}
      {status === 'error' && (
        <p className="auth-error">Could not resend the email. Please try again shortly.</p>
      )}
      <p>
        <Link to="/login">Return to login</Link>
      </p>
    </section>
  )
}
