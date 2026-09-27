# CareSphere AI — Authorization

This document describes the RBAC + permission engine built in Phase 4. It
implements the middle of the request flow in
[docs/ARCHITECTURE.md](ARCHITECTURE.md):

```
Authentication  →  Authorization  →  Data Scope  →  RAG / DB Retrieval → ...
     (Phase 3)        (Phase 4)
```

No clinical API, RAG, or LLM code exists yet (Phases 5-8) - this phase is
the engine those will call, exercised here through direct unit/integration
tests and one small internal endpoint (`GET /api/authz/me`).

## Authentication vs authorization

These are two different questions, answered by two different layers, and
CareSphere keeps them in two different HTTP status codes:

| | Question | Answered by | Status on failure |
|---|---|---|---|
| **Authentication** | Who is making this request? | `app/auth/` (Phase 3): verify the Supabase JWT, resolve it to a `users` row | `401` |
| **Authorization** | Is that person allowed to do *this*? | `app/permissions/` (Phase 4): role → permissions → hospital/department/resource scope | `403` |

A `401` means the request carries no valid, verifiable identity at all -
missing header, invalid signature, expired token. A `403` means the
identity is valid and verified, but the thing being asked for isn't
authorized - missing permission, wrong hospital, unassigned doctor,
inactive account. **Phase 4 never turns an authorization failure into a
401**, and never turns an authentication failure into a 403; conflating them
would let a client distinguish "you don't exist" from "you're not allowed",
which is exactly the kind of detail a 403 is supposed to avoid leaking (see
"What a 403 does and doesn't reveal" below).

`require_permission()` (`app/permissions/dependencies.py`) is built directly
on top of Phase 3's `get_current_auth_user` rather than re-implementing any
part of it - authentication and the active-account check stay exactly where
Phase 3 put them; Phase 4 only adds the permission/scope layer on top.

## Roles

The seven roles from Phase 2, unchanged:

`PATIENT`, `DOCTOR`, `NURSE`, `RECEPTIONIST`, `STAFF`, `HOSPITAL_ADMIN`,
`SUPER_ADMIN` (`app/permissions/roles.py`).

## Permissions

Nineteen permission codes, seeded in `database/seeds/0001_demo_data.sql` and
mirrored as typed constants in `app/permissions/permissions.py`
(`Permission.VIEW_OWN_PROFILE` etc.) so application code references a name,
not a string literal. The constants are just a vocabulary - they carry no
information about which role has which permission.

## Role ↔ permission relationship

**Never hard-coded.** `role_permissions` (Phase 2) is the only source of
truth. `resolve_user_scope()` (`app/permissions/scope.py`) queries it fresh
on every request:

```sql
SELECT p.code FROM role_permissions rp
JOIN roles r ON r.id = rp.role_id
JOIN permissions p ON p.id = rp.permission_id
WHERE r.name = :role_name
```

`backend/tests/test_permission_engine.py::test_permission_mutation_changes_has_permission_result`
proves this concretely: it deletes a `role_permissions` row mid-test,
observes `has_permission()` flip from `True` to `False` with no code change
or process restart, then restores the row and observes it flip back.

## User scope

`UserScope` (`app/permissions/scope.py`) is what the rest of the engine
authorizes against - built once per request, entirely from the database,
never from client input:

```python
@dataclass(frozen=True)
class UserScope:
    user_id: uuid.UUID
    auth_user_id: uuid.UUID
    role: Role
    permissions: frozenset[str]
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    patient_id: uuid.UUID | None   # set only for PATIENT
    doctor_id: uuid.UUID | None    # set only for DOCTOR
    staff_id: uuid.UUID | None     # set only for NURSE/RECEPTIONIST/STAFF
```

`resolve_user_scope(db, current_user)` takes the `AuthenticatedUser` Phase 3
already resolved from the verified JWT and looks up everything else:
`role_permissions` for the permission set, and `patients`/`doctors`/`staff`
(by `user_id`, each of which carries a unique constraint - `patients.user_id`
as of `0015_patients_user_id_unique.sql`, added in this phase for exactly
this lookup to be well-defined) for the role-specific id.

## Role-specific scope

| Role | `hospital_id` | `department_id` | `patient_id` | `doctor_id` | `staff_id` |
|---|---|---|---|---|---|
| PATIENT | ✓ | - | ✓ | - | - |
| DOCTOR | ✓ | ✓ | - | ✓ | - |
| NURSE | ✓ | ✓ | - | - | ✓ |
| RECEPTIONIST | ✓ | ✓ | - | - | ✓ |
| STAFF | ✓ | ✓ | - | - | ✓ |
| HOSPITAL_ADMIN | ✓ | ✓ | - | - | - |
| SUPER_ADMIN | ✓ | ✓ (may be null) | - | - | - |

## Hospital scope

Every hospital-scoped resource check compares `resource.hospital_id ==
scope.hospital_id`, derived from the resource itself (looked up in the
database), never from a client-supplied `hospital_id` - there is no code
path anywhere in `app/permissions/` that reads a hospital id from a request.
`SUPER_ADMIN` is the one deliberate, explicit exception (see "SUPER_ADMIN"
below) - every other role is hospital-bound.

`backend/tests/test_resource_authorization.py`'s hospital-isolation tests
create a *second* hospital and patient at test time specifically to prove
this at the application-authorization layer - Phase 2's composite foreign
keys already prevent a row from *existing* with mismatched hospital ids;
this phase proves the authorization *code* independently rejects
cross-hospital access even when the data itself is valid.

## Department scope

Not every resource type needs department matching - this phase defines it
per resource, not as a blanket rule:

- **Appointments**, for `RECEPTIONIST`/`STAFF`: `scope.department_id ==
  appointment.department_id`. Front-desk and department staff work within
  one department; a Pharmacy staff member does not see Cardiology's
  schedule.
- **Appointments**, for `HOSPITAL_ADMIN`: hospital-wide, not
  department-limited - administrators oversee the whole hospital.
- **Patients, medical records, lab reports, prescriptions**: no department
  check anywhere. Clinical resource access is governed by the doctor-patient
  assignment (or, for `PATIENT`, ownership) instead - see "Patient
  ownership" and "Doctor-patient assignment" below.

## Patient ownership

A `PATIENT`'s scope carries exactly one `patient_id` (their own). Resource
checks for that role are a single equality: `scope.patient_id ==
resource.patient_id`. There is no other patient a `PATIENT` scope can ever
match - not through a resource id in a request, not through any permission.

## Doctor-patient assignment

A `DOCTOR`'s access to a specific patient's resources requires **both**:

1. the resource's hospital matches the doctor's hospital, and
2. an **active** row in `doctor_patient_assignments` for
   `(scope.doctor_id, patient_id)`.

Same-hospital membership alone is explicitly *not* sufficient - two doctors
in the same hospital do not see each other's unassigned patients. A past
(`is_active = false`) assignment does not grant current access either;
`has_active_assignment()` filters on `is_active` at the SQL level, so a
closed-out assignment behaves identically to one that never existed.

Access to a medical record, lab report, or prescription is **not** governed
by who authored it (`medical_records.doctor_id` etc.) - it's governed by
whether the requesting doctor is assigned to the record's *patient*. A care
team sharing a patient can all see that patient's records once assigned;
`can_access_medical_record()` never reads `medical_records.doctor_id` for
this reason.

## Resource authorization functions

`app/permissions/authorization.py`:

```python
can_access_patient(db, scope, patient_id) -> bool
can_access_appointment(db, scope, appointment_id) -> bool
can_access_medical_record(db, scope, record_id) -> bool
can_access_lab_report(db, scope, report_id) -> bool
can_access_prescription(db, scope, prescription_id) -> bool
```

Each resolves the resource's true owner from the database first (never
trusting the id as self-proving) and then applies the role-specific rule
above. `authorize(db, scope, permission, resource=(kind, id))` composes a
permission check with one of these - see "Permission + scope" below for why
both are always required together.

## SUPER_ADMIN

No `if role == SUPER_ADMIN: allow_everything()` exists anywhere in this
codebase. `SUPER_ADMIN`'s actual behavior is entirely the composition of:

- **Permission**: whatever `role_permissions` grants it - today that's
  `view_own_profile`, `manage_users`, `manage_roles`, `manage_departments`,
  `manage_hospital_documents`, `view_hospital_operations`,
  `view_hospital_analytics`. It does **not** include
  `view_patient_medical_records`, so a `SUPER_ADMIN` cannot read medical
  records under the current seed, despite being the "highest" role.
- **Resource relationship**: the one place `SUPER_ADMIN` gets explicit
  special-case code is the hospital-scope check in each `can_access_*`
  function (`if scope.role is Role.SUPER_ADMIN: return True`, before the
  hospital comparison) - representing that this role is the one genuinely
  cross-hospital scope in the system. It is still gated by whether
  `role_permissions` grants the relevant permission in the first place.

Extending `SUPER_ADMIN` (or any role) to a new permission is a
`role_permissions` data change, not a code change.

## Permission + scope

Holding a permission does not imply access to every resource of that type.
The canonical example, straight from the seed and the test suite:

```
Doctor A has permission: view_patient_medical_records
Doctor A is NOT assigned to Patient C

Doctor A → Patient C's medical record → DENY
```

`authorize()` always checks both, in order - permission first (cheap, no
resource lookup needed), then the resource relationship:

```python
def authorize(db, scope, permission, *, resource=None):
    if not has_permission(scope, permission):
        raise PermissionDenied(...)
    if resource is not None:
        kind, resource_id = resource
        if not _RESOURCE_CHECKS[kind](db, scope, resource_id):
            raise PermissionDenied(...)
```

## What a 403 does and doesn't reveal

`require_permission`'s `HTTPException(403, ...)` always uses the same
generic message ("You do not have permission to perform this action.")
regardless of *why* - missing permission, wrong hospital, no assignment, or
(further upstream, in Phase 3's `get_current_auth_user`) an inactive
account. A caller cannot distinguish "this patient doesn't exist", "this
patient exists but isn't yours", and "you don't have this permission at
all" from the response. `PermissionDenied.reason` (the specific cause) is
for logs/tests, never serialized into the HTTP response.

## Frontend vs backend security

`ProtectedRoute` (Phase 3) and `hasPermission()` / `AppShell`'s nav
filtering (`frontend/src/auth/permissions.ts`, this phase) are both **UX
only**. They exist so the UI doesn't show a "Manage users" link to someone
who'd immediately get a 403 clicking it - nothing more. Every protected
backend operation independently re-derives the caller's permission and
scope from the database on every request; a user who edits `localStorage`,
disables JavaScript, or calls the API directly with devtools gains exactly
nothing, because the frontend was never part of the enforcement.

## `/api/auth/me` vs `/api/authz/me`

Phase 3's `GET /api/auth/me` (identity: name, email, role, hospital,
`is_active`) is unchanged by this phase. `GET /api/authz/me` (this phase) is
a separate, deliberately minimal endpoint returning `role`, `hospital_id`,
`department_id`, and the caller's resolved `permissions` list - the Phase 4
instructions' suggested internal verification surface for the permission
engine, and what the frontend's `AuthProvider` calls to populate
`hasPermission()`. It takes no input from the client (no body, no query
parameters are read), so there is nothing for a client to manipulate into a
different answer -
`backend/tests/test_authz_endpoint.py::test_authz_me_ignores_client_supplied_identity`
sends `role=SUPER_ADMIN`/`hospital_id=...`/`patient_id=...` as query
parameters and asserts the response is unaffected.

It is not a clinical data API and doesn't become the foundation for one;
Phase 6 will build real resource endpoints against `authorize()` and the
`can_access_*` functions directly.

## Example allow/deny cases (from the seed and the test suite)

Doctor A (`CGH-D-0001`) is assigned to Patient A and Patient B; Doctor B
(`CGH-D-0002`) is assigned only to Patient C:

```
Patient A → own profile/appointments/reports/prescriptions   ALLOW
Patient A → Patient B's appointment/lab report/prescription  DENY

Doctor A → Patient A medical record      ALLOW
Doctor A → Patient B medical record      ALLOW
Doctor A → Patient C medical record      DENY   (not assigned)

Doctor B → Patient C lab report          ALLOW
Doctor B → Patient A prescription        DENY   (not assigned)

Hospital A doctor → Hospital B patient   DENY   (hospital isolation)
SUPER_ADMIN → patient in any hospital    ALLOW  (can_access_patient), but
                                           still gated by role_permissions
                                           for the actual permission used
```

## Limitations

**No nurse-patient assignment table.** `NURSE` has `view_assigned_patients`
and `view_vital_records` in the seed, but Phase 2's schema has no
nurse-to-patient relationship table analogous to
`doctor_patient_assignments`. `can_access_patient()` therefore returns
`False` for every `NURSE` scope rather than inventing an implicit
hospital-wide or department-wide grant - those two permissions currently
cannot be exercised against any specific patient. If nurse-patient
assignment is needed, it should be a new migration (e.g.
`nurse_patient_assignments`, mirroring `doctor_patient_assignments`) plus a
new branch in the `can_access_*` functions - not a change to any existing
migration, and not an implicit grant added to work around the missing
table.

**`RECEPTIONIST`/`STAFF` have no clinical resource access at all** in
`can_access_patient`/`can_access_medical_record`/etc. - only the
department-scoped appointment access described above. This matches the
seed, which grants neither role any clinical permission.

## Future RAG authorization

Phase 2 already created the document-authorization tables
(`document_allowed_roles`, `document_authorized_doctors`,
`document_authorized_staff`, `documents.sensitivity`/`patient_id`) this
phase's engine is designed to extend to. The intended shape, for the RAG
phase: resolve `UserScope` exactly as here, then filter the pgvector query
by a `WHERE` clause built from `scope.role` (via `document_allowed_roles`),
`scope.hospital_id`/`department_id`, and - for `PATIENT_SPECIFIC` documents
- the same `can_access_patient()` relationship this phase already
implements. The LLM must never see a document chunk that query didn't
return; retrieval authorization happens before the LLM call, never inside
prompt content (see [docs/ARCHITECTURE.md](ARCHITECTURE.md)).

## Authorization pipeline

```
JWT
 ↓
Application User            (Phase 3: get_current_auth_user)
 ↓                           - 401 if missing/invalid/expired token
 ↓                           - 403 if unprovisioned or inactive
Role
 ↓
Permissions                 (resolve_user_scope: role_permissions query)
 ↓
Hospital Scope               (can_access_* / authorize())
 ↓
Department Scope             (appointments only, RECEPTIONIST/STAFF)
 ↓
Resource Relationship         (patient ownership / doctor assignment)
 ↓
Authorization Decision        (authorize(): PermissionDenied -> 403, or allow)
```
