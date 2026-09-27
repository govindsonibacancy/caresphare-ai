# CareSphere AI — Permission-Aware RAG Retrieval

> **Superseded in part by Phase 9.** This document describes the Phase 8
> vector-only retrieval design. As of Phase 9, `POST /api/rag/search`
> combines this vector path with a PostgreSQL lexical path, fusion, and
> deterministic reranking - see
> [docs/RAG_HYBRID_SEARCH.md](RAG_HYBRID_SEARCH.md) for the current
> end-to-end architecture. Everything on this page about **authorization**
> (hospital isolation, role/department/doctor/staff semantics, SUPER_ADMIN/
> HOSPITAL_ADMIN behavior, enumeration protection) is unchanged and still
> accurate - Phase 9 did not touch any authorization rule, only added a
> second candidate-generation path that shares the exact same predicate
> (see `app/repositories/rag_repository.py`'s
> `_authorized_documents_where_clause`). The one thing that *did* change
> is renamed below: `search_authorized_chunks()` is now
> `search_vector_candidates()`, one of two candidate-generation functions
> rather than the only one.

Phase 8 adds the one retrieval endpoint the ingestion pipeline (Phase 7) was
built for: `POST /api/rag/search`. Given a natural-language question, it
returns only the `document_chunks` rows the authenticated caller is
authorized to see - never a broader result set filtered afterward. This
phase produces **chunks only**. It does not call an LLM, does not generate
an answer. Phase 8 itself did not implement hybrid search or reranking -
see [docs/RAG_HYBRID_SEARCH.md](RAG_HYBRID_SEARCH.md) for Phase 9, which did.

## Retrieval architecture (Phase 8 - see RAG_HYBRID_SEARCH.md for the current pipeline)

```
authenticated user (JWT)
   ↓  app/auth/dependencies.py: get_current_auth_user           (Phase 3)
   ↓  app/permissions/dependencies.py: get_user_scope
UserScope (role, hospital_id, department_id, doctor_id, staff_id)  (Phase 4)
   ↓  app/api/rag.py: POST /api/rag/search
   ↓  app/rag/retrieval_service.py: search()
   ├─ embed the query text (Ollama all-minilm, 384 dimensions - Phase 7's embedding client, reused unchanged)
   └─ app/repositories/rag_repository.py: search_vector_candidates()  (Phase 9 renamed this from search_authorized_chunks())
        SELECT ... FROM document_chunks JOIN documents
        WHERE <hospital + status + active + role/department/doctor/staff predicate>
        ORDER BY embedding <=> :query_embedding
        LIMIT :top_k
   ↓
RagSearchResponse { results: [...] }
```

Layering follows `docs/ARCHITECTURE.md`'s existing plan for this exact
module (`app/rag/` - "embeds queries, runs pgvector search within a
supplied scope", from Phase 1's scaffold) plus the established
`app/repositories/` convention ("the only layer that runs SQL against
PostgreSQL"). No new database/session infrastructure was created;
`app/core/db.py`'s `get_db`/`Session` are reused exactly as every other
phase uses them.

## `UserScope`

Reused from Phase 4 (`app/permissions/scope.py`) without modification.
`resolve_user_scope()` resolves `role`, `hospital_id`, `department_id`,
and (role-dependent) `doctor_id`/`staff_id` from the database for the
authenticated user on every request - never accepted from the client (see
"Query-parameter manipulation" below). This is the only source of identity
the retrieval query uses.

## Document authorization ("access semantics")

Reuses Phase 7's three existing access-control tables exactly as they
are - `document_allowed_roles`, `document_authorized_doctors`,
`document_authorized_staff` - with no new access-control model. The
semantics were not obvious from the schema alone (the phase 7 migration
comments hint at the intent but don't fully specify it), so they were
worked out and are fixed here as the authoritative definition, then
verified by `backend/tests/test_rag_search.py`.

**A user is authorized to retrieve a chunk from document `d` if, in
addition to the hospital/status/active gates below, at least one of three
independent grants applies (an OR/union, not an AND):**

1. **Role-based grant**: the user's role is in `d`'s
   `document_allowed_roles`, **and** `d` is either hospital-wide
   (`department_id IS NULL`) or in the user's own department.
2. **Doctor-specific grant**: the user is a doctor and their `doctor_id` is
   in `d`'s `document_authorized_doctors` - **independent of `d`'s
   department restriction**.
3. **Staff-specific grant**: the user holds a `staff` row (NURSE,
   RECEPTIONIST, or STAFF designation) and their `staff_id` is in `d`'s
   `document_authorized_staff` - also independent of department.

The department restriction only narrows the *role-based* grant. A
specific-doctor/staff grant is, per `database/migrations/0012_documents.sql`'s
own comment, "beyond default role-based access" - the natural reading of
"beyond" is that it's an additional, independent grant, not a further
restriction, so a doctor named in `document_authorized_doctors` can see the
document even if they're not in the document's department. This was a
deliberate interpretation, not an assumption left unstated - see the four
worked examples below, each verified by a `test_rag_search.py` test.

| | Department | Allowed roles | Authorized doctors | Authorized staff | Result |
|---|---|---|---|---|---|
| **A** | Emergency | DOCTOR, NURSE | - | - | Any DOCTOR/NURSE **in Emergency**; DOCTOR/NURSE in another department excluded; RECEPTIONIST/STAFF excluded regardless of department. |
| **B** | Emergency | DOCTOR | A, B | - | Any DOCTOR in Emergency, **plus** doctors A and B even if they're not in Emergency. Doctor C (not A/B, not in Emergency) excluded. |
| **C** | *(none)* | DOCTOR, NURSE | - | - | Any DOCTOR/NURSE in the hospital, **any department** (no department gate at all, since `department_id IS NULL`). RECEPTIONIST/STAFF excluded. |
| **D** | *(none)* | *(none)* | - | Staff A | Only Staff A - no role grants anything here, since `allowed_roles` is empty; every other staff member, and every doctor, is excluded. |

`backend/tests/test_rag_search.py`'s `rag_documents` fixture builds real
Examples A, C, B (minus the role-path half, to isolate the doctor-specific
grant), and D against seeded users, and tests every cell of the table above
end-to-end through the live API.

### `SUPER_ADMIN`

Unconditional bypass of every predicate above (hospital, department, role,
doctor, staff) - mirrors the exact pattern every existing `can_access_*`/
scope-clause function in this codebase already uses for `SUPER_ADMIN`
(`app/permissions/authorization.py`, `app/repositories/clinical_repository.py`).
`SUPER_ADMIN` still cannot retrieve a `PENDING`/`PROCESSING`/`FAILED`/
archived document - that gate is availability, not a
relationship-to-caller check, and applies to everyone.

### `HOSPITAL_ADMIN`

**Deliberately given no implicit bypass.** Unlike `SUPER_ADMIN`,
`HOSPITAL_ADMIN` goes through the exact same role/doctor/staff predicate as
every other role - they only retrieve a document if `HOSPITAL_ADMIN` is
itself listed in that document's `allowed_roles` (a real, supported
configuration - `AllowedRoleName` includes it), or they hold a
doctor/staff-specific grant (they typically won't). This was a considered
choice, not an oversight: unlike `appointments` (Phase 6, where
`HOSPITAL_ADMIN` gets hospital-wide operational access by design), a RAG
document's access list is the exact mechanism an admin just configured
when uploading it - giving `HOSPITAL_ADMIN` an automatic bypass here would
partially defeat the feature the admin is using. See "Known limitations"
if this turns out to be the wrong call for a future phase.

## Hospital isolation

`d.hospital_id = scope.hospital_id` is ANDed into the query for every role
except `SUPER_ADMIN` (`app/repositories/rag_repository.py`,
`_hospital_clause`) - never expressed as, or influenced by, a client-
supplied `hospital_id`. There is no `hospital_id` field on
`RagSearchRequest` at all (see "Query-parameter manipulation"), so there is
nothing for a client to override even in principle.
`test_hospital_a_user_cannot_retrieve_hospital_b_document` uses the
existing real second-hospital fixture (`second_hospital`) to prove a
Hospital A `HOSPITAL_ADMIN` gets zero results for a Hospital B document a
relevant query would otherwise clearly match, while `SUPER_ADMIN` gets it.

## Role filtering

See "Document authorization" above - `r.name = :scope_role` joined through
`document_allowed_roles`, `scope_role` always the server-resolved
`UserScope.role`, never a client-supplied value.

## Department filtering

`d.department_id IS NULL OR d.department_id = :scope_department_id` -
narrows only the role-based grant (see "Document authorization"). A user
with no department (`scope.department_id IS NULL`, e.g. most
`SUPER_ADMIN`/`PATIENT` rows) can only reach hospital-wide documents
through the role-based path, since `d.department_id = NULL` is never true
in SQL - this falls out of the query without any special-cased handling.

## Doctor-specific restrictions

`EXISTS (SELECT 1 FROM document_authorized_doctors WHERE document_id = d.id
AND doctor_id = :scope_doctor_id)`, only added to the query when
`scope.doctor_id IS NOT NULL` (i.e. the caller is a `DOCTOR`).
`scope.doctor_id` comes from `UserScope`'s own `doctors WHERE user_id =
:user_id` resolution (Phase 4) - there is no `doctor_id` field on
`RagSearchRequest` for a client to supply instead.
`test_authorized_doctor_specific_grant_is_included`/
`test_unauthorized_doctor_is_excluded_from_doctor_specific_document`/
`test_non_doctor_is_excluded_from_doctor_specific_document` cover doctor A
(authorized), doctor C (not authorized), and a non-doctor respectively.

## Staff-specific restrictions

Same pattern against `document_authorized_staff`/`scope.staff_id`.
`scope.staff_id` is resolved for `NURSE`, `RECEPTIONIST`, and `STAFF`
alike (all three map to a `staff` row, distinguished by
`staff.designation` - there is no separate "nurses" table, per Phase 2's
schema). `test_authorized_staff_specific_grant_is_included` uses a NURSE
holding the grant; `test_unauthorized_staff_is_excluded_from_staff_specific_document`
proves an un-named RECEPTIONIST and STAFF member are excluded even though
they hold a `staff_id` of the same *kind*.

## Active/processing status

`d.status = 'COMPLETED' AND d.is_active` is ANDed into every query,
unconditionally (including for `SUPER_ADMIN`) - a document that is
`PENDING`/`PROCESSING`/`FAILED`, or has been archived
(`is_active = false`), is never returned, regardless of how well its
(possibly stale, possibly absent) chunks would otherwise match. Verified
directly: `test_non_completed_document_is_excluded` flips a real,
successfully-ingested document's `status` to each of `PENDING`/
`PROCESSING`/`FAILED` after the fact and confirms it drops out of results;
`test_archived_document_is_excluded` does the same via the real
`POST .../archive` endpoint.

## Vector search

`pgvector`'s cosine-distance operator (`<=>`) between
`document_chunks.embedding` and the query embedding, ascending (closest
first), `LIMIT :limit`. Each candidate's `score` here is `1 -
cosine_distance` (higher is more similar) - but as of Phase 9 this raw
per-path score is internal only; the response's public `score` is the
hybrid/reranked value described in docs/RAG_HYBRID_SEARCH.md. The
embedding literal is built and bound the
same way Phase 7's `document_repository.insert_chunk` already does
(`"[" + ",".join(...) + "]"` cast via `CAST(:query_embedding AS vector)`),
so there is exactly one convention for pgvector literals in this codebase,
not two.

## Query embedding

`app/rag/retrieval_service.py` calls
`app.services.documents.embedding.get_embedding_service()` - the exact
Ollama `all-minilm` client Phase 7 built for ingestion, imported and
reused unchanged, not duplicated. A query is embedded through the same
model/runtime a chunk was embedded through, which is what makes cosine
similarity between them meaningful at all.

### The 384-dimension requirement

`OllamaEmbeddingService.embed()` (Phase 7) already validates every
returned vector's length against `settings.embedding_dimension` (`384`)
before returning it, raising `EmbeddingDimensionMismatch` - this runs
**before** retrieval's SQL query is ever built, so a wrongly-shaped query
vector can never reach pgvector.
`app/rag/retrieval_service.py` wraps that (and any other embedding-service
failure) into a generic `RetrievalError`, surfaced by the API as a safe
`503` - never a raw dimension number or connection detail
(`test_query_embedding_dimension_mismatch_returns_safe_error`).
`test_query_embedding_is_384_dimensional` independently confirms the live
service's actual output size.

## SQL-level authorization

The single rule this whole phase exists to enforce: **the authorization
predicate and the vector `ORDER BY`/`LIMIT` are the same SQL statement.**
`app/repositories/rag_repository.py`'s `search_vector_candidates()` (and,
as of Phase 9, `search_lexical_candidates()` alongside it - see
docs/RAG_HYBRID_SEARCH.md) is the entire authorization boundary for
retrieval - there is no Python-side
step anywhere in this codebase (in the repository, the service, or the
router) that takes a broader result set and narrows it. The prohibited
shape -

```python
results = vector_search()          # NOT what this code does
results = filter_authorized(results)
```

- does not exist here to disable or bypass; the WHERE clause itself is the
only place the decision is made. This is proven directly, not just
asserted, by
`test_repository_excludes_unauthorized_chunks_at_the_sql_level`, which
calls `rag_repository.search_vector_candidates()` directly - skipping the
API router and the service layer entirely - with an unauthorized
`UserScope` built by hand, against a document whose chunk is confirmed
(via a direct `document_chunks` count) to actually exist in the database,
and confirms the SQL query itself never returns it; the same test then
repeats the call with an authorized scope and confirms it does.

## Enumeration protection

An unauthorized-only query returns exactly `{"results": []}` - the same
shape a query that legitimately has no relevant authorized content would
return. There is no `total`, `total_documents`, `accessible_documents`, or
any other count field on `RagSearchResponse` at all
(`app/schemas/rag.py`), so there is no channel through which the existence
or number of unauthorized documents could leak, deliberately, not just by
oversight - `test_unauthorized_only_query_reveals_nothing_beyond_empty_results`
asserts the exact response shape.

## Empty-result behavior

Same shape whether the cause is "no authorized document is relevant to
this query" or "no document exists for this hospital/role at all" -
`test_no_matching_authorized_document_returns_empty_results` and
`test_patient_never_retrieves_role_scoped_documents` (a `PATIENT` gets
`{"results": []}` for every query in the fixture, by construction - see
"Patients" below) both assert the identical shape.

## Audit behavior

One new event, `RAG_SEARCH_PERFORMED`, written through the existing
`app/audit/events.py`'s `record_event()` (no second audit mechanism) on
every search - `resource_type="rag_search"`, `resource_id=None` (a search
isn't "about" one resource the way a document/patient access is),
`hospital_id=scope.hospital_id`, `metadata={"top_k": ..., "result_count":
...}`. **Deliberately never logged**: the query text itself (may contain
sensitive clinical phrasing typed by the user), retrieved chunk content,
embeddings, or anything beyond the two counters above -
`test_search_writes_audit_event_without_query_text` asserts a distinctive
marker string placed in the test's query never appears anywhere in the
written row.

## Performance / indexing

No new migration was added this phase. `document_chunks` already has
`idx_document_chunks_document_id` (Phase 2); `documents` already has
`idx_documents_hospital_id`, `idx_documents_status`, and
`idx_documents_is_active` (Phase 2/7) - sufficient for this project's
synthetic-demo data volume (a handful of documents, a few dozen chunks).
No ANN vector index (`ivfflat`/`hnsw`) was added, for the same reason
`database/migrations/0013_document_chunks.sql`'s own comment already gives:
both index types are approximate and are tuned against a populated table,
and this project's dataset doesn't yet justify one - an exact sequential
scan over a few dozen rows is both correct (exact nearest-neighbor, not
approximate) and fast. Adding `CREATE INDEX ON document_chunks USING hnsw
(embedding vector_cosine_ops)` (the exact line that comment already
suggests) is the natural next step once a real data volume exists, and
does not require changing `rag_repository.py`'s query at all - only the
query planner's chosen access path would change.

## Security tests

49 tests in `backend/tests/test_rag_search.py` (see "Test coverage" in the
final report) - authentication, every cell of the access-semantics table
above, hospital isolation, `SUPER_ADMIN` bypass, patients, processing-
status/active-state exclusion, enumeration protection, query validation
(empty/blank/oversized query, invalid `top_k`), query-parameter
manipulation (every authorization-adjacent field name rejected outright by
`extra="forbid"`), injection safety, embedding-dimension safety, audit
content, and the direct repository-level proof described under "SQL-level
authorization".

## Known limitations

- **`HOSPITAL_ADMIN` has no implicit "see everything in my hospital"
  bypass for RAG retrieval** (see "Document authorization" - `HOSPITAL_ADMIN`
  section). If a future product requirement wants hospital-wide RAG
  visibility for admins, that's an explicit addition to
  `_document_authorization_clause`, not something this phase silently
  assumed either way.
- **No patient-facing RAG documents.** `document_allowed_roles` excludes
  `PATIENT` at the schema/form level (Phase 7), and this phase adds no
  patient-specific retrieval path - a `PATIENT` calling `/api/rag/search`
  always gets `{"results": []}`. `PATIENT_SPECIFIC` is a real
  `documents.sensitivity` value in Phase 2's schema, but Phase 7's upload
  form never offers it and no document with that sensitivity exists yet;
  building patient-document retrieval semantics without a concrete
  requirement would mean inventing them, which this phase's instructions
  explicitly avoid.
- **No permission gate beyond authentication.** There is no
  `manage_hospital_documents`-style permission required to call
  `/api/rag/search` - any authenticated, active user may call it, because
  document-level authorization (not a blanket "can search" permission) is
  what decides what comes back, and the seed has no dedicated
  "can use RAG search" permission to gate on. A `PATIENT` calling the
  endpoint is not an authorization bug; it always yields `{"results": []}`
  by construction.
- **Synchronous, single-query retrieval only.** No result caching, no
  pagination beyond `top_k`, no background processing - consistent with
  Phase 7's synchronous ingestion and this project's demo scale.

## How Phase 9 extended retrieval

Not built in Phase 8, deliberately - and, per the update note at the top
of this document, now built (hybrid search, reranking) or still
deliberately deferred (everything else) as of Phase 9:

- ~~Hybrid keyword + vector search~~ - **built in Phase 9**, see
  [docs/RAG_HYBRID_SEARCH.md](RAG_HYBRID_SEARCH.md).
- ~~Reranking~~ - **built in Phase 9** (a deterministic feature-scoring
  stage, not a cross-encoder model - see docs/RAG_HYBRID_SEARCH.md,
  "Reranking").
- Ollama (or any model) generating a natural-language answer from these
  chunks - still not built; this project returns chunks, never prose, as
  of Phase 9.
- Conversational memory / multi-turn context - still not built.
- A SQL-vs-RAG query router deciding which retrieval path a question needs
  - still not built.
- LLM-generated citations (retrieval preserves the source metadata -
  `document_id`, `document_title`, `chunk_id`, `page`, `section` - a later
  citation feature will need, but does not itself format or present a
  citation) - still not built.
