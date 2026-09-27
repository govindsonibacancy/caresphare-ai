"""Request/response shapes for POST /api/rag/search - see
docs/RAG_RETRIEVAL.md and docs/RAG_HYBRID_SEARCH.md. `RagSearchRequest`
deliberately has no field that could name a user/hospital/role/doctor/
staff/permission - the authenticated identity is the only source of
authorization for this endpoint (see app/permissions/scope.py, UserScope),
and `extra="forbid"` rejects any attempt to submit one rather than
silently ignoring it.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sources.models import SourceType

MAX_QUERY_LENGTH = 2000
MAX_TOP_K = 20
DEFAULT_TOP_K = 5


class RagSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=MAX_QUERY_LENGTH)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class RagSearchResult(BaseModel):
    """One authorized chunk. Never includes the raw embedding vector,
    internal storage keys, or anything beyond what's needed to eventually
    show/cite a source - see docs/RAG_RETRIEVAL.md, "Result schema"."""

    document_id: uuid.UUID
    document_title: str
    document_type: str
    chunk_id: uuid.UUID
    chunk_index: int
    content: str
    # As of Phase 9, this is the final hybrid/reranked score (RRF fusion of
    # vector + lexical rank, plus a deterministic query-term-overlap boost)
    # - NOT a raw cosine similarity anymore. See docs/RAG_HYBRID_SEARCH.md,
    # "Hybrid scoring"/"Reranking". Still higher-is-better, but no longer
    # bounded to [0, 1] or comparable to a Phase-8-era score.
    score: float
    page: int | None
    section: str | None


class RagSearchResponse(BaseModel):
    results: list[RagSearchResult]
    # Phase 15: a server-generated correlation id (app/core/request_context.py)
    # echoed back for support/debugging purposes only - never an
    # authorization mechanism, never accepted from the client. `None` only
    # as this schema's own default before the API layer attaches the real
    # value - see docs/AUDIT_AND_OBSERVABILITY.md, "Request/correlation ID".
    request_id: uuid.UUID | None = None


# --- POST /api/rag/answer (Phase 10) - see docs/LLM_GENERATION.md ---------


class RagAnswerRequest(RagSearchRequest):
    """Extends `RagSearchRequest` (same `query`/`top_k`, same
    `extra="forbid"` protection against a client-supplied authorization/
    context/model/system-prompt override) with exactly one additional
    field - `conversation_id` (Phase 13, see
    docs/CONVERSATIONAL_AUTH_ROUTING.md, "Conversation API"). Optional on
    the first turn (a new conversation is created); on any later turn it
    is never trusted at face value - the backend looks it up and verifies
    ownership against the *current* `UserScope`
    (app/services/conversation_service.py) before using it for anything.
    There is still no field here that could name a hospital/user/role/
    permission, or override generation parameters - `extra="forbid"`
    (inherited) rejects any attempt to add one.
    """

    conversation_id: uuid.UUID | None = None


class SourceReference(BaseModel):
    """Metadata only - never chunk content (the answer text itself already
    reflects it), never embeddings, never storage keys or authorization
    detail, never a raw structured-record foreign key. `number`/`id` are
    the backend's own stable "SOURCE N" label (see
    app/sources/registry.py, Phase 12) - the same label the model was
    instructed to cite - so a client can match a `[Source N]` citation in
    `answer` back to this authoritative list. See
    docs/SOURCES_AND_CITATIONS.md, "Citation validation": the model's own
    citations are never trusted as a source of truth - this list, computed
    entirely from already-authorized retrieval results before generation
    ever ran, is.

    The `document_*`/`chunk_id`/`page`/`section` fields are populated only
    for `type == SourceType.DOCUMENT` (a RAG chunk) - `None` for every
    structured source type, which carries no field beyond `label` at all
    (see docs/SOURCES_AND_CITATIONS.md, "Structured sources").
    """

    number: int
    id: str
    type: SourceType
    label: str
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    document_type: str | None = None
    chunk_id: uuid.UUID | None = None
    page: int | None = None
    section: str | None = None


class RagAnswerResponse(BaseModel):
    answer: str
    sources: list[SourceReference]
    # None specifically means no generation call was made at all (the
    # no-context path - see docs/LLM_GENERATION.md, "No-context behavior")
    # - never a placeholder for "unknown".
    model: str | None
    # Phase 13: always populated in the actual HTTP response - the
    # server-assigned id of the conversation this turn belongs to (newly
    # created if the request didn't supply one). Pass it back as
    # `RagAnswerRequest.conversation_id` to continue the same bounded
    # conversation. `None` only as this schema's own default before
    # app/llm/answer_service.py's `answer()` attaches the real value - see
    # docs/CONVERSATIONAL_AUTH_ROUTING.md, "Conversation API".
    conversation_id: uuid.UUID | None = None
    # Phase 15: see RagSearchResponse.request_id above - same purpose,
    # same guarantees.
    request_id: uuid.UUID | None = None
