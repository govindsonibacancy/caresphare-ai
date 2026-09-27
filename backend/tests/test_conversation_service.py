"""app/repositories/conversation_repository.py +
app/services/conversation_service.py: direct DB-backed tests, no HTTP, no
LLM dependency. See docs/CONVERSATIONAL_AUTH_ROUTING.md, "Conversation
model" and "Ownership".
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import get_session_factory
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import conversation_repository
from app.services import conversation_service
from app.services.conversation_service import ConversationNotFound

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"
PATIENT_EMAIL = "asha.verma@example-patient.example"


@pytest.fixture
def session(db_engine):
    s = get_session_factory()()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    yield
    with db_engine.connect() as conn:
        conn.execute(text("DELETE FROM conversation_messages"))
        conn.execute(text("DELETE FROM conversations"))
        conn.commit()


def _scope_for(db_engine, email: str) -> UserScope:
    with db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT u.id, u.auth_user_id, u.hospital_id, u.department_id, r.name AS role "
                "FROM users u JOIN roles r ON r.id = u.role_id WHERE u.email = :email"
            ),
            {"email": email},
        ).mappings().one()
    return UserScope(
        user_id=row["id"],
        auth_user_id=row["auth_user_id"],
        role=Role(row["role"]),
        permissions=frozenset(),
        hospital_id=row["hospital_id"],
        department_id=row["department_id"],
        patient_id=None,
        doctor_id=None,
        staff_id=None,
    )


# --- conversation creation ---------------------------------------------


def test_no_conversation_id_creates_a_new_conversation(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_service.resolve_conversation(session, scope=scope, conversation_id=None)
    session.commit()
    assert conversation.user_id == scope.user_id
    assert conversation.hospital_id == scope.hospital_id


def test_new_conversation_has_correct_owner_and_hospital(db_engine, session):
    scope = _scope_for(db_engine, PATIENT_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()
    fetched = conversation_repository.get_conversation(session, conversation.id)
    assert fetched is not None
    assert fetched.user_id == scope.user_id
    assert fetched.hospital_id == scope.hospital_id


# --- conversation retrieval / ownership ---------------------------------


def test_owner_can_continue_their_own_conversation(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    created = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    resolved = conversation_service.resolve_conversation(session, scope=scope, conversation_id=created.id)
    assert resolved.id == created.id


def test_another_user_cannot_access_conversation(db_engine, session):
    owner_scope = _scope_for(db_engine, DOCTOR_EMAIL)
    created = conversation_repository.create_conversation(session, user_id=owner_scope.user_id, hospital_id=owner_scope.hospital_id)
    session.commit()

    other_scope = _scope_for(db_engine, PATIENT_EMAIL)
    with pytest.raises(ConversationNotFound):
        conversation_service.resolve_conversation(session, scope=other_scope, conversation_id=created.id)


def test_invalid_conversation_id_raises_not_found(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    with pytest.raises(ConversationNotFound):
        conversation_service.resolve_conversation(session, scope=scope, conversation_id=uuid.uuid4())


def test_cross_hospital_access_is_rejected(db_engine, session, second_hospital):
    owner_scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)  # Hospital A
    created = conversation_repository.create_conversation(session, user_id=owner_scope.user_id, hospital_id=owner_scope.hospital_id)
    session.commit()

    hospital_b_scope = UserScope(
        user_id=second_hospital["admin_user_id"],
        auth_user_id=second_hospital["admin_user_id"],
        role=Role.HOSPITAL_ADMIN,
        permissions=frozenset(),
        hospital_id=second_hospital["hospital_id"],
        department_id=None,
        patient_id=None,
        doctor_id=None,
        staff_id=None,
    )
    with pytest.raises(ConversationNotFound):
        conversation_service.resolve_conversation(session, scope=hospital_b_scope, conversation_id=created.id)


def test_super_admin_can_access_any_conversation(db_engine, session, second_hospital):
    """Matches the exact SUPER_ADMIN bypass every other can_access_*
    check in this codebase already has - not a new bypass."""
    owner_scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    created = conversation_repository.create_conversation(session, user_id=owner_scope.user_id, hospital_id=owner_scope.hospital_id)
    session.commit()

    super_admin_scope = _scope_for(db_engine, SUPER_ADMIN_EMAIL)
    resolved = conversation_service.resolve_conversation(session, scope=super_admin_scope, conversation_id=created.id)
    assert resolved.id == created.id


# --- message validation -----------------------------------------------


def test_invalid_role_is_rejected_by_the_database(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    with pytest.raises(IntegrityError):
        conversation_repository.add_message(session, conversation_id=conversation.id, role="SYSTEM", content="hello")
    session.rollback()


def test_blank_content_is_rejected_by_the_database(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    with pytest.raises(IntegrityError):
        conversation_repository.add_message(session, conversation_id=conversation.id, role="USER", content="   ")
    session.rollback()


def test_valid_user_and_assistant_messages_are_accepted(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    user_msg = conversation_repository.add_message(session, conversation_id=conversation.id, role="USER", content="hi")
    assistant_msg = conversation_repository.add_message(
        session, conversation_id=conversation.id, role="ASSISTANT", content="hello"
    )
    session.commit()
    assert user_msg.role == "USER"
    assert assistant_msg.role == "ASSISTANT"
    assert assistant_msg.seq > user_msg.seq  # deterministic ordering, server-assigned


# --- context limits: bounded history ------------------------------------


def test_old_turns_are_truncated_beyond_max_turns(db_engine, session, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("CONVERSATION_MAX_TURNS", "2")
    config_module.get_settings.cache_clear()
    try:
        scope = _scope_for(db_engine, DOCTOR_EMAIL)
        conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
        session.commit()

        for i in range(5):
            conversation_repository.add_message(session, conversation_id=conversation.id, role="USER", content=f"question {i}")
            conversation_repository.add_message(session, conversation_id=conversation.id, role="ASSISTANT", content=f"answer {i}")
        session.commit()

        history = conversation_service.load_bounded_history(session, conversation.id)
        assert history.turn_count == 2
        assert "question 0" not in history.text
        assert "question 4" in history.text  # only the most recent turns survive
    finally:
        config_module.get_settings.cache_clear()


def test_max_context_chars_drops_oldest_turns_first(db_engine, session, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("CONVERSATION_MAX_TURNS", "10")
    monkeypatch.setenv("CONVERSATION_MAX_CONTEXT_CHARS", "200")
    config_module.get_settings.cache_clear()
    try:
        scope = _scope_for(db_engine, DOCTOR_EMAIL)
        conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
        session.commit()

        for i in range(10):
            conversation_repository.add_message(
                session, conversation_id=conversation.id, role="USER", content=f"question number {i} " * 3
            )
            conversation_repository.add_message(
                session, conversation_id=conversation.id, role="ASSISTANT", content=f"answer number {i} " * 3
            )
        session.commit()

        history = conversation_service.load_bounded_history(session, conversation.id)
        assert len(history.text) <= 200
        assert "question number 9" in history.text  # most recent turn always kept if it fits
        assert "question number 0" not in history.text
    finally:
        config_module.get_settings.cache_clear()


def test_llm_never_receives_unbounded_history(db_engine, session, monkeypatch):
    """A conversation with far more turns than the configured maximum
    still only ever produces a bounded history text - the LLM-facing
    payload can never grow without limit."""
    from app.core import config as config_module

    monkeypatch.setenv("CONVERSATION_MAX_TURNS", "3")
    config_module.get_settings.cache_clear()
    try:
        scope = _scope_for(db_engine, DOCTOR_EMAIL)
        conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
        session.commit()

        for i in range(50):
            conversation_repository.add_message(session, conversation_id=conversation.id, role="USER", content=f"q{i}")
            conversation_repository.add_message(session, conversation_id=conversation.id, role="ASSISTANT", content=f"a{i}")
        session.commit()

        history = conversation_service.load_bounded_history(session, conversation.id)
        assert history.turn_count <= 3
    finally:
        config_module.get_settings.cache_clear()


def test_record_turn_persists_exactly_one_user_and_one_assistant_message(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    conversation_service.record_turn(session, conversation=conversation, user_message="hi", assistant_message="hello")
    session.commit()

    messages = conversation_repository.list_recent_messages(session, conversation.id, max_turns=10)
    assert len(messages) == 2
    assert messages[0].role == "USER"
    assert messages[1].role == "ASSISTANT"


# --- Phase 17: conversation title ---------------------------------------


def test_title_is_derived_from_the_first_user_message(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    conversation_service.record_turn(
        session, conversation=conversation, user_message="What is the hospital policy on visiting hours?", assistant_message="..."
    )
    session.commit()

    fetched = conversation_repository.get_conversation(session, conversation.id)
    assert fetched.title == "What is the hospital policy on visiting hours?"


def test_title_is_set_only_once_not_overwritten_by_later_turns(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    conversation_service.record_turn(session, conversation=conversation, user_message="First question", assistant_message="a1")
    session.commit()
    conversation_service.record_turn(session, conversation=conversation, user_message="Second question", assistant_message="a2")
    session.commit()

    fetched = conversation_repository.get_conversation(session, conversation.id)
    assert fetched.title == "First question"


def test_title_is_truncated_and_whitespace_collapsed():
    long_message = "  This    is a very long first message " + ("padding " * 20)
    title = conversation_service._derive_title(long_message)
    assert len(title) <= 61  # _TITLE_MAX_LENGTH + ellipsis
    assert title.endswith("…")
    assert "  " not in title


def test_title_handles_blank_message_safely():
    assert conversation_service._derive_title("   \n\t  ") == ""


# --- Phase 17: sources persisted with assistant messages -----------------


def test_assistant_message_sources_are_persisted_and_user_message_has_none(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()

    sources = [{"number": 1, "id": "source-1", "type": "document", "label": "Policy", "document_id": None,
                "document_title": None, "document_type": None, "chunk_id": None, "page": None, "section": None}]
    conversation_service.record_turn(
        session, conversation=conversation, user_message="hi", assistant_message="hello", assistant_sources=sources
    )
    session.commit()

    messages = conversation_repository.list_recent_messages(session, conversation.id, max_turns=10)
    assert messages[0].sources == []
    assert messages[1].sources == sources


# --- Phase 17: conversation list / detail --------------------------------


def test_list_conversations_returns_only_the_callers_own_conversations(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    other_scope = _scope_for(db_engine, PATIENT_EMAIL)
    mine = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    conversation_repository.create_conversation(session, user_id=other_scope.user_id, hospital_id=other_scope.hospital_id)
    session.commit()

    records, total = conversation_service.list_conversations(session, scope=scope, page=1, page_size=20)
    assert total == 1
    assert [r.id for r in records] == [mine.id]


def test_list_conversations_orders_by_last_activity_desc(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    first = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()
    second = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()
    # Touch `first` last, so it should now sort ahead of `second` despite
    # having been created earlier.
    conversation_repository.touch_conversation(session, first.id)
    session.commit()

    records, _total = conversation_service.list_conversations(session, scope=scope, page=1, page_size=20)
    assert [r.id for r in records] == [first.id, second.id]


def test_list_conversations_is_empty_for_a_user_with_no_conversations(db_engine, session):
    scope = _scope_for(db_engine, PATIENT_EMAIL)
    records, total = conversation_service.list_conversations(session, scope=scope, page=1, page_size=20)
    assert records == []
    assert total == 0


def test_get_conversation_detail_returns_conversation_and_messages(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    conversation = conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)
    session.commit()
    conversation_service.record_turn(session, conversation=conversation, user_message="hi", assistant_message="hello")
    session.commit()

    detail, messages = conversation_service.get_conversation_detail(session, scope=scope, conversation_id=conversation.id)
    assert detail.id == conversation.id
    assert len(messages) == 2


def test_get_conversation_detail_raises_not_found_for_another_users_conversation(db_engine, session):
    owner_scope = _scope_for(db_engine, DOCTOR_EMAIL)
    created = conversation_repository.create_conversation(session, user_id=owner_scope.user_id, hospital_id=owner_scope.hospital_id)
    session.commit()

    other_scope = _scope_for(db_engine, PATIENT_EMAIL)
    with pytest.raises(ConversationNotFound):
        conversation_service.get_conversation_detail(session, scope=other_scope, conversation_id=created.id)
