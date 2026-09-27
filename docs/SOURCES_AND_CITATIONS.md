# CareSphere AI — Source Authority & Citations

Phase 12 makes every answer `POST /api/rag/answer` returns traceable to
the specific, already-authorized evidence that produced it - both Phase
9's RAG document chunks and Phase 11's structured PostgreSQL records -
through one unified, backend-owned source model. **The backend is the
sole authority on source identity.** The LLM may reference a source
number the backend already showed it, but it can never invent, modify, or
authorize one.

## Why citations exist

A healthcare assistant's answer is only useful if its claims can be
traced back to something real. Phases 10-11 already assembled bounded,
authorized context and generated grounded answers, but structured records
had no citation mechanism at all (only RAG chunks were numbered), and
nothing validated that a model's own `[Source N]` reference actually
named something real. Phase 12 closes both gaps without changing how
retrieval or authorization work.

## Source authority model

```
Authorized Retrieval  (Phase 9 search() / Phase 11 run_structured_query())
        ↓
Backend Source Registry  (app/sources/registry.py - request-scoped, in-memory)
        ↓
Backend Source IDs  ("SOURCE 1", "SOURCE 2", ... - assigned here, nowhere else)
        ↓
Context supplied to LLM  (app/rag/context.py + app/llm/answer_service.py's structured formatter)
        ↓
LLM answer + optional [Source N] references
        ↓
Backend citation validation  (app/sources/citations.py)
        ↓
Final response (registry's own source list - never anything parsed from the model)
```

The prohibited shape - `LLM invents citation → API trusts citation` - has
no code path in this codebase: `RagAnswerResponse.sources` is built
exclusively from the `SourceRegistry`, which was fully populated *before*
`get_llm_service().generate()` is ever called. Nothing the model returns
can add, modify, or remove an entry from it.

## Unified source model

`app/sources/models.py`'s `SourceType` - a closed enum matching exactly
the real, implemented retrieval capabilities, no more:

```python
class SourceType(StrEnum):
    DOCUMENT = "document"           # Phase 9 RAG chunks
    APPOINTMENT = "appointment"     # Phase 11 MY_APPOINTMENTS / PATIENT_APPOINTMENTS
    MEDICAL_RECORD = "medical_record"   # Phase 11 MY_MEDICAL_RECORDS
    LAB_REPORT = "lab_report"       # Phase 11 MY_LAB_REPORTS
    PRESCRIPTION = "prescription"   # Phase 11 MY_PRESCRIPTIONS
    DOCTOR = "doctor"               # Phase 11 DOCTOR_DIRECTORY
    DEPARTMENT = "department"       # Phase 11 DEPARTMENT_DIRECTORY
```

**No `PATIENT` type exists.** No Phase 11 structured intent returns a
patient-profile record directly - `PATIENT_APPOINTMENTS` resolves a named
patient only to look up *their appointments*, which are the actual
`APPOINTMENT`-typed source. Adding a source type with nothing that could
ever produce it was explicitly out of scope.

`Source` (same module) is the one representation for both kinds - a
`number`/`id` (backend-assigned, see "Source numbering"), a `type`, a
`label`, and a set of document-only fields
(`document_id`/`document_title`/`document_type`/`chunk_id`/`page`/
`section`) that are simply `None` for a structured source.

## RAG sources

Every chunk `app/rag/context.py`'s `build_context()` actually includes in
the bounded reference context (unchanged Phase 9/10 logic - see
`docs/LLM_GENERATION.md`, "Context construction") is registered exactly
once, in the same order, via:

```python
for context_source in context.sources:
    registry.add(
        type=SourceType.DOCUMENT,
        label=context_source.result.document_title,
        document_id=..., document_title=..., document_type=...,
        chunk_id=..., page=..., section=...,
    )
```

No additional document is retrieved for citation purposes - a source is
registered *because* it was already in the bounded context, never the
other way around. `build_context()` gained one new parameter,
`start_number` (default `1`, so calling it without one is byte-identical
to Phase 10), so its "SOURCE N" text labels can continue a numbering
sequence a structured registration already started (see "Source
ordering").

## Structured sources

Every record `run_structured_query()` actually returns (Phase 11,
unmodified) is registered via `app/llm/answer_service.py`'s
`_register_structured_sources()`, which maps the result's `source` string
(`"appointments"`, `"lab_reports"`, etc.) to a `SourceType` via
`STRUCTURED_SOURCE_TYPES`, and builds a safe, human-readable `label` via
`structured_query_service.build_source_label()` - for example
`"Appointment — 2026-09-25 10:30:00"`, `"Lab Report — Fasting Blood
Glucose"`, `"Doctor — Rohan Mehta"`. **A structured source carries no
field beyond `type`/`label` at all** - no raw `appointment_id`/
`patient_id`/foreign key is exposed as citation metadata, matching Phase
11's own data-minimization stance; the label itself is already safe,
backend-built text, not a database identifier.

## Hybrid sources

For a `HYBRID` route, structured sources are registered **first**, then
RAG sources continue the same numbering (`build_context(...,
start_number=len(registry) + 1)`) - so a hybrid answer's context might
show `SOURCE 1`/`SOURCE 2` as appointments and `SOURCE 3` as a policy
document, all in one unified sequence the model sees and can cite from
uniformly. Both `<STRUCTURED_DATA>` and `<REFERENCE_CONTEXT>` sections use
these same backend-assigned numbers - there is no separate,
independently-numbered scheme for each kind.

## Source registry

`app/sources/registry.py`'s `SourceRegistry` - a plain, request-scoped,
in-memory list. A **fresh instance is created inside every single
`answer()` call** (`app/llm/answer_service.py`) and discarded when that
call returns; nothing about it is persisted to the database, cached, or
shared across requests or users (`test_two_registries_never_share_state`
proves this directly). It has exactly one write method (`add()`, which
always appends and assigns the next sequential number) and one read
method relevant to validation (`is_valid(number)`) - there is no method
that could remove, renumber, or overwrite an entry, and no method that
queries anything itself.

## Source numbering

Deterministic, sequential, backend-assigned, unique within one answer:
`number = len(existing sources) + 1` at the moment of registration, never
random, never reused, never assigned by the model or accepted from a
client. `id` is `f"source-{number}"` - a stable, API-facing string form of
the same fact, not an independent identifier the model or client could
forge a mismatch with.

## Source ordering

Structured sources are registered before RAG sources (see "Hybrid
sources") - a deliberate, documented choice, not the model's or a sort
function's decision. Within each kind, the existing order is preserved
exactly: structured records in `run_structured_query()`'s own return
order (Phase 11, unmodified), RAG chunks in Phase 9's fused-and-reranked
relevance order (unmodified). Nothing here re-sorts by anything the model
produced.

## Citation validation

`app/sources/citations.py`'s `validate_citations(answer_text, registry)`
parses every `[Source N]`-shaped marker (`\[Source\s+(-?\d+)\]`,
case-insensitive) out of the model's own answer text and classifies each
as:

- **valid** - `registry.is_valid(N)` is true (an actual, backend-
  registered source) - deduplicated to one entry per number, in
  first-seen order.
- **invalid** - `N` is `0`, negative, larger than anything ever
  registered, or otherwise out of range - a hallucinated or malformed
  reference.

Malformed syntax (`[Source]`, `[Source abc]`, `(Source 1)`, plain `Source
1` with no brackets) simply produces **no match at all** - the regex
either matches a well-formed `[Source N]` or it doesn't; there is no
partial-match/exception path, and a garbled answer is never allowed to
fail the whole request over unparseable citation syntax. An invalid
number is recorded (currently surfaced only as an audit count, see "Audit
logging") but **never becomes a `Source` object, never appears in
`sources`, and never triggers a new database lookup** - it is simply
ignored, exactly as required.

## Authorization boundary

Citation validation runs entirely **after** generation, over data that
was already authorized **before** generation - it is bookkeeping, not a
security gate. The actual authorization boundary is upstream and
unchanged from Phases 9/11: `search()`'s SQL predicate and
`run_structured_query()`'s permission check are what decide whether
something can ever reach the registry at all. If routing or a classifier
were ever wrong, the worst case is still a correctly-empty registry (see
Phases 9's/11's own "authorization is enforced by the repository, not a
filter" proofs, re-verified here for the registry boundary specifically
by `test_unauthorized_rag_source_never_reaches_registry_or_llm` and
`test_unauthorized_structured_source_never_reaches_registry`, which
inspect the registry *before* any LLM call, not just the final response).

## LLM limitations

The `SourceReference`/`Source` model and citation validation are entirely
independent of model quality - they hold regardless of what the model
does. In practice, this project's small local `llama3.2:3b` model does not
always cite cleanly: during manual verification of a `HYBRID` question
combining several structured records with a document, the model at least
once produced a degenerate response that echoed the raw prompt structure
back (including a self-invented follow-up question) instead of answering.
The **architecture held correctly regardless** - the registry and
`sources` in the response were still built entirely from real, authorized
data, independent of the garbled answer text - but this is an honest,
observed limitation of a small local model under a moderately complex
combined prompt, not a citation-system failure. See
`docs/LLM_GENERATION.md`'s and `docs/QUERY_ROUTING.md`'s own equivalent
notes on small-model quality.

## What citations prove

> This source was authorized (by an independent, pre-existing check) and
> supplied as evidence available to the answer-generation step for this
> specific request.

## What citations do NOT prove

> The model's factual statement has been independently verified as
> correct.

Phase 12 establishes **provenance**, not **fact-checking**. If the model
writes "the cancellation period is 48 hours [Source 1]" while Source 1's
actual text says 24 hours, the system still correctly reports that
Source 1 was real, authorized, and available - it does **not** attempt to
verify that the model's stated number matches the source's actual
content. Building that would require a second model, a semantic-matching
system, or a claim-level fact-checker - explicitly out of scope for this
phase (see "No fact-checker" below) and, more importantly, a much harder
and different problem than source authority.

## No fact-checker

Not built, and not planned to be confused with this phase's actual scope:
a second LLM pass, an external fact-checking service, web search, a
citation-verification model, or semantic claim-matching against source
text. `sources` in the response is evidence that was *available*, not a
certified-correct bibliography.

## Data minimization / what is never exposed

Never in a `Source`/`SourceReference`, for either kind: storage paths,
filesystem paths, object-storage keys, embeddings, raw database
internals, `document_allowed_roles`/`document_authorized_doctors`/
`document_authorized_staff` rows (authorization metadata is an internal
security mechanism, never citation content), or - for structured sources
specifically - any raw foreign key (`appointment_id`, `patient_id`, etc.)
beyond the safe, pre-built `label`.

## Source enumeration protection

There is no source-search or source-listing endpoint, and no field on
`RagSearchRequest` that could ask for "all sources" or a source range - a
query like "Show me sources 1 through 100" is handled exactly like any
other natural-language question (routed, authorized, answered from
whatever that specific authorized retrieval actually returns), never as a
request for an enumerated source catalogue.
`test_source_enumeration_request_exposes_nothing` confirms the returned
source count is always bounded by the caller's own real, authorized
result - never inflated by the phrasing of the question.

## Client override protection

`RagSearchRequest` (reused unmodified, `extra="forbid"`) has no
`sources`/`source_ids`/`citations`/`document_ids`/`appointment_ids` field
- a client attempting to submit evidence, or to name which sources should
back an answer, gets a `422`, never silent ignoring or acceptance.

## Audit logging

`RAG_ANSWER_GENERATED`'s `metadata` gained two fields:
`cited_valid_count`/`cited_invalid_count` (how many distinct valid/invalid
citation numbers appeared in the answer) - counts only. **Never logged**:
which specific sources were cited, source labels, source content, the
full answer text, or anything from Phase 10/11's existing "never log"
list (query text, prompt, chunk/record content) - unchanged.

## Database

**No migration was required, and no citation state is persisted.** The
`SourceRegistry` lives only in memory for the duration of one `answer()`
call.

## Testing strategy

46 new tests across three files, none requiring a live Ollama server for
generation (always mocked):

- `test_sources_registry.py` (12): sequential/deterministic numbering,
  no-reuse, request-scoped isolation between two registries, `is_valid()`
  bounds checking (`0`/negative/out-of-range), the structured
  source-type-mapping's exact coverage, and confirmation that a
  structured source carries no document fields (and a RAG source does).
- `test_citations.py` (15): every case from this phase's own instructions
  - valid, multiple valid, duplicate (normalized), unknown, malformed
    (five different malformed shapes, none raising), `Source 0`, negative,
    very large, repeated, empty registry, no citations at all, and
    case-insensitivity/duplicate-preservation of the raw extraction step.
- `test_sources_e2e.py` (19): RAG and structured source registration
  through the real API, hybrid ordering (structured before RAG,
  sequential numbers), all three citation-validation scenarios (valid
  citation kept, hallucinated `[Source 999]` never becomes a real source,
  no-citation answers still report available evidence), client-override
  rejection (5 fields), source-enumeration safety, the two **critical**
  boundary tests (unauthorized RAG/structured source proven absent from
  both the registry and what was actually sent to the mocked LLM, not
  just the final response), cross-hospital isolation for both source
  kinds, and regression coverage confirming `AMBIGUOUS`/`UNSUPPORTED`/
  no-context routes still return an empty, well-formed `sources: []`.

## Known limitations

- **No claim-level attribution.** A citation associates a source with the
  *answer as a whole* having had it available, not with a specific
  sentence or clause - Phase 12 does not pretend to have exact,
  sentence-level provenance (see "What citations do NOT prove").
- **`sources` in the response includes every source supplied to the
  model, not only ones it actually cited.** This was a deliberate choice
  (see "What citations do NOT prove" / Phase 12's own instruction not to
  under-report available evidence) - a client wanting only cited sources
  can cross-reference the `[Source N]` markers in `answer` against the
  `number`/`id` fields itself.
- **Small local model citation quality is imperfect**, as documented under
  "LLM limitations" above - the source-authority architecture is correct
  regardless, but the model does not always produce clean, well-cited
  prose for complex combined prompts.
- **No fact-checking of any kind** - deliberately, see "No fact-checker".
