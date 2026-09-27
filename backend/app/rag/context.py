"""Builds a bounded, numbered LLM context from Phase 9's already-
authorized, already-reranked results - see docs/LLM_GENERATION.md,
"Context construction". Pure and DB-free: takes `RagSearchResult` objects
an authorized retrieval already produced, never queries anything itself,
never re-derives authorization, never sees a chunk retrieval didn't
already prove authorized.

Relevance order (Phase 9's reranked order) is preserved exactly - sources
are numbered SOURCE 1, SOURCE 2, ... in the order they arrived, never
reordered or re-scored here.
"""

import dataclasses

from app.schemas.rag import RagSearchResult

_MIN_USEFUL_CONTENT_CHARS = 20  # a block this small or smaller isn't worth truncating-and-keeping


@dataclasses.dataclass(frozen=True)
class ContextSource:
    """One source actually included in the built context - `number` is the
    stable "SOURCE N" label used both in the prompt text and, via
    `RagAnswerResponse.sources`, in the API response. This is the backend's
    own authoritative numbering; the model is never trusted to invent or
    renumber sources (see docs/LLM_GENERATION.md, "Citation security")."""

    number: int
    result: RagSearchResult


@dataclasses.dataclass(frozen=True)
class ContextBundle:
    text: str
    sources: list[ContextSource]


def _format_block(number: int, result: RagSearchResult, *, content: str) -> str:
    lines = [f"SOURCE {number}", f"Title: {result.document_title}", f"Document Type: {result.document_type}"]
    if result.page is not None:
        lines.append(f"Page: {result.page}")
    if result.section:
        lines.append(f"Section: {result.section}")
    lines.append("Content:")
    lines.append(content)
    return "\n".join(lines)


def build_context(
    results: list[RagSearchResult], *, max_chunks: int, max_chars: int, start_number: int = 1
) -> ContextBundle:
    """Enforces BOTH limits together: at most `max_chunks` sources, and the
    combined formatted text never exceeds `max_chars`. A source that would
    overflow the remaining character budget has its `content` truncated
    (plain Python string slicing - always code-point-safe, since `str` is
    already a sequence of Unicode code points, never raw bytes) rather than
    being dropped outright, unless the remaining budget is too small to
    hold anything useful, in which case retrieval stops there - lower-
    ranked sources are never included ahead of higher-ranked ones to "make
    room".

    `start_number` (Phase 12) lets a caller continue a single, unified
    "SOURCE N" numbering across multiple source kinds - see
    docs/SOURCES_AND_CITATIONS.md, "Source registry". Structured sources
    (if any) are registered first, so RAG numbering picks up where they
    left off; called with no `start_number`, this is unchanged from
    Phase 10 (numbering starts at 1).
    """
    sources: list[ContextSource] = []
    blocks: list[str] = []
    remaining_chars = max_chars
    separator = "\n\n"

    for result in results[:max_chunks]:
        # Blocks are joined with a separator - every block after the first
        # costs its own length *plus* one separator, which must be counted
        # against the budget too, or the final joined text can overshoot
        # `max_chars` by a couple of characters per source.
        separator_cost = len(separator) if blocks else 0
        content = result.content
        number = start_number + len(sources)
        block = _format_block(number, result, content=content)

        if len(block) + separator_cost > remaining_chars:
            overhead = len(block) - len(content) + separator_cost  # header/labels never truncated, only content
            available_for_content = remaining_chars - overhead
            if available_for_content < _MIN_USEFUL_CONTENT_CHARS:
                break
            content = content[:available_for_content]
            block = _format_block(number, result, content=content)

        blocks.append(block)
        sources.append(ContextSource(number=number, result=result))
        remaining_chars -= len(block) + separator_cost
        if remaining_chars <= 0:
            break

    return ContextBundle(text=separator.join(blocks), sources=sources)
