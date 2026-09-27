"""POST /api/rag/search: authentication, the documented access-semantics
model (role + department, specific-doctor, specific-staff, SUPER_ADMIN
bypass), hospital isolation, enumeration/leakage protection, query
validation, query-parameter manipulation, injection safety, processing-
status/active-state exclusion, audit behavior, and - the single most
important test in this file - direct proof that authorization is enforced
by the repository's SQL query itself, not a Python-side filter. See
docs/RAG_RETRIEVAL.md.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.jwt_verifier import TokenClaims, TokenVerificationError, get_token_verifier
from app.core.db import get_session_factory
from app.main import app
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import rag_repository
from app.services.documents.embedding import EmbeddingDimensionMismatch, get_embedding_service

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
STAFF_EMAIL = "neha.joshi@caresphere-demo.example"  # STAFF designation, Pharmacy dept
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"  # CGH-D-0001, Cardiology
DOCTOR_2_EMAIL = "priya.nair@caresphere-demo.example"  # CGH-D-0002, General Medicine
NURSE_EMAIL = "kavya.iyer@caresphere-demo.example"  # CGH-S-0001, Nursing dept
RECEPTIONIST_EMAIL = "sanjay.rao@caresphere-demo.example"  # CGH-S-0002, Administration
PATIENT_EMAIL = "asha.verma@example-patient.example"


def _upload(headers, *, filename="policy.txt", content=b"Some policy content.", **form):
    files = {"file": (filename, content, "text/plain")}
    data = {
        "title": form.pop("title", "RAG Test Document"),
        "document_type": form.pop("document_type", "HOSPITAL_POLICY"),
        "sensitivity": form.pop("sensitivity", "PUBLIC"),
        **form,
    }
    return client.post("/api/admin/documents", files=files, data=data, headers=headers)


def _search(headers, **body):
    body.setdefault("query", "test query")
    body.setdefault("top_k", 5)
    return client.post("/api/rag/search", json=body, headers=headers)


def _result_document_ids(response) -> set[str]:
    return {r["document_id"] for r in response.json()["results"]}


@pytest.fixture(autouse=True)
def _cleanup_rag_audit_logs(db_engine):
    """RAG_SEARCH_PERFORMED rows have resource_id=NULL (there's no single
    resource a search is "about"), so cleanup_document's per-document audit
    cleanup never touches them - clean this file's own audit rows here
    instead, so the suite leaves no residue."""
    yield
    with db_engine.connect() as conn:
        conn.execute(text("DELETE FROM audit_logs WHERE resource_type = 'rag_search'"))
        conn.commit()


@pytest.fixture
def rag_documents(auth_as, auth_headers, cleanup_document, db_engine):
    """Uploads the four documents used throughout this file's access-
    semantics tests - one per example in docs/RAG_RETRIEVAL.md, "Access
    semantics":

    - dept_scoped: department + role restricted (Example A)
    - hospital_wide: role restricted only, no department (Example C)
    - doctor_specific: authorized_doctor_ids only, no role fallback (Example B, minus the role path)
    - staff_specific: authorized_staff_ids only, no role fallback (Example D)
    """
    with db_engine.connect() as conn:
        doctor_a_id = str(conn.execute(text("SELECT id FROM doctors WHERE employee_number = 'CGH-D-0001'")).scalar_one())
        nurse_staff_id = str(conn.execute(text("SELECT id FROM staff WHERE employee_number = 'CGH-S-0001'")).scalar_one())
        cardiology_id = str(conn.execute(text("SELECT id FROM departments WHERE code = 'CARD'")).scalar_one())

    auth_as(HOSPITAL_ADMIN_EMAIL)
    ids = {}

    resp = _upload(
        auth_headers,
        filename="dept-scoped.md",
        content=b"# Cardiology Follow-Up Protocol\n\nPatients with stable angina should be scheduled for a "
        b"cardiology follow-up appointment within two weeks of discharge.",
        title="Cardiology Follow-Up Protocol",
        document_type="CLINICAL_GUIDELINE",
        department_id=cardiology_id,
        allowed_roles=["DOCTOR", "NURSE"],
    )
    assert resp.status_code == 201, resp.text
    ids["dept_scoped"] = resp.json()["id"]

    resp = _upload(
        auth_headers,
        filename="hospital-wide.md",
        content=b"# Hand Hygiene Policy\n\nAll clinical staff must perform hand hygiene using alcohol-based "
        b"sanitizer before and after every patient contact.",
        title="Hand Hygiene Policy",
        document_type="HOSPITAL_POLICY",
        allowed_roles=["DOCTOR", "NURSE"],
    )
    assert resp.status_code == 201, resp.text
    ids["hospital_wide"] = resp.json()["id"]

    resp = _upload(
        auth_headers,
        filename="doctor-specific.md",
        content=b"# Confidential Peer Review Notes\n\nThis peer review summary discusses a specific surgical "
        b"case reserved for named reviewing physicians only.",
        title="Confidential Peer Review Notes",
        document_type="CLINICAL_GUIDELINE",
        authorized_doctor_ids=[doctor_a_id],
    )
    assert resp.status_code == 201, resp.text
    ids["doctor_specific"] = resp.json()["id"]

    resp = _upload(
        auth_headers,
        filename="staff-specific.md",
        content=b"# Nursing Shift Handoff Checklist\n\nUse this structured checklist during every nursing "
        b"shift handoff to ensure continuity of care.",
        title="Nursing Shift Handoff Checklist",
        document_type="NURSING_PROCEDURE",
        authorized_staff_ids=[nurse_staff_id],
    )
    assert resp.status_code == 201, resp.text
    ids["staff_specific"] = resp.json()["id"]

    yield ids

    for document_id in ids.values():
        cleanup_document(document_id)


# --- authentication ----------------------------------------------------


def test_search_without_token_is_401():
    response = _search({})
    assert response.status_code == 401


def test_search_with_invalid_token_is_401():
    @dataclass
    class _FakeVerifier:
        def verify(self, token: str) -> TokenClaims:
            raise TokenVerificationError("bad signature")

    app.dependency_overrides[get_token_verifier] = lambda: _FakeVerifier()
    try:
        response = _search({"Authorization": "Bearer bad"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_token_verifier, None)


def test_search_with_inactive_user_is_403(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": DOCTOR_EMAIL}).one()
        conn.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": row.id})
        conn.commit()
    try:
        auth_as(DOCTOR_EMAIL)
        response = _search(auth_headers)
        assert response.status_code == 403
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("UPDATE users SET is_active = true WHERE id = :id"), {"id": row.id})
            conn.commit()


# --- basic retrieval -----------------------------------------------------


def test_authorized_doctor_retrieves_relevant_chunk(auth_as, auth_headers, rag_documents):
    auth_as(DOCTOR_EMAIL)  # CARD department - matches dept_scoped's restriction
    response = _search(auth_headers, query="cardiology follow-up appointment for stable angina", top_k=5)
    assert response.status_code == 200
    body = response.json()
    assert rag_documents["dept_scoped"] in _result_document_ids(response)
    result = next(r for r in body["results"] if r["document_id"] == rag_documents["dept_scoped"])
    assert result["document_title"] == "Cardiology Follow-Up Protocol"
    assert "embedding" not in result
    assert "cardiology" in result["content"].lower()


def test_no_matching_authorized_document_returns_empty_results(auth_as, auth_headers, rag_documents):
    auth_as(PATIENT_EMAIL)
    response = _search(auth_headers, query="hand hygiene policy sanitizer", top_k=5)
    assert response.status_code == 200
    assert response.json()["results"] == []


# --- role + department semantics (Example A / C) --------------------------


def test_role_match_but_department_mismatch_is_excluded(auth_as, auth_headers, rag_documents):
    """dept_scoped is restricted to Cardiology - DOCTOR_2 (General Medicine)
    holds the allowed DOCTOR role but is in the wrong department."""
    auth_as(DOCTOR_2_EMAIL)
    response = _search(auth_headers, query="cardiology follow-up appointment for stable angina", top_k=20)
    assert response.status_code == 200
    assert rag_documents["dept_scoped"] not in _result_document_ids(response)


def test_nurse_in_wrong_department_is_also_excluded(auth_as, auth_headers, rag_documents):
    """The department gate applies to every role on the role-based path,
    not just DOCTOR - NURSE (Nursing dept) is excluded from a
    Cardiology-restricted document exactly like DOCTOR_2 (General
    Medicine) is above, even though NURSE is itself an allowed role."""
    auth_as(NURSE_EMAIL)
    response = _search(auth_headers, query="cardiology follow-up appointment for stable angina", top_k=20)
    assert rag_documents["dept_scoped"] not in _result_document_ids(response)


def test_role_not_in_allowed_roles_is_excluded_regardless_of_department(auth_as, auth_headers, rag_documents):
    auth_as(RECEPTIONIST_EMAIL)
    response = _search(auth_headers, query="cardiology follow-up appointment for stable angina", top_k=20)
    assert rag_documents["dept_scoped"] not in _result_document_ids(response)


def test_hospital_wide_document_ignores_department(auth_as, auth_headers, rag_documents):
    """hospital_wide has allowed_roles=[DOCTOR, NURSE] and no department
    restriction - any DOCTOR/NURSE in the hospital is eligible regardless
    of their own department."""
    for email in (DOCTOR_EMAIL, DOCTOR_2_EMAIL, NURSE_EMAIL):
        auth_as(email)
        response = _search(auth_headers, query="hand hygiene policy alcohol sanitizer", top_k=20)
        assert response.status_code == 200
        assert rag_documents["hospital_wide"] in _result_document_ids(response), email

    for email in (RECEPTIONIST_EMAIL, STAFF_EMAIL, PATIENT_EMAIL):
        auth_as(email)
        response = _search(auth_headers, query="hand hygiene policy alcohol sanitizer", top_k=20)
        assert rag_documents["hospital_wide"] not in _result_document_ids(response), email


# --- doctor-specific semantics (Example B, minus role fallback) -----------


def test_authorized_doctor_specific_grant_is_included(auth_as, auth_headers, rag_documents):
    auth_as(DOCTOR_EMAIL)  # doctor_a - explicitly authorized
    response = _search(auth_headers, query="confidential peer review surgical case", top_k=20)
    assert rag_documents["doctor_specific"] in _result_document_ids(response)


def test_unauthorized_doctor_is_excluded_from_doctor_specific_document(auth_as, auth_headers, rag_documents):
    auth_as(DOCTOR_2_EMAIL)  # not in authorized_doctor_ids, and allowed_roles is empty
    response = _search(auth_headers, query="confidential peer review surgical case", top_k=20)
    assert rag_documents["doctor_specific"] not in _result_document_ids(response)


def test_non_doctor_is_excluded_from_doctor_specific_document(auth_as, auth_headers, rag_documents):
    auth_as(NURSE_EMAIL)
    response = _search(auth_headers, query="confidential peer review surgical case", top_k=20)
    assert rag_documents["doctor_specific"] not in _result_document_ids(response)


# --- staff-specific semantics (Example D) ---------------------------------


def test_authorized_staff_specific_grant_is_included(auth_as, auth_headers, rag_documents):
    auth_as(NURSE_EMAIL)  # the specifically-authorized staff member
    response = _search(auth_headers, query="nursing shift handoff checklist continuity of care", top_k=20)
    assert rag_documents["staff_specific"] in _result_document_ids(response)


def test_unauthorized_staff_is_excluded_from_staff_specific_document(auth_as, auth_headers, rag_documents):
    for email in (RECEPTIONIST_EMAIL, STAFF_EMAIL):
        auth_as(email)
        response = _search(auth_headers, query="nursing shift handoff checklist continuity of care", top_k=20)
        assert rag_documents["staff_specific"] not in _result_document_ids(response), email


def test_doctor_is_excluded_from_staff_specific_document(auth_as, auth_headers, rag_documents):
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers, query="nursing shift handoff checklist continuity of care", top_k=20)
    assert rag_documents["staff_specific"] not in _result_document_ids(response)


# --- hospital isolation + SUPER_ADMIN bypass ------------------------------


def test_hospital_admin_role_grant_is_scoped_to_own_hospital(auth_as, auth_headers, cleanup_document):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="admin-only.md",
        content=b"# HR Escalation Policy\n\nOnly hospital administrators may escalate HR complaints "
        b"through this internal process.",
        title="HR Escalation Policy",
        document_type="HR_POLICY",
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        response = _search(auth_headers, query="HR escalation policy internal process", top_k=10)
        assert doc_id in _result_document_ids(response)

        auth_as(DOCTOR_EMAIL)
        response = _search(auth_headers, query="HR escalation policy internal process", top_k=10)
        assert doc_id not in _result_document_ids(response)
    finally:
        cleanup_document(doc_id)


def test_hospital_a_user_cannot_retrieve_hospital_b_document(auth_as, auth_headers, cleanup_document, second_hospital):
    auth_as(SUPER_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="hospital-b-only.md",
        content=b"# Hospital B Orientation Guide\n\nThis unique onboarding guide is specific to Hospital B "
        b"and its own internal procedures.",
        title="Hospital B Orientation Guide",
        document_type="HR_POLICY",
        hospital_id=str(second_hospital["hospital_id"]),
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)  # Hospital A
        response = _search(auth_headers, query="Hospital B orientation guide onboarding procedures", top_k=10)
        assert response.status_code == 200
        assert doc_id not in _result_document_ids(response)

        auth_as(SUPER_ADMIN_EMAIL)
        response = _search(auth_headers, query="Hospital B orientation guide onboarding procedures", top_k=10)
        assert doc_id in _result_document_ids(response)
    finally:
        cleanup_document(doc_id)


# --- patients ---------------------------------------------------------


def test_patient_never_retrieves_role_scoped_documents(auth_as, auth_headers, rag_documents):
    """PATIENT is never a valid document_allowed_roles entry (see
    docs/RAG_INGESTION.md - the upload form excludes it) and has no
    doctor_id/staff_id - a patient gets zero results from every document in
    this fixture, by construction, not by a special-cased check."""
    auth_as(PATIENT_EMAIL)
    for query in (
        "cardiology follow-up appointment for stable angina",
        "hand hygiene policy alcohol sanitizer",
        "confidential peer review surgical case",
        "nursing shift handoff checklist continuity of care",
    ):
        response = _search(auth_headers, query=query, top_k=20)
        assert response.status_code == 200
        assert response.json()["results"] == []


# --- processing status / active state -------------------------------------


@pytest.mark.parametrize("status", ["PENDING", "PROCESSING", "FAILED"])
def test_non_completed_document_is_excluded(db_engine, auth_as, auth_headers, cleanup_document, status):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="status-test.md",
        content=b"# Radiology Contrast Safety Checklist\n\nVerify allergy history before administering "
        b"iodinated contrast for any radiology procedure.",
        title="Radiology Contrast Safety Checklist",
        document_type="CLINICAL_GUIDELINE",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        with db_engine.connect() as conn:
            conn.execute(text("UPDATE documents SET status = :status WHERE id = :id"), {"status": status, "id": doc_id})
            conn.commit()

        auth_as(DOCTOR_EMAIL)
        response = _search(auth_headers, query="radiology contrast safety allergy checklist", top_k=10)
        assert response.status_code == 200
        assert doc_id not in _result_document_ids(response)
    finally:
        cleanup_document(doc_id)


def test_archived_document_is_excluded(auth_as, auth_headers, cleanup_document):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="archive-test.md",
        content=b"# Pathology Sample Labeling Guide\n\nEvery pathology sample must be labeled with patient "
        b"identifiers before transport.",
        title="Pathology Sample Labeling Guide",
        document_type="SOP",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    try:
        archive_resp = client.post(f"/api/admin/documents/{doc_id}/archive", headers=auth_headers)
        assert archive_resp.status_code == 200

        auth_as(DOCTOR_EMAIL)
        response = _search(auth_headers, query="pathology sample labeling guide patient identifiers", top_k=10)
        assert response.status_code == 200
        assert doc_id not in _result_document_ids(response)
    finally:
        cleanup_document(doc_id)


# --- enumeration / leakage protection ---------------------------------


def test_unauthorized_only_query_reveals_nothing_beyond_empty_results(auth_as, auth_headers, rag_documents):
    auth_as(RECEPTIONIST_EMAIL)
    response = _search(auth_headers, query="confidential peer review surgical case", top_k=20)
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert set(body.keys()) == {"results", "request_id"}  # request_id (Phase 15) is a safe correlation id, not a leak
    assert "total" not in body
    assert "total_documents" not in body
    assert "accessible_documents" not in body


# --- query validation ---------------------------------------------------


def test_empty_query_rejected(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers, query="")
    assert response.status_code == 422


def test_whitespace_only_query_rejected(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers, query="   ")
    assert response.status_code == 422


def test_oversized_query_rejected(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers, query="x" * 2001)
    assert response.status_code == 422


def test_missing_query_field_rejected(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = client.post("/api/rag/search", json={"top_k": 5}, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.parametrize("top_k", [1, 5, 20])
def test_valid_top_k_accepted(auth_as, auth_headers, top_k):
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers, top_k=top_k)
    assert response.status_code == 200


@pytest.mark.parametrize("top_k", [0, -1, 21, 1000])
def test_invalid_top_k_rejected(auth_as, auth_headers, top_k):
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers, top_k=top_k)
    assert response.status_code == 422


def test_non_integer_top_k_rejected(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = client.post("/api/rag/search", json={"query": "test", "top_k": "abc"}, headers=auth_headers)
    assert response.status_code == 422


def test_default_top_k_used_when_omitted(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = client.post("/api/rag/search", json={"query": "test query"}, headers=auth_headers)
    assert response.status_code == 200


# --- query-parameter manipulation: no field can expand authorization ------


@pytest.mark.parametrize(
    "field,value",
    [
        ("hospital_id", str(uuid.uuid4())),
        ("department_id", str(uuid.uuid4())),
        ("doctor_id", str(uuid.uuid4())),
        ("staff_id", str(uuid.uuid4())),
        ("patient_id", str(uuid.uuid4())),
        ("role", "SUPER_ADMIN"),
        ("user_id", str(uuid.uuid4())),
        ("permissions", ["manage_hospital_documents"]),
    ],
)
def test_authoritative_fields_are_rejected_not_silently_ignored(auth_as, auth_headers, field, value):
    auth_as(DOCTOR_EMAIL)
    response = client.post(
        "/api/rag/search", json={"query": "test query", "top_k": 5, field: value}, headers=auth_headers
    )
    assert response.status_code == 422


# --- injection safety ---------------------------------------------------


def test_malicious_query_string_is_handled_safely(db_engine, auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    malicious = "'; DROP TABLE documents; --"
    response = _search(auth_headers, query=malicious, top_k=3)
    assert response.status_code == 200
    assert isinstance(response.json()["results"], list)
    with db_engine.connect() as conn:
        # the table must still exist and be queryable
        count = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
        assert count >= 0


# --- embedding dimension safety --------------------------------------


def test_query_embedding_dimension_mismatch_returns_safe_error(monkeypatch, auth_as, auth_headers):
    class _WrongDimensionService:
        def embed(self, texts):
            raise EmbeddingDimensionMismatch("Expected 384-dimensional embeddings, got 10.")

    monkeypatch.setattr("app.rag.retrieval_service.get_embedding_service", lambda: _WrongDimensionService())
    auth_as(DOCTOR_EMAIL)
    response = _search(auth_headers)
    assert response.status_code == 503
    assert "10" not in response.json()["detail"]


def test_query_embedding_is_384_dimensional():
    vector = get_embedding_service().embed(["a representative RAG search query"])[0]
    assert len(vector) == 384


# --- audit behavior ------------------------------------------------------


def test_search_writes_audit_event_without_query_text(db_engine, auth_as, auth_headers, rag_documents):
    auth_as(DOCTOR_EMAIL)
    query = "cardiology follow-up appointment for stable angina - unique marker qzk1"
    response = _search(auth_headers, query=query, top_k=5)
    assert response.status_code == 200

    with db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT action, resource_type, resource_id, metadata FROM audit_logs "
                "WHERE action = 'RAG_SEARCH_PERFORMED' ORDER BY created_at DESC LIMIT 1"
            )
        ).mappings().one()

    assert row["resource_type"] == "rag_search"
    assert row["resource_id"] is None
    assert "top_k" in row["metadata"]
    assert "result_count" in row["metadata"]
    assert "qzk1" not in str(row["metadata"])
    assert "query" not in row["metadata"]
    assert "content" not in row["metadata"]


# --- the most important security test in this file -----------------------


def test_repository_excludes_unauthorized_chunks_at_the_sql_level(db_engine, auth_as, auth_headers, cleanup_document):
    """Calls app/repositories/rag_repository.py's search_vector_candidates()
    directly - bypassing the API router and the service layer entirely - to
    prove authorization is enforced by the SQL query itself. This is not
    `results = vector_search(); results = filter_authorized(results)`:
    there is no such Python-side filtering step anywhere in this codebase
    for the caller's request to have skipped - the WHERE clause is the only
    place the decision is made. See docs/RAG_HYBRID_SEARCH.md, "Security
    tests" (the lexical path's equivalent proof is
    test_lexical_repository_excludes_unauthorized_chunks_at_the_sql_level
    in test_rag_hybrid_search.py).
    """
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _upload(
        auth_headers,
        filename="doctor-only.md",
        content=b"# Doctor Only Restricted Content\n\nThis paragraph is restricted to the DOCTOR role only, "
        b"marked with the unique token zzqx-repo-test.",
        title="Doctor Only Restricted Content",
        document_type="CLINICAL_GUIDELINE",
        allowed_roles=["DOCTOR"],
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]

    try:
        with db_engine.connect() as conn:
            chunk_count = conn.execute(
                text("SELECT count(*) FROM document_chunks WHERE document_id = :id"), {"id": doc_id}
            ).scalar_one()
            hospital_id = conn.execute(
                text("SELECT hospital_id FROM documents WHERE id = :id"), {"id": doc_id}
            ).scalar_one()
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
                    "JOIN doctors d ON d.user_id = u.id "
                    "WHERE u.email = :email"
                ),
                {"email": DOCTOR_EMAIL},
            ).mappings().one()

        assert chunk_count > 0  # the chunk genuinely exists in the DB

        query_embedding = get_embedding_service().embed(["doctor only restricted content zzqx-repo-test"])[0]

        receptionist_scope = UserScope(
            user_id=receptionist["id"],
            auth_user_id=receptionist["auth_user_id"],
            role=Role.RECEPTIONIST,
            permissions=frozenset(),
            hospital_id=hospital_id,
            department_id=receptionist["department_id"],
            patient_id=None,
            doctor_id=None,
            staff_id=None,
        )
        doctor_scope = UserScope(
            user_id=doctor["id"],
            auth_user_id=doctor["auth_user_id"],
            role=Role.DOCTOR,
            permissions=frozenset(),
            hospital_id=hospital_id,
            department_id=doctor["department_id"],
            patient_id=None,
            doctor_id=doctor["doctor_id"],
            staff_id=None,
        )

        session = get_session_factory()()
        try:
            unauthorized_results = rag_repository.search_vector_candidates(
                session, scope=receptionist_scope, query_embedding=query_embedding, limit=chunk_count + 5
            )
            authorized_results = rag_repository.search_vector_candidates(
                session, scope=doctor_scope, query_embedding=query_embedding, limit=chunk_count + 5
            )
        finally:
            session.close()

        assert doc_id not in {str(r.document_id) for r in unauthorized_results}
        assert doc_id in {str(r.document_id) for r in authorized_results}
    finally:
        cleanup_document(doc_id)
