"""Query-time RAG retrieval: embed the user's question, run two
authorization-scoped candidate searches (vector + lexical) against
`document_chunks`, fuse and rerank the bounded candidate pool, and return
the final authorized results - see docs/RAG_HYBRID_SEARCH.md. This module
never generates a natural-language answer; Phase 9's boundary stops at
returning authorized chunks.

`app/rag/` is where docs/ARCHITECTURE.md already designated "embeds
queries, runs pgvector search within a supplied scope" (Phase 1's
scaffold) - this is that module, not a new layer.
"""

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.audit import events as audit_events
from app.core.config import get_settings
from app.permissions.scope import UserScope
from app.rag import reranking
from app.rag.hybrid import reciprocal_rank_fusion
from app.repositories import rag_repository
from app.schemas.rag import RagSearchResponse, RagSearchResult
from app.services.documents.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingServiceError,
    get_embedding_service,
)

logger = logging.getLogger(__name__)


class RetrievalError(Exception):
    """Wraps an embedding-service failure into a safe message for the API
    layer - never a raw connection error or internal detail."""


def search(
    session: Session, *, scope: UserScope, query: str, top_k: int, request_id: uuid.UUID | None = None
) -> RagSearchResponse:
    """`get_embedding_service()` is the exact Ollama `all-minilm` client
    Phase 7 built for ingestion (app/services/documents/embedding.py) -
    reused unchanged here rather than duplicated, so a query embedding is
    guaranteed to come from the same model/runtime/dimension as the chunk
    embeddings it's being compared against. Its own dimension check
    (`EmbeddingDimensionMismatch`) runs before this function ever reaches
    the database, so a wrongly-shaped query vector can never reach
    pgvector.

    Both `search_vector_candidates` and `search_lexical_candidates`
    (app/repositories/rag_repository.py) apply the identical authorization
    predicate before either one's `ORDER BY`/`LIMIT` runs - fusion and
    reranking below operate only on candidates both paths already proved
    authorized, never on a broader set later narrowed in Python.
    """
    settings = get_settings()
    start = time.monotonic()

    try:
        embeddings = get_embedding_service().embed([query])
    except (EmbeddingServiceError, EmbeddingDimensionMismatch) as exc:
        raise RetrievalError("The search service is temporarily unavailable.") from exc
    query_embedding = embeddings[0]

    vector_limit = top_k * settings.rag_vector_candidate_multiplier
    vector_candidates = rag_repository.search_vector_candidates(
        session, scope=scope, query_embedding=query_embedding, limit=vector_limit
    )

    # A lexical-search failure (see docs/RAG_HYBRID_SEARCH.md, "Fallback
    # behavior" - realistically only reachable via a genuine DB-level fault,
    # since websearch_to_tsquery never raises on malformed input) falls
    # back to vector-only results rather than a 500 - it can never bypass
    # authorization, since the failure happens *inside* the authorized
    # query itself, before any row is used, not after.
    lexical_limit = top_k * settings.rag_lexical_candidate_multiplier
    try:
        lexical_candidates = rag_repository.search_lexical_candidates(
            session, scope=scope, query_text=query, limit=lexical_limit
        )
    except Exception:
        logger.warning("RAG lexical search failed; falling back to vector-only results.", exc_info=True)
        session.rollback()
        lexical_candidates = []

    fused = reciprocal_rank_fusion(
        vector_candidates,
        lexical_candidates,
        vector_weight=settings.rag_vector_weight,
        lexical_weight=settings.rag_lexical_weight,
        k=settings.rag_rrf_k,
    )
    reranked = reranking.rerank(fused, query=query, exact_match_bonus=settings.rag_rerank_exact_match_bonus)
    final_candidates = reranking.limit_per_document(
        reranked, max_per_document=settings.rag_max_chunks_per_document, top_k=top_k
    )

    results = [
        RagSearchResult(
            document_id=candidate.chunk.document_id,
            document_title=candidate.chunk.document_title,
            document_type=candidate.chunk.document_type,
            chunk_id=candidate.chunk.chunk_id,
            chunk_index=candidate.chunk.chunk_index,
            content=candidate.chunk.content,
            score=candidate.final_score,
            page=candidate.chunk.page_number,
            section=candidate.chunk.heading,
        )
        for candidate in final_candidates
    ]

    # Never the query text or chunk content - see docs/RAG_HYBRID_SEARCH.md,
    # "Audit behavior". Candidate-pool counts are diagnostics only, never
    # reveal anything about *unauthorized* documents (every count here is
    # already post-authorization).
    audit_events.record_event(
        session,
        actor_user_id=scope.user_id,
        action="RAG_SEARCH_PERFORMED",
        resource_type="rag_search",
        resource_id=None,
        hospital_id=scope.hospital_id,
        request_id=request_id,
        metadata={
            "top_k": top_k,
            "result_count": len(results),
            "vector_candidate_count": len(vector_candidates),
            "lexical_candidate_count": len(lexical_candidates),
            "hybrid_candidate_count": len(fused),
            "duration_ms": int((time.monotonic() - start) * 1000),
        },
    )
    session.commit()

    return RagSearchResponse(results=results)
