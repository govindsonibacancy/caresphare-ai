"""Grounded answer generation - orchestrates Phase 13 conversation
ownership/history, Phase 11 query routing, Phase 9 RAG retrieval, the
Phase 11 structured query service, the Phase 12 unified source registry,
context construction, prompt building, Phase 10's Ollama generation, and
Phase 15's audit/observability metadata. See docs/LLM_GENERATION.md
("Answer pipeline"), docs/QUERY_ROUTING.md, docs/SOURCES_AND_CITATIONS.md,
docs/CONVERSATIONAL_AUTH_ROUTING.md, and docs/AUDIT_AND_OBSERVABILITY.md.

Order is fixed and never reversed: conversation ownership -> bounded
history load -> routing (a classification only, never an authorization
decision, now optionally history-aware) -> the appropriate CURRENT
authorized retrieval (RAG and/or structured, always re-run fresh this
turn - never reused from a previous turn) -> source registration ->
bounded context -> Phase 10's exact prompt/LLM machinery -> citation
validation -> persist this turn -> audit. There is no code path here (or
anywhere in this codebase) where the model decides what to retrieve, sees
a SQL string, queries the database itself, invents a source, requests
additional data, or is granted access to something because a previous
turn was once authorized to see it - see docs/LLM_GENERATION.md, "No LLM
tool access", docs/SOURCES_AND_CITATIONS.md, "Authorization boundary", and
docs/CONVERSATIONAL_AUTH_ROUTING.md, "Authorization" (conversation context
is never authorization).
"""

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.audit import events as audit_events
from app.audit.taxonomy import AuditResult, ErrorCode
from app.core.config import get_settings
from app.llm.ollama_client import LLMGenerationTimeout, LLMServiceError, get_llm_service
from app.llm.prompts import build_messages
from app.permissions.scope import UserScope
from app.rag.context import build_context
from app.rag.retrieval_service import RetrievalError
from app.rag.retrieval_service import search as retrieval_search
from app.routing.intents import Route, StructuredIntent
from app.routing.query_router import route_query
from app.schemas.rag import RagAnswerResponse, SourceReference
from app.services import conversation_service
from app.services.structured_query_service import (
    StructuredOutcome,
    StructuredResult,
    build_source_label,
    is_intent_authorized,
    run_structured_query,
)
from app.sources.citations import validate_citations
from app.sources.models import STRUCTURED_SOURCE_TYPES, Source, SourceType
from app.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

_UNSUPPORTED_MESSAGE = (
    "I can't help with that request. I can answer questions about your own hospital data "
    "(such as appointments, lab reports, or prescriptions) or hospital policies and procedures."
)
_AMBIGUOUS_INTENT_MESSAGE = (
    "Your question could mean a few different things - could you clarify whether you're asking about "
    "your medical records, lab reports, or prescriptions?"
)


class AnswerError(Exception):
    """Wraps a retrieval or LLM-generation failure into a safe message for
    the API layer - never a raw connection error or internal detail."""


# Re-exported so tests can monkeypatch/reference it as
# `app.llm.answer_service.ConversationNotFound` alongside `AnswerError`,
# without importing from app.services.conversation_service directly.
ConversationNotFound = conversation_service.ConversationNotFound


def _register_structured_sources(registry: SourceRegistry, result: StructuredResult) -> list[Source]:
    """Registers every record actually returned by an authorized
    structured query - never a fresh lookup, never more than what
    `run_structured_query` already proved authorized. Returns the
    registered `Source` objects in the same order as `result.records`, so
    a caller can zip them together for text formatting."""
    source_type = STRUCTURED_SOURCE_TYPES.get(result.source, SourceType.DOCUMENT)
    return [registry.add(type=source_type, label=build_source_label(result.source, record)) for record in result.records]


def _structured_text(result: StructuredResult, registered: list[Source]) -> str:
    blocks = []
    for source, record in zip(registered, result.records, strict=True):
        data = ", ".join(f"{key}: {value}" for key, value in record.items() if value is not None)
        blocks.append(f"SOURCE {source.number}\nType: {source.type.value.upper()}\nLabel: {source.label}\nData:\n{data}")
    return "\n\n".join(blocks)


def _source_reference(source: Source) -> SourceReference:
    return SourceReference(
        number=source.number,
        id=source.id,
        type=source.type,
        label=source.label,
        document_id=source.document_id,
        document_title=source.document_title,
        document_type=source.document_type,
        chunk_id=source.chunk_id,
        page=source.page,
        section=source.section,
    )


def _audit(
    session: Session,
    scope: UserScope,
    *,
    request_id: uuid.UUID | None,
    route: Route,
    intent: StructuredIntent | None,
    result: AuditResult,
    result_count: int,
    model: str | None,
    conversation_id: uuid.UUID | None = None,
    classification_source: str | None = None,
    total_duration_ms: int | None = None,
    retrieval_duration_ms: int | None = None,
    generation_duration_ms: int | None = None,
    source_types: list[str] | None = None,
    cited_valid_count: int | None = None,
    cited_invalid_count: int | None = None,
    error_code: ErrorCode | None = None,
) -> None:
    # Never the query text, the prompt, structured record content, RAG
    # content, conversation history, or the generated answer - see
    # docs/LLM_GENERATION.md, "Audit logging", docs/QUERY_ROUTING.md,
    # "Audit logging", docs/CONVERSATIONAL_AUTH_ROUTING.md, "Audit
    # logging", and docs/AUDIT_AND_OBSERVABILITY.md. Only counts,
    # timings, and closed classification labels - never *which* sources
    # were cited, never message content. Admin AI activity is identified
    # here by `structured_intent` starting with `ADMIN_` - there is no
    # separate admin-specific event type (see docs/ADMIN_AI.md,
    # docs/AUDIT_AND_OBSERVABILITY.md, "Admin AI events").
    metadata: dict[str, object] = {
        "route": route.value,
        "result": result.value,
        "result_count": result_count,
        "model": model,
    }
    if intent is not None:
        metadata["structured_intent"] = intent.value
    if conversation_id is not None:
        metadata["conversation_id"] = str(conversation_id)
    if classification_source is not None:
        metadata["classification_source"] = classification_source
    if total_duration_ms is not None:
        metadata["total_duration_ms"] = total_duration_ms
    if retrieval_duration_ms is not None:
        metadata["retrieval_duration_ms"] = retrieval_duration_ms
    if generation_duration_ms is not None:
        metadata["generation_duration_ms"] = generation_duration_ms
    if source_types is not None:
        metadata["source_types"] = source_types
    if cited_valid_count is not None:
        metadata["cited_valid_count"] = cited_valid_count
        metadata["cited_invalid_count"] = cited_invalid_count
    if error_code is not None:
        metadata["error_code"] = error_code.value
    audit_events.record_event(
        session,
        actor_user_id=scope.user_id,
        action="RAG_ANSWER_GENERATED",
        resource_type="rag_answer",
        resource_id=None,
        hospital_id=scope.hospital_id,
        request_id=request_id,
        metadata=metadata,
    )
    session.commit()


def _generate(
    session: Session,
    *,
    scope: UserScope,
    query: str,
    top_k: int,
    history: str | None,
    request_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> RagAnswerResponse:
    """The Phase 9-14 pipeline, essentially unchanged in shape except that
    `route_query`/`build_messages` optionally receive bounded conversation
    `history`, and every return/raise point now also records a Phase 15
    audit event carrying safe, allowlisted metadata (never the query text
    or answer text). Returns a response with no `conversation_id` set; the
    caller (`answer()`) attaches it.
    """
    settings = get_settings()
    start_total = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start_total) * 1000)

    route = route_query(query, history=history)

    if route.route == Route.UNSUPPORTED:
        _audit(
            session,
            scope,
            request_id=request_id,
            route=route.route,
            intent=None,
            result=AuditResult.UNSUPPORTED,
            result_count=0,
            model=None,
            conversation_id=conversation_id,
            classification_source=route.classification_source,
            total_duration_ms=_elapsed_ms(),
        )
        return RagAnswerResponse(answer=_UNSUPPORTED_MESSAGE, sources=[], model=None)

    if route.route == Route.AMBIGUOUS:
        # Pure intent-ambiguity (e.g. "tell me about my records") is
        # resolved without touching the database at all - there is
        # nothing to retrieve until the user clarifies.
        _audit(
            session,
            scope,
            request_id=request_id,
            route=route.route,
            intent=None,
            result=AuditResult.AMBIGUOUS,
            result_count=0,
            model=None,
            conversation_id=conversation_id,
            classification_source=route.classification_source,
            total_duration_ms=_elapsed_ms(),
        )
        return RagAnswerResponse(answer=_AMBIGUOUS_INTENT_MESSAGE, sources=[], model=None)

    registry = SourceRegistry()
    structured_text: str | None = None
    retrieval_duration_ms = 0

    if route.route in (Route.STRUCTURED, Route.HYBRID) and route.structured_intent is not None:
        # ALWAYS a fresh, current-turn call - never anything cached or
        # reused from a previous turn's result, regardless of whether this
        # turn is a follow-up (see docs/CONVERSATIONAL_AUTH_ROUTING.md,
        # "No authorization through history").
        retrieval_start = time.monotonic()
        structured_result = run_structured_query(
            session, scope=scope, intent=route.structured_intent, entity_reference=route.entity_reference, query=query
        )
        retrieval_duration_ms += int((time.monotonic() - retrieval_start) * 1000)
        if structured_result.outcome == StructuredOutcome.AMBIGUOUS:
            # Entity-level ambiguity (e.g. two matching patient names) -
            # the candidate names are already within the caller's own
            # authorized boundary (see structured_query_service), so
            # listing them is not a leak. Phase 14's admin intents may
            # instead supply a ready-made `ambiguous_message` (e.g. an
            # unresolvable date-range phrase) - see
            # StructuredResult.ambiguous_message and
            # docs/ADMIN_AI.md, "Date-range handling".
            if structured_result.ambiguous_message:
                message = structured_result.ambiguous_message
            else:
                candidates = "; ".join(structured_result.ambiguous_candidates or [])
                message = f"I found more than one match ({candidates}). Could you clarify which one you mean?"
            _audit(
                session,
                scope,
                request_id=request_id,
                route=route.route,
                intent=route.structured_intent,
                result=AuditResult.AMBIGUOUS,
                result_count=0,
                model=None,
                conversation_id=conversation_id,
                classification_source=route.classification_source,
                total_duration_ms=_elapsed_ms(),
                retrieval_duration_ms=retrieval_duration_ms,
            )
            return RagAnswerResponse(answer=message, sources=[], model=None)
        if structured_result.outcome == StructuredOutcome.OK:
            registered = _register_structured_sources(registry, structured_result)
            structured_text = _structured_text(structured_result, registered)

    rag_context_text: str | None = None
    if route.route in (Route.RAG, Route.HYBRID):
        retrieval_start = time.monotonic()
        try:
            # Also always a fresh, current-turn, fully-authorized call -
            # see the comment above.
            search_response = retrieval_search(session, scope=scope, query=query, top_k=top_k)
        except RetrievalError as exc:
            # Never call the LLM with an incomplete/unknown authorization
            # state - a retrieval failure stops here, before any context
            # or prompt is built.
            _audit(
                session,
                scope,
                request_id=request_id,
                route=route.route,
                intent=route.structured_intent,
                result=AuditResult.FAILED,
                result_count=0,
                model=None,
                conversation_id=conversation_id,
                classification_source=route.classification_source,
                total_duration_ms=_elapsed_ms(),
                retrieval_duration_ms=retrieval_duration_ms + int((time.monotonic() - retrieval_start) * 1000),
                error_code=ErrorCode.RETRIEVAL_FAILURE,
            )
            raise AnswerError("The answer generation service is temporarily unavailable.") from exc
        retrieval_duration_ms += int((time.monotonic() - retrieval_start) * 1000)
        if search_response.results:
            # Structured sources (if any) were registered first - RAG
            # numbering picks up where they left off, so the single
            # "SOURCE N" sequence the model sees is unified across both
            # kinds (see docs/SOURCES_AND_CITATIONS.md, "Source ordering").
            context = build_context(
                search_response.results,
                max_chunks=settings.rag_max_context_chunks,
                max_chars=settings.rag_max_context_chars,
                start_number=len(registry) + 1,
            )
            rag_context_text = context.text
            for context_source in context.sources:
                registry.add(
                    type=SourceType.DOCUMENT,
                    label=context_source.result.document_title,
                    document_id=context_source.result.document_id,
                    document_title=context_source.result.document_title,
                    document_type=context_source.result.document_type,
                    chunk_id=context_source.result.chunk_id,
                    page=context_source.result.page,
                    section=context_source.result.section,
                )

    if structured_text is None and rag_context_text is None:
        # No authorized, relevant data from either path - never ask the
        # model to guess, and never call Ollama at all. See
        # docs/LLM_GENERATION.md, "No-context behavior": the message is
        # centralized configuration, not an inline string, specifically so
        # it's easy to audit for accidental leakage of unauthorized-data
        # existence. This is also what protects against stale/revoked
        # authorization on a follow-up turn (see
        # docs/CONVERSATIONAL_AUTH_ROUTING.md, "Stale authorization
        # protection") - a resource no longer authorized simply never
        # produces any text here, history or no history.
        #
        # Phase 15: this branch covers two different audit-relevant
        # situations that must look identical in the RESPONSE (the same
        # safe canned message, per the enumeration-protection convention
        # above) but should NOT look identical in the audit trail - a
        # genuine "nothing relevant exists" versus "the caller isn't
        # authorized for this structured intent at all". `is_intent_authorized`
        # re-checks the exact same permission `run_structured_query` already
        # checked (see its own docstring) purely to choose this label - it
        # never changes what was actually retrieved or returned.
        result = AuditResult.NO_DATA
        if (
            route.route in (Route.STRUCTURED, Route.HYBRID)
            and route.structured_intent is not None
            and not is_intent_authorized(scope, route.structured_intent)
        ):
            result = AuditResult.DENIED
        _audit(
            session,
            scope,
            request_id=request_id,
            route=route.route,
            intent=route.structured_intent,
            result=result,
            result_count=0,
            model=None,
            conversation_id=conversation_id,
            classification_source=route.classification_source,
            total_duration_ms=_elapsed_ms(),
            retrieval_duration_ms=retrieval_duration_ms or None,
        )
        return RagAnswerResponse(answer=settings.rag_answer_no_context_message, sources=[], model=None)

    messages = build_messages(query=query, context_text=rag_context_text, structured_text=structured_text, history_text=history)

    generation_start = time.monotonic()
    try:
        answer_text = get_llm_service().generate(messages=messages)
    except LLMGenerationTimeout as exc:
        _audit(
            session,
            scope,
            request_id=request_id,
            route=route.route,
            intent=route.structured_intent,
            result=AuditResult.FAILED,
            result_count=len(registry),
            model=None,
            conversation_id=conversation_id,
            classification_source=route.classification_source,
            total_duration_ms=_elapsed_ms(),
            retrieval_duration_ms=retrieval_duration_ms or None,
            generation_duration_ms=int((time.monotonic() - generation_start) * 1000),
            error_code=ErrorCode.LLM_TIMEOUT,
        )
        raise AnswerError("The answer generation service is temporarily unavailable.") from exc
    except LLMServiceError as exc:
        _audit(
            session,
            scope,
            request_id=request_id,
            route=route.route,
            intent=route.structured_intent,
            result=AuditResult.FAILED,
            result_count=len(registry),
            model=None,
            conversation_id=conversation_id,
            classification_source=route.classification_source,
            total_duration_ms=_elapsed_ms(),
            retrieval_duration_ms=retrieval_duration_ms or None,
            generation_duration_ms=int((time.monotonic() - generation_start) * 1000),
            error_code=ErrorCode.LLM_UNAVAILABLE,
        )
        raise AnswerError("The answer generation service is temporarily unavailable.") from exc
    generation_duration_ms = int((time.monotonic() - generation_start) * 1000)

    # Provenance bookkeeping only - never re-derives or widens the
    # registry, never fails the answer over an invalid/hallucinated
    # citation (see docs/SOURCES_AND_CITATIONS.md, "Citation validation").
    citations = validate_citations(answer_text, registry)

    _audit(
        session,
        scope,
        request_id=request_id,
        route=route.route,
        intent=route.structured_intent,
        result=AuditResult.SUCCESS,
        result_count=len(registry),
        model=settings.ollama_llm_model,
        conversation_id=conversation_id,
        classification_source=route.classification_source,
        total_duration_ms=_elapsed_ms(),
        retrieval_duration_ms=retrieval_duration_ms or None,
        generation_duration_ms=generation_duration_ms,
        source_types=sorted({source.type.value for source in registry.sources}),
        cited_valid_count=len(citations.valid),
        cited_invalid_count=len(citations.invalid),
    )

    return RagAnswerResponse(
        answer=answer_text,
        sources=[_source_reference(source) for source in registry.sources],
        model=settings.ollama_llm_model,
    )


def answer(
    session: Session,
    *,
    scope: UserScope,
    query: str,
    top_k: int,
    conversation_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
) -> RagAnswerResponse:
    """Public entry point. `conversation_id` is never trusted at face
    value - `conversation_service.resolve_conversation` looks it up and
    verifies ownership against the *current* `UserScope` (see
    docs/CONVERSATIONAL_AUTH_ROUTING.md, "Ownership"); omitting it starts a
    brand new conversation. Every turn re-derives authorization from
    scratch (`_generate` above) regardless of conversation history - the
    conversation only ever supplies bounded, previously-said *text* to
    help interpret what the current question means, never a shortcut
    around retrieval/authorization.

    `request_id` (Phase 15) is a server-generated correlation id (see
    app/core/request_context.py) - optional so every pre-Phase-15 direct
    caller/test keeps working unchanged; carried through to every audit
    event this call produces, never used for authorization.
    """
    try:
        conversation = conversation_service.resolve_conversation(session, scope=scope, conversation_id=conversation_id)
    except ConversationNotFound:
        # Phase 15: a conversation-ownership denial is exactly the kind of
        # security-relevant boundary event docs/AUDIT_AND_OBSERVABILITY.md
        # asks to make observable - audited with the *attempted* id (safe:
        # just a UUID, never conversation content) but no query text, no
        # message content. The response this produces (a 404, identical
        # for "doesn't exist" and "not yours" - see
        # docs/CONVERSATIONAL_AUTH_ROUTING.md, "Ownership") is unaffected.
        audit_events.record_event(
            session,
            actor_user_id=scope.user_id,
            action="CONVERSATION_DENIED",
            resource_type="conversation",
            resource_id=conversation_id,
            hospital_id=scope.hospital_id,
            request_id=request_id,
            metadata={"result": AuditResult.DENIED.value, "error_code": ErrorCode.NOT_FOUND.value},
        )
        session.commit()
        raise

    history = conversation_service.load_bounded_history(session, conversation.id)

    response = _generate(
        session,
        scope=scope,
        query=query,
        top_k=top_k,
        history=history.text or None,
        request_id=request_id,
        conversation_id=conversation.id,
    )

    settings = get_settings()
    conversation_service.record_turn(
        session,
        conversation=conversation,
        user_message=query[: settings.conversation_max_message_chars],
        assistant_message=response.answer[: settings.conversation_max_message_chars],
        # Phase 17: persisted so a historical turn can render the exact
        # same citations a freshly-generated one would - see
        # docs/CONVERSATION_HISTORY.md, "Sources and citations". Plain
        # JSON-safe dicts (mode="json" turns uuid.UUID fields into
        # strings) - the same SourceReference shape already returned to
        # the client in this response, never anything additional.
        assistant_sources=[source.model_dump(mode="json") for source in response.sources],
    )
    session.commit()

    return response.model_copy(update={"conversation_id": conversation.id})
