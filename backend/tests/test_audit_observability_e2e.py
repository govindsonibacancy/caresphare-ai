"""End-to-end Phase 15 audit/observability tests through the real API:
request-id generation/propagation/persistence, authorization-denial and
conversation-ownership-denial auditing, routing/structured/RAG/LLM/
citation/Admin-AI observability metadata, and - the most important tests
in this file - direct proof that no PHI, secret, SQL, token, or raw
answer/prompt text ever reaches persistent audit storage, using sentinel
values specifically designed to make accidental leakage obvious. See
docs/AUDIT_AND_OBSERVABILITY.md, "Security tests" / "PHI minimization".

Ollama generation is always mocked (never a live call).
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.routing.intents import QueryRoute, Route, StructuredIntent

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"
PATIENT_EMAIL = "asha.verma@example-patient.example"


def _ask(headers, **body):
    body.setdefault("query", "test question")
    body.setdefault("top_k", 5)
    return client.post("/api/rag/answer", json=body, headers=headers)


def _upload(headers, *, filename="policy.txt", content=b"Some policy content.", **form):
    files = {"file": (filename, content, "text/plain")}
    data = {
        "title": form.pop("title", "Audit Observability Test Document"),
        "document_type": form.pop("document_type", "HOSPITAL_POLICY"),
        "sensitivity": form.pop("sensitivity", "PUBLIC"),
        **form,
    }
    return client.post("/api/admin/documents", files=files, data=data, headers=headers)


class _FakeLLM:
    def __init__(self, response_text="Generated answer."):
        self.response_text = response_text
        self.calls: list[list[dict[str, str]]] = []

    def generate(self, *, messages):
        self.calls.append(messages)
        return self.response_text


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    yield
    with db_engine.connect() as conn:
        conn.execute(text("DELETE FROM conversation_messages"))
        conn.execute(text("DELETE FROM conversations"))
        conn.execute(text("DELETE FROM audit_logs WHERE resource_type IN ('rag_search', 'rag_answer', 'conversation')"))
        conn.execute(text("DELETE FROM audit_logs WHERE action = 'AUTHORIZATION_DENIED'"))
        conn.commit()


@pytest.fixture
def fake_llm(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr("app.llm.answer_service.get_llm_service", lambda: fake)
    return fake


def _force_route(monkeypatch, route: Route = Route.RAG, intent=None, entity_reference=None):
    monkeypatch.setattr(
        "app.llm.answer_service.route_query",
        lambda query, **kwargs: QueryRoute(
            route=route, structured_intent=intent, entity_reference=entity_reference, classification_source="test"
        ),
    )


def _events_for_request(db_engine, request_id: str, action: str | None = None) -> list[dict]:
    query = "SELECT action, resource_type, resource_id, metadata FROM audit_logs WHERE request_id = :request_id"
    params: dict[str, object] = {"request_id": request_id}
    if action is not None:
        query += " AND action = :action"
        params["action"] = action
    with db_engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


def _all_metadata_text(db_engine) -> str:
    """Every row's action + resource_type + metadata, flattened into one
    string - a single haystack sentinel-search checks against, so a leak
    into *any* column/field is caught, not just the one a test author
    happened to think to check individually."""
    with db_engine.connect() as conn:
        rows = conn.execute(text("SELECT action, resource_type, metadata FROM audit_logs")).mappings().all()
    return "\n".join(f"{r['action']} {r['resource_type']} {json.dumps(r['metadata'])}" for r in rows)


# --- request id: generation, propagation, non-authorization -----------------


def test_request_id_is_present_in_response_and_header(auth_as, auth_headers, fake_llm, monkeypatch):
    _force_route(monkeypatch)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _ask(auth_headers, query="What is the hospital policy?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] is not None
    uuid.UUID(body["request_id"])  # well-formed
    assert resp.headers["X-Request-ID"] == body["request_id"]


def test_request_id_is_unique_per_request(auth_as, auth_headers, fake_llm, monkeypatch):
    _force_route(monkeypatch)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    first = _ask(auth_headers, query="What is the hospital policy?").json()["request_id"]
    second = _ask(auth_headers, query="What is the hospital policy?").json()["request_id"]
    assert first != second


def test_client_supplied_request_id_header_is_never_used(auth_as, auth_headers, fake_llm, monkeypatch):
    """A client cannot inject its own correlation id - the header is
    always server-generated, regardless of what an inbound request sends."""
    _force_route(monkeypatch)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    spoofed = "11111111-1111-1111-1111-111111111111"
    resp = client.post(
        "/api/rag/answer",
        json={"query": "What is the hospital policy?", "top_k": 5},
        headers={**auth_headers, "X-Request-ID": spoofed},
    )
    assert resp.status_code == 200
    assert resp.json()["request_id"] != spoofed


def test_request_id_is_persisted_and_correlates_the_audit_event(auth_as, auth_headers, fake_llm, monkeypatch, db_engine):
    _force_route(monkeypatch)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _ask(auth_headers, query="What is the hospital policy?")
    request_id = resp.json()["request_id"]
    events = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")
    assert len(events) == 1


# --- authorization denial auditing ------------------------------------------


def test_authorization_denial_is_audited_with_safe_metadata_only(auth_as, auth_headers, db_engine):
    auth_as(PATIENT_EMAIL)
    resp = client.post(
        "/api/admin/invitations",
        json={"email": "someone@example.com", "first_name": "A", "last_name": "B", "role": "STAFF"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    request_id = resp.headers["X-Request-ID"]
    events = _events_for_request(db_engine, request_id, action="AUTHORIZATION_DENIED")
    assert len(events) == 1
    metadata = events[0]["metadata"]
    assert metadata["permission"] == "manage_users"
    # No resource content, no request body content, nothing beyond the
    # permission code that was checked.
    assert "someone@example.com" not in json.dumps(metadata)


def test_successful_authorized_request_produces_no_denial_event(auth_as, auth_headers, db_engine):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = client.get("/api/admin/invitations", headers=auth_headers)
    assert resp.status_code == 200
    request_id = resp.headers["X-Request-ID"]
    assert _events_for_request(db_engine, request_id, action="AUTHORIZATION_DENIED") == []


# --- conversation-ownership denial auditing --------------------------------


def test_conversation_denial_is_audited_without_message_content(auth_as, auth_headers, fake_llm, monkeypatch, db_engine):
    _force_route(monkeypatch)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    first = _ask(auth_headers, query="What is the hospital policy?")
    conversation_id = first.json()["conversation_id"]

    auth_as(DOCTOR_EMAIL)
    second = _ask(auth_headers, query="SECRET_QUERY_SENTINEL_998877", conversation_id=conversation_id)
    assert second.status_code == 404
    request_id = second.headers["X-Request-ID"]
    events = _events_for_request(db_engine, request_id, action="CONVERSATION_DENIED")
    assert len(events) == 1
    # The attempted conversation id is safe to record (just a UUID, no
    # content) and useful for investigating repeated probing.
    assert str(events[0]["resource_id"]) == conversation_id
    assert "SECRET_QUERY_SENTINEL_998877" not in json.dumps(events[0]["metadata"])


# --- routing / structured / RAG observability -------------------------------


def test_routing_metadata_is_recorded_without_raw_query_text(auth_as, auth_headers, fake_llm, monkeypatch, db_engine):
    _force_route(monkeypatch, route=Route.STRUCTURED, intent=StructuredIntent.MY_APPOINTMENTS)
    auth_as(PATIENT_EMAIL)
    unique_query = "QUERY_TEXT_SENTINEL_445566 what are my appointments"
    resp = _ask(auth_headers, query=unique_query)
    request_id = resp.json()["request_id"]
    events = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")
    assert len(events) == 1
    metadata = events[0]["metadata"]
    assert metadata["route"] == "STRUCTURED"
    assert metadata["classification_source"] == "test"
    assert "QUERY_TEXT_SENTINEL_445566" not in json.dumps(metadata)


def test_structured_query_result_and_duration_are_recorded(auth_as, auth_headers, fake_llm, monkeypatch, db_engine):
    _force_route(monkeypatch, route=Route.STRUCTURED, intent=StructuredIntent.MY_APPOINTMENTS)
    auth_as(PATIENT_EMAIL)
    resp = _ask(auth_headers, query="what are my appointments")
    request_id = resp.json()["request_id"]
    metadata = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")[0]["metadata"]
    assert isinstance(metadata["total_duration_ms"], int)
    assert metadata["total_duration_ms"] >= 0
    assert "retrieval_duration_ms" in metadata
    assert "SELECT" not in json.dumps(metadata).upper()


def test_rag_search_metadata_has_no_query_or_chunk_content(auth_as, auth_headers, cleanup_document, db_engine):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    upload_resp = _upload(
        auth_headers,
        filename="unique-content.md",
        content=b"# Policy\n\nUnique marker CHUNK_CONTENT_SENTINEL_112233.",
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    assert upload_resp.status_code == 201, upload_resp.text
    doc_id = upload_resp.json()["id"]
    try:
        resp = client.post(
            "/api/rag/search",
            json={"query": "CHUNK_CONTENT_SENTINEL_112233", "top_k": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        request_id = resp.json()["request_id"]
        events = _events_for_request(db_engine, request_id, action="RAG_SEARCH_PERFORMED")
        assert len(events) == 1
        metadata_text = json.dumps(events[0]["metadata"])
        assert "CHUNK_CONTENT_SENTINEL_112233" not in metadata_text
        assert "duration_ms" in events[0]["metadata"]
    finally:
        cleanup_document(doc_id)


# --- LLM observability -------------------------------------------------------


def test_llm_observability_records_model_and_duration_never_prompt_or_answer(
    auth_as, auth_headers, cleanup_document, monkeypatch, db_engine
):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    upload_resp = _upload(
        auth_headers,
        filename="llm-observability-test.md",
        content=b"# Policy\n\nUnique marker zzqx-llm-observability-test.",
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    doc_id = upload_resp.json()["id"]
    try:
        _force_route(monkeypatch)
        fake = _FakeLLM(response_text="ANSWER_TEXT_SENTINEL_334455 the policy is...")
        monkeypatch.setattr("app.llm.answer_service.get_llm_service", lambda: fake)
        resp = _ask(auth_headers, query="PROMPT_TEXT_SENTINEL_667788 zzqx-llm-observability-test")
        request_id = resp.json()["request_id"]
        metadata = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")[0]["metadata"]
        assert metadata["result"] == "SUCCESS"
        assert isinstance(metadata.get("generation_duration_ms"), int)
        metadata_text = json.dumps(metadata)
        assert "ANSWER_TEXT_SENTINEL_334455" not in metadata_text
        assert "PROMPT_TEXT_SENTINEL_667788" not in metadata_text
    finally:
        cleanup_document(doc_id)


def test_llm_failure_is_audited_as_failed_with_a_safe_error_code(
    auth_as, auth_headers, cleanup_document, monkeypatch, db_engine
):
    from app.llm.ollama_client import LLMServiceUnavailable

    auth_as(HOSPITAL_ADMIN_EMAIL)
    upload_resp = _upload(
        auth_headers,
        filename="llm-failure-test.md",
        content=b"# Policy\n\nUnique marker zzqx-llm-failure-test.",
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    doc_id = upload_resp.json()["id"]
    try:
        _force_route(monkeypatch)

        class _BrokenLLM:
            def generate(self, *, messages):
                raise LLMServiceUnavailable("Could not reach the language model service.")

        monkeypatch.setattr("app.llm.answer_service.get_llm_service", lambda: _BrokenLLM())
        resp = _ask(auth_headers, query="zzqx-llm-failure-test")
        assert resp.status_code == 503
        request_id = resp.headers["X-Request-ID"]
        metadata = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")[0]["metadata"]
        assert metadata["result"] == "FAILED"
        assert metadata["error_code"] == "LLM_UNAVAILABLE"
        assert "Could not reach" not in json.dumps(metadata)
    finally:
        cleanup_document(doc_id)


def test_retrieval_failure_is_audited_as_failed(auth_as, auth_headers, monkeypatch, db_engine):
    from app.rag.retrieval_service import RetrievalError

    _force_route(monkeypatch)
    monkeypatch.setattr(
        "app.llm.answer_service.retrieval_search",
        lambda *a, **k: (_ for _ in ()).throw(RetrievalError("boom")),
    )
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _ask(auth_headers, query="what is the policy")
    assert resp.status_code == 503
    request_id = resp.headers["X-Request-ID"]
    metadata = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")[0]["metadata"]
    assert metadata["result"] == "FAILED"
    assert metadata["error_code"] == "RETRIEVAL_FAILURE"


# --- citation observability --------------------------------------------------


def test_citation_metrics_are_recorded_without_the_answer_text(auth_as, auth_headers, cleanup_document, monkeypatch, db_engine):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    upload_resp = _upload(
        auth_headers,
        filename="citation-test.md",
        content=b"# Policy\n\nUnique marker zzqx-audit-citation-test.",
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    doc_id = upload_resp.json()["id"]
    try:
        fake = _FakeLLM(response_text="ANSWER_SENTINEL_99999 [Source 1] and a fake [Source 999].")
        monkeypatch.setattr("app.llm.answer_service.get_llm_service", lambda: fake)
        _force_route(monkeypatch, route=Route.RAG)
        resp = _ask(auth_headers, query="zzqx-audit-citation-test")
        assert resp.status_code == 200
        request_id = resp.json()["request_id"]
        metadata = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")[0]["metadata"]
        assert metadata["cited_valid_count"] == 1
        assert metadata["cited_invalid_count"] == 1
        assert "ANSWER_SENTINEL_99999" not in json.dumps(metadata)
        assert "document" in metadata["source_types"]
    finally:
        cleanup_document(doc_id)


# --- Admin AI observability (no new event type; existing one, labeled) -----


def test_admin_ai_query_uses_the_same_event_type_labeled_by_intent(auth_as, auth_headers, fake_llm, monkeypatch, db_engine):
    _force_route(monkeypatch, route=Route.STRUCTURED, intent=StructuredIntent.ADMIN_EMPLOYEE_SUMMARY)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    resp = _ask(auth_headers, query="how many employees do we have")
    request_id = resp.json()["request_id"]
    metadata = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")[0]["metadata"]
    assert metadata["structured_intent"] == "ADMIN_EMPLOYEE_SUMMARY"
    # No new event type was created for Admin AI (see docs/ADMIN_AI.md /
    # docs/AUDIT_AND_OBSERVABILITY.md, "Admin AI events") - it's the same
    # RAG_ANSWER_GENERATED event, identified only by the intent value.
    # No raw administrative record content (role counts, names) is ever
    # persisted - just the closed set of allowlisted keys.
    assert set(metadata.keys()) <= {
        "route",
        "result",
        "result_count",
        "model",
        "structured_intent",
        "conversation_id",
        "classification_source",
        "total_duration_ms",
        "retrieval_duration_ms",
        "generation_duration_ms",
        "source_types",
        "cited_valid_count",
        "cited_invalid_count",
        "error_code",
    }


def test_admin_ai_denial_is_audited_as_denied_not_no_data(auth_as, auth_headers, fake_llm, monkeypatch, db_engine):
    _force_route(monkeypatch, route=Route.STRUCTURED, intent=StructuredIntent.ADMIN_EMPLOYEE_SUMMARY)
    auth_as(PATIENT_EMAIL)
    resp = _ask(auth_headers, query="how many employees do we have")
    assert resp.status_code == 200
    request_id = resp.json()["request_id"]
    metadata = _events_for_request(db_engine, request_id, action="RAG_ANSWER_GENERATED")[0]["metadata"]
    assert metadata["result"] == "DENIED"
    assert fake_llm.calls == []


# --- privacy: sentinel leakage sweep across ALL persisted audit data -------


def test_no_sentinel_values_ever_appear_in_persisted_audit_data(
    auth_as, auth_headers, cleanup_document, monkeypatch, db_engine
):
    """The single most important test in this file: exercises the full
    pipeline (auth -> authorization -> routing -> retrieval -> LLM ->
    citations -> conversation persistence -> audit) with sentinel values
    planted in the query, the uploaded document, and the model's answer,
    then sweeps every column of every audit_logs row for all of them."""
    phi_sentinel = "PHI_SENTINEL_12345"
    answer_sentinel = "ANSWER_SENTINEL_99999"
    sql_sentinel = "SQL_SENTINEL_SELECT * FROM patients"

    auth_as(HOSPITAL_ADMIN_EMAIL)
    upload_resp = _upload(
        auth_headers,
        filename="phi-sentinel.md",
        content=f"# Policy\n\nContains {phi_sentinel} for leak-testing.".encode(),
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    doc_id = upload_resp.json()["id"]
    try:
        fake = _FakeLLM(response_text=f"{answer_sentinel} - the policy mentions {phi_sentinel}. [Source 1]")
        monkeypatch.setattr("app.llm.answer_service.get_llm_service", lambda: fake)
        _force_route(monkeypatch, route=Route.RAG)

        resp = _ask(auth_headers, query=f"{phi_sentinel} {sql_sentinel}")
        assert resp.status_code == 200

        haystack = _all_metadata_text(db_engine)
        for sentinel in (phi_sentinel, answer_sentinel, sql_sentinel):
            assert sentinel not in haystack, f"{sentinel} leaked into persisted audit data"
    finally:
        cleanup_document(doc_id)


def test_no_credential_or_token_like_values_appear_in_authorization_denial_audit(auth_as, auth_headers, db_engine):
    secret_sentinel = "SECRET_SENTINEL_67890"
    token_sentinel = "TOKEN_SENTINEL_ABCDE"
    auth_as(PATIENT_EMAIL)
    resp = client.post(
        "/api/admin/invitations",
        json={"email": f"{secret_sentinel}@example.com", "first_name": token_sentinel, "last_name": "B", "role": "STAFF"},
        headers={**auth_headers, "Authorization": f"Bearer {token_sentinel}"},
    )
    assert resp.status_code == 403
    haystack = _all_metadata_text(db_engine)
    assert secret_sentinel not in haystack
    assert token_sentinel not in haystack


# --- cross-hospital / cross-user audit correctness --------------------------


def test_audit_event_hospital_and_actor_are_correct_and_isolated(
    auth_as, auth_headers, second_hospital, fake_llm, monkeypatch, db_engine
):
    _force_route(monkeypatch)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    a_resp = _ask(auth_headers, query="What is the hospital policy?")
    a_request_id = a_resp.json()["request_id"]

    auth_as(second_hospital["admin_email"])
    try:
        b_resp = _ask(auth_headers, query="What is the hospital policy?")
        b_request_id = b_resp.json()["request_id"]

        with db_engine.connect() as conn:
            a_hospital = conn.execute(
                text("SELECT hospital_id FROM audit_logs WHERE request_id = :r"), {"r": a_request_id}
            ).scalar_one()
            b_hospital = conn.execute(
                text("SELECT hospital_id FROM audit_logs WHERE request_id = :r"), {"r": b_request_id}
            ).scalar_one()
        assert str(a_hospital) != str(b_hospital)
        with db_engine.connect() as conn:
            a_actor = conn.execute(text("SELECT actor_user_id FROM audit_logs WHERE request_id = :r"), {"r": a_request_id}).scalar_one()
            b_actor = conn.execute(text("SELECT actor_user_id FROM audit_logs WHERE request_id = :r"), {"r": b_request_id}).scalar_one()
        assert str(a_actor) != str(b_actor)
    finally:
        with db_engine.connect() as conn:
            conn.execute(
                text(
                    "DELETE FROM conversation_messages WHERE conversation_id IN "
                    "(SELECT id FROM conversations WHERE user_id = :u)"
                ),
                {"u": second_hospital["admin_user_id"]},
            )
            conn.execute(text("DELETE FROM conversations WHERE user_id = :u"), {"u": second_hospital["admin_user_id"]})
            conn.commit()
