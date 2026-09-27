# CareSphere AI — Hybrid Search + Reranking

Phase 9 upgrades Phase 8's vector-only retrieval into a hybrid pipeline:
vector (semantic) search and PostgreSQL lexical (keyword) search run as two
independent, equally-authorized candidate-generation paths, are fused by
Reciprocal Rank Fusion (RRF), reranked by a small deterministic relevance
signal, and capped for per-document diversity - all before the final
`top_k` is returned. `POST /api/rag/search`'s request/response shape is
unchanged from Phase 8 (see "API" below); everything described here is
what happens between "authenticated user" and "authorized results."

The security invariant carried over from Phase 8, unchanged: **authorization
happens before an unauthorized chunk can influence the result** - for
*both* candidate paths now, not just one.

## Why vector-only search is insufficient

Vector similarity is good at *semantic* relevance ("what is this document
conceptually about") but weak at *exact* relevance - a query that names a
specific drug, an equipment model number, a policy code, or an exact
phrase the admin wrote doesn't reliably out-rank a document that is merely
*topically* similar, because embedding similarity has no notion of "this
exact token appears here." A hospital knowledge base is full of exactly
this kind of content (drug names, equipment identifiers, form numbers,
department codes) where an exact keyword match is often the strongest
possible relevance signal.

## Why lexical search is useful

PostgreSQL's native full-text search (`tsvector`/`tsquery`) finds and
ranks exact/stemmed term matches directly - no embedding, no model call,
deterministic, and (via a `GIN` index) fast. It complements vector search
precisely where vector search is weakest: exact terminology. Combining the
two, rather than picking one, is what "hybrid" means here.

## Hybrid architecture

```
                         Authenticated User
                                │
                                ▼
                            UserScope
                                │
                                ▼
                Authorization-aware SQL predicate
             (_authorized_documents_where_clause - ONE
              shared clause both paths below are built on)
                         /             \
                        /               \
                       ▼                 ▼
               Vector Search       Lexical Search
          (pgvector <=>, top_k*4)  (tsvector @@ tsquery, top_k*4)
                    │                   │
                    ▼                   ▼
             Vector Candidates   Lexical Candidates
                    \                   /
                     \                 /
                      ▼               ▼
                     Candidate Merge (by chunk_id)
                           │
                           ▼
                       Deduplicate
                           │
                           ▼
                Hybrid / RRF Score (app/rag/hybrid.py)
                           │
                           ▼
             Reranking (app/rag/reranking.py - deterministic
                    query-term-overlap boost)
                           │
                           ▼
          Document-level diversity cap (max N per document)
                           │
                           ▼
                       Final top_k
                           │
                           ▼
                    Authorized Sources
```

Concretely, in code:

```
POST /api/rag/search  (app/api/rag.py)
   ↓
app/rag/retrieval_service.py: search()
   ├─ embed query (Ollama all-minilm, 384-dim - Phase 7's client, reused)
   ├─ app/repositories/rag_repository.py: search_vector_candidates()   (authorized, limit = top_k * RAG_VECTOR_CANDIDATE_MULTIPLIER)
   ├─ app/repositories/rag_repository.py: search_lexical_candidates()  (authorized, limit = top_k * RAG_LEXICAL_CANDIDATE_MULTIPLIER)
   ├─ app/rag/hybrid.py: reciprocal_rank_fusion()      - fuse + dedupe
   ├─ app/rag/reranking.py: rerank()                   - deterministic term-overlap boost
   └─ app/rag/reranking.py: limit_per_document()        - diversity cap, truncate to top_k
   ↓
RagSearchResponse { results: [...] }
```

Layering is unchanged from Phase 8 (`docs/ARCHITECTURE.md`'s existing
`app/rag/` + `app/repositories/` plan) - Phase 9 adds two new small,
focused modules (`app/rag/hybrid.py`, `app/rag/reranking.py`) rather than
restructuring anything.

## Authorization flow

Identical to Phase 8 (`docs/RAG_RETRIEVAL.md`, "Document authorization"),
re-verified rather than re-explained here: `UserScope` (Phase 4) is the
sole source of authorization context, resolved server-side from the
authenticated JWT, never accepted from the client. **What Phase 9 changed
architecturally**: the WHERE-clause predicate that used to live only in
`search_authorized_chunks()` is now factored into
`_authorized_documents_where_clause()`
(`app/repositories/rag_repository.py`) and called identically by both
`search_vector_candidates()` and `search_lexical_candidates()` - so the two
paths cannot drift into enforcing different rules. Neither the RRF fusion
stage nor the reranking stage nor the diversity cap ever queries the
database or re-derives authorization; they only ever see chunks that one
of the two already-authorized SQL queries returned. There is no code path
in this codebase shaped like:

```python
results = vector_search()          # NOT what this code does
results = filter_authorized(results)
```

for either retrieval path - proven directly (not just asserted) by two
tests: `test_repository_excludes_unauthorized_chunks_at_the_sql_level`
(vector path, in `test_rag_search.py`) and
`test_lexical_repository_excludes_unauthorized_chunks_at_the_sql_level`
(lexical path, in `test_rag_hybrid_search.py`), each calling its
repository function directly - bypassing the API router and the service
layer entirely - with a hand-built unauthorized `UserScope`, against a
chunk confirmed to actually exist and actually match, and confirming the
SQL query itself never returns it.

No Phase 8 authorization rule was changed: hospital isolation, the
role+department / doctor-specific / staff-specific OR-of-three-grants
model, the unconditional `SUPER_ADMIN` bypass, `HOSPITAL_ADMIN`'s
deliberate lack of an implicit bypass, `PATIENT`'s always-empty result,
and the `COMPLETED`+`is_active` gate are all identical to Phase 8 and are
re-verified (not just assumed) by this phase's lexical-path tests, plus
the full Phase 8 suite (`test_rag_search.py`, unchanged, still passing).

## Candidate generation

Each path generates a **pool** larger than the final `top_k`, never just
`top_k` from each:

```
vector_limit  = top_k * RAG_VECTOR_CANDIDATE_MULTIPLIER    (default: top_k * 4)
lexical_limit = top_k * RAG_LEXICAL_CANDIDATE_MULTIPLIER   (default: top_k * 4)
```

Vector candidates: pgvector cosine-distance nearest-neighbor
(`ORDER BY embedding <=> :query_embedding LIMIT :vector_limit`), same
query shape as Phase 8.

Lexical candidates: PostgreSQL full-text search using
`websearch_to_tsquery('english', :query_text)` - chosen specifically
because it **never raises a syntax error** for arbitrary user input
(unlike `to_tsquery`, which expects operator syntax) - matched against
`document_chunks.search_vector` (see "Database changes" below), ranked by
`ts_rank_cd`, `LIMIT :lexical_limit`. `query_text` is always a bound SQL
parameter, never interpolated into the query text, so it is not an
injection vector regardless of content - the same convention every other
repository in this codebase already uses.

The client only ever controls `query`/`top_k` (unchanged from Phase 8);
the multipliers are server-side configuration, never client-supplied (see
"Configuration").

## Hybrid scoring (Reciprocal Rank Fusion)

`app/rag/hybrid.py`'s `reciprocal_rank_fusion()`:

```
RRF(chunk) = vector_weight  / (k + vector_rank)     [0 if not in the vector candidate list]
           + lexical_weight / (k + lexical_rank)    [0 if not in the lexical candidate list]
```

`vector_rank`/`lexical_rank` are each list's own 1-based position (best
match = rank 1), **not** the raw score - RRF deliberately combines rank
*position*, not raw score value, because cosine similarity and
`ts_rank_cd` are on incomparable scales (there is no principled way to add
"0.82 cosine similarity" to "0.014 ts_rank_cd" directly). A chunk found by
only one path gets a single term, never a penalty for the missing one.
This function is pure and DB-free - it operates only on the two
already-authorized, already-bounded candidate lists, deterministic given
the same inputs (`test_rrf_is_deterministic`), and its weights are fully
load-bearing, not decorative
(`test_rrf_configurable_weights_change_the_outcome` proves setting one
weight to `0` always lets the other path's exclusive candidate win).

Deduplication falls out of the fusion key being `chunk_id`: a chunk present
in both candidate lists is fused into exactly one result, never returned
twice (`test_rrf_deduplicates_a_chunk_present_in_both_lists`,
`test_final_results_never_contain_duplicate_chunk_ids`).

## Reranking

No ML cross-encoder model is used. This environment's Python (3.14) has no
available `torch`/`onnxruntime` wheel - the exact constraint Phase 7
already documented for why a Python-side embedding library couldn't be
used either (`docs/RAG_INGESTION.md`, "Embedding model") - and Ollama has
no dedicated local reranking model comparable to how `all-minilm` covers
embeddings. Introducing a paid external reranking API was explicitly
disallowed by this phase's own instructions, as was sending hospital
document content to any external service.

Instead: `app/rag/reranking.py`'s `rerank()` adds a small, deterministic,
fully-explainable **query-term-overlap boost** on top of each candidate's
RRF score:

```
final_score = rrf_score + RAG_RERANK_EXACT_MATCH_BONUS * term_overlap
```

`term_overlap` is the fraction of the query's own significant terms
(lowercased, length >= 3 characters, deduplicated) that appear verbatim in
the chunk's content - `0.0` (a no-op, falling back to pure RRF order) for
degenerate queries with no such terms
(`test_rerank_falls_back_to_rrf_order_for_degenerate_query`). This never
touches the database, never widens the candidate set beyond what fusion
already produced, and is fully deterministic
(`test_rerank_is_deterministic`) and order-only
(`test_rerank_never_changes_the_candidate_set_only_the_order` - the same
set of chunk ids goes in and comes out, only reordered).

This is a considered choice of "Option B" (deterministic feature scoring)
over "Option A" (a local cross-encoder model), for the concrete reason
above, not a default because no other option was considered.

### Document-level diversity

`app/rag/reranking.py`'s `limit_per_document()` walks the reranked
(best-first) list and keeps at most `RAG_MAX_CHUNKS_PER_DOCUMENT` (default
**3**) chunks from any single document, stopping once `top_k` results are
collected - so one large, strongly-matching document cannot fill the
entire result set and crowd out other authorized, relevant documents. It
is order-preserving (never reorders, only skips candidates that would
exceed a document's cap - `test_limit_per_document_is_order_preserving`)
and deterministic
(`test_limit_per_document_caps_chunks_from_one_document`,
`test_limit_per_document_stops_at_top_k`).

## Configuration

All new Phase 9 settings (`backend/app/core/config.py`,
`backend/.env.example`), environment-variable-overridable, no client
control over any of them:

| Setting | Env var | Default | Meaning |
|---|---|---|---|
| `rag_vector_candidate_multiplier` | `RAG_VECTOR_CANDIDATE_MULTIPLIER` | `4` | Vector candidate pool = `top_k * this` |
| `rag_lexical_candidate_multiplier` | `RAG_LEXICAL_CANDIDATE_MULTIPLIER` | `4` | Lexical candidate pool = `top_k * this` |
| `rag_vector_weight` | `RAG_VECTOR_WEIGHT` | `0.7` | RRF weight for the vector path |
| `rag_lexical_weight` | `RAG_LEXICAL_WEIGHT` | `0.3` | RRF weight for the lexical path |
| `rag_rrf_k` | `RAG_RRF_K` | `60` | RRF's rank-damping constant (the value from the original RRF paper, Cormack et al. 2009) |
| `rag_rerank_exact_match_bonus` | `RAG_RERANK_EXACT_MATCH_BONUS` | `0.05` | Weight of the term-overlap reranking boost |
| `rag_max_chunks_per_document` | `RAG_MAX_CHUNKS_PER_DOCUMENT` | `3` | Diversity cap per document in the final result |

The `0.7`/`0.3` vector/lexical weight split is a reasonable starting
default (favoring semantic relevance, which is usually the stronger signal
for natural-language questions, while still letting exact-term matches
meaningfully contribute) - **not** claimed to be optimal for every corpus;
it is a config value specifically so it can be tuned later without a code
change. `test_rrf_configurable_weights_change_the_outcome` proves the
weights are genuinely load-bearing.

## Fallback behavior

| Case | Behavior |
|---|---|
| Vector + lexical both return candidates | Fused via RRF (see above). |
| Vector only | Fusion naturally reduces to vector-rank-only scoring - no special-casing needed. |
| Lexical only | Same, reduces to lexical-rank-only scoring. |
| Neither returns candidates | `{"results": []}` - identical shape to every other empty-result case (see "Enumeration protection"), never reveals whether unauthorized documents exist. |
| Lexical search raises an exception | Caught in `app/rag/retrieval_service.py`, logged (no query text/content), session rolled back, treated as zero lexical candidates - the search continues with vector-only results rather than a `500`. **Cannot bypass authorization**: the failure occurs *inside* the already-authorized query itself, before any row is used - there is no code path where a failure skips the WHERE clause and returns unauthorized data instead. In practice this is very hard to trigger with real Postgres input, because `websearch_to_tsquery` never raises for malformed text (confirmed empirically - empty string and punctuation-only input both return a valid, matching-nothing empty tsquery, not an error); the fallback is tested via `test_lexical_search_failure_falls_back_to_vector_only_results`, which monkeypatches the repository call to force a failure deterministically. |

## Deduplication

Covered under "Hybrid scoring" above - a chunk found by both paths is
fused into one `FusedCandidate` keyed by `chunk_id`, never returned twice.

## Performance considerations

- Both candidate queries are bounded (`LIMIT :vector_limit` /
  `LIMIT :lexical_limit`) - never an unbounded scan of `document_chunks`.
- Fusion, reranking, and the diversity cap operate only on the bounded,
  already-merged candidate pool (at most `vector_limit + lexical_limit`
  items, deduplicated) - never on the full table, never re-querying the
  database.
- `document_chunks.search_vector` has a `GIN` index
  (`idx_document_chunks_search_vector`, migration `0018`) - the lexical
  query's `@@` predicate can use it. The vector query is unchanged from
  Phase 8: no ANN index yet (see `docs/RAG_RETRIEVAL.md`, "Performance /
  indexing" - still true, this project's data volume doesn't justify one;
  the same reasoning applies to the lexical path's dataset size).
- No Python-side authorization, no full-table chunk loading - both
  candidate-generation SQL statements return only already-authorized rows.

## Security considerations

Every Phase 8 guarantee re-verified for the new lexical path specifically
(hospital isolation, role/department/doctor/staff authorization, SUPER_ADMIN
bypass, `COMPLETED`+active gating, enumeration protection, no client-
controlled authorization field, SQL-injection safety via bound parameters)
- see "Testing strategy" below for exactly which tests cover which
guarantee. The two most important, both proven directly rather than
asserted: (1) authorization is enforced by each path's own SQL predicate,
never a Python filter (see "Authorization flow"); (2) the internal
candidate-pool diagnostics added to the audit event
(`vector_candidate_count`/`lexical_candidate_count`/`hybrid_candidate_count`)
cannot leak anything about unauthorized documents, because every count is
already computed **after** the authorized-only SQL query ran - there is no
"total documents" or "documents you can't see" count anywhere in this
codebase.

## Audit behavior

Still the single `RAG_SEARCH_PERFORMED` event
(`app/audit/events.py`, unchanged mechanism), with three new diagnostic
fields added to `metadata`: `vector_candidate_count`,
`lexical_candidate_count`, `hybrid_candidate_count` (post-fusion,
pre-diversity-cap size) - alongside the existing `top_k`/`result_count`.
Still never logs the raw query text, chunk content, embeddings, or any
authorization metadata -
`test_candidate_counts_are_audited_and_bounded_by_configured_multipliers`
confirms the new fields are present and bounded by the configured
multipliers; the Phase 8 test proving the query text itself never appears
in the audit row (`test_search_writes_audit_event_without_query_text`,
`test_rag_search.py`) is unchanged and still passes.

## Testing strategy

77 tests across two files: `test_rag_search.py` (49, from Phase 8,
unchanged and still passing - full authorization/enumeration/query-
validation coverage for the vector path and the end-to-end endpoint) and
`test_rag_hybrid_search.py` (28, new this phase):

- **Pure unit tests for `reciprocal_rank_fusion()`** (no DB): vector-only,
  lexical-only, found-by-both scores higher than either alone,
  deduplication, determinism, configurable weights are load-bearing, rank
  position matters, empty-input handling.
- **Pure unit tests for `rerank()`/`limit_per_document()`** (no DB): exact-
  term-overlap boost changes order when justified, determinism, graceful
  no-op for degenerate queries, never alters the candidate *set* - only
  its order, diversity cap enforcement, order-preservation, `top_k`
  truncation.
- **Lexical repository authorization tests** (real DB): term-overlap
  requirement (the deterministic way this suite demonstrates a genuine
  "vector-only match" - see the note below), the direct SQL-level
  authorization proof (this file's parallel to Phase 8's vector-path
  equivalent), hospital isolation, and injection safety.
- **End-to-end hybrid tests** through the real API: lexical-failure
  fallback, candidate-pool/config-bound audit diagnostics, no duplicate
  chunk ids in a real response, punctuation/numeric/short/stopword-only
  query edge cases, fully-empty-candidate-pool safe response.

**A deliberate testing-strategy choice**: "vector-only match" / "lexical-
only match" behavior (Phase 9's required test items 1-3) is proven at two
different levels rather than one fragile end-to-end one. The fusion logic
itself is unit-tested directly with synthetic candidate lists (fully
deterministic, no embedding-model dependency at all). Separately, one
integration-level test
(`test_lexical_candidates_require_actual_term_overlap`) demonstrates the
real, deterministic mechanism *why* a vector-only match can occur in
practice: `websearch_to_tsquery` AND-combines query terms by default, so a
query sharing zero vocabulary with a document's content is guaranteed to
produce zero lexical candidates for it, while pgvector's un-thresholded
top-N nearest-neighbor search still returns that same chunk (trivially, in
a small test corpus, but *for the correct structural reason* - vector
search has no relevance threshold, lexical search does). Constructing a
true end-to-end "lexical finds it but a *tightly-bounded* vector top-k
would have missed it" scenario would require a large, carefully-tuned
corpus and would be inherently sensitive to `all-minilm`'s specific
embedding behavior - fragile, not more rigorous - so this suite does not
attempt it; the fusion unit tests already prove the *fusion logic* handles
that case correctly regardless of how the candidates were produced.

## Clear Phase 9 boundary

Per this phase's own explicit instructions, **not** implemented:

- Ollama (or any model) answer generation, prompt construction, or
  natural-language responses - this phase still returns chunks, never
  prose.
- Conversational memory / multi-turn context.
- SQL-vs-RAG query routing, or any structured-data + RAG orchestration.
- Citations beyond the retrieval metadata already preserved
  (`document_id`, `document_title`, `chunk_id`, `page`, `section`) - no
  citation *formatting* or presentation logic was added.

## API

**Unchanged from Phase 8.** `POST /api/rag/search` still accepts exactly
`{"query": str, "top_k"?: int}` (`extra="forbid"` still rejects every
authorization-shaped field) and returns `{"results": [...]}` in the same
shape. The one documented change is the *meaning* of `score` - see
`app/schemas/rag.py`'s `RagSearchResult.score` docstring and
`docs/RAG_RETRIEVAL.md`'s "Vector search" section: it is now the final
hybrid/reranked value, not a raw cosine similarity, still higher-is-better,
no longer guaranteed to be in `[0, 1]`.

## Database changes

One migration, `database/migrations/0018_document_chunks_search_vector.sql`:
adds `document_chunks.search_vector`, a `tsvector` **generated column**
(`GENERATED ALWAYS AS (to_tsvector('english', content)) STORED`), plus a
`GIN` index on it. A generated column, not an application-maintained one,
specifically so:

- Every pre-existing chunk became searchable immediately when the
  migration ran (Postgres computes the generated value for every existing
  row as part of the `ALTER TABLE`).
- Every newly-ingested chunk becomes searchable automatically on `INSERT` -
  **zero changes were needed to Phase 7's ingestion code**
  (`app/repositories/document_repository.py`'s `insert_chunk`) for this to
  work, because Postgres computes the column, not application code.

No other schema change was needed or made.
