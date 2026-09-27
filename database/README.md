# Database

PostgreSQL with the [pgvector](https://github.com/pgvector/pgvector) extension.
In production this is the same Postgres instance provisioned by Supabase
(Supabase Postgres ships pgvector); locally you can run Homebrew's
`postgresql@17` + `pgvector` formulas, or any Postgres image that has the
`vector` extension available.

Homebrew's `pgvector` bottle is only built against `postgresql@17`/`@18`, not
`@16` - if you `brew install postgresql@16 pgvector`, `CREATE EXTENSION
vector` will fail with "extension is not available". Use `postgresql@17`.

SQL migrations are the authoritative schema definition. The backend does not
maintain a parallel set of ORM model classes that duplicate this schema -
see [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) and `backend/app/core/db.py`.

**Synthetic data only.** Every row `database/seeds/` inserts is fictional.
Never load real patient, staff, or hospital data into this project.

## Layout

- `migrations/` — hand-written, sequentially numbered SQL migrations, applied
  in order. Each migration is additive; none of them are rolled back or
  edited after being applied.
- `seeds/` — demo data for local development, also sequentially numbered.
  See "Idempotency" in `0001_demo_data.sql` for which parts are safe to
  re-run.

## Migration order

| Migration | Adds |
|---|---|
| `0001_enable_extensions.sql` | `pgcrypto`, `vector` extensions |
| `0002_helpers.sql` | `set_updated_at()` trigger function |
| `0003_hospitals_and_departments.sql` | `hospitals`, `departments` |
| `0004_roles_and_permissions.sql` | `roles`, `permissions`, `role_permissions` |
| `0005_users.sql` | `users` (application profile; `auth_user_id` links to Supabase Auth) |
| `0006_patients_doctors_staff.sql` | `patients`, `doctors`, `staff` |
| `0007_doctor_patient_assignments.sql` | `doctor_patient_assignments` |
| `0008_appointments.sql` | `appointments` |
| `0009_medical_records.sql` | `medical_records` |
| `0010_lab_reports.sql` | `lab_reports` |
| `0011_prescriptions.sql` | `prescriptions` |
| `0012_documents.sql` | `documents`, `document_allowed_roles`, `document_authorized_doctors`, `document_authorized_staff` |
| `0013_document_chunks.sql` | `document_chunks` (pgvector `embedding` column) |
| `0014_audit_logs.sql` | `audit_logs` |
| `0015_patients_user_id_unique.sql` | unique constraint on `patients.user_id` |
| `0016_employee_invitations.sql` | `employee_invitations` |
| `0017_documents_ingestion_metadata.sql` | `documents` gains `mime_type`/`file_size`/`content_hash`/`processing_error`/`is_active` (see `docs/RAG_INGESTION.md`) |
| `0018_document_chunks_search_vector.sql` | `document_chunks.search_vector` (generated `tsvector` column) + `GIN` index (see `docs/RAG_HYBRID_SEARCH.md`) |

## Local setup

```bash
# 1. Install and start Postgres 17 + pgvector (once)
brew install postgresql@17 pgvector
brew services start postgresql@17

# 2. Create the role and database
/usr/local/opt/postgresql@17/bin/createuser -s caresphere
/usr/local/opt/postgresql@17/bin/createdb -O caresphere caresphere
/usr/local/opt/postgresql@17/bin/psql -c "ALTER USER caresphere WITH PASSWORD 'caresphere';"

# 3. Run migrations, in order
export PGURL="postgresql://caresphere:caresphere@localhost:5432/caresphere"
for f in database/migrations/*.sql; do
  /usr/local/opt/postgresql@17/bin/psql "$PGURL" -v ON_ERROR_STOP=1 -f "$f"
done

# 4. Load the demo seed
/usr/local/opt/postgresql@17/bin/psql "$PGURL" -v ON_ERROR_STOP=1 -f database/seeds/0001_demo_data.sql
```

(If `psql` is already on your `PATH` - e.g. via a Postgres.app install or a
Docker container - drop the `/usr/local/opt/postgresql@17/bin/` prefix.)

### Verify

```bash
psql "$PGURL" -c "\dt"                                               # list tables
psql "$PGURL" -c "SELECT name FROM roles ORDER BY name;"             # 7 roles
psql "$PGURL" -c "SELECT count(*) FROM permissions;"                 # 19 permissions
psql "$PGURL" -c "SELECT count(*) FROM role_permissions;"
psql "$PGURL" -c "SELECT code, name FROM hospitals;"                 # CGH / CareSphere General Hospital
psql "$PGURL" -c "SELECT count(*) FROM departments;"                 # 8
psql "$PGURL" -c "SELECT patient_number FROM patients ORDER BY 1;"   # CGH-P-0001..0003
psql "$PGURL" -c "SELECT employee_number FROM doctors ORDER BY 1;"   # CGH-D-0001..0002
psql "$PGURL" -c "SELECT count(*) FROM doctor_patient_assignments WHERE is_active;"
psql "$PGURL" -c "\d document_chunks"                                # embedding: vector(384)
```

`backend/tests/test_database_schema.py` runs an equivalent (and more
thorough) check as part of `pytest`, using whatever `DATABASE_URL` the
backend is configured with; it skips itself if no database is reachable.

## Table overview

- **Hospital organization**: `hospitals`, `departments`.
- **Identity/RBAC**: `roles`, `permissions`, `role_permissions` (Phase 4 will
  read this to authorize requests), `users` (the hospital-facing profile for
  every signed-in identity - patients included).
- **Clinical roles**: `patients`, `doctors`, `staff` (nurses, receptionists,
  and other non-clinician staff, distinguished by `staff.designation`).
- **Care relationships**: `doctor_patient_assignments` - a doctor only has an
  active assignment to the patients they actually care for; this is what
  Phase 4 authorization will check before granting a doctor patient-level
  access, not the doctor's role alone.
- **Clinical data**: `appointments`, `medical_records`, `lab_reports`,
  `prescriptions`.
- **RAG source data**: `documents`, `document_chunks`, plus the authorization
  join tables `document_allowed_roles`, `document_authorized_doctors`,
  `document_authorized_staff`.
- **Audit**: `audit_logs`.

## Major relationships

```
hospitals
 ├── departments
 ├── users            (role_id -> roles, department_id -> departments)
 ├── patients
 ├── doctors           (department_id -> departments)
 ├── staff             (department_id -> departments)
 ├── appointments       (-> patients, doctors, departments)
 ├── medical_records    (-> patients, doctors)
 ├── lab_reports        (-> patients, doctors)
 ├── prescriptions      (-> patients, doctors)
 ├── documents          (-> departments, patients)
 └── audit_logs

doctors -> doctor_patient_assignments -> patients
documents -> document_chunks
documents -> document_allowed_roles -> roles
documents -> document_authorized_doctors -> doctors
documents -> document_authorized_staff -> staff
```

Every hospital-scoped child table stores its own `hospital_id` *and* a
composite foreign key (e.g. `(patient_id, hospital_id) REFERENCES patients
(id, hospital_id)`) so the database itself rejects a row whose patient/doctor/
department belongs to a different hospital than the row claims. This is what
"hospital isolation must be supported by the schema" means concretely here -
enforced at the constraint level, not just by application code remembering
to filter correctly.

## Authorization-related relationships

This migration set does not implement authorization (that's Phase 4) but
gives it a data model to read:

- `roles` / `permissions` / `role_permissions` — what a role is allowed to do
  in general.
- `doctor_patient_assignments` — which specific patients a specific doctor
  may access; a doctor does not get implicit access to every patient in the
  hospital.
- `document_allowed_roles` / `document_authorized_doctors` /
  `document_authorized_staff` / `documents.sensitivity` /
  `documents.patient_id` — which roles/individuals may see a given RAG
  source document, down to a single patient-specific document if needed.

## RAG-related tables

- `documents` holds metadata (title, type, sensitivity, processing `status`)
  for source material - hospital policies, clinical guidelines, and so on.
  No ingestion happens in this phase; `status` stays `PENDING` for every row
  created here.
- `document_chunks` holds the (future) chunked text and its embedding.
  `embedding` is `vector(384)`, matching `sentence-transformers/all-MiniLM-L6-v2`
  (the backend's default `EMBEDDING_MODEL`). If the embedding model changes,
  the column's fixed dimension has to change with it - see the comment in
  `0013_document_chunks.sql`.

## Synthetic-data policy

`database/seeds/0001_demo_data.sql` is fictional data for local development
only: one demo hospital, 8 departments, all 7 roles, all defined permissions
with a non-universal role mapping, 8 demo users (one per role plus a second
doctor), 3 patients, 2 doctors, 3 doctor-patient assignments (chosen so a
later authorization test can prove both an allowed and a denied access),
4 appointments, 3 medical records, 3 lab reports, and 3 prescriptions. No
passwords or credentials are stored anywhere in this schema. Emails use the
RFC 2606 `.example` TLD (e.g. `rohan.mehta@caresphere-demo.example`) rather
than `.test` - Pydantic's `EmailStr` (via `email-validator`) rejects `.test`
as a reserved/non-deliverable domain, which would break `GET /api/auth/me`
for these users once the backend serializes their email through that schema.

**`auth_user_id` values in the seed are placeholder UUIDs, not real Supabase
accounts** (see [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#authentication)).
They exist only so the seed's foreign keys are self-consistent. You cannot
sign in as a seeded user, and the backend's JWT verification will correctly
reject any token claiming one of these ids, because no such token can ever
be legitimately issued by a real Supabase project. To interact with a seeded
user through real authentication in local dev: create a real user in your
Supabase project (dashboard, or `supabase.auth.admin.createUser`), then
`UPDATE users SET auth_user_id = '<the real Supabase user id>' WHERE email =
'<seeded email>';`. Do not script this against production data, and never
commit a real `auth_user_id` into this repository.

## Patient registration hospital assignment

Self-registered patients (`POST /api/auth/register`) are always assigned to
the hospital named by the backend's `DEFAULT_PATIENT_HOSPITAL_CODE` setting
(`CGH`, i.e. the seeded CareSphere General Hospital) - see
`backend/app/repositories/user_repository.get_default_patient_hospital_id`.
Patients cannot select a hospital themselves. This is the simplification
Phase 3 explicitly allows for a single-hospital demo; a multi-hospital
onboarding flow (e.g. a hospital picker backed by a public "list active
hospitals" endpoint) is future work, not implemented here.
