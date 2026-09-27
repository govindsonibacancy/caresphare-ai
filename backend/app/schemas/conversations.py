"""Request/response shapes for GET /api/conversations and
GET /api/conversations/{id} (Phase 17). See docs/CONVERSATION_HISTORY.md.

`SourceReference` is imported, not redefined - a historical message's
citations must be exactly the same shape `POST /api/rag/answer` already
returns, never a second, divergent source model (see
docs/SOURCES_AND_CITATIONS.md, "Unified source model").
"""

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel

from app.schemas.rag import SourceReference


class ConversationSummary(BaseModel):
    """One row of GET /api/conversations - summary fields only, never
    message content (see docs/CONVERSATION_HISTORY.md, "History data" -
    the list view must never require loading every conversation's
    messages just to render itself)."""

    id: uuid.UUID
    title: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    last_activity_at: datetime.datetime


class ConversationMessage(BaseModel):
    """One message in a conversation's bounded history - the same
    content/role Phase 13 already persists, plus (Phase 17) the sources
    that backed an ASSISTANT message. `sources` is always `[]` for a USER
    message."""

    id: uuid.UUID
    role: Literal["USER", "ASSISTANT"]
    content: str
    sources: list[SourceReference]
    created_at: datetime.datetime


class ConversationDetail(ConversationSummary):
    """GET /api/conversations/{id} - a conversation plus its bounded
    message history (see docs/CONVERSATION_HISTORY.md, "Bounded, not
    paginated, message history" - the same `conversation_max_turns`
    window Phase 13's LLM context already uses, never the full,
    unbounded table)."""

    messages: list[ConversationMessage]
