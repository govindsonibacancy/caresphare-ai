# CareSphere AI — Audit & Observability

Phase 15 makes CareSphere AI able to answer *"what happened during an AI
request?"* - who, when, which route/intent, which authorization outcome,
how long each stage took, whether it succeeded, and why it failed - without
storing the healthcare content, prompts, answers, or SQL that produced
those outcomes. **Audit metadata is not application data.** This phase
extends the existing `audit_logs` table and `app/audit/events.py` module
Phase 2/3/5/6/9-14 already built; it does not introduce a second logging
system.

## Purpose

Phases 1-14 already write audit rows for specific business events
(`USER_CREATED`, `PATIENT_RECORD_ACCESSED`, `RAG_ANSWER_GENERATED`, ...),
but three real gaps existed before this phase, found by inspection, not
assumption:

1. **No request correlation.** Nothing tied the several audit rows one
   HTTP request could produce (e.g. `RAG_SEARCH_PERFORMED` plus
   `RAG_ANSWER_GENERATED`) back together, and nothing let an operator ask
   "show me everything for the request the user just reported."
2. **No authorization-denial auditing at all.** `require_permission`'s
   403 path (`app/permissions/dependencies.py`) and
   `structured_query_service.run_structured_query`'s internal permission
   check both silently produced a safe response with **no audit trail
   whatsoever** - a repeated attempt to access something forbidden left
   no trace.
3. **No failure auditing for retrieval/LLM errors.** A `RetrievalError`
   or `LLMServiceError` inside `app/llm/answer_service.py` propagated
   straight to a `503` with no audit event recording that a request
   failed, or why.

Phase 15 closes exactly these three gaps, plus records a handful of safe
timing/count metrics (`docs/ADMIN_AI.md`/`docs/QUERY_ROUTING.md`/
`docs/SOURCES_AND_CITATIONS.md` had already established the "counts and
labels, never content" pattern for the fields that did exist) - and
nothing else. It is not a new logging framework, not a dashboard, and not
an analytics platform.

## Architecture

```
Client
  ↓
RequestIDMiddleware (app/core/request_context.py) - generates request_id
  ↓
Authentication (Phase 3, unchanged)
  ↓
Current UserScope (Phase 4, unchanged)
  ↓
Authorization (require_permission / run_structured_query's internal check)
  ↓ (denied -> AUTHORIZATION_DENIED audit event, here)
Query routing (Phase 11, unchanged decision logic)
  ↓
Structured / RAG / Hybrid / Admin retrieval (Phases 6/9/11/14, unchanged)
  ↓
SourceRegistry (Phase 12, unchanged)
  ↓
LLM (Phase 10, unchanged)
  ↓
Citation validation (Phase 12, unchanged)
  ↓
Response (+ request_id)
  ↓
Audit event (RAG_ANSWER_GENERATED / RAG_SEARCH_PERFORMED, extended metadata)
```

Every box above "Response" makes exactly the same decision it made before
Phase 15 - this phase observes the pipeline, it does not alter what any
stage decides. No authorization check, retrieval query, routing decision,
or citation validation was changed; only what gets *recorded* about them.

## Request/correlation ID

`app/core/request_context.py`:

- `RequestIDMiddleware` (a `BaseHTTPMiddleware`, registered in
  `app/main.py`) generates one `uuid.uuid4()` per incoming HTTP request,
  stores it on `request.state.request_id`, and echoes it back as an
  `X-Request-ID` response header on every response, including error ones.
- `get_request_id(request) -> uuid.UUID` is a small FastAPI dependency
  reading that same value - used by `app/api/rag.py`'s two endpoints, and
  available to any future endpoint via ordinary `Depends()` injection,
  without needing to thread it through every intervening function
  manually (see "Why not thread it everywhere" below).

**Always server-generated, never accepted from a client.** An inbound
`X-Request-ID` header is completely ignored -
`test_client_supplied_request_id_header_is_never_used` and
`test_client_supplied_header_is_ignored_even_for_an_unauthenticated_endpoint`
prove this directly. This project has no existing distributed-tracing
infrastructure to interoperate with, so accepting an inbound trace id
would only add a trust boundary with no real benefit.

`request_id` is never used for authorization, and is a different kind of
value from `user_id`/`conversation_id`/`hospital_id`/`source_id`/
`document_id` - it identifies *this one HTTP call*, nothing else, and
carries no ownership or access semantics of its own.

### Why not thread it everywhere

Section 9 of this phase's own brief asks for the smallest maintainable
architecture. Two different mechanisms are used, deliberately:

- **Middleware + FastAPI dependency** gets `request_id` from the
  transport layer to any API endpoint "for free," the same way `UserScope`
  already flows via `Depends()` - no new pattern.
- **A handful of explicit, optional keyword arguments** (`request_id:
  uuid.UUID | None = None`) carry it the rest of the way down the one
  call chain that actually produces audit events for the AI pipeline
  (`answer()` → `_generate()` → `_audit()`, and `search()`). This is
  additive - every pre-Phase-15 direct caller/test of these functions
  keeps working unchanged, since the parameter defaults to `None`.

`request_id` propagation is scoped to the AI/RAG/Admin pipeline (this
phase's own architecture diagram) and to `AUTHORIZATION_DENIED` (which
piggybacks on `require_permission`, instrumenting *every*
permission-gated REST endpoint at once - see below). Older, unrelated
audit call sites (clinical record access, invitations, document
management) are unchanged and do not yet carry a `request_id` - see
"Known limitations."

## Audit event taxonomy

No new event-type enum was introduced for actions - `action` has always
been a plain string column, and this phase keeps it that way, adding two
new values to the existing vocabulary:

| Action | Since | Change in Phase 15 |
|---|---|---|
| `USER_CREATED` | Phase 3 | unchanged |
| `USER_INVITED` / `INVITATION_ACCEPTED` / `INVITATION_REVOKED` | Phase 5 | unchanged |
| `PATIENT_RECORD_ACCESSED` / `MEDICAL_RECORD_ACCESSED` / `LAB_REPORT_ACCESSED` / `PRESCRIPTION_ACCESSED` | Phase 6 | unchanged |
| `DOCUMENT_UPLOADED` / `DOCUMENT_UPDATED` / `DOCUMENT_ARCHIVED` / `DOCUMENT_PROCESSING_FAILED` | Phase 7 | unchanged |
| `RAG_SEARCH_PERFORMED` | Phase 9 | metadata gained `duration_ms`, `request_id` |
| `RAG_ANSWER_GENERATED` | Phase 10-14 | metadata substantially extended (see below); also used for Admin AI, unchanged since Phase 14 |
| **`AUTHORIZATION_DENIED`** | **Phase 15 (new)** | a `PermissionDenied` at any `require_permission`-gated endpoint |
| **`CONVERSATION_DENIED`** | **Phase 15 (new)** | a `ConversationNotFound` (not-owned or nonexistent conversation) |

Two closed, small enums back these events' metadata
(`app/audit/taxonomy.py`), used instead of ad hoc strings so metadata
stays comparable across call sites:

```python
class AuditResult(StrEnum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    NO_DATA = "NO_DATA"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"

class ErrorCode(StrEnum):
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS_QUERY = "AMBIGUOUS_QUERY"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_TIMEOUT = "LLM_TIMEOUT"
```

Only categories this codebase can actually produce are listed - no
speculative "just in case" values.

### Admin AI: no new event type

`docs/ADMIN_AI.md`'s own audit finding (re-confirmed during this phase's
inspection): Phase 14 never introduced a distinct `ADMIN_AI_QUERY`
event - every admin intent already flows through the same
`RAG_ANSWER_GENERATED` event Phase 10 built, distinguishable entirely by
`structured_intent` starting with `ADMIN_`. Phase 15 keeps this - adding
a parallel event type here would be exactly the "dozens of nearly
identical events" this phase's own brief warns against, for zero
additional query capability (`WHERE metadata->>'structured_intent' LIKE
'ADMIN_%'` already answers "show me Admin AI activity").

### Conversations: one denial event, not three

Section 12 of this phase's brief lists `CONVERSATION_CREATED`/
`CONVERSATION_ACCESSED`/`CONVERSATION_DENIED` as candidates. Only
`CONVERSATION_DENIED` was implemented as a new event. Creating a
distinct "accessed" event on *every single conversation turn* (which is
every `/api/rag/answer` call with a `conversation_id`) would multiply
audit volume by roughly 2x for information the main completion event
already carries just as well: `RAG_ANSWER_GENERATED`'s metadata gained a
`conversation_id` field (see below), so "which conversation was this
turn part of" is already answerable from the one event every turn
already produces. `CONVERSATION_DENIED` is different in kind - it is a
genuine security-boundary event (an ownership check failed), not routine
traffic, and is exactly the kind of event this phase's own instructions
say is worth its own record.

## Authorization auditing

`app/permissions/dependencies.py`'s `require_permission` dependency -
the single shared FastAPI dependency every permission-gated REST endpoint
in this codebase already uses - now records `AUTHORIZATION_DENIED` on a
`PermissionDenied` catch, before raising the `403`:

```python
audit_events.record_event(
    db,
    actor_user_id=scope.user_id,
    action="AUTHORIZATION_DENIED",
    resource_type=None,
    hospital_id=scope.hospital_id,
    request_id=request_id,
    metadata={"permission": permission},
)
```

One small change to one shared dependency instruments every existing
endpoint that uses it at once - `admin_invitations`, `admin_documents`,
`admin_departments`, `appointments`, `medical_records`, `lab_reports`,
`prescriptions`, and any future one - without editing each of them
individually. **Only the permission code that was checked is recorded** -
never the resource, never the request body, never which record was being
requested. `test_authorization_denial_is_audited_with_safe_metadata_only`
confirms a submitted request body value never appears in the resulting
audit row.

`/api/rag/answer`'s own internal authorization (`run_structured_query`'s
`has_permission` check, Phase 11) is a different, narrower kind of
"denial" - the response is always the same safe no-context message
whether the reason was "nothing relevant exists" or "not authorized for
this intent" (the existing enumeration-protection convention, unchanged).
To let the *audit* record distinguish these without changing the
response, `structured_query_service.py` gained a small, read-only
`is_intent_authorized(scope, intent)` function - it performs the exact
same permission check `run_structured_query` already does internally,
purely so `app/llm/answer_service.py` can choose an audit `result` label
(`DENIED` vs `NO_DATA`). This is never a second, independent
authorization decision - the actual gate is still, and only, inside
`run_structured_query` itself;
`test_admin_ai_denial_is_audited_as_denied_not_no_data` confirms the
label without changing anything about the response.

### Authorization denial must not leak information

No event described above ever records resource *content* - a denied
request for another patient's records produces
`{"permission": "view_patient_medical_records"}` (or whichever code was
checked), never that patient's name, id, or any record field. A
conversation-ownership denial records the *attempted* `conversation_id`
(safe: just a UUID, useful for spotting repeated probing) but never any
message content - see "Conversation auditing" below.

## Routing auditing

`RAG_ANSWER_GENERATED`'s metadata gained `classification_source` - the
value `QueryRoute.classification_source` already carried
(`"deterministic"`/`"llm"`/`"fallback"`, see docs/QUERY_ROUTING.md) but
that `_audit()` never actually recorded before this phase. `route` and
`structured_intent` were already recorded since Phase 11/12 - unchanged.
The raw query text has never been, and is still never, persisted
anywhere in an audit row.

## Structured query auditing

No new fields specific to structured queries beyond what routing/timing
already cover: `result_count` (already existed), plus the new
`retrieval_duration_ms` (time spent inside `run_structured_query`,
separate from `generation_duration_ms` and `total_duration_ms` - see
"LLM observability" below). Never the SQL, never the raw records, never
a filter value that could contain PHI (e.g. a resolved department/role
name is never written back into metadata).

## RAG auditing

`RAG_SEARCH_PERFORMED` (`app/rag/retrieval_service.py`) is unchanged in
shape, plus:

- `duration_ms` - wall-clock time for the whole `search()` call
  (embedding + vector search + lexical search + fusion + reranking).
- `request_id` - now threaded from `/api/rag/search`'s endpoint.

Everything this event already recorded before Phase 15 - `top_k`,
`result_count`, `vector_candidate_count`, `lexical_candidate_count`,
`hybrid_candidate_count` - was already safe (post-authorization counts
only, never the query text or chunk content); Phase 15 did not need to
change any of that.

`RAG_ANSWER_GENERATED` gained `source_types` - the sorted, deduplicated
list of `SourceType` values actually registered this turn (e.g.
`["document"]` or `["administrative_summary", "document"]` for a hybrid
answer) - a safe, small, useful signal for "what kind of evidence backed
this answer" without listing the sources themselves.

## LLM observability

`RAG_ANSWER_GENERATED` already recorded `model` and
`generation_duration_ms` since Phase 10; Phase 15 adds:

- `total_duration_ms` - the whole `_generate()` call, from routing
  through response construction.
- `retrieval_duration_ms` - time spent in `run_structured_query`/
  `retrieval_search` specifically (whichever ran; summed for `HYBRID`).
- A distinct `error_code` (`LLM_TIMEOUT` vs `LLM_UNAVAILABLE`) when
  generation fails - see "Error classification" below.

**No token counts.** Ollama's `/api/chat` response does include
`prompt_eval_count`/`eval_count` fields, but capturing them would require
changing `LLMService.generate()`'s return contract from `-> str` to
something richer - a change that would ripple through every fake LLM test
double across five-plus existing test files (Phase 10-14's own
`_FakeLLM` classes). This phase's own instructions explicitly permit
skipping token counts rather than modifying the LLM integration "solely
to approximate" them, and that is the call made here - documented,
not silently dropped. Character-length metrics were considered as a safe
proxy but ultimately not added either, to keep this phase's change to
`ollama_client.py` at exactly zero lines; `generation_duration_ms` and
`retrieval_duration_ms` already answer the practically useful question
("was the LLM slow, or was retrieval slow?").

## Citation observability

Unchanged in shape since Phase 12 (`cited_valid_count`/
`cited_invalid_count`) - already safe (counts only). Never the answer
text, never which specific `[Source N]` values were cited.
`test_citation_metrics_are_recorded_without_the_answer_text` plants a
sentinel string in the mocked model's answer and confirms it never
appears in the persisted metadata.

## Conversation auditing

- `RAG_ANSWER_GENERATED` gained `conversation_id` (a UUID string) - which
  conversation this turn belonged to, for every successful or
  safely-declined turn.
- `CONVERSATION_DENIED` (new, see "Audit event taxonomy") - an ownership
  check failure, audited with the attempted `conversation_id` as
  `resource_id` and nothing else.
- **Never the message content.** The actual conversation text lives only
  in `conversation_messages` (Phase 13, unchanged, bounded, already
  documented in docs/CONVERSATIONAL_AUTH_ROUTING.md) - audit rows never
  duplicate it.

### Conversation id is not an authorization mechanism

Appearing in an audit row does not change what a `conversation_id`
*means* - `resolve_conversation`'s ownership check (Phase 13, unchanged)
remains the only thing that decides whether a given id may be continued.
Nothing in Phase 15 reads audit history to make an authorization
decision.

## Admin AI auditing

Covered above ("Admin AI: no new event type") - the existing
`RAG_ANSWER_GENERATED` event, `structured_intent` prefixed `ADMIN_`,
now additionally carries `result` (so an admin permission denial reads
as `DENIED`, not a bare `NO_DATA` indistinguishable from "nothing to
show"), `classification_source`, and the timing/source-type fields every
other answer gets. No admin record content (employee counts, invitation
emails, document titles) is ever written into audit metadata - confirmed
by `test_admin_ai_query_uses_the_same_event_type_labeled_by_intent`'s
explicit allowlisted-keys assertion.

## Privacy / data minimization

**Never persisted, in any audit row, verified by
`test_no_sentinel_values_ever_appear_in_persisted_audit_data`/
`test_no_credential_or_token_like_values_appear_in_authorization_denial_audit`
using sentinel values planted at every stage of a real request (query
text, an uploaded document's content, the mocked model's answer, a
submitted request body, an `Authorization` header):**

- Patient/medical record content, lab results, prescription text.
- Raw RAG chunk/document content.
- Conversation message content.
- Full prompts sent to the LLM.
- Full model responses.
- SQL of any kind.
- JWTs, access/refresh tokens, Supabase service keys, passwords,
  invitation tokens, API keys, cookie values, `Authorization` headers.
- Embedding vectors.
- Hidden model reasoning/chain-of-thought (Ollama's response never
  includes any in this project's usage, and nothing here would forward
  it if it did).

**What audit rows do contain:** actor/hospital ids, a request id, a
closed `action` string, a closed `result`/`error_code` value where
applicable, route/intent labels, counts (`result_count`, `source_count`
via `source_types`' length, `cited_valid_count`/`cited_invalid_count`),
durations, and the model name string (a configuration value, not
content).

## Error classification

Every failure this pipeline can actually produce maps to one
`ErrorCode`:

| Exception | ErrorCode |
|---|---|
| `PermissionDenied` (via `require_permission`) | `AUTHORIZATION_DENIED` |
| `ConversationNotFound` | `NOT_FOUND` |
| `RetrievalError` (embedding-service failure) | `RETRIEVAL_FAILURE` |
| `LLMGenerationTimeout` | `LLM_TIMEOUT` |
| `LLMServiceUnavailable` / `LLMModelNotFound` / `LLMMalformedResponse` | `LLM_UNAVAILABLE` |

The raw exception message is never stored - only the fixed code above.
`LLMGenerationTimeout` is checked before the general `LLMServiceError`
catch (it's a subclass) specifically so a slow model and an unreachable
one are distinguishable in the audit trail without any new exception
type being introduced.

## Audit transaction safety

Decision, made explicit rather than left implicit: **audit persistence is
best-effort for every event type in this codebase**, old and new alike.
`app/audit/events.py`'s `record_event` wraps its `INSERT` in its own
SAVEPOINT (`session.begin_nested()` - the same pattern
`app/services/invitation_service.py` already used for an analogous need)
and catches `SQLAlchemyError` around it, logging a safe operational
warning (the event's `action` name only - never the metadata content or
the raw exception text, which could itself echo a bound parameter value)
and continuing. A failed audit write:

- Never turns a successful user request into a `500`.
- Never poisons the caller's own transaction - the caller's subsequent
  `session.commit()` (e.g. persisting a conversation turn, an invitation,
  a document) still succeeds normally, because the SAVEPOINT rollback is
  contained to the audit insert itself.

`test_audit_write_failure_is_swallowed_and_does_not_poison_the_session`
and `test_audit_write_failure_does_not_block_a_subsequent_real_insert`
verify this directly, including the case where the failure happens
before any database round trip at all (an unserializable metadata value).

`require_permission`'s own audit call additionally wraps its `db.commit()`
in a `try/except`, for the same reason - a failed audit commit must still
result in the original, correct `403`, never a `500` instead.

## Audit queryability

No frontend/API surface was built for this (see "Out of scope" in the
final report) - "make the underlying audit data queryable" was satisfied
at the schema/index level:

- `database/migrations/0020_audit_logs_request_id.sql` adds a top-level,
  indexed `request_id` column (previously, a request id would only have
  lived inside the freeform `metadata` JSONB, requiring a sequential scan
  to find). `idx_audit_logs_request_id` makes "find every event for
  request X" an indexed lookup.
- Every other listed access pattern (`action`, `hospital_id`,
  `actor_user_id`, `created_at`) was already indexed by Phase 2's
  original migration - no additional index was needed for those.

## Audit access

No new audit-read API endpoint was built in this phase - deliberately
deferred (see "Out of scope"/"Known limitations"). Authorized internal
tooling can query `audit_logs` directly (the existing pattern every prior
phase's own manual verification already used via `psql`), filtered by
the columns/indexes above. Building `GET /api/admin/audit` with its own
permission gate, pagination, and allowlisted filters is real, additional
scope this phase's own brief explicitly allows deferring ("if not
necessary for this phase, keep audit querying at repository/service
level and document that a UI/API is deferred").

## Performance considerations

- One INSERT per audit event, unchanged in count from before Phase 15 -
  no event that used to write once now writes more than once.
- The SAVEPOINT wrapper (`begin_nested()`) adds a small, constant,
  already-established-pattern overhead (the same mechanism
  `invitation_service.py` already pays for its own inserts) - not a new
  cost class, and not per-row (one savepoint per event, not per metadata
  field).
- No N+1 queries were introduced: `is_intent_authorized` is a pure
  in-memory permission-set membership check (no DB access at all - see
  `has_permission`'s own docstring), and every new timing measurement is
  a `time.monotonic()` call, not a database round trip.
- Empirical measurement (30 sequential `POST /api/rag/answer` calls,
  mocked LLM, local Postgres, `UNSUPPORTED` route so no retrieval/LLM
  work masks the pipeline+audit overhead itself): **~15ms average per
  request** (min 10ms, max ~105ms on the first, connection-warming call).
  This includes conversation creation, history load, routing, and the
  audit write - not a formal benchmark, but consistent with "no
  meaningful latency regression," matching this phase's own instruction
  not to require a strict microbenchmark absent existing performance
  infrastructure.

## Known limitations

- **`request_id` propagation does not cover every audit call site.**
  Clinical-record-access events (Phase 6), invitation events (Phase 5),
  and document-management events (Phase 7) do not yet carry a
  `request_id` - only the AI/RAG/Admin pipeline and `AUTHORIZATION_DENIED`
  (via the shared `require_permission` dependency) do. Retrofitting the
  remainder was judged out of proportion to this phase's stated
  architecture diagram, which is scoped to "AI requests."
- **No token-level LLM metrics** - see "LLM observability."
- **No audit-read API/dashboard** - see "Audit access."
- **No automated audit retention/cleanup job.** Phase 13's
  `conversation_retention_days` is a *different* data class (bounded
  conversational context) and this phase does not apply, borrow, or
  extend it to audit data, per this phase's own explicit instruction.
  Audit retention remains a documented gap, not a policy - a future
  phase should define one (likely longer than conversation retention,
  since audit/compliance requirements are usually distinct from and
  longer than operational-context requirements).
- **No distributed tracing.** `request_id` correlates events within this
  single backend's audit log; it is not a trace context propagated to
  Ollama, Supabase, or any other external system - none of which are
  altered by this phase.
- **Authorization-denial auditing covers `require_permission`-gated
  endpoints and `run_structured_query`'s admin-labeling, not every
  possible authorization decision in the codebase** - resource-level
  checks like `can_access_patient`/`can_access_appointment`
  (`app/permissions/authorization.py`) are called directly by some
  endpoints without going through `require_permission`, and a denial
  there (a `404`, per this codebase's existing enumeration-protection
  convention) is not separately audited by this phase.

## Out of scope

Confirmed not built:

- No frontend audit dashboard.
- No SIEM integration.
- No user profiling, behavior scores, or risk scores.
- No predictive analytics.
- No ML/model "quality score" of any kind.
- No raw prompt logging.
- No raw answer logging.
- No PHI stored in any audit record.
- No new parallel audit/logging system - Phase 2's `audit_logs` table and
  `app/audit/events.py` remain the only mechanism.
