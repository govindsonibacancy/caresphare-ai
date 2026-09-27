import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.request_context import get_request_id
from app.llm.answer_service import AnswerError, ConversationNotFound
from app.llm.answer_service import answer as generate_answer
from app.permissions.dependencies import get_user_scope
from app.permissions.scope import UserScope
from app.rag.retrieval_service import RetrievalError, search
from app.schemas.rag import RagAnswerRequest, RagAnswerResponse, RagSearchRequest, RagSearchResponse

router = APIRouter(prefix="/api/rag", tags=["rag"])


@router.post("/search", response_model=RagSearchResponse)
def search_documents(
    payload: RagSearchRequest,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
    request_id: uuid.UUID = Depends(get_request_id),
) -> RagSearchResponse:
    """No permission gate beyond authentication - see docs/RAG_RETRIEVAL.md,
    "Who can call this endpoint". Every result is independently authorized
    per-document by app/repositories/rag_repository.py's SQL predicate; a
    caller with no authorized documents (e.g. PATIENT, who holds no
    document_allowed_roles/authorized_doctors/authorized_staff grant under
    the current seed) simply gets `{"results": []}` - the same shape a
    caller with authorized-but-irrelevant documents would get, so no
    response shape ever reveals whether unauthorized documents exist.
    """
    try:
        response = search(db, scope=scope, query=payload.query, top_k=payload.top_k, request_id=request_id)
    except RetrievalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return response.model_copy(update={"request_id": request_id})


@router.post("/answer", response_model=RagAnswerResponse)
def answer_question(
    payload: RagAnswerRequest,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
    request_id: uuid.UUID = Depends(get_request_id),
) -> RagAnswerResponse:
    """`RagAnswerRequest` extends `RagSearchRequest` with exactly one
    optional field, `conversation_id` (Phase 13) - no field here can name
    a model, temperature, system prompt, hospital/user/role, or any other
    authorization value; see docs/LLM_GENERATION.md, "Public RAG answer
    API" and docs/CONVERSATIONAL_AUTH_ROUTING.md, "Conversation API".
    Grounded generation over Phase 9/11's authorized retrieval, with
    Phase 13's bounded conversation context - never SQL generation, never
    unbounded/long-term memory, never LLM database access.

    A `conversation_id` naming a conversation that doesn't exist, or
    belongs to someone else, is a `404` - identical in shape for both
    cases, so this endpoint never reveals whether another user's
    conversation exists (see docs/CONVERSATIONAL_AUTH_ROUTING.md,
    "Ownership").

    `request_id` (Phase 15) is a server-generated correlation id (see
    app/core/request_context.py) - never accepted from the client,
    carried through to every audit event this call produces, and echoed
    back on the response purely for support/debugging correlation, never
    as an authorization mechanism.
    """
    try:
        response = generate_answer(
            db,
            scope=scope,
            query=payload.query,
            top_k=payload.top_k,
            conversation_id=payload.conversation_id,
            request_id=request_id,
        )
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    except AnswerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return response.model_copy(update={"request_id": request_id})
