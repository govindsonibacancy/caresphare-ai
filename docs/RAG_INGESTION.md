# CareSphere AI — RAG Document Management + Ingestion

Phase 7 turns Phase 2's dormant RAG schema (`documents`, `document_chunks`,
`document_allowed_roles`, `document_authorized_doctors`,
`document_authorized_staff`) into a working ingestion pipeline: a hospital
admin uploads a file, the backend validates it, stores it, extracts its
text, cleans it, chunks it, embeds each chunk, and persists the chunks -
all without the admin ever touching a chunk or an embedding by hand. This
phase is upload + storage + embeddings only. It does **not** implement
retrieval - see "What Phase 8 will implement" at the end.

## Existing document schema

Nothing here is new. Phase 2 (`database/migrations/0012_documents.sql`,
`0013_document_chunks.sql`) already created every table this phase writes
to:

- **`documents`** - one row per uploaded file: `hospital_id`,
  `department_id` (nullable - hospital-wide if absent), `title`,
  `filename`, `document_type` (a fixed 9-value `CHECK` constraint -
  `HOSPITAL_POLICY`, `CLINICAL_GUIDELINE`, `NURSING_PROCEDURE`,
  `MEDICATION_GUIDELINE`, `EMERGENCY_PROCEDURE`, `PATIENT_EDUCATION`,
  `HR_POLICY`, `SOP`, `GENERAL_INFORMATION`), `description`, `sensitivity`
  (`PUBLIC`/`INTERNAL`/`CLINICAL`/`CONFIDENTIAL`/`PATIENT_SPECIFIC`),
  `patient_id` (only for `PATIENT_SPECIFIC`), `uploaded_by`, `status`
  (`PENDING`/`PROCESSING`/`COMPLETED`/`FAILED`).
- **`document_chunks`** - `document_id`, `chunk_index`, `content`,
  `page_number`, `metadata` (`jsonb`), `embedding VECTOR(384)`.
- **`document_allowed_roles`** / **`document_authorized_doctors`** /
  **`document_authorized_staff`** - the three normalized access-control join
  tables (see "Access-control metadata").

This phase reused this model exactly as designed rather than inventing a
parallel one - in particular, `app/schemas/documents.py`'s `DocumentType`
is the existing 9-value list, not the differently-worded list this phase's
own brief suggested as an example.

**One schema change was required** (`database/migrations/0017_documents_ingestion_metadata.sql`),
adding five columns Phase 2 didn't anticipate: `mime_type`, `file_size`,
`content_hash` (the ingestion pipeline's fingerprint - see "Idempotency"),
`processing_error` (a safe failure message), `is_active` (see
"Re-ingestion and versioning"), plus a partial unique index enforcing
idempotency and an index on `is_active`. No new tables, no change to the
existing status vocabulary or the three access-control tables.

## Upload workflow

```
Admin (HOSPITAL_ADMIN / SUPER_ADMIN / STAFF - see "Who can upload")
   ↓  multipart POST /api/admin/documents
validation.validate_upload()          - extension, magic bytes, size, filename safety
   ↓
resolve_hospital_scope()              - which hospital this upload actually belongs to
   ↓
_validate_access_metadata()           - department/doctor/staff ids re-checked server-side
   ↓
SHA-256 content hash + duplicate check - see "Idempotency"
   ↓
documents row created (status=PENDING), access-control rows set, file saved to storage
   ↓  DOCUMENT_UPLOADED audit event, commit
_run_ingestion(): mark PROCESSING
   ↓
extract_text()  → clean_text()  → chunk_pages()  → embed()
   ↓
document_chunks rows persisted (384-dim vectors)
   ↓
mark COMPLETED (or FAILED at any step - see "Failure handling")
   ↓
DocumentDetail returned to the admin (already COMPLETED or FAILED, never PENDING/PROCESSING)
```

All of this runs synchronously inside the upload request
(`app/services/documents/ingestion_service.py`, `upload_document()` →
`_run_ingestion()`) - there is no background job queue in this project yet,
and for a local/demo system with small documents this is the simplest
correct choice. `create_document`-then-`run_ingestion` are already two
separate calls, so a later phase could move extraction/chunking/embedding
to a background worker without reshaping this module's public functions.

### Who can upload

Gated entirely by the existing `manage_hospital_documents` permission via
`require_permission()` (`app/permissions/dependencies.py`) - there is no
`if user.role == ...` anywhere in `app/api/admin_documents.py`. Under the
current seed (`database/seeds/0001_demo_data.sql`) that permission is held
by `HOSPITAL_ADMIN`, `SUPER_ADMIN`, **and `STAFF`** - the seed was audited
before writing this phase's authorization code, and `STAFF` holding this
permission was a discovery, not an assumption; `DOCTOR`, `NURSE`,
`RECEPTIONIST`, and `PATIENT` do not have it and get `403`. If the seed's
`role_permissions` changes, who can upload changes with it automatically -
nothing in the upload path needs to be touched.

## Metadata

| Field | Source | Client can set it? |
|---|---|---|
| `title`, `document_type`, `sensitivity`, `description`, `department_id`, `allowed_roles`, `authorized_doctor_ids`, `authorized_staff_ids` | Upload form | Yes - all independently re-validated server-side |
| `hospital_id` | `resolve_hospital_scope(scope, ...)` | Only `SUPER_ADMIN` may submit one, and only to target a *real* hospital - everyone else is pinned to `scope.hospital_id` regardless of what's submitted |
| `id`, `filename` (client's original name, stored for display only - never used as the storage path), `uploaded_by`, `mime_type`, `file_size`, `content_hash`, `status`, `processing_error`, `chunk_count`, `created_at`, `updated_at` | Backend-derived | **No** - none of these has a corresponding upload form field at all; submitting them anyway (`test_upload_ignores_client_supplied_authoritative_fields`) has zero effect |

`patient_id`/`PATIENT_SPECIFIC` sensitivity is schema-supported but
deliberately not exposed on this admin bulk-upload form - it names one
patient and belongs to a different workflow than hospital-knowledge-base
documents. This form's `DocumentSensitivity` is the other four values only.

**Version/effective-date fields were considered and deliberately not
added.** Phase 2's schema has no `version`/`effective_from`/`effective_to`
columns, and nothing in this phase's ingestion or authorization logic
needs them - "one currently-active document per piece of content" (see
"Re-ingestion and versioning") is sufficient for correct RAG behavior
without them. Adding unused columns "for later" was judged out of scope.

## Access-control metadata

Reuses the three existing join tables unchanged -
`document_repository.py`'s `set_allowed_roles`/`set_authorized_doctors`/
`set_authorized_staff` (delete-then-reinsert) and
`get_allowed_role_names`/`get_authorized_doctor_ids`/
`get_authorized_staff_ids`. The upload form offers every role except
`PATIENT` (`AllowedRoleName` in `app/schemas/documents.py`) - patient-facing
knowledge documents aren't a requirement this phase, and there is no
`PATIENT_SPECIFIC` document type on this form to justify it.

Every doctor/staff/department id submitted is **independently
re-validated against the resolved target hospital**, never trusted because
the client selected it in a dropdown:
`ingestion_service._validate_access_metadata()` calls
`invitation_repository.department_belongs_to_hospital()` and
`clinical_repository.get_doctor_hospital_id()`/`get_staff_hospital_id()`
for every id in the payload, raising `PermissionDenied` (→ `403`) on the
first mismatch. `PATCH` re-runs the identical checks
(`update_document()`) against the document's own `hospital_id`. Covered by
`test_hospital_admin_cannot_authorize_another_hospitals_doctor`/`_staff` and
the `test_patch_rejects_*_from_another_hospital` tests.

Two purpose-built read endpoints populate the upload form's doctor/staff
pickers with real names (`app/api/admin_directory.py`):
`GET /api/admin/doctors` / `GET /api/admin/staff`, hospital-scoped via
`scope.hospital_id`, gated on the same `manage_hospital_documents`
permission.

## File validation

`app/services/documents/validation.py`'s `validate_upload()`, before
anything is written to disk or the database:

1. **Filename safety** - rejects a missing filename, `/`, `\`, or a NUL
   byte, a filename over 255 characters, or one starting with `.`. This
   check exists independently of storage (see "Storage architecture" - the
   filename is never used to build a path anyway), specifically to reject
   traversal-shaped input outright rather than relying on the storage
   layer's own defense as the only line of protection.
2. **Extension allowlist** - `pdf` / `docx` / `txt` / `md` only
   (`SUPPORTED_EXTENSIONS`), a deliberately small, reliably-processable set,
   not "every format Python can theoretically parse."
3. **Non-empty and size** - `0` bytes rejected; over
   `max_document_upload_size_mb` (default **20 MB**,
   `backend/app/core/config.py`) rejected.
4. **Content-based check, not just the extension** - `.pdf` must start with
   the real PDF magic bytes (`%PDF-`); `.docx` must start with the ZIP
   magic bytes (`PK\x03\x04`, since DOCX is a ZIP container); `.txt`/`.md`
   must actually decode as UTF-8. A `.pdf` extension on non-PDF bytes is
   rejected as `CorruptFile`, not silently accepted.

Every failure is a `422` with a message from `DocumentValidationError`'s
own safe `str()` - never a raw parser exception. Uploaded content is never
executed or interpreted as code at any stage.

## Storage architecture

`app/services/documents/storage.py` defines a `FileStorage` Protocol
(`save`/`read`/`delete`) with one implementation today,
`LocalFileStorage`, so a later phase can swap in object storage (S3-
compatible, etc.) without touching `ingestion_service.py` - nothing outside
`storage.py` knows the files are on local disk.

Files are stored at `<DOCUMENT_STORAGE_PATH>/<document's own UUID>.<ext>`
(default `./data/documents/`, gitignored) - **never the client-supplied
filename**. This isn't sanitization of the client filename, it's
non-use of it: the storage key is built entirely from the server-generated
document id, so a traversal-shaped filename has no path to traverse with
in the first place (`_resolve()` additionally takes only the file's
`.name` via `PurePosixPath` as defense in depth). Verified by
`test_uploaded_file_is_never_stored_under_client_filename`.

Raw file bytes are never written into `audit_logs` or any other row beyond
the file itself - see "Audit logging". Files are not stored in Postgres
(no `bytea`/large-object column); nothing in this phase required that.

## Text extraction

`app/services/documents/extraction.py`'s `DocumentExtractor` Protocol, one
implementation per format:

- **`TextExtractor`** / **`MarkdownExtractor`** - `.txt`/`.md` have no
  pagination concept; the whole file is one `ExtractedPage` with
  `page_number=None`. Markdown syntax (`#`/`##` headings) is kept as-is,
  not stripped - it's exactly what chunking looks for.
- **`PdfExtractor`** - one `ExtractedPage` per PDF page (via `pypdf`), so
  page numbers survive all the way into `document_chunks.page_number`.
- **`DocxExtractor`** - `.docx` has no page concept, so instead its
  "Heading 1"/"Heading 2"/"Heading 3" paragraph styles are converted to
  markdown-style `#`/`##`/`###` prefixes (via `python-docx`), so section
  structure survives into chunking the same way a `.md` source's does,
  rather than being lost entirely.

Any underlying parser failure is wrapped in `ExtractionError` with a safe,
generic message (`extract_text()`'s broad `except Exception`) - never a raw
parser traceback.

## Text cleaning

`app/services/documents/cleaning.py`'s `clean_text()`: normalizes
`\r\n`/`\r`/form-feed page-break artifacts to `\n`, collapses runs of
spaces/tabs and 3+ blank lines, strips trailing whitespace per line. It
**never rewrites, summarizes, or otherwise reinterprets the source
material's actual words** - the RAG knowledge base must represent the
source document, not an LLM's interpretation of it, so this step is pure
whitespace normalization. Paragraph boundaries (blank lines) and heading
prefixes are deliberately preserved, since chunking depends on both to find
section boundaries.

## Chunking

`app/services/documents/chunking.py`'s `chunk_pages()` - deterministic and
never LLM-based; the same input always produces the same chunks
(`test_chunk_pages_is_deterministic`). Rules:

- A markdown heading line (`#` through `######`) always starts a new
  chunk - a section's content is never silently merged with the section
  before it, and overlap is not carried across a heading boundary for the
  same reason.
- Otherwise, paragraphs (blank-line-separated) accumulate into a buffer
  until adding the next one would exceed `document_chunk_size_chars`
  (default **1200** characters - roughly 200-300 tokens, a standard RAG
  chunk size balancing embedding quality against context size), at which
  point the buffer is flushed as a chunk.
- A single paragraph longer than one whole chunk (rare) is hard-split on
  whitespace boundaries (`_hard_split`) so nothing is silently dropped and
  no chunk ever exceeds the configured size - never mid-word.
- `document_chunk_overlap_chars` (default **150**) characters from the tail
  of one chunk are carried into the start of the next, so a fact split
  across a chunk boundary still appears whole in at least one chunk.
- Each `Chunk` carries `index` (chunk order), `content`, `page_number`
  (from the source page it was built from), and `heading` (the section
  heading active when its content was accumulated - not the next section's,
  a bug caught and fixed during this phase's own testing; see
  `test_chunk_pages_attributes_content_to_the_heading_active_when_it_was_written`
  in `tests/test_document_ingestion.py`, a regression test for it).

## Embedding model

`app/services/documents/embedding.py`'s `OllamaEmbeddingService` calls
Ollama's local `POST /api/embed` with the `all-minilm` model - **not** a
Python-side library (`sentence-transformers`/`fastembed`/etc.). This
project's Python runtime (3.14) has no available `torch` or `onnxruntime`
wheel yet, which every Python-side embedding runtime depends on;
`sentence-transformers` and `fastembed` were both tried and both failed to
install for this reason. Ollama is already this project's designated local
model runtime (a later phase uses it for the LLM too), and `all-minilm` is
Ollama's port of the exact `sentence-transformers/all-MiniLM-L6-v2`
architecture this project always named - the same model, a different
runtime, not a different model chosen for convenience. No paid external
embedding API is used.

### The 384-dimension requirement

`document_chunks.embedding` is `VECTOR(384)` (fixed by Phase 2's migration,
matching `all-MiniLM-L6-v2`'s native output size) - pgvector fixes a
column's dimension at creation time, so the embedding model **must**
produce exactly 384 dimensions; the column was never widened or changed to
fit a different model. `OllamaEmbeddingService.embed()` checks every
returned vector's length against `settings.embedding_dimension` (`384`)
before returning, raising `EmbeddingDimensionMismatch` on any mismatch -
this check runs **before** any chunk reaches the database. As defense in
depth beyond that application-level check, pgvector's own column type
would itself reject a wrongly-shaped vector at `INSERT` time; both layers
are exercised in `tests/test_document_ingestion.py`
(`test_embedding_service_rejects_wrong_dimension_vectors` for the
application-level check, `test_wrong_dimension_embedding_marks_document_failed_with_zero_chunks`
for the DB-level defense with a stub service that skips the app-level
check on purpose). A dimension mismatch is treated exactly like any other
ingestion failure: the document is marked `FAILED`, never left partially
populated.

## Idempotency

A SHA-256 hash of the uploaded file's exact bytes (`content_hash`) is the
duplicate-detection key, scoped per hospital
(`(hospital_id, content_hash)`, not global - the same policy text uploaded
to two different hospitals is not a duplicate). Two layers:

1. **App-level pre-check** - `find_active_document_by_hash()` runs before
   storing/extracting/embedding anything, so a duplicate is rejected
   (`409 DuplicateDocument`) without wasting any ingestion work.
2. **DB-level guarantee** - a partial unique index,
   `documents_hospital_content_hash_active_key ON documents (hospital_id,
   content_hash) WHERE is_active AND content_hash IS NOT NULL`
   (migration `0017`), is what's actually relied on for correctness under
   concurrency: two uploads of identical content arriving at the same time
   both pass the app-level check, but only one `INSERT` can win; the loser
   raises `IntegrityError`, caught and re-raised as the same
   `DuplicateDocument` the pre-check would have produced.

**Explicit behavior: reject, never silently duplicate.** A byte-identical
active document already existing in the same hospital always produces a
`409`, whether the pre-check or the DB constraint catches it -
`test_duplicate_content_upload_rejected`.

## Re-ingestion and versioning

There is no separate "version" concept - re-uploading content is just an
upload, subject to the same idempotency rule above. The way to replace an
active document's content is: `POST .../archive` the old one (sets
`is_active = false`, which removes it from the partial unique index's
scope), then upload the replacement. This guarantees **at most one active
document per piece of content per hospital** at any time - there is no
state in which two active documents with identical content, or an
unintended "old version still searchable alongside the new one," can exist.
`test_reuploading_after_archive_is_allowed` proves the archive-then-
reupload path works; `archive_document()`
(`app/services/documents/ingestion_service.py`) does not delete a
document's chunks or history, only flips `is_active`.

## Processing states

The existing four-value `status` vocabulary
(`PENDING`/`PROCESSING`/`COMPLETED`/`FAILED`, Phase 2's `CHECK` constraint)
is reused exactly - no second, competing status system was introduced.
Because ingestion runs synchronously inside the upload request, an admin
never actually observes `PENDING`/`PROCESSING` in the API response; the
`POST /api/admin/documents` response always reflects the final
`COMPLETED`/`FAILED` state. The intermediate states exist in the database
transiently (and are visible via `GET /api/admin/documents/{id}` if
queried mid-request from another connection) and are what a future
background-worker version of this pipeline would actually expose over
time.

## Failure handling

A failure at **any** step - extraction, chunking (a document producing zero
usable chunks is itself treated as a failure), embedding (including a
dimension mismatch), or an unexpected DB error mid-insert - routes through
one shared function, `ingestion_service._fail_ingestion()`:
`db.rollback()` (discarding any chunks already inserted in this attempt,
so a failure never leaves a partial chunk set), `mark_failed()` with a safe
message, a `DOCUMENT_PROCESSING_FAILED` audit event, commit. A document is
**never** left `COMPLETED` with missing or partial chunks -
`test_wrong_dimension_embedding_marks_document_failed_with_zero_chunks` and
`test_ingestion_failure_never_leaves_document_completed` confirm zero
chunk rows remain after a mid-pipeline failure. Known/expected failures
(`ExtractionError`, `EmbeddingServiceError`, `EmbeddingDimensionMismatch`)
use their own exception's message (already safe by construction - see
each module); a genuinely unexpected exception (a DB error, etc.) instead
gets a fixed generic message, deliberately never the raw exception text,
since that could contain SQL, a file path, or other internal detail an
administrator reading `processing_error` has no business seeing.

## Audit logging

Reuses Phase 2's single `audit_logs` table - no second audit mechanism.
Four new event types, all written through the existing
`app/audit/events.py`'s `record_event()`:

- `DOCUMENT_UPLOADED` - on successful storage (before ingestion runs),
  `metadata={"document_type": ..., "sensitivity": ...}`.
- `DOCUMENT_PROCESSING_FAILED` - on any ingestion failure,
  `metadata={"reason": <safe failure message>}`.
- `DOCUMENT_UPDATED` - on a successful `PATCH`, no metadata beyond the
  standard `resource_type`/`resource_id`/`hospital_id` columns.
- `DOCUMENT_ARCHIVED` - on a successful archive.

Never logged, in any of these: document text, chunk content, embeddings,
raw file bytes, or any field beyond what's listed above - `metadata` never
carries more than a document type/sensitivity/failure-reason string.

## Security boundaries

Everything a request touches is re-verified server-side, independent of
whatever the client submitted or selected in the UI:

- **Authentication** - the same Supabase JWT verification every other
  phase uses (`get_current_auth_user`); missing/invalid token → `401`,
  inactive account → `403`.
- **Authorization (who)** - `manage_hospital_documents`, permission-driven,
  not role-hardcoded (see "Who can upload").
- **Authorization (which hospital)** - `resolve_hospital_scope()`; only
  `SUPER_ADMIN` may target a hospital other than their own, and even then
  only a hospital that actually exists.
- **Authorization (department/doctor/staff)** - every id independently
  re-validated against the resolved hospital, both on upload and on
  `PATCH` (see "Access-control metadata").
- **File content** - extension, magic bytes, size, non-empty, safe
  filename (see "File validation"); nothing uploaded is ever executed or
  interpreted as code.
- **Metadata authority** - `hospital_id`, `uploaded_by`, `status`,
  `chunk_count`, `content_hash` have no client-writable form field at all
  (see "Metadata").
- **Enumeration protection** - `GET`/`PATCH`/`archive` on a document that
  either doesn't exist or belongs to another hospital return the identical
  `404` (`_get_owned_document()`/`_not_found()`), so a caller cannot
  distinguish the two.
- **Response minimization** - list/detail responses
  (`DocumentSummary`/`DocumentDetail`) never include raw chunk content or
  embeddings; those live only in `document_chunks`, which no Phase 7
  endpoint exposes at all.

None of this is enforced by the frontend hiding a nav link or a form field
- `frontend/src/features/admin/pages/AdminDocumentsPage.tsx` hides
`/admin/documents` from users without `manage_hospital_documents` purely
for UX (see `frontend/src/auth/permissions.ts`'s existing documented
posture); every one of the boundaries above is a backend check, verified
directly against the API, independent of any frontend state.

## What Phase 8 will implement

Explicitly **not** built in this phase - the ingestion pipeline stops at
"chunks with valid 384-dimensional embeddings sit in `document_chunks`,
correctly scoped by the existing access-control tables." Phase 8 (or
later) is where:

- A user (not just an admin) queries the RAG system and gets back only
  chunks from documents they're authorized to see - permission-aware
  vector search, not "vector search, then filter."
- A semantic search / question-answering API exists for end users at all.
- Hybrid search (vector + keyword/BM25) or reranking is added.
- Ollama (or any runtime) is used for LLM generation, not just embeddings.
- A SQL-vs-RAG query router, or LLM-generated citations, exist.

## Test coverage

**298 backend tests total** (249 regression from Phases 1-6, unchanged in
behavior; **49 new** this phase): 25 in `test_document_upload.py`
(authentication, authorization - role/hospital/department/doctor/staff
escalation, file security, metadata-manipulation-is-ineffective,
idempotency), 23 in `test_document_ingestion.py` (deterministic
extraction/cleaning/chunking unit tests including the heading-attribution
regression test, embedding-dimension enforcement via mocked HTTP responses
- no live Ollama dependency for those - plus DB-integration tests
confirming persisted 384-dimensional vectors, correct chunk ordering, and
zero-chunks-on-failure), and 26 in `test_document_management.py`
(list/detail/update/archive: hospital scoping, pagination, filters,
enumeration protection, cross-hospital PATCH escalation). The upload and
DB-persistence-integration tests require a live local Ollama instance
serving `all-minilm` (the same dependency the ingestion pipeline itself
has) - the pure unit tests in `test_document_ingestion.py` do not.
