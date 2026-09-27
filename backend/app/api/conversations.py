"""GET /api/conversations, GET /api/conversations/{id} - Phase 17. See
docs/CONVERSATION_HISTORY.md.

No permission gate beyond authentication, matching /api/rag/answer's own
model - there is no "permission to have a conversation"; every
authenticated role can already start one via chat (Phase 13). Ownership
(never a broader permission check) is the entire authorization boundary
here, enforced by app/services/conversation_service.py, never by this
router or by the frontend.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api._clinical_common import PageParam, PageSizeParam
from app.core.db import get_db
from app.permissions.dependencies import get_user_scope
from app.permissions.scope import UserScope
from app.schemas.conversations import ConversationDetail, ConversationMessage, ConversationSummary
from app.schemas.pagination import Page
from app.services import conversation_service
from app.services.conversation_service import ConversationNotFound

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _to_summary(record) -> ConversationSummary:
    return ConversationSummary(
        id=record.id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_activity_at=record.last_activity_at,
    )


@router.get("", response_model=Page[ConversationSummary])
def list_conversations(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[ConversationSummary]:
    """Always the caller's own conversations - see
    conversation_service.list_conversations. Summary fields only; no
    message is ever loaded to render this list (see
    docs/CONVERSATION_HISTORY.md, "History data")."""
    records, total = conversation_service.list_conversations(db, scope=scope, page=page, page_size=page_size)
    return Page(items=[_to_summary(r) for r in records], page=page, page_size=page_size, total=total)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    """A conversation that doesn't exist, or belongs to someone else, is a
    404 - identical in shape for both cases (see
    docs/CONVERSATIONAL_AUTH_ROUTING.md, "Ownership"), never revealing
    whether another user's conversation exists."""
    try:
        conversation, messages = conversation_service.get_conversation_detail(db, scope=scope, conversation_id=conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc

    return ConversationDetail(
        **_to_summary(conversation).model_dump(),
        messages=[
            ConversationMessage(id=m.id, role=m.role, content=m.content, sources=m.sources, created_at=m.created_at)
            for m in messages
        ],
    )
