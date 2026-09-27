"""Authorization-scoped candidate retrieval for `document_chunks` - two
independent paths (vector similarity, lexical full-text) sharing one
authorization predicate - see docs/RAG_HYBRID_SEARCH.md and
docs/RAG_RETRIEVAL.md, "Access semantics".

The one rule this whole module exists to enforce: authorization is a
predicate evaluated *inside* the same SQL query that does the similarity/
relevance ranking, for BOTH paths, never a Python-side filter applied to
an unauthorized-first result set. There is no code path here that fetches
chunks broadly and then decides which ones the caller may see.
`_authorized_documents_where_clause` is the single place that predicate is
expressed, so the two paths can never drift apart.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.permissions.roles import Role
from app.permissions.scope import UserScope

_CHUNK_COLUMNS = """
    dc.id AS chunk_id,
    dc.document_id AS document_id,
    d.title AS document_title,
    d.document_type AS document_type,
    dc.chunk_index AS chunk_index,
    dc.content AS content,
    dc.page_number AS page_number,
    dc.metadata AS metadata
"""


@dataclass(frozen=True)
class ChunkSearchResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    document_type: str
    chunk_index: int
    content: str
    page_number: int | None
    heading: str | None
    score: float
    """Raw path-specific relevance (cosine similarity for the vector path,
    `ts_rank_cd` for the lexical path) - NOT comparable across paths, which
    is exactly why fusion (app/rag/hybrid.py) combines the two paths by
    rank, never by this raw score. Kept for diagnostics, not the final
    API-facing score (see app/rag/reranking.py, RankedCandidate.final_score)."""


def _row_to_result(row: Row) -> ChunkSearchResult:
    return ChunkSearchResult(
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        document_title=row.document_title,
        document_type=row.document_type,
        chunk_index=row.chunk_index,
        content=row.content,
        page_number=row.page_number,
        heading=(row.metadata or {}).get("heading"),
        score=float(row.score),
    )


def _document_authorization_clause(scope: UserScope) -> tuple[str, dict[str, object]]:
    """Expresses exactly the same access model documented in
    docs/RAG_RETRIEVAL.md, "Access semantics" as a SQL predicate on the
    `documents` row aliased `d`:

    - SUPER_ADMIN: unconditional bypass (TRUE) - mirrors every other
      can_access_*/scope-clause function's SUPER_ADMIN behavior
      (app/permissions/authorization.py, app/repositories/clinical_repository.py).
    - Everyone else: authorized if role-based access applies (their role is
      in document_allowed_roles for this document AND the document is
      hospital-wide or in their own department), OR they are individually
      named in document_authorized_doctors, OR individually named in
      document_authorized_staff. This is an OR/union of three independent
      grants, not an AND - a specific-doctor/staff grant is "beyond default
      role-based access" (see database/migrations/0012_documents.sql's own
      comment), so it is never narrowed by the document's department
      restriction the way the role-based path is.
    """
    if scope.role is Role.SUPER_ADMIN:
        return "TRUE", {}

    clauses = [
        "EXISTS ("
        "SELECT 1 FROM document_allowed_roles dar "
        "JOIN roles r ON r.id = dar.role_id "
        "WHERE dar.document_id = d.id AND r.name = :scope_role "
        "AND (d.department_id IS NULL OR d.department_id = :scope_department_id)"
        ")"
    ]
    params: dict[str, object] = {"scope_role": scope.role.value, "scope_department_id": scope.department_id}

    if scope.doctor_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM document_authorized_doctors dad "
            "WHERE dad.document_id = d.id AND dad.doctor_id = :scope_doctor_id)"
        )
        params["scope_doctor_id"] = scope.doctor_id

    if scope.staff_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM document_authorized_staff das "
            "WHERE das.document_id = d.id AND das.staff_id = :scope_staff_id)"
        )
        params["scope_staff_id"] = scope.staff_id

    return "(" + " OR ".join(clauses) + ")", params


def _hospital_clause(scope: UserScope) -> tuple[str, dict[str, object]]:
    if scope.role is Role.SUPER_ADMIN:
        return "TRUE", {}
    return "d.hospital_id = :scope_hospital_id", {"scope_hospital_id": scope.hospital_id}


def _authorized_documents_where_clause(scope: UserScope) -> tuple[str, dict[str, object]]:
    """The single shared predicate both candidate-generation paths below
    build their query on: hospital isolation + only-searchable-if-COMPLETED-
    and-active + the role/doctor/staff access model. Extracted once so the
    vector and lexical paths cannot silently drift apart from each other -
    see this module's docstring.
    """
    hospital_sql, hospital_params = _hospital_clause(scope)
    auth_sql, auth_params = _document_authorization_clause(scope)
    where_sql = f"{hospital_sql} AND d.status = 'COMPLETED' AND d.is_active AND {auth_sql}"
    return where_sql, {**hospital_params, **auth_params}


def search_vector_candidates(
    session: Session, *, scope: UserScope, query_embedding: list[float], limit: int
) -> list[ChunkSearchResult]:
    """Cosine-distance nearest-neighbor search (`<=>`) - one of the two
    candidate-generation paths app/rag/hybrid.py fuses. `limit` is a
    candidate-pool size (see docs/RAG_HYBRID_SEARCH.md, "Candidate pool"),
    not the caller's final `top_k` - the caller (app/rag/retrieval_service.py)
    requests a larger pool than the final result size, narrowed later by
    fusion/reranking, never here.
    """
    where_sql, params = _authorized_documents_where_clause(scope)

    # pgvector's text input format is a plain `[v1,v2,...]` literal - same
    # convention app/repositories/document_repository.py's insert_chunk
    # uses, no separate pgvector Python adapter needed.
    embedding_literal = "[" + ",".join(repr(float(value)) for value in query_embedding) + "]"

    rows = session.execute(
        text(
            f"""
            SELECT
                {_CHUNK_COLUMNS},
                1 - (dc.embedding <=> CAST(:query_embedding AS vector)) AS score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE {where_sql}
            ORDER BY dc.embedding <=> CAST(:query_embedding AS vector)
            LIMIT :limit
            """
        ),
        {**params, "query_embedding": embedding_literal, "limit": limit},
    ).all()

    return [_row_to_result(row) for row in rows]


def search_lexical_candidates(session: Session, *, scope: UserScope, query_text: str, limit: int) -> list[ChunkSearchResult]:
    """PostgreSQL native full-text search - the other candidate-generation
    path. `websearch_to_tsquery` (not `to_tsquery`/`plainto_tsquery`) is
    used specifically because it never raises on malformed input - a raw
    user search string (quotes, "OR", punctuation, stray operators) always
    parses to *some* tsquery, possibly an empty one that matches nothing,
    never a syntax error - see docs/RAG_HYBRID_SEARCH.md, "Fallback
    behavior". `query_text` is always a bound parameter, never
    interpolated into the SQL text, so this is not an injection vector
    regardless of what the query string contains.
    """
    where_sql, params = _authorized_documents_where_clause(scope)

    rows = session.execute(
        text(
            f"""
            SELECT
                {_CHUNK_COLUMNS},
                ts_rank_cd(dc.search_vector, websearch_to_tsquery('english', :query_text)) AS score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE {where_sql} AND dc.search_vector @@ websearch_to_tsquery('english', :query_text)
            ORDER BY score DESC
            LIMIT :limit
            """
        ),
        {**params, "query_text": query_text, "limit": limit},
    ).all()

    return [_row_to_result(row) for row in rows]
