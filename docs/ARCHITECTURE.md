# CareSphere AI — Architecture

CareSphere AI is a chat-first healthcare data assistant. The system's defining
constraint is that **the LLM never decides who is allowed to see what**. Every
piece of context the LLM receives has already been filtered by the backend
against the requesting user's authenticated identity, role, and data scope.

All data in this project is synthetic/demo data. No real patient information
is used or stored.

## Request flow

```
Authentication  →  Authorization  →  Data Scope  →  RAG / DB Retrieval
      →  Authorized Context  →  LLM  →  Response
```

1. **Authentication** — Supabase Auth issues a JWT for a signed-in user. The
   frontend attaches it to every API request; the backend verifies it against
   Supabase's JWKS (signature, issuer, audience, expiration) and resolves it
   to a CareSphere application user - see "Authentication" below.
2. **Authorization** — the backend resolves the verified user to an internal
   identity: role, hospital, department, and (for clinical roles) active
   patient relationships.
3. **Data scope** — the role + identity are turned into a concrete filter:
   which hospitals/departments/patients/documents this specific request is
   allowed to touch. This is plain backend logic, computed before any
   retrieval happens.
4. **RAG / database retrieval** — SQL queries and pgvector similarity search
   are executed *with the scope filter already applied* (e.g. a
   `WHERE hospital_id = :scope_hospital AND department_id = ANY(:scope_departments)`
   clause on the vector search, not a post-filter on the results).
5. **Authorized context** — only the rows/chunks that survived the scoped
   query are assembled into the prompt context.
6. **LLM** — Ollama receives the user's question plus the pre-authorized
   context and generates a response. It has no access to the database, no
   tool that can widen its own context, and no way to "ask for more."
7. **Response** — returned to the frontend. The full request (who, what
   scope, what was retrieved, what was asked) is written to the audit log.

The important property: if the authorization/data-scope step has a bug that
*under*-scopes access, that's a bug to fix. The LLM step can never be the
thing that fixes or works around a scoping bug, because it never has a path
to data outside the context it was handed.

## Components

### Frontend — `frontend/`

React + TypeScript + Vite. Structured by feature, mirroring the product
surfaces:

```
src/
  app/            route table
  auth/           AuthProvider (session + current-user state), auth.service
                  (Supabase sign-up/in/out/reset wrappers), ProtectedRoute
  components/     shared layout/UI (app shell, nav)
  features/
    auth/         login/register/verify-email/forgot-password/reset-password/
                  accept-invitation pages
    chat/         the chat-first assistant UI
    documents/    RAG source documents visible to the current user (a later
                  phase - Phase 8 added the backend retrieval API only,
                  no chat/search UI yet)
    admin/        employee invitations, RAG document upload/management
                  (HOSPITAL_ADMIN, SUPER_ADMIN, and any role holding
                  manage_hospital_documents)
    audit/        audit log viewer (HOSPITAL_ADMIN, SUPER_ADMIN)
    profile/      current user's account/role/hospital info
  lib/            API client (attaches the Supabase access token), Supabase client
  types/          shared TS types (e.g. Role)
```

The frontend never computes authorization. It sends the user's request to the
backend and renders whatever the backend decided is safe to return.

### Backend — `backend/app/`

FastAPI + Pydantic, layered so authorization stays separable from everything
else:

```
app/
  api/            HTTP routes — thin, delegate to services
  core/           settings/config, app wiring
  auth/           verifies the Supabase JWT, resolves the current user
  permissions/    Role enum + the rules that turn (user, role, scope) into filters
  repositories/   the only layer that runs SQL against PostgreSQL
  rag/            embeds queries, runs pgvector search *within* a supplied scope
  llm/            Ollama client + prompt construction — takes pre-authorized
                   context in, has no DB or retrieval access of its own
  services/       orchestrates auth → permissions → repositories/rag → llm
  schemas/        Pydantic request/response models
  models/         ORM/table definitions
  audit/          writes the audit trail
```

`services/` is where the request-flow pipeline above is actually implemented:
it calls `auth` to get the user, `permissions` to get a scope, `repositories`/
`rag` to fetch data *using* that scope, and only then calls `llm`.

### Authentication — Supabase Auth

```
React Frontend → Supabase Auth → Supabase JWT → Authorization: Bearer <token>
    → FastAPI verifies the JWT (JWKS: signature, issuer, audience, expiration)
    → extract Supabase auth user id (`sub`)
    → look up users.auth_user_id → CareSphere application user
```

Supabase owns authentication identity: email, password, email verification,
password reset, and the session/access/refresh tokens. **No password or
token is ever stored in the CareSphere database.** CareSphere Postgres owns
everything about the *application* user: hospital, role, department,
patient/doctor/staff profile, and `is_active` status, linked to Supabase via
`users.auth_user_id`.

The frontend talks to Supabase directly (`frontend/src/lib/supabase.ts`,
`frontend/src/auth/auth.service.ts`) for sign-up, sign-in, sign-out,
password reset, and session/token retrieval - the backend is never in that
request path. What the backend does own is verifying the resulting token and
resolving it to an application identity:

- **JWT verification** (`backend/app/auth/jwt_verifier.py`) — checks
  signature (against the Supabase project's JWKS), issuer, audience, and
  expiration. It never decodes a token with signature verification disabled
  and never trusts a client-supplied claim. The verifier is injected as a
  FastAPI dependency (`get_token_verifier`) specifically so it can be swapped
  for a test double without touching route code or making a real network
  call in tests.
- **Identity resolution** (`backend/app/auth/dependencies.py`,
  `get_current_auth_user`) — looks up the verified token's `sub` claim
  against `users.auth_user_id`. An identity with no matching row is rejected
  (`403`, "not provisioned") rather than auto-created with any role; an
  `is_active = false` row is also rejected (`403`). Neither case makes a
  permission decision - that's Phase 4. This is as far as Phase 3 goes:
  **`GET /api/auth/me` returns who the user is, not what they're allowed to do.**
- **Patient self-registration** (`POST /api/auth/register`,
  `backend/app/repositories/user_repository.py`) — the only public
  (unauthenticated) auth endpoint. It never trusts a client-supplied
  `auth_user_id` at face value: it confirms the id names a real Supabase
  identity whose email matches, via Supabase's service-role Admin API
  (`backend/app/auth/supabase_admin.py` - the only place this backend uses
  that key, and it never returns it to a client), before creating the
  `users` + `patients` rows. The request schema has no `role`, `hospital_id`,
  or permission field at all, and rejects unexpected fields outright
  (`extra="forbid"`) rather than silently ignoring them - the role (`PATIENT`)
  and hospital (the single seeded hospital; see
  [database/README.md](../database/README.md#patient-registration-hospital-assignment))
  are decided entirely server-side.
- **Employee accounts are not self-registerable.** There is no
  `/register-doctor` or similar, and no request body field that could name a
  privileged role. Hospital staff accounts are provisioned by a
  `HOSPITAL_ADMIN`-driven invitation flow - Phase 5.
- **Frontend route protection is UX only**
  (`frontend/src/auth/ProtectedRoute.tsx`). It redirects an unauthenticated
  browser away from pages like `/chat`, but it is not the security boundary:
  the backend independently requires and verifies a bearer token on every
  protected endpoint regardless of what the frontend does or doesn't render.
- **Authentication events not yet audited.** `audit_logs` (Phase 2) has an
  integration point for `LOGIN_SUCCESS`/`LOGIN_FAILURE`, and registration
  writes a `USER_CREATED` row today, but sign-in itself is a direct
  frontend-to-Supabase call the backend never observes. Auditing it would
  require either proxying login through the backend or adding a dedicated
  frontend-to-backend notification call - deferred to the dedicated audit
  phase rather than bolted on here (see the comment in
  `backend/app/audit/events.py`).

### Database — PostgreSQL + pgvector

A single PostgreSQL database (Supabase Postgres in production) holds both:

- **Relational data**: hospitals, departments, users, patients, care-team
  relationships, audit logs.
- **Vector data**: document chunks with `pgvector` embedding columns, used
  for RAG similarity search.

Keeping both in one database lets scope filters (hospital/department/patient
ACLs) live in regular relational tables that are joined directly into the
vector search query — there's no separate system to keep in sync.

### RAG pipeline — `backend/app/services/documents/` (ingestion, Phase 7) + `backend/app/rag/` (retrieval + hybrid search, Phase 8/9) + `backend/app/llm/` (answer generation, Phase 10) + `backend/app/routing/` (query routing, Phase 11) + `backend/app/services/conversation_service.py` (conversational context, Phase 13)

1. **Ingestion (Phase 7 — implemented)**: an admin uploads a source document
   (hospital policy, department guideline, etc.); the backend validates,
   stores, extracts, cleans, chunks, and embeds it via Ollama's local
   `all-minilm` model (384 dimensions, matching `document_chunks.embedding
   VECTOR(384)`), then persists the chunks. See
   [docs/RAG_INGESTION.md](RAG_INGESTION.md) for the full pipeline.
2. **Retrieval (Phase 8 — implemented)**: `POST /api/rag/search` embeds the
   user's question with the same model, then runs a pgvector similarity
   search **scoped** to the caller's authorized hospital/department/
   doctor/staff document-access set in a single SQL query - the
   authorization predicate and the `ORDER BY`/`LIMIT` are the same
   statement, never a filter applied to results afterward. See
   [docs/RAG_RETRIEVAL.md](RAG_RETRIEVAL.md).
3. **Hybrid search + reranking (Phase 9 — implemented)**: the same endpoint
   now also runs an equally-authorized PostgreSQL full-text search
   (`document_chunks.search_vector`, a generated `tsvector` column) as a
   second candidate path, fuses it with the vector path via Reciprocal
   Rank Fusion, and reranks with a deterministic query-term-overlap
   signal - both candidate paths share one authorization predicate, so
   neither can drift from the other. Still returns authorized chunks only;
   no LLM is called and no answer is generated - that's Phase 10. See
   [docs/RAG_HYBRID_SEARCH.md](RAG_HYBRID_SEARCH.md).
4. **LLM answer generation (Phase 10 — implemented)**: `POST /api/rag/answer`
   reuses the exact same authorized retrieval (steps 2-3, unmodified),
   builds a bounded context from the results, and generates a grounded
   answer via a local Ollama chat model (`llama3.2:3b`) - structurally
   separated system/user/reference-material prompt sections defend against
   prompt injection from retrieved document content, and the model has no
   tool/database/file access of any kind. No query routing, no SQL
   generation, no conversational memory. See
   [docs/LLM_GENERATION.md](LLM_GENERATION.md).
5. **SQL + RAG query router (Phase 11 — implemented)**: the same endpoint
   now first classifies a question (`app/routing/query_router.py`) as
   `RAG`/`STRUCTURED`/`HYBRID`/`AMBIGUOUS`/`UNSUPPORTED` - a
   classification only, never an authorization decision - then dispatches
   to Phase 9's unmodified retrieval and/or a small, backend-owned
   allowlist of structured intents (`app/services/structured_query_service.py`)
   that reuse the exact Phase 4-6 `UserScope`/`can_access_*`/
   `clinical_repository.py` authorization, never a parallel one. The LLM
   never generates SQL, never queries PostgreSQL, and never decides what
   to retrieve. See [docs/QUERY_ROUTING.md](QUERY_ROUTING.md).
6. **Source authority & citations (Phase 12 — implemented)**: every
   answer's RAG chunks and/or structured records are registered in a
   fresh, request-scoped `SourceRegistry` (`app/sources/`) *before* the
   LLM is ever called, with deterministic, backend-assigned `[Source N]`
   numbering unified across both kinds. The model may cite a number it was
   shown; a citation naming anything else is validated against the
   registry and safely ignored - it can never create a new source object.
   See [docs/SOURCES_AND_CITATIONS.md](SOURCES_AND_CITATIONS.md).
7. **Conversational auth routing (Phase 13 — implemented)**: the same
   endpoint now optionally accepts a `conversation_id`, server-verified
   against the *current* `UserScope` (never trusted at face value), and
   loads a small, bounded slice of that conversation's recent text to
   help routing resolve a follow-up like "What department is that?".
   Conversation context is never authorization - every turn re-runs
   steps 2-6 above completely fresh, so a resource whose access is
   revoked between turns is unreachable on the very next turn even in
   the same conversation. See
   [docs/CONVERSATIONAL_AUTH_ROUTING.md](CONVERSATIONAL_AUTH_ROUTING.md).
8. **Admin AI (Phase 14 — implemented)**: five closed, admin-only
   `StructuredIntent` values (employee/invitation/appointment/document
   summaries) layered onto step 5's existing dispatch and permission
   check - no new route, no new router, no arbitrary SQL. The LLM never
   generates SQL, never queries the database, and never decides who is
   an administrator; every aggregate is computed in PostgreSQL and gated
   by permissions that already existed (`manage_users`,
   `manage_hospital_documents`, `view_hospital_analytics`, the last
   previously unused). See [docs/ADMIN_AI.md](ADMIN_AI.md).
9. **Audit & observability (Phase 15 — implemented)**: a server-generated,
   never-client-supplied `request_id` (`app/core/request_context.py`)
   correlates every stage of one AI request; `require_permission`
   (`app/permissions/`) and conversation-ownership denials are now
   audited (`AUTHORIZATION_DENIED`/`CONVERSATION_DENIED`) for the first
   time, and the existing `RAG_SEARCH_PERFORMED`/`RAG_ANSWER_GENERATED`
   events gained safe timing/classification/source-type metadata - never
   query text, answers, SQL, or credentials. Audit persistence is
   best-effort (a SAVEPOINT-isolated write) and never turns a successful
   request into a failure. See
   [docs/AUDIT_AND_OBSERVABILITY.md](AUDIT_AND_OBSERVABILITY.md).
10. **Frontend AI chat (Phase 16 — implemented)**: `frontend/src/features/chat/`
    consumes this endpoint exactly as documented above - the frontend makes
    no authorization/retrieval/routing decision of its own. `[Source N]`
    citations are rendered as interactive elements matched against the
    response's own `sources` list (never invented from a bare number), and
    Markdown is rendered without ever executing raw HTML. See
    [docs/FRONTEND_AI_CHAT.md](FRONTEND_AI_CHAT.md).
11. **Conversation history (Phase 17 — implemented)**: `GET
    /api/conversations`/`GET /api/conversations/{id}` (`app/api/conversations.py`)
    expose Phase 13's existing conversation model for browsing/reopening -
    both reuse `resolve_conversation`'s exact ownership check, never a
    second authorization path. `conversations.title` (deterministic,
    derived from the first message) and `conversation_messages.sources`
    (the same `SourceReference` list already returned per turn) are the
    only new columns (migration 0021). See
    [docs/CONVERSATION_HISTORY.md](CONVERSATION_HISTORY.md).

### RBAC / permission filtering — `backend/app/permissions/`

Implemented in Phase 4; see **[docs/AUTHORIZATION.md](AUTHORIZATION.md)** for
the full model, role-by-role scope table, and worked allow/deny examples.
Summary:

- `roles`/`permissions`/`role_permissions` (Phase 2) are the only source of
  truth for what a role can do - never hard-coded in Python, always a live
  query (`resolve_user_scope`).
- `UserScope` (`app/permissions/scope.py`) is built fresh per request from
  the database: role, permission set, hospital/department, and (role-
  dependent) `patient_id`/`doctor_id`/`staff_id`.
- `has_permission(scope, code)` + resource-relationship functions
  (`can_access_patient`/`can_access_appointment`/`can_access_medical_record`/
  `can_access_lab_report`/`can_access_prescription`, `app/permissions/authorization.py`)
  compose into `authorize()` - holding a permission never implies access to
  every resource of that type; a doctor's actual patient access is governed
  by `doctor_patient_assignments`, not hospital membership alone.
- `require_permission(code)` (`app/permissions/dependencies.py`) is the
  FastAPI dependency endpoints use; it's built on Phase 3's
  `get_current_auth_user`, not a reimplementation of authentication.

This module produces the scope object that `repositories/` and `rag/` (later
phases) consume — it does not itself touch business logic outside
authorization.

### LLM — Ollama (`backend/app/llm/`)

Implemented in Phase 10 - see [docs/LLM_GENERATION.md](LLM_GENERATION.md).
A locally-hosted model (`llama3.2:3b`) served via Ollama. It receives a
prompt built entirely from the authorized context assembled in the
previous steps, plus the user's question. It has no function-calling
access to the database, no ability to request additional records, and no
visibility into other users' data.

### Audit logging — `backend/app/audit/`

Every request that reaches the retrieval/LLM stage is logged: which user,
role, scope, what was retrieved (at a reference level, not full content
duplication), and what question was asked. `HOSPITAL_ADMIN` and
`SUPER_ADMIN` can review this via the frontend's `audit` feature.

## What's implemented so far

- **Phase 1** — project structure, configuration, backend health-check.
- **Phase 2** — the full hospital data model (schema + synthetic seed data);
  see [database/README.md](../database/README.md).
- **Phase 3** — Supabase authentication: patient self-registration, login/
  logout/forgot-password/reset-password on the frontend, and backend JWT
  verification + identity resolution (`GET /api/auth/me`), as described
  above.
- **Phase 4** — the RBAC + permission engine: `UserScope` resolution,
  `has_permission`/`authorize`/`can_access_*`, the `require_permission`
  FastAPI dependency, and `GET /api/authz/me`. See
  [docs/AUTHORIZATION.md](AUTHORIZATION.md).
- **Phase 5** — employee invitations: a `HOSPITAL_ADMIN`/`SUPER_ADMIN`
  invites a `DOCTOR`/`NURSE`/`RECEPTIONIST`/`STAFF` employee
  (`POST /api/admin/invitations`), who accepts it at `/accept-invitation`
  (`POST /api/invitations/{token}/accept`) to become a provisioned user.
  Reuses Phase 3's Supabase Admin verification and Phase 4's
  `require_permission`/`UserScope` rather than adding a parallel
  authorization path. See [docs/EMPLOYEE_INVITATIONS.md](EMPLOYEE_INVITATIONS.md).
- **Phase 6** — read-only structured APIs for `patients`/`doctors`/
  `departments`/`appointments`/`medical-records`/`lab-reports`/
  `prescriptions`, built entirely on Phase 4's `authorize()`/`can_access_*`
  (extended with `can_access_doctor`/`can_access_department`, not
  reimplemented) - hospital/department/patient-ownership/doctor-assignment
  scope is enforced in the SQL query itself, not filtered after fetching.
  See [docs/SECURE_DATA_APIS.md](SECURE_DATA_APIS.md).
- **Phase 7** — RAG document ingestion: an admin (any role holding the
  existing `manage_hospital_documents` permission) uploads a PDF/DOCX/TXT/MD
  file (`POST /api/admin/documents`), which the backend validates, stores,
  extracts, cleans, chunks, and embeds (Ollama `all-minilm`, 384
  dimensions) into Phase 2's existing `documents`/`document_chunks`/
  access-control schema - no new tables, no LLM involvement, no retrieval.
  See [docs/RAG_INGESTION.md](RAG_INGESTION.md).
- **Phase 8** — permission-aware RAG retrieval: `POST /api/rag/search`
  embeds a question and returns only `document_chunks` the caller is
  authorized to see, via a single SQL query combining hospital/department/
  role/doctor/staff authorization with the pgvector similarity search -
  never vector-search-then-filter. No LLM call, no generated answer. See
  [docs/RAG_RETRIEVAL.md](RAG_RETRIEVAL.md).
- **Phase 9** — hybrid search + reranking: `POST /api/rag/search` adds a
  PostgreSQL full-text candidate path alongside the Phase 8 vector path
  (one shared authorization predicate for both), fused via Reciprocal Rank
  Fusion and reranked by a deterministic query-term-overlap signal - no ML
  reranker model, no LLM. See
  [docs/RAG_HYBRID_SEARCH.md](RAG_HYBRID_SEARCH.md).
- **Phase 10** — Ollama LLM answer generation: `POST /api/rag/answer`
  reuses Phase 9's retrieval unmodified, builds a bounded context, and
  generates a grounded answer + source references via a local Ollama chat
  model (`llama3.2:3b`) - structural system/user/reference-material prompt
  separation defends against prompt injection from retrieved document
  content; the model has no database/file/tool access. See
  [docs/LLM_GENERATION.md](LLM_GENERATION.md).
- **Phase 11** — SQL + RAG query router: `POST /api/rag/answer` classifies
  a question (RAG / structured / hybrid / ambiguous / unsupported) and
  answers from authorized PostgreSQL data, RAG documents, or both - a
  small, backend-owned allowlist of structured intents (appointments,
  medical records, lab reports, prescriptions, a named-patient lookup, and
  doctor/department directories), each reusing Phase 4-6's existing
  authorization exactly, never a parallel check. No arbitrary SQL
  generation, no LLM database access. See
  [docs/QUERY_ROUTING.md](QUERY_ROUTING.md).
- **Phase 12** — source authority & citations: a unified, request-scoped
  source registry (`app/sources/`) assigns deterministic `[Source N]`
  numbers to every RAG chunk and structured record actually supplied to
  the LLM, before generation ever runs. The model's own citations are
  validated against it afterward - a real source is kept, a hallucinated
  one (out of range, including `0`/negative) is safely ignored and never
  becomes a real source object. No fact-checking, no claim-level
  attribution, no persisted citation state. See
  [docs/SOURCES_AND_CITATIONS.md](SOURCES_AND_CITATIONS.md).
- **Phase 13** — conversational auth routing: `POST /api/rag/answer`
  supports bounded, multi-turn conversations (`conversations`/
  `conversation_messages`, server-verified ownership, a capped history
  window) so a follow-up can be understood without ever treating history
  as authorization - every turn independently re-derives the caller's
  current `UserScope` and re-runs retrieval from scratch, so revoked
  access is denied on the very next turn even in the same conversation.
  No long-term/semantic memory, no agent framework, no tool calling. See
  [docs/CONVERSATIONAL_AUTH_ROUTING.md](CONVERSATIONAL_AUTH_ROUTING.md).
- **Phase 14** — admin AI: read-only administrative intelligence for
  `HOSPITAL_ADMIN`/`SUPER_ADMIN` - employee counts, pending invitations,
  appointment activity, and document inventory, each a closed,
  permission-gated `StructuredIntent` reusing Phase 11's routing and
  Phase 12's source registry unmodified. No arbitrary SQL, no write
  operations, no employment/clinical decisions, no predictive claims.
  See [docs/ADMIN_AI.md](ADMIN_AI.md).
- **Phase 15** — audit & observability: a server-generated `request_id`
  correlates every stage of an AI request; permission and
  conversation-ownership denials are audited for the first time
  (`AUTHORIZATION_DENIED`/`CONVERSATION_DENIED`); existing RAG/answer
  audit events gained safe routing/timing/source-type metadata. Audit
  metadata is not application data - no query text, answer text, RAG
  content, SQL, or credentials are ever persisted, and audit writes are
  best-effort so they can never turn a successful request into a
  failure. See
  [docs/AUDIT_AND_OBSERVABILITY.md](AUDIT_AND_OBSERVABILITY.md).
- **Phase 16** — frontend AI chat: `/chat` consumes the unmodified
  `POST /api/rag/answer` contract - grounded answers, interactive
  citations matched against the response's own `sources` (never
  invented), a source detail panel, and bounded conversation
  continuation. No authorization/retrieval/routing logic exists in the
  frontend; Markdown never executes raw HTML; no chat content is ever
  persisted to browser storage. See
  [docs/FRONTEND_AI_CHAT.md](FRONTEND_AI_CHAT.md).
- **Phase 17** — conversation history & persistent chat UX: two new
  read-only endpoints (`GET /api/conversations`, `GET /api/conversations/{id}`)
  let a user list and reopen their own past conversations, restored
  identically via `/chat/:conversationId` after a refresh or direct link.
  Both reuse Phase 13's exact ownership check - no new authorization
  path. Historical messages (including their sources) render through the
  same Phase 16 components, never a second renderer. See
  [docs/CONVERSATION_HISTORY.md](CONVERSATION_HISTORY.md).
