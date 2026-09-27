"""Deterministic post-fusion reranking + document-level diversity - see
docs/RAG_HYBRID_SEARCH.md, "Reranking".

No ML cross-encoder model is used: this environment's Python (3.14) has no
available `torch`/`onnxruntime` wheel - the exact constraint that already
ruled out a Python-side embedding library in Phase 7
(docs/RAG_INGESTION.md, "Embedding model") - and Ollama has no dedicated
local reranking model to reuse the way `all-minilm` covers embeddings.
Instead: a small, transparent, deterministic query-term-overlap boost on
top of each candidate's RRF score. This never touches the database and
only ever operates on the already-bounded, already-authorized candidate
list app/rag/hybrid.py produced - it cannot create, widen, or narrow
authorization, only reorder candidates that were already proven
authorized.
"""

import re
import uuid
from dataclasses import dataclass

from app.rag.hybrid import FusedCandidate
from app.repositories.rag_repository import ChunkSearchResult

_TOKEN_RE = re.compile(r"\w+")
_MIN_TOKEN_LENGTH = 3  # skips noise like "is"/"of"/"a" from the overlap signal


@dataclass(frozen=True)
class RankedCandidate:
    chunk: ChunkSearchResult
    rrf_score: float
    term_overlap: float
    final_score: float


def _query_terms(query: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(query.lower()) if len(t) >= _MIN_TOKEN_LENGTH}


def rerank(candidates: list[FusedCandidate], *, query: str, exact_match_bonus: float) -> list[RankedCandidate]:
    """`final_score = rrf_score + exact_match_bonus * term_overlap`, where
    `term_overlap` is the fraction of the query's own significant terms
    (length >= 3, deduplicated, case-insensitive) that appear verbatim in
    the chunk's content - 0.0 when the query has no such terms, so this
    stage is a no-op (falls back to RRF order) for very short/degenerate
    queries rather than distorting them. Deterministic and explainable:
    the same candidates + query always produce the same order, and every
    component of the score is inspectable.
    """
    terms = _query_terms(query)
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        content_lower = candidate.chunk.content.lower()
        overlap = (sum(1 for term in terms if term in content_lower) / len(terms)) if terms else 0.0
        final_score = candidate.rrf_score + exact_match_bonus * overlap
        ranked.append(
            RankedCandidate(chunk=candidate.chunk, rrf_score=candidate.rrf_score, term_overlap=overlap, final_score=final_score)
        )

    ranked.sort(key=lambda r: r.final_score, reverse=True)
    return ranked


def limit_per_document(ranked: list[RankedCandidate], *, max_per_document: int, top_k: int) -> list[RankedCandidate]:
    """Walks the already-reranked (best-first) list and keeps at most
    `max_per_document` chunks from any one document, stopping once `top_k`
    results are collected - so one large, strongly-matching document can't
    fill the entire result set and crowd out other authorized, relevant
    documents. Order-preserving and deterministic: never reorders, only
    skips candidates that would exceed a document's cap.
    """
    counts: dict[uuid.UUID, int] = {}
    limited: list[RankedCandidate] = []
    for candidate in ranked:
        document_id = candidate.chunk.document_id
        if counts.get(document_id, 0) >= max_per_document:
            continue
        counts[document_id] = counts.get(document_id, 0) + 1
        limited.append(candidate)
        if len(limited) >= top_k:
            break
    return limited
