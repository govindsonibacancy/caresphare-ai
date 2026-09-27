"""Reciprocal Rank Fusion (RRF) - combines the vector and lexical candidate
lists (app/repositories/rag_repository.py) into one ranked, deduplicated
list. See docs/RAG_HYBRID_SEARCH.md, "Hybrid scoring".

Pure and DB-free: operates only on the two already-bounded candidate lists
each retrieval path already produced (see docs/RAG_HYBRID_SEARCH.md,
"Candidate pool") - never touches the database, never re-derives
authorization, never sees a chunk that wasn't already proven authorized by
the SQL predicate both lists were built from.

RRF, not a weighted sum of raw scores, specifically because cosine
similarity and `ts_rank_cd` are not on comparable scales - RRF sidesteps
that entirely by combining rank *position* within each list, not the raw
score value.
"""

from dataclasses import dataclass

from app.repositories.rag_repository import ChunkSearchResult


@dataclass(frozen=True)
class FusedCandidate:
    chunk: ChunkSearchResult
    vector_rank: int | None
    lexical_rank: int | None
    rrf_score: float


def reciprocal_rank_fusion(
    vector_candidates: list[ChunkSearchResult],
    lexical_candidates: list[ChunkSearchResult],
    *,
    vector_weight: float,
    lexical_weight: float,
    k: int,
) -> list[FusedCandidate]:
    """`vector_candidates`/`lexical_candidates` must already be sorted
    best-first (both repository functions return them that way) - rank is
    derived from list position (1-based), not from `ChunkSearchResult.score`.
    A chunk present in only one list gets no contribution from the other -
    never a penalty, just an absent term in the sum. Deduplication
    (docs/RAG_HYBRID_SEARCH.md, "Deduplication") falls out of using
    `chunk_id` as the fusion key: a chunk appearing in both lists is fused
    into exactly one `FusedCandidate`.
    """
    vector_ranks = {c.chunk_id: rank for rank, c in enumerate(vector_candidates, start=1)}
    lexical_ranks = {c.chunk_id: rank for rank, c in enumerate(lexical_candidates, start=1)}

    chunks_by_id: dict = {c.chunk_id: c for c in vector_candidates}
    for c in lexical_candidates:
        chunks_by_id.setdefault(c.chunk_id, c)

    fused: list[FusedCandidate] = []
    for chunk_id, chunk in chunks_by_id.items():
        vector_rank = vector_ranks.get(chunk_id)
        lexical_rank = lexical_ranks.get(chunk_id)
        score = 0.0
        if vector_rank is not None:
            score += vector_weight / (k + vector_rank)
        if lexical_rank is not None:
            score += lexical_weight / (k + lexical_rank)
        fused.append(FusedCandidate(chunk=chunk, vector_rank=vector_rank, lexical_rank=lexical_rank, rrf_score=score))

    fused.sort(key=lambda f: f.rrf_score, reverse=True)
    return fused
