# CareSphere AI — SQL + RAG Query Router

Phase 11 lets a natural-language question sent to `POST /api/rag/answer`
be answered from structured PostgreSQL data, RAG document knowledge, or
both - **routing is a classification, never an authorization decision**.
The LLM never sees SQL, never touches PostgreSQL, and never decides what
it is allowed to see; every branch that actually retrieves data reuses an
existing, independently-enforced authorization check from Phases 4-9.

## Why SQL + RAG routing exists

Phases 8-10 could only ever answer from RAG documents (hospital policies,
guidelines). A large share of real questions ("What appointments do I
have?", "Show my lab reports") are about the caller's own structured
clinical data, which lives in PostgreSQL tables, not in any document.
Phase 11 adds a second, independent retrieval path for that data and a
router that decides which path (or both) a question needs - without ever
letting the model itself query the database.

## Architecture

```
                    Authenticated User
                           │
                           ▼
                       UserScope
                           │
                           ▼
                     Query Router
              (app/routing/query_router.py)
              /            |             \
             /             |              \
            ▼              ▼               ▼
    Structured SQL        RAG          AMBIGUOUS /
  (existing authorized  (Phase 9      UNSUPPORTED
   repository calls)  hybrid retrieval)  (no retrieval,
            │              │             safe canned
            ▼              ▼              response)
      Structured      RAG chunks
       records       + context
            \              /
             \            /
              ▼          ▼
          Bounded, authorized context
                    │
                    ▼
          Phase 10 prompt + Ollama
                    │
                    ▼
             Answer + sources
```

In code: `app/api/rag.py`'s existing `POST /api/rag/answer` →
`app/llm/answer_service.py::answer()`, which now calls
`app.routing.query_router.route_query()` first, then dispatches to
`app.services.structured_query_service.run_structured_query()` and/or
Phase 9's unmodified `app.rag.retrieval_service.search()`, then Phase 10's
unmodified context/prompt/Ollama machinery. No new endpoint was added
(see "API" in docs/LLM_GENERATION.md - unchanged).

## Route types

`app/routing/intents.py`'s `Route` enum:

- **`RAG`** - pure document/policy questions. Unchanged from Phase 10.
- **`STRUCTURED`** - the answer comes entirely from PostgreSQL, via one of
  the allowlisted `StructuredIntent` values below.
- **`HYBRID`** - both a structured query and a RAG search run, and their
  results are combined into one bounded, clearly-separated context.
- **`AMBIGUOUS`** - the question could reasonably mean more than one
  thing; the system asks for clarification instead of guessing.
- **`UNSUPPORTED`** - the question looks like an attempt to manipulate the
  system (SQL-shaped text, "ignore permissions", "all patients", etc.),
  not a real information request.

## Supported structured intents

`app/routing/intents.py`'s `StructuredIntent` enum - the complete,
currently-implemented set (see `app/services/structured_query_service.py`'s
`_HANDLERS`, which asserts at import time that every enum value has a
handler):

| Intent | Backend query reused | Notes |
|---|---|---|
| `MY_APPOINTMENTS` | `clinical_repository.list_appointments` | "Mine" comes entirely from the existing scope clause - no `patient_id` filter needed. |
| `MY_MEDICAL_RECORDS` | `clinical_repository.list_medical_records` | |
| `MY_LAB_REPORTS` | `clinical_repository.list_lab_reports` | For a DOCTOR, resolves to their *assigned patients'* lab reports - the same existing scope-clause meaning Phase 6's own endpoint already has for that role, not a new behavior (see "Known limitations"). |
| `MY_PRESCRIPTIONS` | `clinical_repository.list_prescriptions` | Same pattern as lab reports. |
| `PATIENT_APPOINTMENTS` | `clinical_repository.find_patients_by_name` (new, see below) then `list_appointments` | Named-patient lookup - see "Patient / resource references". |
| `DOCTOR_DIRECTORY` | `clinical_repository.list_doctors` | A hospital-wide directory/roster query, not "who is treating me" (there is no such capability in the existing data model to expose - see "Known limitations"). |
| `DEPARTMENT_DIRECTORY` | `clinical_repository.list_hospital_departments` | |

No other intent exists. Adding a `StructuredIntent` value without a
handler is a bug (the module-level assertion in
`structured_query_service.py` would fail at import time), not a supported
extension point - intents were deliberately kept to what the *existing*
authorization/data model can genuinely answer, per this phase's own
instruction not to add an intent "merely because it sounds useful."

## Structured query flow

```
Router decides StructuredIntent
        ↓
run_structured_query(session, scope, intent, entity_reference)
        ↓
has_permission(scope, <intent's required Permission set>)?  -- NO → NO_DATA, same shape as "genuinely nothing found"
        ↓ YES
existing clinical_repository.list_*() call, scope-clause-protected exactly as Phase 6's own REST endpoint
        ↓
minimized dict records (only fields relevant to the question - see "Data minimization")
```

`_INTENT_PERMISSIONS` (`app/services/structured_query_service.py`) is a
plain mapping from each `StructuredIntent` to the *exact same*
`Permission` tuple Phase 6's own router already requires for that
resource (e.g. `MY_APPOINTMENTS` requires
`VIEW_OWN_APPOINTMENTS`/`CREATE_APPOINTMENTS`/`MANAGE_APPOINTMENTS`,
identical to `app/api/appointments.py`'s `LIST_PERMISSIONS`) - composed
from the existing `Permission` enum, never a new check.

## RAG flow

Unchanged: `app.rag.retrieval_service.search()`, Phase 9's exact function,
called with no modification. See docs/RAG_HYBRID_SEARCH.md.

## Hybrid flow

Both the structured query and the RAG search run (each independently
authorized), and both are placed in the same prompt, clearly separated:

```
<STRUCTURED_DATA>
Source: appointments
- appointment_id: ..., date: ..., time: ..., status: ..., doctor: ..., department: ..., reason: ...
</STRUCTURED_DATA>

<REFERENCE_CONTEXT>
SOURCE 1
Title: Appointment Cancellation Policy
...
</REFERENCE_CONTEXT>

<USER_QUESTION>
...
</USER_QUESTION>
```

`app/llm/prompts.py`'s `build_messages()` gained an optional
`structured_text` parameter for this - calling it with only `context_text`
(Phase 10's original call shape) produces byte-identical output to before
`structured_text` existed, so Phase 10's own prompt tests remain valid
unchanged.

## Authorization boundary ("routing is not authorization")

The router's decision is a **classification only**. `StructuredIntent`
being `MEDICAL_RECORDS`-shaped does not mean the caller may see medical
records - `run_structured_query()` independently re-checks the exact
`Permission` set that resource already requires, then calls the exact
scope-clause-protected repository function Phase 6's own REST endpoint
uses. If the router is ever wrong (e.g. Layer 2's classifier hallucinates
a structured intent no permission actually grants), the *worst* case is a
correctly-enforced `NO_DATA` result - never data exposure - because
authorization happens downstream of routing, independently, every time.

Reused without modification or duplication: `UserScope` (Phase 4),
`has_permission`/`authorize` (Phase 4), every `can_access_*` function
(Phase 4, used unchanged by Phase 6's REST endpoints, which Phase 11 does
not touch), every scope-clause helper and `list_*`/`get_*` function in
`clinical_repository.py` (Phase 6), and Phase 9's `search()` (unchanged).
**No `can_query_patient()`/`can_query_appointment()`/`can_query_lab()` or
any other parallel authorization function was created.**

## Why arbitrary SQL generation is prohibited

The LLM never sees a SQL string, a table name, or a database connection,
and has no tool/function that could execute one. `StructuredIntent` is a
closed enum resolved to one specific, pre-written, parameterized
repository query - there is no code path from natural language to an
arbitrary `SELECT`. `RagSearchRequest` (reused unmodified for
`POST /api/rag/answer`, `extra="forbid"`) has no `sql`/`route`/`intent`/
`filters`/`table` field at all, so a client attempting to submit one gets
a `422`, not silent ignoring - see "Query manipulation" below.

## LLM's role

Classification (Layer 2, optional, see below) and final answer generation
only. The LLM:

- **Never** authenticates or authorizes anyone.
- **Never** retrieves data itself - every retrieval happens before the
  model is ever invoked.
- **Never** receives a database connection, credentials, or a tool that
  could query one.
- **Never** decides what data it needs mid-conversation - the fixed order
  is routing → retrieval → context → generation, never reversed (this
  phase explicitly prohibits `question → LLM → "I need X" → retrieve X`).

## How intent classification works

Two layers, Layer 1 always tried first (`app/routing/query_router.py`):

**Layer 1 (deterministic)**: regex/keyword recognition for obvious
phrasings - "my appointments", "my lab reports", a capitalized name
followed by "'s appointments", "policy"/"guideline"/"procedure" for RAG,
and - checked *first*, before anything else - SQL-shaped or
manipulation-shaped text (`SELECT ... FROM`, `DROP TABLE`, `ignore
permissions`, `all patients`, `hospital_id=`, `patient_id=`, etc.) routes
straight to `UNSUPPORTED` regardless of what else the query contains.

**Layer 2 (LLM classification, only when Layer 1 doesn't confidently
match)**: reuses Phase 10's exact `get_llm_service()` - no second Ollama
client, no new prompt framework, just a small, strictly-constrained
classification prompt (`_CLASSIFIER_SYSTEM_PROMPT`) instructing the model
to respond with only `{"route": "...", "intent": "..."}` from the closed
enum. The response is parsed and validated against the real `Route`/
`StructuredIntent` enums; **any** parsing failure, invalid value,
inconsistent shape (e.g. `STRUCTURED` with a `null` intent), or LLM-service
failure falls back to `Route.RAG` - never to a sensitive structured route
on uncertainty. This is deliberately the *safe* default: RAG has its own
complete, independent authorization boundary, so an uncertain
classification degrading to "search documents" can never expose
structured data it shouldn't.

`classification_source` (`"deterministic"` | `"llm"` | `"fallback"`) is
internal diagnostics only - it is never part of `RagAnswerResponse` (see
"Routing confidence" below) or logged in a client-visible place.

## Routing confidence

A Layer-2 classification is not trusted as authorization evidence
regardless of how confident the underlying model output looks - there is
no numeric confidence score exposed by this phase at all (the classifier
prompt does not ask for one), and even if it did, `run_structured_query()`
would still independently re-check permissions. "The classifier said
`STRUCTURED`" and "the caller is authorized" are two separate,
independently-verified facts.

## Ambiguity handling

Two distinct kinds, both resolved without guessing:

1. **Intent ambiguity** (router-level, e.g. "Tell me about my records.") -
   `Route.AMBIGUOUS` with no structured intent at all; a static
   clarification message is returned without touching the database.
2. **Entity ambiguity** (service-level, e.g. two patients named "Asha
   Verma" both within the caller's own authorized set) -
   `run_structured_query()` returns `StructuredOutcome.AMBIGUOUS` with the
   matching candidate names. Listing those names is not a leak: they were
   already resolved through the *same* authorization-scoped
   `find_patients_by_name()` query used for the real lookup, so every
   listed name is one the caller is already authorized to see. A caller
   never sees a candidate from outside their own authorized boundary, and
   the system never silently guesses which one was meant.

Neither ambiguity path ever triggers a second, broader, or unauthorized
query "just to figure out what the user meant."

## Patient / resource references

A name mentioned in natural language (e.g. "Show John Smith's
appointments") is never converted directly to a patient id and queried.
`clinical_repository.find_patients_by_name()` (new this phase) resolves it
through a subquery using the *exact* `_patients_table_scope_clause` the
`patients` list endpoint already uses - so name resolution itself can
never reveal the existence of a patient outside the caller's own
authorization boundary, and a zero-match result is indistinguishable from
"exists but you're not authorized" (both produce the same `NO_DATA`
outcome, matching this project's established enumeration-protection
convention from Phases 6-9).

## Security model

- **SQL injection**: every value from user input reaches the database only
  as a bound parameter (`find_patients_by_name`'s `ILIKE` pattern, exactly
  like every other `clinical_repository.py` function) - never string
  interpolation. Tested directly with SQL-injection-shaped queries.
- **Authorization-field manipulation**: `RagSearchRequest`'s
  `extra="forbid"` rejects any client-submitted `route`, `intent`, `sql`,
  `hospital_id`, `patient_id`, `filters`, or similar field with `422` -
  never silently ignored, never accepted and discarded after the fact.
- **RAG is never a security fallback**: a `STRUCTURED`-route query that
  hits `NO_DATA` does *not* fall back to a RAG search of the same terms -
  the router decided this was a structured question, and an
  unauthorized/empty structured result stays that way. (A `HYBRID`
  question genuinely requesting both is not "falling back" - both paths
  were independently authorized from the start.)
- **Cross-hospital isolation**: `find_patients_by_name`'s subquery and
  every `list_*` call's scope clause pin to `scope.hospital_id` (except
  the existing `SUPER_ADMIN` bypass) exactly as Phase 6 already
  established - Phase 11 introduces no new hospital-scoping rule.

## Data minimization

Every structured handler in `structured_query_service.py` returns a small,
purpose-built dict per record (e.g. an appointment becomes `date`, `time`,
`status`, `doctor`, `department`, `reason` - never the raw `hospital_id`/
`patient_id`/`doctor_id` foreign keys, never unrelated resources). Asking
"what time is my appointment tomorrow?" never causes prescriptions, lab
reports, or other patients' data to be fetched - only the one intent's own
bounded query runs. Every list query is capped at
`_STRUCTURED_QUERY_LIMIT` (10) records - never an unbounded fetch.

## Audit logging

Still the single `RAG_ANSWER_GENERATED` event (no new audit mechanism).
`metadata` gained two fields: `route` (the `Route` value) and
`structured_intent` (when applicable) - alongside the existing
`result_count`/`model`/`generation_duration_ms`. **Still never logged**:
the query text, the full prompt, structured record content, RAG chunk
content, or the generated answer - unchanged from Phase 10.

## Performance

Every structured query is a single, bounded (`LIMIT 10`), indexed,
scope-clause-filtered SQL call - never a full-table fetch followed by
Python filtering. `PATIENT_APPOINTMENTS`'s name resolution is one bounded
(`LIMIT 5`) query; the subsequent appointment fetch is a second bounded
query - two queries total, not N+1. Appointment/doctor-directory records
resolve a doctor's/department's display name via a handful (at most
`_STRUCTURED_QUERY_LIMIT`) of additional indexed primary-key lookups
(`get_user_display_name`/`get_department`) - bounded by the same small
limit, not the unbounded-table-scan pattern this phase's instructions
warn against.

## Database changes

**No migration was required.** Phase 11 reuses Phase 2/6's existing
`patients`/`doctors`/`departments`/`appointments`/`medical_records`/
`lab_reports`/`prescriptions`/`users` tables and their existing indexes/
foreign keys exactly as they are. Two small additions were made to
`app/repositories/clinical_repository.py` - `find_patients_by_name()` and
`get_user_display_name()` - both pure Python functions built on the
existing schema and existing scope-clause helpers, not new tables or
columns.

## Testing strategy

75 new tests across three files (plus a small fix to four pre-existing
Phase 10 tests), none requiring a live Ollama server for
generation or classification (both are always mocked):

- `test_query_router.py` (37): every Layer 1 pattern (RAG/structured/
  hybrid/ambiguous/unsupported, including malicious/SQL-shaped input and
  the "unsupported check runs before structured recognition" ordering
  guarantee) plus every Layer 2 fallback path (malformed JSON, invalid
  route/intent values, inconsistent shapes, LLM failure - all falling back
  to `RAG`, never a structured route).
- `test_structured_query_service.py` (18): real-database authorization
  tests reusing the actual seeded users - patient/doctor/nurse/receptionist/
  staff/`SUPER_ADMIN` permission behavior, assigned-vs-unassigned doctor
  patient lookups, cross-hospital isolation, entity ambiguity (a real
  duplicate-named patient fixture), and data minimization (no internal
  id/authorization fields in returned records).
- `test_query_routing_e2e.py` (20): all three route types through the real
  `POST /api/rag/answer` endpoint, query-manipulation and client-override
  rejection, cross-hospital structured isolation end-to-end, and - the
  most important test in this file - direct proof (both at the service
  boundary and end-to-end through a mocked LLM) that an unauthorized
  structured query's result never reaches context construction or the
  model.
- Existing Phase 10 tests (`test_llm_answer_service.py`) needed a small,
  explicit fix: four tests whose query text didn't match any Layer 1
  pattern now pin routing to `RAG` via a `force_rag_route` fixture, rather
  than silently depending on a live Layer-2 classification call they were
  never designed to exercise. This is a routing-awareness fix, not a
  behavior change - every one of those tests still verifies exactly what
  it verified in Phase 10.

## Known limitations

- **`DOCTOR_DIRECTORY` is a hospital-wide roster, not "who is treating
  me."** There is no "my assigned doctor(s)" capability in the existing
  data model to expose without inventing new query semantics, so this
  phase did not add one - a `DOCTOR` role also lacks `VIEW_HOSPITAL_OPERATIONS`
  in the seed, so `DOCTOR_DIRECTORY` correctly returns `NO_DATA` for a
  doctor caller (verified by `test_doctor_lacks_permission_for_department_directory`
  and its `DOCTOR_DIRECTORY` equivalent).
- **`MY_LAB_REPORTS`/`MY_PRESCRIPTIONS` for a `DOCTOR` resolve to their
  *assigned patients'* records, not "the doctor's own."** This is the
  existing `_patient_owned_table_scope_clause` scope rule Phase 6 already
  established for that role, reused unmodified - not a new or surprising
  behavior, but worth naming explicitly since the intent's name ("MY_...")
  could otherwise read as patient-only.
- **No named-entity resolution for medical records/lab reports/
  prescriptions** - only `PATIENT_APPOINTMENTS` supports a named-patient
  reference. Extending the same pattern to the other three resources is
  straightforward (the entity-resolution mechanism is resource-agnostic)
  but was deliberately scoped out to keep this phase's surface area
  reviewable, per its own instruction not to add intents without a proven
  need.
- **Layer 2 classification quality is only as good as the local
  `llama3.2:3b` model.** The fallback-to-RAG-on-uncertainty design means a
  misclassification is never a security problem, but it can mean a
  legitimate structured question occasionally gets answered as a (likely
  empty) RAG search instead - a quality limitation, not a safety one.
- **Small local model instruction-following for structured data is
  imperfect.** During manual verification, `llama3.2:3b` sometimes
  declined to state clearly-authorized structured data (citing "patient
  privacy") or echoed internal formatting back verbatim, despite the
  system prompt explicitly stating the data was already authorized for
  the requester. The *architecture* was confirmed correct in every case
  (routing, authorization, and what data reached the model were all
  correct) - this is a model-quality limitation of a small 3B-parameter
  local model, consistent with the same honest limitation already noted
  in docs/LLM_GENERATION.md for Phase 10.
