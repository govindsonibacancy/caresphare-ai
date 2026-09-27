"""Conversation ownership, bounded history loading/formatting, message
persistence, and (Phase 17) conversation listing/detail retrieval for the
frontend history UX. See docs/CONVERSATIONAL_AUTH_ROUTING.md and
docs/CONVERSATION_HISTORY.md.

This is NOT an authorization system for clinical/RAG data - it only
answers "may this caller see/continue this conversation", a simple
identity check, never "may this caller see this appointment/document",
which remains entirely Phase 4/6/9/11's job, re-run fresh on every turn
regardless of what conversation this is part of (see
app/llm/answer_service.py). Conversation history can only ever influence
*what the current question is asking about* - never *whether the caller
is allowed to have it answered*.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import conversation_repository
from app.repositories.conversation_repository import ConversationRecord, MessageRecord

ROLE_USER = "USER"
ROLE_ASSISTANT = "ASSISTANT"

# A conversation list is capped the same way every other list endpoint in
# this codebase caps a page (see app/schemas/pagination.py) - a user's
# conversation *count* has no upper bound (unlike message count per
# conversation, which conversation_max_turns already bounds), so this list
# must be paginated for real, not just in principle.
CONVERSATION_LIST_MAX_PAGE_SIZE = 50

# Deterministic title derivation - see docs/CONVERSATION_HISTORY.md,
# "Conversation title". Never an LLM call: the title is exactly a bounded,
# whitespace-collapsed prefix of the user's own first message, nothing
# invented or summarized.
_TITLE_MAX_LENGTH = 60
_WHITESPACE_RE = re.compile(r"\s+")


class ConversationNotFound(Exception):
    """Raised for both a genuinely nonexistent conversation and one that
    exists but belongs to someone else - identical handling, so a caller
    can never distinguish the two (see docs/CONVERSATIONAL_AUTH_ROUTING.md,
    "Ownership" - the same enumeration-protection convention Phase 7's
    document endpoints already established)."""


@dataclass(frozen=True)
class BoundedHistory:
    text: str
    turn_count: int


def _can_access_conversation(scope: UserScope, conversation: ConversationRecord) -> bool:
    """SUPER_ADMIN gets the same unconditional bypass every other
    can_access_*-style check in this codebase already gives it (Phase 4's
    can_access_patient/can_access_appointment, Phase 8/9's rag_repository
    predicate) - not a new bypass, the existing one applied consistently.
    Every other role must own the conversation outright: both the user and
    the hospital it was created under must match exactly."""
    if scope.role is Role.SUPER_ADMIN:
        return True
    return conversation.user_id == scope.user_id and conversation.hospital_id == scope.hospital_id


def resolve_conversation(session: Session, *, scope: UserScope, conversation_id: uuid.UUID | None) -> ConversationRecord:
    """No `conversation_id` -> start a brand new conversation owned by the
    current caller. A provided `conversation_id` is never trusted at face
    value - it is looked up, and ownership is verified against the
    *current* `UserScope`, not anything cached from a previous request.
    """
    if conversation_id is None:
        return conversation_repository.create_conversation(session, user_id=scope.user_id, hospital_id=scope.hospital_id)

    conversation = conversation_repository.get_conversation(session, conversation_id)
    if conversation is None or not _can_access_conversation(scope, conversation):
        raise ConversationNotFound("conversation not found")
    return conversation


def list_conversations(session: Session, *, scope: UserScope, page: int, page_size: int) -> tuple[list[ConversationRecord], int]:
    """Always "my conversations" - self-scoped by `scope.user_id`
    regardless of role (see docs/CONVERSATION_HISTORY.md,
    "Authorization"). SUPER_ADMIN's existing per-conversation bypass
    (`_can_access_conversation`) is unrelated - it lets SUPER_ADMIN *open*
    a specific conversation by id if needed, never lists every user's
    conversations as "their own"."""
    page_size = min(page_size, CONVERSATION_LIST_MAX_PAGE_SIZE)
    return conversation_repository.list_conversations_for_user(
        session, user_id=scope.user_id, hospital_id=scope.hospital_id, limit=page_size, offset=(page - 1) * page_size
    )


def get_conversation_detail(
    session: Session, *, scope: UserScope, conversation_id: uuid.UUID
) -> tuple[ConversationRecord, list[MessageRecord]]:
    """Reuses `resolve_conversation`'s exact ownership check (never a
    second, divergent authorization path) plus the same bounded
    `list_recent_messages` query Phase 13's LLM-context loading already
    uses - a conversation's displayed history is never larger than what
    the backend itself already considers "the active window" (see
    docs/CONVERSATION_HISTORY.md, "Bounded, not paginated, message
    history")."""
    conversation = resolve_conversation(session, scope=scope, conversation_id=conversation_id)
    settings = get_settings()
    messages = conversation_repository.list_recent_messages(session, conversation.id, max_turns=settings.conversation_max_turns)
    return conversation, messages


def load_bounded_history(session: Session, conversation_id: uuid.UUID) -> BoundedHistory:
    """Loads at most `conversation_max_turns` recent turns (one bounded,
    indexed query - see conversation_repository.list_recent_messages),
    then formats them as plain `Role: content` lines, dropping the
    *oldest* included turns first if the formatted text would still
    exceed `conversation_max_context_chars` - never truncating a single
    message mid-sentence, and never silently including more than the
    configured budget in what's sent to the LLM.
    """
    settings = get_settings()
    messages = conversation_repository.list_recent_messages(session, conversation_id, max_turns=settings.conversation_max_turns)

    lines = [f"{'User' if m.role == ROLE_USER else 'Assistant'}: {m.content}" for m in messages]

    # Drop oldest lines first until the joined text fits the char budget -
    # mirrors app/rag/context.py's "never include a lower-priority item
    # ahead of a higher-priority one" philosophy, just inverted (here,
    # *recency* is priority, so the oldest turns are dropped, never the
    # newest).
    while lines and len("\n".join(lines)) > settings.conversation_max_context_chars:
        lines.pop(0)

    return BoundedHistory(text="\n".join(lines), turn_count=len(lines) // 2)


def _derive_title(user_message: str) -> str:
    """A bounded, whitespace-collapsed prefix of the user's own first
    message - never an LLM call, never anything beyond what the user
    already typed (see docs/CONVERSATION_HISTORY.md, "Conversation
    title"). Collapsing whitespace keeps a title that spans newlines from
    looking broken in a single-line history list."""
    collapsed = _WHITESPACE_RE.sub(" ", user_message).strip()
    if len(collapsed) <= _TITLE_MAX_LENGTH:
        return collapsed
    return collapsed[:_TITLE_MAX_LENGTH].rstrip() + "…"


def record_turn(
    session: Session,
    *,
    conversation: ConversationRecord,
    user_message: str,
    assistant_message: str,
    assistant_sources: list[dict] | None = None,
) -> None:
    """Persists exactly one USER message and one ASSISTANT message - never
    called for a hard failure (see app/llm/answer_service.py, which only
    reaches this after a response was successfully computed, including the
    safe canned-message branches). Message length is validated by the
    request schema before this is ever reached (see app/schemas/rag.py) -
    this function trusts its caller on that, but the DB's own CHECK
    constraint (non-blank content) is defense in depth regardless.

    `assistant_sources` (Phase 17) is persisted alongside the assistant
    message so a historical turn can render its citations exactly like a
    freshly-generated one - see docs/CONVERSATION_HISTORY.md, "Sources and
    citations". The conversation's `title` (Phase 17) is set from this
    turn's user message only if it doesn't already have one - every turn
    after the first is a no-op for the title.
    """
    conversation_repository.set_title_if_unset(session, conversation.id, _derive_title(user_message))
    conversation_repository.add_message(session, conversation_id=conversation.id, role=ROLE_USER, content=user_message)
    conversation_repository.add_message(
        session,
        conversation_id=conversation.id,
        role=ROLE_ASSISTANT,
        content=assistant_message,
        sources=assistant_sources,
    )
    conversation_repository.touch_conversation(session, conversation.id)
