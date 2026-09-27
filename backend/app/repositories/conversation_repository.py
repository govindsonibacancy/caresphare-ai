"""Data access for `conversations`/`conversation_messages` (Phase 13,
migration 0019; `title`/`sources` added by Phase 17, migration 0021). See
docs/CONVERSATIONAL_AUTH_ROUTING.md and docs/CONVERSATION_HISTORY.md.

This module never makes an authorization decision - it is a plain
repository, following the exact pattern every other `*_repository.py` in
this codebase uses (plain functions taking a `Session`, returning frozen
dataclasses, no ORM). Ownership/hospital-scope verification lives in
`app/services/conversation_service.py`, one layer up - consistent with how
`clinical_repository.py`'s scope-clause helpers are the mechanism and
`app/permissions/authorization.py` is the policy.
"""

import datetime
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ConversationRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    hospital_id: uuid.UUID
    title: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    last_activity_at: datetime.datetime


@dataclass(frozen=True)
class MessageRecord:
    id: uuid.UUID
    conversation_id: uuid.UUID
    seq: int
    role: str
    content: str
    sources: list[dict]
    created_at: datetime.datetime


_CONVERSATION_COLUMNS = "id, user_id, hospital_id, title, created_at, updated_at, last_activity_at"
_MESSAGE_COLUMNS = "id, conversation_id, seq, role, content, sources, created_at"


def create_conversation(session: Session, *, user_id: uuid.UUID, hospital_id: uuid.UUID) -> ConversationRecord:
    row = (
        session.execute(
            text(
                f"INSERT INTO conversations (user_id, hospital_id) VALUES (:user_id, :hospital_id) "
                f"RETURNING {_CONVERSATION_COLUMNS}"
            ),
            {"user_id": user_id, "hospital_id": hospital_id},
        )
        .mappings()
        .one()
    )
    return ConversationRecord(**row)


def get_conversation(session: Session, conversation_id: uuid.UUID) -> ConversationRecord | None:
    row = (
        session.execute(text(f"SELECT {_CONVERSATION_COLUMNS} FROM conversations WHERE id = :id"), {"id": conversation_id})
        .mappings()
        .first()
    )
    return ConversationRecord(**row) if row else None


def list_conversations_for_user(
    session: Session, *, user_id: uuid.UUID, hospital_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[ConversationRecord], int]:
    """Always self-scoped, regardless of role - listing is "my
    conversations", never "conversations I'm allowed to see" (the latter,
    broader concept doesn't exist here - see docs/CONVERSATION_HISTORY.md,
    "Authorization"). Ordered by `last_activity_at DESC`
    (`idx_conversations_user_id_last_activity_at`, migration 0021) so the
    most recently active conversation always sorts first, matching what
    `touch_conversation` actually updates every turn.
    """
    params = {"user_id": user_id, "hospital_id": hospital_id}
    total = session.execute(
        text("SELECT count(*) FROM conversations WHERE user_id = :user_id AND hospital_id = :hospital_id"), params
    ).scalar_one()
    rows = (
        session.execute(
            text(
                f"SELECT {_CONVERSATION_COLUMNS} FROM conversations "
                "WHERE user_id = :user_id AND hospital_id = :hospital_id "
                "ORDER BY last_activity_at DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return [ConversationRecord(**row) for row in rows], total


def set_title_if_unset(session: Session, conversation_id: uuid.UUID, title: str) -> None:
    """Only ever sets a title once (the `WHERE title IS NULL` makes this
    safe to call on every turn without overwriting a title a prior turn
    already set - see conversation_service.py's `record_turn`)."""
    session.execute(
        text("UPDATE conversations SET title = :title WHERE id = :id AND title IS NULL"),
        {"id": conversation_id, "title": title},
    )


def touch_conversation(session: Session, conversation_id: uuid.UUID) -> None:
    session.execute(
        text("UPDATE conversations SET last_activity_at = now() WHERE id = :id"), {"id": conversation_id}
    )


def add_message(
    session: Session, *, conversation_id: uuid.UUID, role: str, content: str, sources: list[dict] | None = None
) -> MessageRecord:
    """`role` must already be one of 'USER'/'ASSISTANT' by the time it
    reaches here (see conversation_service.py) - the DB's own CHECK
    constraint is defense in depth, not the primary validation point.
    `sources` (Phase 17) is only ever non-empty for an ASSISTANT message -
    a USER message is never given one."""
    row = (
        session.execute(
            text(
                f"INSERT INTO conversation_messages (conversation_id, role, content, sources) "
                f"VALUES (:conversation_id, :role, :content, CAST(:sources AS jsonb)) RETURNING {_MESSAGE_COLUMNS}"
            ),
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "sources": json.dumps(sources or []),
            },
        )
        .mappings()
        .one()
    )
    return MessageRecord(**row)


def list_recent_messages(session: Session, conversation_id: uuid.UUID, *, max_turns: int) -> list[MessageRecord]:
    """Returns up to `max_turns` turns (a turn = one USER+ASSISTANT pair,
    so up to `max_turns * 2` messages) in chronological order - the
    bounded history bound to `app/core/config.py`'s
    `conversation_max_turns` (see docs/CONVERSATIONAL_AUTH_ROUTING.md,
    "Bounded history"). Fetches the most recent `max_turns * 2` rows by
    `seq DESC` (one indexed, LIMIT-bounded query - never the whole
    conversation), then reverses to chronological order.

    Reused unchanged for two purposes: building the LLM's bounded history
    text (Phase 13, `content`/`role` only) and rendering a conversation's
    message history in the UI (Phase 17, also uses `sources`) - one query,
    one bound, not two divergent implementations of "how much history is
    enough" (see docs/CONVERSATION_HISTORY.md, "Reused, not duplicated").
    """
    rows = (
        session.execute(
            text(
                f"SELECT {_MESSAGE_COLUMNS} FROM conversation_messages "
                "WHERE conversation_id = :conversation_id ORDER BY seq DESC LIMIT :limit"
            ),
            {"conversation_id": conversation_id, "limit": max_turns * 2},
        )
        .mappings()
        .all()
    )
    return [MessageRecord(**row) for row in reversed(rows)]
