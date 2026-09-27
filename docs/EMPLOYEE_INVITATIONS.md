# CareSphere AI — Employee Invitations

Phase 5 builds the only way a `DOCTOR`, `NURSE`, `RECEPTIONIST`, or `STAFF`
account can come into existence. There is no public employee registration
page, and there never will be one on this codebase's design - see
"Security model" below.

```
Hospital Admin (manage_users, own hospital scope)
   ↓ POST /api/admin/invitations
Invitation created (PENDING, hashed token, 7-day expiry)
   ↓ emailed (DevelopmentEmailService logs it locally - see below)
Invitee opens /accept-invitation?token=...
   ↓ GET /api/invitations/{token}            (preview, masked email)
   ↓ frontend: supabase.auth.signUp()         (invitee's own password)
   ↓ POST /api/invitations/{token}/accept     (auth_user_id only)
Supabase identity verified against the invitation's email
   ↓
users + doctors/staff row created, invitation -> ACCEPTED, all in one
PostgreSQL transaction
   ↓
Employee can log in - role/hospital/department exactly as invited
```

## Patient vs employee onboarding

Two structurally separate flows, on purpose (Phase 3 established the
patient side; this phase never touches it):

| | Patient (`/register`) | Employee (`/accept-invitation`) |
|---|---|---|
| Who initiates | The patient, unauthenticated | A `HOSPITAL_ADMIN`/`SUPER_ADMIN` with `manage_users` |
| Role | Always `PATIENT`, hard-coded server-side | One of `DOCTOR`/`NURSE`/`RECEPTIONIST`/`STAFF`, chosen by the admin, never the invitee |
| Hospital | Single seeded default (`DEFAULT_PATIENT_HOSPITAL_CODE`) | The inviting admin's own hospital scope (or, for `SUPER_ADMIN`, an explicitly named one) |
| Backend endpoint | `POST /api/auth/register` (`app/api/auth.py`) | `POST /api/invitations/{token}/accept` (`app/api/invitations.py`) |
| Identity check | Supabase Admin API cross-check on `auth_user_id` + email | Same cross-check, plus the invitation record itself |

Both flows share the same underlying pattern - the frontend creates the
Supabase identity, the backend verifies it via the Admin API before
provisioning anything - but they are separate endpoints with separate
request schemas, so a patient can never accidentally (or deliberately) end
up on the path that assigns a role.

## Allowed roles

`DOCTOR`, `NURSE`, `RECEPTIONIST`, `STAFF` - enforced at the schema level:
`CreateInvitationRequest.role` is `Literal["DOCTOR", "NURSE", "RECEPTIONIST",
"STAFF"]` (`app/schemas/invitations.py`), so `PATIENT`/`HOSPITAL_ADMIN`/
`SUPER_ADMIN` aren't valid input at all - submitting one is a `422` before
any business logic runs, not a filtered-out choice. `INVITABLE_ROLES` in
`app/services/invitation_service.py` mirrors the same set for any
programmatic caller of the service layer directly.

## Hospital scope

Every invitation is created and listed within a hospital, resolved from the
caller's own `UserScope.hospital_id` (Phase 4) - never a client-supplied
value, with one exception:

- `HOSPITAL_ADMIN` (and any other non-`SUPER_ADMIN` role with
  `manage_users`): `hospital_id` in the request is optional and, if given,
  **must equal their own scope** or the request is denied
  (`_resolve_invitation_hospital_id`, `app/services/invitation_service.py`).
  This is the same "accept it for convenience but validate it can't escape
  scope" pattern Phase 4 uses elsewhere.
- `SUPER_ADMIN`: may pass an explicit `hospital_id` to target any hospital,
  per Phase 4's existing authorization model (the same role that gets a
  hospital-scope bypass in `can_access_patient` et al.) - not a new
  special case invented for this phase.

## Department scope

If `department_id` is supplied, it must belong to the *resolved* target
hospital (`department_belongs_to_hospital`) - a `HOSPITAL_ADMIN` cannot
attach another hospital's department to an invitation into their own
hospital, and the composite foreign key
(`employee_invitations_department_hospital_fkey`, mirroring Phase 2's
pattern) makes this true at the schema level too, not just in application
code.

## Token security

- **Generation**: `secrets.token_urlsafe(32)` - 256 bits from the OS CSPRNG.
  Not a UUID, not a timestamp, not derived from the invitee's email or any
  other guessable input (`app/auth/invitation_tokens.py`).
- **Storage**: only `sha256(token)` is ever written to
  `employee_invitations.invitation_token_hash`. The raw token exists only in
  the URL handed to the invitee and is never persisted anywhere.
- **Lookup, not comparison**: acceptance hashes the presented token and does
  an indexed equality lookup (`WHERE invitation_token_hash = :hash`) rather
  than comparing raw token strings byte-by-byte in application code - there
  is no manual constant-time comparison to get right or get wrong, because
  there is no secret-value string comparison happening in Python at all.
- **Expiration**: `EMPLOYEE_INVITATION_EXPIRY_DAYS` (default 7), read from
  one place (`Settings.employee_invitation_expiry_days`) and applied when
  the invitation is created. An expired `PENDING` invitation is rejected at
  acceptance and lazily flipped to `EXPIRED` at that moment (or by
  `list_invitations_for_scope`'s opportunistic sweep before a list read) -
  see "Invitation lifecycle".

## Invitation lifecycle

```
PENDING ──accept (valid, unexpired)──▶ ACCEPTED
PENDING ──revoke (by an authorized admin)──▶ REVOKED
PENDING ──expires_at passes──▶ EXPIRED   (checked lazily, not by a cron job)
```

`ACCEPTED`/`REVOKED`/`EXPIRED` are terminal - none of them can transition
anywhere else. Rows are never deleted (`REVOKED`/`EXPIRED` stay in the
table), preserving the full history for audit. The status vocabulary is a
database `CHECK` constraint, not a free-text column.

`employee_invitations_accepted_consistency_check` additionally guarantees an
`ACCEPTED` row always has both `accepted_at` and `accepted_user_id` set, and
every other status has neither - a small schema-level invariant that keeps
"who accepted this and when" trustworthy without extra application code.

## Acceptance flow

1. Frontend reads `token` from `/accept-invitation?token=...` and calls
   `GET /api/invitations/{token}` (public - the invitee has no account yet)
   for a masked preview: name, role, department, status, expiry. The full
   email is not returned (see "Duplicate/email handling" for why the
   invitee still has to type it themselves).
2. If `status` isn't `PENDING`, the page explains why (already accepted /
   expired / revoked) and stops - no form is shown.
3. Otherwise, the invitee fills in their real email (must match the
   invitation) and chooses a password, and the frontend calls
   `supabase.auth.signUp()` directly - exactly the patient-registration
   pattern, so the backend is never in the password's path.
4. The frontend calls `POST /api/invitations/{token}/accept` with only the
   new `auth_user_id`. The backend:
   - locks the invitation row (`SELECT ... FOR UPDATE`) for the rest of
     this transaction,
   - re-checks status and expiry under that lock,
   - rejects if this `auth_user_id` is already a provisioned CareSphere
     user,
   - calls the Supabase Admin API to confirm `auth_user_id` is real and its
     email matches the invitation's (never trusting the request body's
     claim at face value - identical to patient registration's check),
   - creates `users` (role/hospital/department from the invitation, never
     from the request) plus a `doctors` or `staff` row,
   - marks the invitation `ACCEPTED`,
   - writes an `INVITATION_ACCEPTED` audit row,
   - commits.
5. The employee can now sign in normally (Phase 3's login flow, unchanged).

## Supabase account creation

The invitee's Supabase identity is created **client-side**
(`supabase.auth.signUp()`), the same as patient registration - the backend
never receives or generates a password. The backend's only Supabase
interaction is the Admin API read (`get_user_by_id`, backed by the
service-role key) used to verify the identity before provisioning -
identical infrastructure to `app/api/auth.py`'s registration endpoint, not a
second implementation.

## Application-user provisioning

```
Supabase user (created client-side, verified server-side)
   ↓
users.auth_user_id = that id
   ↓ role_id, hospital_id, department_id — all copied from the invitation row
users row created
   ↓ role == DOCTOR?
   ├── yes → doctors row (user_id, hospital_id, department_id, generated employee_number)
   └── no  → staff row  (user_id, hospital_id, department_id, generated employee_number, designation = role)
```

No `patients` row is ever created here, and `PATIENT` is not a reachable
value of `invitation.role` - employees never get a patient profile.

## Failure recovery

- **Duplicate pending invitation** (same hospital + email, still `PENDING`):
  rejected outright (`409`) - enforced by a partial unique index
  (`employee_invitations_pending_hospital_email_key`), so it's correct even
  under concurrent create requests, not just a check-then-insert race.
- **Email already belongs to a user**: rejected (`409`) before an invitation
  is even created.
- **Expired invitation presented at accept time**: rejected (`409`), and the
  row is flipped to `EXPIRED` in the same request - no separate sweep job
  needed to keep `status` accurate for that row afterward.
- **Concurrent acceptance of the same invitation**: the `SELECT ... FOR
  UPDATE` row lock serializes the two transactions - the second sees the
  now-non-`PENDING` status and fails cleanly (`409`). Proven under real
  thread concurrency against Postgres in
  `backend/tests/test_invitation_acceptance.py::test_concurrent_acceptance_exactly_one_succeeds`.
- **Supabase Admin API unreachable during accept**: `503`, and nothing is
  provisioned - the invitation stays `PENDING` and can be retried once the
  service is back.
- **Employee creates their Supabase account but never calls `/accept`**
  (abandons the flow, or the browser crashes between steps 3 and 4 above):
  the Supabase identity exists but no `users` row does. This is not a stuck
  state - the invitation is still `PENDING`, so the same link can simply be
  used again, and `supabase.auth.signUp()` for an existing-but-unconfirmed
  email is itself idempotent on Supabase's side. No reconciliation job is
  needed because nothing was partially written to CareSphere's own
  database: the `users`/`doctors`/`staff`/invitation-status writes all
  happen in one PostgreSQL transaction that only commits once every step
  (including the Supabase Admin API check) has already succeeded - so
  CareSphere's own database is never left half-provisioned. True
  cross-system atomicity between Supabase and PostgreSQL is not attempted;
  what's implemented instead is: never write partially on the PostgreSQL
  side, and make the whole flow safely retryable from the frontend when the
  Supabase side already succeeded.
- **Department deactivated/changed after an invitation is created but
  before it's accepted**: not re-validated at accept time - the invitation
  is a snapshot of the admin's decision at creation time, not a live
  reference. This is a deliberate scope limitation, not an oversight; adding
  live re-validation is straightforward future work if needed.

## Revocation

`POST /api/admin/invitations/{id}/revoke`, gated by `manage_users` plus the
same hospital-scope check as creation (a `HOSPITAL_ADMIN` cannot revoke
another hospital's invitation - `SUPER_ADMIN` can revoke any). Only a
`PENDING` invitation can be revoked (`409` otherwise - it makes no sense to
revoke something already accepted or expired). Revocation updates `status`
to `REVOKED`; the row is never deleted, so the invitation - and who created
and who revoked it - remains in the audit trail.

## Audit logging

Reuses Phase 2's `audit_logs` - no second audit table. Events added this
phase: `USER_INVITED` (on create; metadata: `email`, `role`,
`department_id`), `INVITATION_ACCEPTED` (on accept; metadata: `role` only -
the accepting user is `actor_user_id`, so their identity is already on the
row without repeating it in metadata), `INVITATION_REVOKED` (on revoke;
metadata: `email`). `INVITATION_EXPIRED` is not written as a distinct audit
event - expiry is a passive status transition (see "Invitation lifecycle"),
not an action any actor took, and audit rows model actions. Never written to
metadata: the raw token, a password, a JWT, a refresh token, or the
service-role key.

## Local development email behavior

No real email provider is integrated (`app/services/email/`). The active
implementation, `DevelopmentEmailService`, logs the invitation - including
the full URL, and therefore the raw token - at `INFO` level. This is the one
place in the codebase permitted to do that, and only because there is no
other channel to hand a developer the link locally. A real provider
implementation (added later, behind the same `EmailService` interface) must
send the email and must never log the token or URL. The invitation-create
API response also includes `invitation_url` once, to the admin who just
created it (`InvitationCreatedResponse`) - not logged, not persisted, not
returned by any other endpoint - specifically so the flow is testable
without email delivery at all.

## Security model

- **No public employee registration** exists or is planned to exist as a
  route - `app/api/invitations.py`'s two endpoints are the entire public
  surface, and neither accepts a role, hospital, or department from the
  client.
- **The client never supplies a privileged value that is trusted**: role
  comes from a closed `Literal` type; hospital/department are validated
  against the caller's own scope or rejected; `auth_user_id` is verified
  against Supabase before use; nothing resembling `user_id`,
  `invited_by_user_id`, `permission_ids`, or `status` is accepted by any
  request schema (`extra="forbid"` on both `CreateInvitationRequest` and
  `AcceptInvitationRequest`).
- **Every invitation operation reuses Phase 4's `require_permission`** -
  no separate authorization path was written for this phase.
