# CareSphere AI

Your Intelligent Healthcare Data Assistant — a demo project showing a
permission-aware, RAG-backed chat assistant for hospital staff and patients.
All data used in this project is synthetic/demo data; no real patient
information is used.

The core design principle: **the LLM never decides authorization**. The
backend resolves the authenticated user's role, hospital, department, and
patient relationships into a data scope *before* any retrieval happens, and
the LLM only ever sees context the backend already authorized. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Stack

- **Frontend**: React, TypeScript, Vite
- **Backend**: Python, FastAPI, Pydantic
- **Auth**: Supabase Auth
- **Database**: PostgreSQL + pgvector
- **LLM**: Ollama
- **Embeddings**: Ollama (`all-minilm`, 384 dimensions)

## Structure

```
frontend/   React + TypeScript + Vite app
backend/    FastAPI service
database/   SQL migrations and seed data
docs/       Architecture and development docs
```

## Getting started

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Status

- **Phase 1** — project structure, configuration, and a backend health-check
  endpoint.
- **Phase 2** — the full hospital data model: PostgreSQL schema for
  hospitals/departments, RBAC (roles/permissions), patients/doctors/staff,
  doctor-patient assignments, appointments, medical records, lab reports,
  prescriptions, RAG document metadata (schema only), and audit logs, plus
  synthetic seed data. See [database/README.md](database/README.md).
- **Phase 3** — Supabase authentication: patient self-registration
  (`/register`), login/logout, forgot/reset password, email verification,
  and backend JWT verification + identity resolution (`GET /api/auth/me`).
  See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#authentication--supabase-auth).
- **Phase 4** — the RBAC + permission engine: role → permission resolution
  from the database, hospital/department/resource-scoped authorization
  (`GET /api/authz/me`). See [docs/AUTHORIZATION.md](docs/AUTHORIZATION.md).
- **Phase 5** — employee invitations: hospital admins invite
  doctors/nurses/receptionists/staff (`/admin`), who accept at
  `/accept-invitation` to become provisioned users - no public employee
  registration exists. See
  [docs/EMPLOYEE_INVITATIONS.md](docs/EMPLOYEE_INVITATIONS.md).
- **Phase 6** — secure, read-only structured APIs for patients, doctors,
  departments, appointments, medical records, lab reports, and
  prescriptions - every query is authorization-scoped at the database level
  (hospital isolation, doctor-patient assignment, patient ownership), not
  filtered after fetching. See
  [docs/SECURE_DATA_APIS.md](docs/SECURE_DATA_APIS.md).
- **Phase 7** — RAG document management + ingestion: admins upload
  PDF/DOCX/TXT/MD hospital documents (`/admin/documents`), which the
  backend validates, stores, extracts, cleans, chunks, and embeds
  (Ollama `all-minilm`, 384 dimensions) into the existing document schema -
  no retrieval or LLM involvement yet. See
  [docs/RAG_INGESTION.md](docs/RAG_INGESTION.md).
- **Phase 8** — permission-aware RAG retrieval: `POST /api/rag/search`
  returns only the document chunks the authenticated caller is authorized
  to see, via a single SQL query that combines hospital/department/role/
  doctor/staff authorization with the pgvector similarity search itself -
  never a broader search filtered afterward. No LLM call, no generated
  answer yet. See [docs/RAG_RETRIEVAL.md](docs/RAG_RETRIEVAL.md).
- **Phase 9** — hybrid search + reranking: `POST /api/rag/search` now
  combines pgvector similarity search with PostgreSQL native full-text
  search (both independently authorization-scoped, via the same shared SQL
  predicate), fuses the two with Reciprocal Rank Fusion, and reranks with a
  deterministic query-term-overlap signal - still no LLM call, no generated
  answer. See [docs/RAG_HYBRID_SEARCH.md](docs/RAG_HYBRID_SEARCH.md).
- **Phase 10** — Ollama LLM answer generation: `POST /api/rag/answer`
  generates a grounded natural-language answer, with source references,
  over Phase 9's authorized retrieval results only - a local Ollama chat
  model (`llama3.2:3b`), structural prompt-injection defense, a healthcare
  safety boundary, and no LLM tool/database access of any kind. No query
  routing, no SQL generation, no conversational memory, no chat UI yet.
  See [docs/LLM_GENERATION.md](docs/LLM_GENERATION.md).
- **Phase 11** — SQL + RAG query router: `POST /api/rag/answer` now routes
  a question between structured PostgreSQL data (a small, backend-owned
  allowlist of intents like "my appointments"), RAG documents, or both -
  routing is a classification only, never an authorization decision, and
  the LLM never generates SQL or touches the database. See
  [docs/QUERY_ROUTING.md](docs/QUERY_ROUTING.md).
- **Phase 12** — source authority & citations: every answer's evidence
  (RAG document chunks and/or Phase 11 structured records) is registered
  in a request-scoped, backend-owned source registry with deterministic
  `[Source N]` numbering - the LLM may cite a number the backend already
  showed it, but can never invent, modify, or authorize a source; a
  hallucinated citation is safely ignored, never fabricated into a real
  one. See [docs/SOURCES_AND_CITATIONS.md](docs/SOURCES_AND_CITATIONS.md).
- **Phase 13** — conversational auth routing: `POST /api/rag/answer`
  supports bounded, multi-turn conversations (an optional
  `conversation_id`, server-verified ownership) so a follow-up like "What
  department is that?" can be understood - but conversation context is
  never authorization. Every turn independently re-derives the caller's
  current `UserScope` and re-runs fully-authorized retrieval from
  scratch, so a resource whose access is revoked between turns is denied
  on the very next turn even in the same conversation. See
  [docs/CONVERSATIONAL_AUTH_ROUTING.md](docs/CONVERSATIONAL_AUTH_ROUTING.md).
- **Phase 14** — admin AI: `POST /api/rag/answer` answers read-only
  administrative questions ("How many active employees do we have?",
  "Show me appointment activity for Cardiology") through five closed,
  admin-only intents layered onto Phase 11's existing structured-query
  routing - the LLM never generates SQL, never touches the database, and
  never decides who counts as an administrator; every aggregate is
  computed in PostgreSQL, gated by permissions that already existed
  (`manage_users`, `manage_hospital_documents`, `view_hospital_analytics`),
  and source-backed via Phase 12's unmodified registry. See
  [docs/ADMIN_AI.md](docs/ADMIN_AI.md).
- **Phase 15** — audit & observability: every AI request now carries a
  server-generated `request_id` (never client-supplied), and
  authorization/conversation-ownership denials are audited for the first
  time (`AUTHORIZATION_DENIED`, `CONVERSATION_DENIED`), alongside richer
  safe metadata (routing layer, retrieval/LLM/total durations, source
  types, a closed error-code vocabulary) on the existing `RAG_SEARCH_PERFORMED`/
  `RAG_ANSWER_GENERATED` events - no PHI, prompts, answers, SQL, or
  credentials are ever persisted. Audit writes are best-effort and never
  turn a successful request into a failure. See
  [docs/AUDIT_AND_OBSERVABILITY.md](docs/AUDIT_AND_OBSERVABILITY.md).
- **Phase 16** — frontend AI chat: a full chat UI (`/chat`) consuming the
  existing, unmodified `POST /api/rag/answer` contract - grounded answers
  with interactive `[Source N]` citations, a source detail panel, bounded
  conversation continuation, and safe handling of ambiguous/unsupported/
  error responses. The frontend makes no authorization, retrieval, or
  clinical decision - it renders exactly what the backend already
  decided, with Markdown rendered safely (no raw HTML execution) and no
  chat content ever persisted to browser storage. See
  [docs/FRONTEND_AI_CHAT.md](docs/FRONTEND_AI_CHAT.md).
- **Phase 17** — conversation history & persistent chat UX: `GET
  /api/conversations` and `GET /api/conversations/{id}` let a user browse
  and reopen their own past conversations (`/chat/:conversationId`),
  restored identically after a refresh or a direct link - both endpoints
  reuse Phase 13's exact ownership check, never a new authorization path.
  Historical assistant messages render through the same Phase 16
  message/citation components, including their original sources. See
  [docs/CONVERSATION_HISTORY.md](docs/CONVERSATION_HISTORY.md).
