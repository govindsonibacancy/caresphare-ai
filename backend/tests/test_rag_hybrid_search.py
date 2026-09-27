"""Phase 9 - hybrid search + reranking: pure unit tests for RRF fusion
(app/rag/hybrid.py) and deterministic reranking/diversity
(app/rag/reranking.py), PostgreSQL lexical-search repository tests
(authorization parity with the vector path), end-to-end hybrid behavior
through POST /api/rag/search, candidate-pool/config tests, lexical-failure
fallback, and injection/enumeration safety for the new lexical path. See
docs/RAG_HYBRID_SEARCH.md.

All Phase 8 authorization guarantees are re-verified here for the lexical
path specifically (test_rag_search.py already covers the vector path and
the end-to-end hybrid endpoint's authorization exhaustively - this file
does not repeat every one of those cases, only the ones specific to the
new lexical path and the fusion/reranking/config layer Phase 9 adds).
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.rag import reranking
from app.rag.hybrid import reciprocal_rank_fusion
from app.repositories import rag_repository
from app.repositories.rag_repository import ChunkSearchResult

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"
RECEPTIONIST_EMAIL = "sanjay.rao@caresphere-demo.example"


def _upload(headers, *, filename="policy.txt", content=b"Some policy content.", **form):
    files = {"file": (filename, content, "text/plain")}
    data = {
        "title": form.pop("title", "Hybrid Search Test Document"),
        "document_type": form.pop("document_type", "HOSPITAL_POLICY"),
        "sensitivity": form.pop("sensitivity", "PUBLIC"),
        **form,
    }
    return client.post("/api/admin/documents", files=files, data=data, headers=headers)


def _search(headers, **body):
    body.setdefault("query", "test query")
    body.setdefault("top_k", 5)
    return client.post("/api/rag/search", json=body, headers=headers)


@pytest.fixture(autouse=True)
def _cleanup_rag_audit_logs(db_engine):
    yield
    with db_engine.connect() as conn:
        conn.execute(text("DELETE FROM audit_logs WHERE resource_type = 'rag_search'"))
        conn.commit()


def _chunk(chunk_id, *, document_id=None, content="some content", score=0.0):
    return ChunkSearchResult(
        chunk_id=chunk_id,
        document_id=document_id or uuid.uuid4(),
        document_title="Test Document",
        document_type="HOSPITAL_POLICY",
        chunk_index=0,
        content=content,
        page_number=None,
        heading=None,
        score=score,
    )


# --- reciprocal_rank_fusion (pure, no DB) ---------------------------------


def test_rrf_chunk_found_by_vector_only():
    chunk_id = uuid.uuid4()
    vector = [_chunk(chunk_id, score=0.9)]
    fused = reciprocal_rank_fusion(vector, [], vector_weight=0.7, lexical_weight=0.3, k=60)
    assert len(fused) == 1
    assert fused[0].vector_rank == 1
    assert fused[0].lexical_rank is None
    assert fused[0].rrf_score == pytest.approx(0.7 / 61)


def test_rrf_chunk_found_by_lexical_only():
    chunk_id = uuid.uuid4()
    lexical = [_chunk(chunk_id, score=0.5)]
    fused = reciprocal_rank_fusion([], lexical, vector_weight=0.7, lexical_weight=0.3, k=60)
    assert len(fused) == 1
    assert fused[0].vector_rank is None
    assert fused[0].lexical_rank == 1
    assert fused[0].rrf_score == pytest.approx(0.3 / 61)


def test_rrf_chunk_found_by_both_scores_higher_than_either_alone():
    chunk_id = uuid.uuid4()
    vector = [_chunk(chunk_id, score=0.9)]
    lexical = [_chunk(chunk_id, score=0.5)]
    fused = reciprocal_rank_fusion(vector, lexical, vector_weight=0.7, lexical_weight=0.3, k=60)
    assert len(fused) == 1
    both_score = fused[0].rrf_score
    only_vector = reciprocal_rank_fusion(vector, [], vector_weight=0.7, lexical_weight=0.3, k=60)[0].rrf_score
    only_lexical = reciprocal_rank_fusion([], lexical, vector_weight=0.7, lexical_weight=0.3, k=60)[0].rrf_score
    assert both_score > only_vector
    assert both_score > only_lexical
    assert both_score == pytest.approx(only_vector + only_lexical)


def test_rrf_deduplicates_a_chunk_present_in_both_lists():
    chunk_id = uuid.uuid4()
    vector = [_chunk(chunk_id, score=0.9)]
    lexical = [_chunk(chunk_id, score=0.5)]
    fused = reciprocal_rank_fusion(vector, lexical, vector_weight=0.7, lexical_weight=0.3, k=60)
    assert len(fused) == 1  # not 2


def test_rrf_is_deterministic():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vector = [_chunk(a, score=0.9), _chunk(b, score=0.8)]
    lexical = [_chunk(c, score=0.6), _chunk(a, score=0.4)]
    first = reciprocal_rank_fusion(vector, lexical, vector_weight=0.7, lexical_weight=0.3, k=60)
    second = reciprocal_rank_fusion(vector, lexical, vector_weight=0.7, lexical_weight=0.3, k=60)
    assert [f.chunk.chunk_id for f in first] == [f.chunk.chunk_id for f in second]
    assert [f.rrf_score for f in first] == [f.rrf_score for f in second]


def test_rrf_configurable_weights_change_the_outcome():
    """With vector_weight=0 the lexical-only chunk always outranks a
    vector-only chunk, and vice versa - proves the weights are actually
    load-bearing, not decorative."""
    vector_only = uuid.uuid4()
    lexical_only = uuid.uuid4()
    vector = [_chunk(vector_only, score=0.99)]
    lexical = [_chunk(lexical_only, score=0.99)]

    vector_favored = reciprocal_rank_fusion(vector, lexical, vector_weight=1.0, lexical_weight=0.0, k=60)
    assert vector_favored[0].chunk.chunk_id == vector_only

    lexical_favored = reciprocal_rank_fusion(vector, lexical, vector_weight=0.0, lexical_weight=1.0, k=60)
    assert lexical_favored[0].chunk.chunk_id == lexical_only


def test_rrf_higher_rank_position_scores_lower():
    a, b = uuid.uuid4(), uuid.uuid4()
    vector = [_chunk(a, score=0.9), _chunk(b, score=0.8)]  # a is rank 1, b is rank 2
    fused = reciprocal_rank_fusion(vector, [], vector_weight=0.7, lexical_weight=0.3, k=60)
    scores = {f.chunk.chunk_id: f.rrf_score for f in fused}
    assert scores[a] > scores[b]


def test_rrf_empty_inputs_produce_empty_output():
    assert reciprocal_rank_fusion([], [], vector_weight=0.7, lexical_weight=0.3, k=60) == []


# --- reranking (pure, no DB) ----------------------------------------------


def test_rerank_boosts_exact_term_overlap():
    from app.rag.hybrid import FusedCandidate

    matching = FusedCandidate(
        chunk=_chunk(uuid.uuid4(), content="the triage checklist covers airway breathing circulation"),
        vector_rank=2,
        lexical_rank=None,
        rrf_score=0.01,
    )
    non_matching = FusedCandidate(
        chunk=_chunk(uuid.uuid4(), content="unrelated administrative parking policy document"),
        vector_rank=1,
        lexical_rank=None,
        rrf_score=0.011,  # slightly higher RRF than `matching`
    )
    ranked = reranking.rerank([non_matching, matching], query="triage checklist airway breathing", exact_match_bonus=0.05)
    # the exact-term-overlap boost is enough to overtake a slightly higher RRF score
    assert ranked[0].chunk.chunk_id == matching.chunk.chunk_id
    assert ranked[0].term_overlap > ranked[1].term_overlap


def test_rerank_is_deterministic():
    from app.rag.hybrid import FusedCandidate

    candidates = [
        FusedCandidate(chunk=_chunk(uuid.uuid4(), content="alpha beta gamma"), vector_rank=1, lexical_rank=None, rrf_score=0.02),
        FusedCandidate(chunk=_chunk(uuid.uuid4(), content="delta epsilon zeta"), vector_rank=2, lexical_rank=None, rrf_score=0.01),
    ]
    first = reranking.rerank(candidates, query="alpha beta", exact_match_bonus=0.05)
    second = reranking.rerank(candidates, query="alpha beta", exact_match_bonus=0.05)
    assert [r.chunk.chunk_id for r in first] == [r.chunk.chunk_id for r in second]
    assert [r.final_score for r in first] == [r.final_score for r in second]


def test_rerank_falls_back_to_rrf_order_for_degenerate_query():
    """A query with no terms >= 3 characters (e.g. only stopword-length
    tokens) contributes zero overlap for every candidate, so RRF order is
    preserved rather than distorted."""
    from app.rag.hybrid import FusedCandidate

    higher = FusedCandidate(chunk=_chunk(uuid.uuid4(), content="anything"), vector_rank=1, lexical_rank=None, rrf_score=0.02)
    lower = FusedCandidate(chunk=_chunk(uuid.uuid4(), content="something else"), vector_rank=2, lexical_rank=None, rrf_score=0.01)
    ranked = reranking.rerank([higher, lower], query="a is of", exact_match_bonus=0.05)
    assert [r.chunk.chunk_id for r in ranked] == [higher.chunk.chunk_id, lower.chunk.chunk_id]


def test_rerank_never_changes_the_candidate_set_only_the_order():
    from app.rag.hybrid import FusedCandidate

    ids = [uuid.uuid4() for _ in range(3)]
    candidates = [FusedCandidate(chunk=_chunk(i, content=f"content {i}"), vector_rank=n + 1, lexical_rank=None, rrf_score=0.01 * (3 - n)) for n, i in enumerate(ids)]
    ranked = reranking.rerank(candidates, query="content", exact_match_bonus=0.05)
    assert {r.chunk.chunk_id for r in ranked} == set(ids)
    assert len(ranked) == len(candidates)


# --- document-level diversity (pure, no DB) -------------------------------


def test_limit_per_document_caps_chunks_from_one_document():
    doc_id = uuid.uuid4()
    other_doc_id = uuid.uuid4()
    ranked = [
        reranking.RankedCandidate(chunk=_chunk(uuid.uuid4(), document_id=doc_id), rrf_score=0.05 - i * 0.001, term_overlap=0.0, final_score=0.05 - i * 0.001)
        for i in range(5)
    ] + [reranking.RankedCandidate(chunk=_chunk(uuid.uuid4(), document_id=other_doc_id), rrf_score=0.001, term_overlap=0.0, final_score=0.001)]

    limited = reranking.limit_per_document(ranked, max_per_document=2, top_k=10)
    from_doc = [r for r in limited if r.chunk.document_id == doc_id]
    from_other = [r for r in limited if r.chunk.document_id == other_doc_id]
    assert len(from_doc) == 2  # capped, even though 5 were available
    assert len(from_other) == 1  # the other document's chunk still gets through


def test_limit_per_document_stops_at_top_k():
    ranked = [
        reranking.RankedCandidate(chunk=_chunk(uuid.uuid4(), document_id=uuid.uuid4()), rrf_score=0.01, term_overlap=0.0, final_score=0.01)
        for _ in range(10)
    ]
    limited = reranking.limit_per_document(ranked, max_per_document=3, top_k=4)
    assert len(limited) == 4


def test_limit_per_document_is_order_preserving():
    a, b = uuid.uuid4(), uuid.uuid4()
    ranked = [
        reranking.RankedCandidate(chunk=_chunk(a), rrf_score=0.02, term_overlap=0.0, final_score=0.02),
        reranking.RankedCandidate(chunk=_chunk(b), rrf_score=0.01, term_overlap=0.0, final_score=0.01),
    ]
    limited = reranking.limit_per_document(ranked, max_per_document=5, top_k=5)
    assert [r.chunk.chunk_id for r in limited] == [a, b]


# --- lexical repository: authorization parity with the vector path -------


def test_lexical_candidates_require_actual_term_overlap(auth_as, auth_headers, cleanup_document):
    """websearch_to_tsquery AND-combines space-separated terms by default -
    a query with zero shared vocabulary against the document's content
    returns no lexical candidates, even though the same (tiny) corpus would
    still appear in a generously-bounded vector candidate pool. This is
    the deterministic way to demonstrate "vector-only match" without
    depending on the embedding model's semantic judgment - see
    docs/RAG_HYBRID_SEARCH.md, "Testing strategy"."""
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="maintenance.md",
        content=b"# Biomedical Equipment Maintenance\n\nThe quarterly maintenance schedule requires biomedical "
        b"equipment inspection every ninety days.",
        title="Biomedical Equipment Maintenance",
        document_type="SOP",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        with db_engine_for_test() as conn:
            hospital_id, department_id = _doctor_scope_row(conn)
        scope = _doctor_scope(hospital_id, department_id)

        session = get_session_factory()()
        try:
            lexical = rag_repository.search_lexical_candidates(
                session, scope=scope, query_text="giraffe elephant zoo animals kangaroo", limit=20
            )
            vector_embedding = _embed("giraffe elephant zoo animals kangaroo")
            vector = rag_repository.search_vector_candidates(session, scope=scope, query_embedding=vector_embedding, limit=20)
        finally:
            session.close()

        assert doc_id not in {str(c.document_id) for c in lexical}
        assert doc_id in {str(c.document_id) for c in vector}  # vector still returns its own top-N regardless
    finally:
        cleanup_document(doc_id)


def test_lexical_repository_excludes_unauthorized_chunks_at_the_sql_level(db_engine, auth_as, auth_headers, cleanup_document):
    """The lexical path's equivalent of
    test_repository_excludes_unauthorized_chunks_at_the_sql_level in
    test_rag_search.py - calls search_lexical_candidates() directly with an
    unauthorized scope and confirms the SQL predicate excludes a chunk that
    genuinely exists and genuinely matches the lexical query."""
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="doctor-only-lexical.md",
        content=b"# Doctor Only Lexical Content\n\nThis paragraph contains the unique lexical marker "
        b"zzqx-lexical-repo-test for full-text search matching.",
        title="Doctor Only Lexical Content",
        document_type="CLINICAL_GUIDELINE",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]

    try:
        with db_engine.connect() as conn:
            hospital_id = conn.execute(text("SELECT hospital_id FROM documents WHERE id = :id"), {"id": doc_id}).scalar_one()
            receptionist = conn.execute(
                text(
                    "SELECT u.id, u.auth_user_id, u.department_id FROM users u "
                    "JOIN roles r ON r.id = u.role_id "
                    "WHERE r.name = 'RECEPTIONIST' AND u.hospital_id = :hospital_id"
                ),
                {"hospital_id": hospital_id},
            ).mappings().one()
            doctor = conn.execute(
                text(
                    "SELECT u.id, u.auth_user_id, u.department_id, d.id AS doctor_id FROM users u "
                    "JOIN doctors d ON d.user_id = u.id WHERE u.email = :email"
                ),
                {"email": DOCTOR_EMAIL},
            ).mappings().one()

        receptionist_scope = UserScope(
            user_id=receptionist["id"], auth_user_id=receptionist["auth_user_id"], role=Role.RECEPTIONIST,
            permissions=frozenset(), hospital_id=hospital_id, department_id=receptionist["department_id"],
            patient_id=None, doctor_id=None, staff_id=None,
        )
        doctor_scope = UserScope(
            user_id=doctor["id"], auth_user_id=doctor["auth_user_id"], role=Role.DOCTOR,
            permissions=frozenset(), hospital_id=hospital_id, department_id=doctor["department_id"],
            patient_id=None, doctor_id=doctor["doctor_id"], staff_id=None,
        )

        session = get_session_factory()()
        try:
            unauthorized = rag_repository.search_lexical_candidates(
                session, scope=receptionist_scope, query_text="zzqx-lexical-repo-test", limit=20
            )
            authorized = rag_repository.search_lexical_candidates(
                session, scope=doctor_scope, query_text="zzqx-lexical-repo-test", limit=20
            )
        finally:
            session.close()

        assert doc_id not in {str(c.document_id) for c in unauthorized}
        assert doc_id in {str(c.document_id) for c in authorized}
    finally:
        cleanup_document(doc_id)


def test_lexical_search_respects_hospital_isolation(auth_as, auth_headers, cleanup_document, second_hospital):
    auth_as("meera.kapoor@caresphere-demo.example")  # SUPER_ADMIN
    resp = _upload(
        auth_headers,
        filename="hospital-b-lexical.md",
        content=b"# Hospital B Lexical Marker\n\nUnique marker zzqx-hospital-b-lexical for isolation testing.",
        title="Hospital B Lexical Marker",
        document_type="HR_POLICY",
        hospital_id=str(second_hospital["hospital_id"]),
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)  # Hospital A
        response = _search(auth_headers, query="zzqx-hospital-b-lexical unique marker", top_k=10)
        assert response.status_code == 200
        assert doc_id not in {r["document_id"] for r in response.json()["results"]}
    finally:
        cleanup_document(doc_id)


# --- lexical injection safety ----------------------------------------------


def test_lexical_search_handles_malicious_query_safely(db_engine, auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    malicious = "'; DROP TABLE document_chunks; -- OR 1=1"
    response = _search(auth_headers, query=malicious, top_k=3)
    assert response.status_code == 200
    assert isinstance(response.json()["results"], list)
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() >= 0


# --- fallback behavior: lexical failure never breaks the whole search -----


def test_lexical_search_failure_falls_back_to_vector_only_results(monkeypatch, auth_as, auth_headers, cleanup_document):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="fallback-test.md",
        content=b"# Fallback Test Document\n\nThis content is used to verify vector-only fallback behavior "
        b"when lexical search fails.",
        title="Fallback Test Document",
        document_type="HOSPITAL_POLICY",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated lexical search failure")

        monkeypatch.setattr("app.repositories.rag_repository.search_lexical_candidates", _boom)

        auth_as(DOCTOR_EMAIL)
        response = _search(auth_headers, query="fallback test document vector-only behavior", top_k=5)
        assert response.status_code == 200
        assert doc_id in {r["document_id"] for r in response.json()["results"]}
    finally:
        cleanup_document(doc_id)


# --- candidate pool / configuration ----------------------------------------


def test_candidate_counts_are_audited_and_bounded_by_configured_multipliers(db_engine, auth_as, auth_headers, cleanup_document):
    from app.core.config import get_settings

    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="candidate-pool-test.md",
        content=b"# Candidate Pool Test\n\nContent used to verify candidate pool sizing and audit metadata.",
        title="Candidate Pool Test",
        document_type="HOSPITAL_POLICY",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        auth_as(DOCTOR_EMAIL)
        top_k = 3
        response = _search(auth_headers, query="candidate pool test content", top_k=top_k)
        assert response.status_code == 200

        with db_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT metadata FROM audit_logs WHERE action = 'RAG_SEARCH_PERFORMED' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            ).mappings().one()

        settings = get_settings()
        assert row["metadata"]["top_k"] == top_k
        assert row["metadata"]["vector_candidate_count"] <= top_k * settings.rag_vector_candidate_multiplier
        assert row["metadata"]["lexical_candidate_count"] <= top_k * settings.rag_lexical_candidate_multiplier
        assert row["metadata"]["hybrid_candidate_count"] >= row["metadata"]["result_count"]
    finally:
        cleanup_document(doc_id)


# --- end-to-end: no duplicate chunks in the final response -----------------


def test_final_results_never_contain_duplicate_chunk_ids(auth_as, auth_headers, cleanup_document):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="dedup-test.md",
        content=b"# Deduplication Test\n\nContent likely to match both the vector and lexical paths for the "
        b"same query about deduplication testing.",
        title="Deduplication Test",
        document_type="HOSPITAL_POLICY",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        auth_as(DOCTOR_EMAIL)
        response = _search(auth_headers, query="deduplication test content matches", top_k=10)
        assert response.status_code == 200
        chunk_ids = [r["chunk_id"] for r in response.json()["results"]]
        assert len(chunk_ids) == len(set(chunk_ids))
    finally:
        cleanup_document(doc_id)


# --- edge cases: punctuation, numbers, short queries ------------------------


@pytest.mark.parametrize(
    "query",
    [
        "!!! ??? ... ,,,",
        "12345 67890",
        "hi",
        "a",
        "the a an of is",  # stopwords/short tokens only
    ],
)
def test_edge_case_queries_never_error(auth_as, auth_headers, query):
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers, query=query, top_k=5)
    assert response.status_code == 200
    assert isinstance(response.json()["results"], list)


def test_empty_candidate_pools_return_empty_results(auth_as, auth_headers):
    """A role/query combination with zero authorized, relevant documents
    at all (both paths empty) must still return the same safe empty
    shape - never an error."""
    auth_as(RECEPTIONIST_EMAIL)
    response = _search(auth_headers, query="completely unrelated nonexistent zzqx query terms", top_k=5)
    assert response.status_code == 200
    assert response.json()["results"] == []


# --- helpers used by the lexical-vs-vector test above -----------------------


def db_engine_for_test():
    from app.core.db import get_engine

    return get_engine().connect()


def _doctor_scope_row(conn):
    row = conn.execute(
        text("SELECT hospital_id, department_id FROM users WHERE email = :email"), {"email": DOCTOR_EMAIL}
    ).mappings().one()
    return row["hospital_id"], row["department_id"]


def _doctor_scope(hospital_id, department_id) -> UserScope:
    session = get_session_factory()()
    try:
        row = session.execute(
            text(
                "SELECT u.id, u.auth_user_id, d.id AS doctor_id FROM users u "
                "JOIN doctors d ON d.user_id = u.id WHERE u.email = :email"
            ),
            {"email": DOCTOR_EMAIL},
        ).mappings().one()
    finally:
        session.close()
    return UserScope(
        user_id=row["id"],
        auth_user_id=row["auth_user_id"],
        role=Role.DOCTOR,
        permissions=frozenset(),
        hospital_id=hospital_id,
        department_id=department_id,
        patient_id=None,
        doctor_id=row["doctor_id"],
        staff_id=None,
    )


def _embed(text_: str) -> list[float]:
    from app.services.documents.embedding import get_embedding_service

    return get_embedding_service().embed([text_])[0]
