# CareSphere AI — Secure Hospital Data APIs

Phase 6 exposes Phase 2's structured hospital data through read-only FastAPI
endpoints, built entirely on Phase 3's authentication and Phase 4's
authorization - no new authorization mechanism was introduced. This is the
secure foundation the later SQL + RAG query router (Phase 11) will sit on
top of; nothing here does retrieval, search, or LLM work.

## API architecture

```
Supabase JWT
   ↓  app/auth/jwt_verifier.py, app/auth/dependencies.py      (Phase 3)
Application user (get_current_auth_user)
   ↓  app/permissions/scope.py                                (Phase 4)
UserScope (role, permissions, hospital/department, patient_id/doctor_id/staff_id)
   ↓  app/permissions/dependencies.py: get_user_scope, require_permission
Permission check (has_permission - one code, or any of several)
   ↓  app/permissions/authorization.py: authorize(), can_access_*()
Resource-level authorization (detail endpoints) / scope-clause (list endpoints)
   ↓  app/repositories/clinical_repository.py
Structured PostgreSQL query, scope baked into the WHERE clause
   ↓
Safe response (app/schemas/clinical.py)
```

Seven resources, each a small router in `app/api/`: `patients.py`,
`doctors.py`, `departments.py`, `appointments.py`, `medical_records.py`,
`lab_reports.py`, `prescriptions.py`. Every one follows the identical
two-endpoint shape:

```
GET /api/<resource>              -> Page[<Resource>Response]
GET /api/<resource>/{id}         -> <Resource>Response
```

`app/api/_clinical_common.py` holds the two pieces of glue every router
uses: `require_any_permission` (list-endpoint permission gate) and
`authorize_resource` (detail-endpoint permission + resource check,
translated to the same generic `403`). Neither contains any resource-
specific logic - that stays in each router and in
`clinical_repository.py`.

## Authentication flow

Unchanged from Phase 3: `get_current_auth_user` verifies the Supabase JWT
against the project's JWKS and resolves it to a `users` row, rejecting a
missing/invalid/expired token with `401` and an unprovisioned or inactive
account with `403`. Every Phase 6 endpoint depends on this - directly (via
`get_user_scope`) - not a reimplementation.
`backend/tests/test_clinical_security.py` re-confirms this behavior through
a Phase 6 endpoint specifically, to prove the new routers actually inherit
it rather than accidentally bypassing it.

## Authorization flow

Two patterns, depending on whether the endpoint is list or detail:

- **Detail** (`GET /api/patients/{id}` etc.): `authorize_resource` calls
  `app/permissions/authorization.py`'s `authorize(db, scope, permission,
  resource=(kind, id))` - the exact Phase 4 function, unmodified in what it
  does (see "has_permission accepts several codes" below for the one
  additive change). This checks the permission, then the resource
  relationship via `can_access_patient`/`can_access_appointment`/
  `can_access_medical_record`/`can_access_lab_report`/
  `can_access_prescription` (all reused, not reimplemented), plus two new
  ones this phase adds to the same registry: `can_access_doctor`,
  `can_access_department`.
- **List** (`GET /api/patients` etc.): there is no single resource id to
  check, so the *same rule* each `can_access_*` function encodes is instead
  expressed as a SQL `WHERE` fragment (`clinical_repository.py`'s
  `_hospital_scope_clause`, `_patient_relationship_clause`,
  `_appointment_scope_clause`) and baked into the query itself - see "List-
  query security" below.

### `has_permission` accepts several codes

The one change to Phase 4's `has_permission`/`authorize`: `permission` may
now be a single code or an iterable of codes (any one present is enough).
This exists because several Phase 6 endpoints are reachable through more
than one already-seeded permission (e.g. `patients` via either
`view_assigned_patients` or `view_patient_basic_information`) - it does not
change what any role can do, it only lets one endpoint's gate express "any
of these", instead of arbitrarily picking one and leaving the other seeded
permission unusable.

## `UserScope` usage

Every router depends on `get_user_scope` (new this phase, in
`app/permissions/dependencies.py`) - the same `resolve_user_scope` Phase 4
built, exposed as a bare dependency with no permission check attached, since
Phase 6 routers need to run a resource-specific `authorize()` call or a
several-permissions-any-of check that `require_permission`'s single-string
signature doesn't fit. `require_permission` itself is untouched.

## Resource-level authorization

| Endpoint | Gate permission(s) | Resource check |
|---|---|---|
| `patients` | `view_assigned_patients` OR `view_patient_basic_information` | `can_access_patient` |
| `doctors` | `view_hospital_operations` | `can_access_doctor` (new) |
| `departments` | `view_hospital_operations` | `can_access_department` (new) |
| `appointments` | `view_own_appointments` OR `create_appointments` OR `manage_appointments` | `can_access_appointment` |
| `medical-records` | `view_patient_medical_records` | `can_access_medical_record` |
| `lab-reports` | `view_own_reports` OR `view_patient_medical_records`* | `can_access_lab_report` |
| `prescriptions` | `view_own_prescriptions` OR `view_patient_medical_records`* | `can_access_prescription` |

\* The seed has no dedicated "view lab reports"/"view prescriptions"
permission for `DOCTOR` - only `view_patient_medical_records`. Rather than
leave a doctor with zero access to their assigned patients' labs and
prescriptions, this permission is reused as the umbrella clinical-data
permission for those two resources too. This is a deliberate, documented
interpretation of the existing seed, not a new permission invented for this
phase - see "Known limitations" for the consequence this has for
`PATIENT`/`medical_records`.

## Hospital isolation

Every `can_access_*`/scope-clause function pins to `scope.hospital_id`
except for `SUPER_ADMIN`, which - exactly as Phase 4 already established for
`can_access_patient` - gets an explicit bypass. No endpoint reads a
`hospital_id` from the client to decide scope; the seven routers have no
`hospital_id` query parameter at all (only `SUPER_ADMIN`'s employee-
invitation flow, Phase 5, ever reads one, and it's separately validated
there). `backend/tests/test_clinical_*.py`'s hospital-isolation tests use a
throwaway second hospital (`second_hospital_clinical_data` fixture) with one
full clinical record set, and prove a Hospital A user gets `403` on every
one of its seven resources while a Hospital B user (in the same fixture)
gets `200` on their own.

## Patient ownership

`can_access_patient`'s `PATIENT` branch (`scope.patient_id ==
resource.patient_id`) is exactly what Phase 4 built - reused unchanged.
`scope.patient_id` itself comes from `resolve_user_scope`'s `patients WHERE
user_id = :user_id` lookup, never from a client-supplied id; nothing in any
Phase 6 endpoint accepts a `user_id` or `patient_id` as a way to say "this
is me."

## Doctor-patient assignment

Also reused unchanged: `can_access_patient`'s `DOCTOR` branch requires an
**active** `doctor_patient_assignments` row, and every list endpoint's SQL
scope clause expresses the identical condition
(`patient_id IN (SELECT patient_id FROM doctor_patient_assignments WHERE
doctor_id = :scope_doctor_id AND is_active)`) rather than a looser
`doctor.hospital_id = patient.hospital_id`/`department_id` check. A doctor
querying a patient they're not assigned to gets `403` on the detail
endpoint and simply never sees that patient's rows on any list endpoint -
proven for patients, appointments, medical records, lab reports, and
prescriptions.

## `SUPER_ADMIN` behavior

No `if role == SUPER_ADMIN` shortcut exists anywhere in Phase 6 (or Phase
4). `SUPER_ADMIN`'s actual reach is the same composition every role gets:
the resource-relationship bypass (`can_access_doctor`/`can_access_patient`/
etc. all return `True` cross-hospital for this role) **and** whatever
`role_permissions` grants. Concretely, under the current seed, `SUPER_ADMIN`
passes the gate for `doctors`/`departments` (`view_hospital_operations`) but
is `403` on `patients`/`appointments`/`medical-records`/`lab-reports`/
`prescriptions` - the seed simply doesn't grant it any of those permissions,
matching `docs/AUTHORIZATION.md`'s "SUPER_ADMIN" section exactly. This is
not narrowed or widened here; Phase 6 exposes the model, it doesn't change
it.

## List-query security

Every `list_*` function in `clinical_repository.py` builds its `WHERE`
clause from the caller's `UserScope` *before* running any query - there is
no "fetch broadly, then check each row in Python" path anywhere in this
phase. The scope-clause helpers
(`_hospital_scope_clause`/`_patient_relationship_clause`/
`_patients_table_scope_clause`/`_patient_owned_table_scope_clause`/
`_appointment_scope_clause`) exist specifically so a list query and a
single-item `can_access_*` check can never disagree: both are written
against the same rule, one expressed as a SQL fragment, the other as a
single-id boolean.

Client-supplied filters (`patient_id`, `doctor_id`, `department_id`,
`status`, date range) are always `AND`ed onto that scope clause, never
`OR`ed and never substituted for it - see each router's list function.
Concretely: a doctor filtering `?patient_id=<someone they're not assigned
to>` gets an empty page, not that patient's data, because the scope clause
already excludes that patient before the filter is even applied. Verified
directly in `backend/tests/test_clinical_appointments.py` and
`test_clinical_security.py`.

## Pagination

`page` (default 1) / `page_size` (default 20, max 100) on every list
endpoint, both enforced by FastAPI/Pydantic's own `Query(ge=..., le=...)`
validation (`app/api/_clinical_common.py`'s `PageParam`/`PageSizeParam`) -
an invalid or oversized value is a `422` before any query runs, never
silently clamped. Every list response is a `Page[T]`
(`app/schemas/pagination.py`): `items`, `page`, `page_size`, `total` - never
a bare array, so there is no unbounded "give me everything" shape available
at all.

## Filtering

Explicit query parameters per resource (see the table above's endpoints);
enum-like filters (`status`, `record_type`) are typed `Literal`s, so an
invalid value is a `422`, not silently ignored or passed through as a raw
string. As covered under "List-query security", filters only ever narrow.

## Sorting

`appointments` is the one endpoint with a `sort_by` parameter, typed
`Literal["appointment_date", "created_at"]` - there is no code path that
interpolates a client string into `ORDER BY`;
`clinical_repository._APPOINTMENT_SORT_COLUMNS` maps the two allowed values
to real column names, and anything else is rejected by FastAPI as a `422`
before it reaches the repository at all.
`backend/tests/test_clinical_appointments.py::test_sort_by_unknown_field_rejected`
sends a SQL-injection-shaped string as `sort_by` and confirms `422`.

## Enumeration protection

Every detail endpoint's `authorize_resource` raises the same generic `403`
("You do not have permission to perform this action.") whether the resource
doesn't exist at all or exists but is outside the caller's scope - this is
Phase 4's existing convention (`authorize()`/`PermissionDenied`), reused
here rather than introducing a `404` for one case and `403` for the other.
A caller cannot distinguish "this patient doesn't exist" from "this patient
exists but isn't yours" from the response, which is the actual protection
enumeration-resistance is for.
`backend/tests/test_clinical_patients.py::test_get_nonexistent_patient_is_403_not_404`
asserts the exact response body for a random UUID.

## Response-data minimization

One explicit Pydantic schema per resource (`app/schemas/clinical.py`) -
never a raw SQLAlchemy/dataclass dump, never a cross-resource "full patient
dump" combining appointments/records/reports/prescriptions into one
response. Fields are the resource's own table columns minus nothing except
what genuinely doesn't belong in an API response (there's no
password/token/service-key column on any of these tables to begin with).
`created_at`/`updated_at` are included (not sensitive); nothing from
`employee_invitations` (token hash, inviter id) or `audit_logs` leaks into
any clinical response - they're entirely different schemas/routers.

## Error handling

`401` (no valid authentication), `403` (authenticated but not authorized -
covers both "missing permission" and "resource not found/out of scope", see
"Enumeration protection"), `422` (invalid pagination/filter/sort input,
handled by FastAPI/Pydantic before any handler code runs). No `404` is used
by these endpoints, deliberately - see "Enumeration protection". No SQL
error, stack trace, or Supabase error ever reaches a response body -
`SupabaseAdminError` (Phase 3/5) is the only place upstream code catches an
external-service failure and returns a generic `503`, and Phase 6 doesn't
call Supabase at all, so that case doesn't arise here.

## Audit logging

Four actions, all reusing Phase 2's `audit_logs` (no second audit table):
`PATIENT_RECORD_ACCESSED`, `MEDICAL_RECORD_ACCESSED`,
`LAB_REPORT_ACCESSED`, `PRESCRIPTION_ACCESSED` - written only on a
successful **detail** GET (not on list views, and not on a denied attempt -
a `403` never reaches the audit call). `metadata` is always `{}`; the
resource is identified entirely by the existing `resource_type`/
`resource_id`/`hospital_id` columns, so there's nothing clinical (no
`clinical_notes`, no `result`, no patient demographics) in the audit row
itself.
`backend/tests/test_clinical_patients.py::test_denied_patient_access_writes_no_audit_event`
and the parallel tests in `test_clinical_medical_lab_prescriptions.py`
confirm both halves (written on success, absent on denial, no clinical
content in the row).

`doctors`/`departments`/`appointments` are not audited - Phase 2's future-
events list (`database/README.md`) never named a
`DOCTOR_ACCESSED`/`APPOINTMENT_ACCESSED` event, and appointments/doctor-
directory access is materially less sensitive than the four clinical record
types that are.

## Test coverage

224 backend tests total (regression: 123 from Phases 1-5, unchanged in
behavior aside from one test intentionally narrowed - see below; **101
new** this phase across `test_clinical_patients.py`,
`test_clinical_appointments.py`,
`test_clinical_medical_lab_prescriptions.py`,
`test_clinical_doctors_departments.py`, `test_clinical_security.py`):
role behavior for all seven roles across all seven resources, hospital
isolation (a real second hospital, not a mocked scope), doctor-patient
assignment (active/none/other-patient), query-parameter manipulation
(`patient_id`/`doctor_id`/`department_id`/`hospital_id`/`role` overrides all
proven inert), enumeration protection, pagination bounds (default, oversized
rejected, invalid rejected, disjoint pages), sort-field allowlisting
(injection-shaped input rejected), and audit-event presence/absence/content.

One existing Phase 3 test,
`test_no_employee_self_registration_routes_exist`, previously asserted the
*entire* app route set; it's narrowed this phase to only assert the
`/api/auth/*` prefix (its actual concern - no employee self-registration
route), since asserting every route in the app would otherwise need editing
on every future phase that adds an unrelated endpoint.

## Known limitations

- **`PATIENT` cannot access the `medical_records` resource at all, even
  their own.** The seed has no `view_own_medical_records` (or equivalent)
  permission for `PATIENT` - only `view_own_appointments`,
  `view_own_reports`, `view_own_prescriptions`. This matches
  `docs/AUTHORIZATION.md`'s existing documented statement ("Patient A
  medical record depends on explicit permission") precisely; Phase 6 did
  not add a permission to work around it, per the phase's explicit
  instruction not to broaden permissions to make an endpoint easier to
  implement.
- **`view_patient_medical_records` is reused for lab reports and
  prescriptions** for `DOCTOR`, since the seed has no more granular
  permission for either. See the footnote in "Resource-level authorization".
- **No write endpoints exist yet** - Phase 6 is read-only by design (create/
  update/delete clinical records is Phase 6's explicit non-goal, left to a
  later phase).
- **`appointments` sorting** only supports `appointment_date`/`created_at`;
  other resources have a fixed sort order (most-recent-first by their
  natural timestamp) with no `sort_by` parameter at all, to keep the
  allowlist small and auditable rather than adding sort options with no
  concrete use case yet.
