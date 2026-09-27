"""Phase 12 - unified source authority and citation model. See
docs/SOURCES_AND_CITATIONS.md.

The backend is the sole owner of source identity: a `SourceRegistry` is
built fresh per answer-generation request from already-authorized RAG and
structured results only, and the LLM's own citation markers
(`[Source N]`) are validated against it afterward, never trusted to
create a source themselves.
"""
