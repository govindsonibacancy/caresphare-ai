-- Phase 9: lexical (full-text) search over document_chunks.content, to
-- complement Phase 8's vector similarity search. See
-- docs/RAG_HYBRID_SEARCH.md.
--
-- A STORED generated column, not an application-maintained one: Postgres
-- computes it automatically from `content` for every existing row when
-- this ALTER TABLE runs, and for every future row on INSERT - Phase 7's
-- ingestion code (app/repositories/document_repository.py's insert_chunk)
-- needs no change at all for newly ingested chunks to become lexically
-- searchable. 'english' matches this project's only supported content
-- language (see docs/RAG_INGESTION.md - no i18n/multi-language ingestion
-- exists yet).

ALTER TABLE document_chunks
    ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX idx_document_chunks_search_vector ON document_chunks USING GIN (search_vector);
