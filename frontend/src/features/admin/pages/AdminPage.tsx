import { type FormEvent, useCallback, useEffect, useState } from 'react'
import {
  type CreateInvitationInput,
  type Department,
  type InvitationSummary,
  type InviteableRole,
  createInvitation,
  listDepartments,
  listInvitations,
  revokeInvitation,
} from '../../../lib/api'

const INVITEABLE_ROLES: InviteableRole[] = ['DOCTOR', 'NURSE', 'RECEPTIONIST', 'STAFF']
const STATUS_FILTERS = ['ALL', 'PENDING', 'ACCEPTED', 'EXPIRED', 'REVOKED'] as const

export function AdminPage() {
  const [invitations, setInvitations] = useState<InvitationSummary[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>('ALL')
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)

  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<InviteableRole>('DOCTOR')
  const [departmentId, setDepartmentId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [lastInvitationUrl, setLastInvitationUrl] = useState<string | null>(null)

  const departmentName = useCallback(
    (id: string | null) => (id ? (departments.find((d) => d.id === id)?.name ?? id) : '-'),
    [departments],
  )

  const refresh = useCallback(async () => {
    setLoading(true)
    setListError(null)
    try {
      const filters = statusFilter === 'ALL' ? {} : { status: statusFilter }
      const [invitationsResult, departmentsResult] = await Promise.all([
        listInvitations(filters),
        listDepartments(),
      ])
      setInvitations(invitationsResult)
      setDepartments(departmentsResult)
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Failed to load invitations.')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function handleInvite(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setSubmitting(true)
    try {
      const input: CreateInvitationInput = {
        email,
        first_name: firstName,
        last_name: lastName,
        role,
        department_id: departmentId || null,
      }
      const created = await createInvitation(input)
      setLastInvitationUrl(created.invitation_url)
      setFirstName('')
      setLastName('')
      setEmail('')
      setDepartmentId('')
      await refresh()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Could not create invitation.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRevoke(id: string) {
    try {
      await revokeInvitation(id)
      await refresh()
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Could not revoke invitation.')
    }
  }

  return (
    <section className="page">
      <h1>Employee invitations</h1>
      <p>
        Invite doctors, nurses, receptionists, and staff. Patients register
        themselves at <code>/register</code> - this is only for hospital
        employees, and the role/hospital are fixed by this form, never by
        the invitee.
      </p>

      <form className="auth-form invite-form" onSubmit={handleInvite}>
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
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as InviteableRole)}>
            {INVITEABLE_ROLES.map((r) => (
              <option key={r} value={r}>
                {r.charAt(0) + r.slice(1).toLowerCase()}
              </option>
            ))}
          </select>
        </label>
        <label>
          Department (optional)
          <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
            <option value="">None</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        {formError && <p className="auth-error">{formError}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Sending invitation...' : 'Send invitation'}
        </button>
      </form>

      {lastInvitationUrl && (
        <p className="invite-url-note">
          No email provider is configured in this demo - here is the invitation link:
          <br />
          <code>{lastInvitationUrl}</code>
        </p>
      )}

      <div className="invite-filters">
        <label>
          Status
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}>
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      {listError && <p className="auth-error">{listError}</p>}
      {loading ? (
        <p>Loading...</p>
      ) : (
        <table className="invite-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Department</th>
              <th>Status</th>
              <th>Expires</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {invitations.map((invitation) => (
              <tr key={invitation.id}>
                <td>
                  {invitation.first_name} {invitation.last_name}
                </td>
                <td>{invitation.email}</td>
                <td>{invitation.role}</td>
                <td>{departmentName(invitation.department_id)}</td>
                <td>{invitation.status}</td>
                <td>{new Date(invitation.expires_at).toLocaleDateString()}</td>
                <td>
                  {invitation.status === 'PENDING' && (
                    <button type="button" onClick={() => handleRevoke(invitation.id)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {invitations.length === 0 && (
              <tr>
                <td colSpan={7}>No invitations found.</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </section>
  )
}
