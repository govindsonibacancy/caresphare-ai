"""End-to-end Phase 17 tests through the real GET /api/conversations and
GET /api/conversations/{id} endpoints: listing (self-scoped, ordered,
paginated), detail retrieval (messages + sources), ownership (cross-user/
cross-hospital 404), empty history, and title derivation as observed
through the real API. See docs/CONVERSATION_HISTORY.md, "Security tests".

Ollama generation is always mocked (never a live call).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.routing.intents import QueryRoute, Route

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"
PATIENT_EMAIL = "asha.verma@example-patient.example"


def _ask(headers, **body):
    body.setdefault("query", "test question")
    body.setdefault("top_k", 5)
    return client.post("/api/rag/answer", json=body, headers=headers)


class _FakeLLM:
    def __init__(self, response_text="Generated answer. [Source 1]"):
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
        conn.commit()


@pytest.fixture
def fake_llm(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr("app.llm.answer_service.get_llm_service", lambda: fake)
    return fake


@pytest.fixture
def force_rag_route(monkeypatch):
    monkeypatch.setattr(
        "app.llm.answer_service.route_query",
        lambda query, **kwargs: QueryRoute(route=Route.RAG, structured_intent=None, entity_reference=None, classification_source="test"),
    )


def _upload(headers, *, filename="policy.txt", content=b"Some policy content.", **form):
    files = {"file": (filename, content, "text/plain")}
    data = {
        "title": form.pop("title", "Conversation History E2E Test Document"),
        "document_type": form.pop("document_type", "HOSPITAL_POLICY"),
        "sensitivity": form.pop("sensitivity", "PUBLIC"),
        **form,
    }
    return client.post("/api/admin/documents", files=files, data=data, headers=headers)


# --- listing ------------------------------------------------------------


def test_unauthenticated_request_cannot_list_conversations(auth_headers):
    resp = client.get("/api/conversations", headers=auth_headers)
    assert resp.status_code == 401


def test_new_user_has_an_empty_conversation_list(auth_as, auth_headers):
    auth_as(PATIENT_EMAIL)
    resp = client.get("/api/conversations", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_only_contains_the_authenticated_users_own_conversations(
    auth_as, auth_headers, fake_llm, force_rag_route
):
    auth_as(DOCTOR_EMAIL)
    _ask(auth_headers, query="What is the hospital policy?")

    auth_as(PATIENT_EMAIL)
    resp = client.get("/api/conversations", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["items"] == []

    auth_as(DOCTOR_EMAIL)
    resp = client.get("/api/conversations", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_list_includes_title_and_timestamps_never_message_content(auth_as, auth_headers, fake_llm, force_rag_route):
    auth_as(DOCTOR_EMAIL)
    _ask(auth_headers, query="What is the hospital policy on visiting hours?")

    resp = client.get("/api/conversations", headers=auth_headers)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["title"] == "What is the hospital policy on visiting hours?"
    assert set(item.keys()) == {"id", "title", "created_at", "updated_at", "last_activity_at"}


def test_list_orders_most_recently_active_first(auth_as, auth_headers, fake_llm, force_rag_route):
    auth_as(DOCTOR_EMAIL)
    first = _ask(auth_headers, query="First conversation").json()["conversation_id"]
    second = _ask(auth_headers, query="Second conversation").json()["conversation_id"]
    # Continue the first conversation - it should now sort ahead of the second.
    _ask(auth_headers, query="Follow-up on first", conversation_id=first)

    resp = client.get("/api/conversations", headers=auth_headers)
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [first, second]


def test_list_pagination_page_size_is_bounded(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    resp = client.get("/api/conversations?page_size=1000", headers=auth_headers)
    assert resp.status_code == 422  # page_size is bounded by the existing shared MAX_PAGE_SIZE validator


# --- detail ---------------------------------------------------------------


def test_detail_returns_messages_and_sources_for_the_owner(auth_as, auth_headers, cleanup_document, fake_llm, force_rag_route):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    upload_resp = _upload(
        auth_headers,
        filename="history-detail-test.md",
        content=b"# Policy\n\nUnique marker zzqx-history-detail-test.",
        allowed_roles=["HOSPITAL_ADMIN"],
    )
    doc_id = upload_resp.json()["id"]
    try:
        first = _ask(auth_headers, query="zzqx-history-detail-test")
        conversation_id = first.json()["conversation_id"]

        resp = client.get(f"/api/conversations/{conversation_id}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == conversation_id
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "USER"
        assert body["messages"][1]["role"] == "ASSISTANT"
        assert len(body["messages"][1]["sources"]) == 1
        assert body["messages"][1]["sources"][0]["number"] == 1
        assert body["messages"][0]["sources"] == []
    finally:
        cleanup_document(doc_id)


def test_detail_for_a_conversation_with_no_messages_yet_is_empty_but_valid(auth_as, auth_headers, db_engine):
    from app.core.db import get_session_factory
    from app.repositories import conversation_repository

    auth_as(DOCTOR_EMAIL)
    # Create a bare conversation directly (never happens via the real API,
    # which only ever creates one alongside a first message) to prove the
    # detail endpoint handles a zero-message conversation safely.
    with db_engine.connect() as conn:
        row = conn.execute(text("SELECT id, hospital_id FROM users WHERE email = :e"), {"e": DOCTOR_EMAIL}).mappings().one()

    session = get_session_factory()()
    try:
        conversation = conversation_repository.create_conversation(session, user_id=row["id"], hospital_id=row["hospital_id"])
        session.commit()
    finally:
        session.close()

    resp = client.get(f"/api/conversations/{conversation.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] is None
    assert body["messages"] == []


def test_nonexistent_conversation_returns_404(auth_as, auth_headers):
    import uuid

    auth_as(DOCTOR_EMAIL)
    resp = client.get(f"/api/conversations/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


def test_another_users_conversation_is_not_exposed(auth_as, auth_headers, fake_llm, force_rag_route):
    auth_as(DOCTOR_EMAIL)
    conversation_id = _ask(auth_headers, query="A private question about my day").json()["conversation_id"]

    auth_as(PATIENT_EMAIL)
    resp = client.get(f"/api/conversations/{conversation_id}", headers=auth_headers)
    assert resp.status_code == 404
    assert "private question" not in resp.text
    assert "title" not in resp.json()


def test_cross_hospital_conversation_is_not_exposed(auth_as, auth_headers, second_hospital, fake_llm, force_rag_route):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    conversation_id = _ask(auth_headers, query="Hospital A conversation").json()["conversation_id"]

    auth_as(second_hospital["admin_email"])
    try:
        resp = client.get(f"/api/conversations/{conversation_id}", headers=auth_headers)
        assert resp.status_code == 404
    finally:
        pass


def test_unauthenticated_request_cannot_read_conversation_detail(auth_headers):
    import uuid

    resp = client.get(f"/api/conversations/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 401
